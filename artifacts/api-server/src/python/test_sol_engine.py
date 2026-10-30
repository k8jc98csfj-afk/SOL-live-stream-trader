import sqlite3
import tempfile
import time
import unittest
from contextlib import closing
from datetime import datetime
from pathlib import Path
from unittest.mock import patch

from sol_engine import (
    DEFAULTS,
    KrakenPublicMarketData,
    PaperTradingEngine,
    build_signal,
    iso_utc,
    to_candle_time,
)


BUY = {
    "direction": "BUY",
    "confidence": 75.0,
    "reason": "test BUY signal",
}
SELL = {
    "direction": "SELL",
    "confidence": 75.0,
    "reason": "test SELL signal",
}
HOLD = {
    "direction": "HOLD",
    "confidence": 0.0,
    "reason": "test HOLD signal",
}


def candle(open_time: int, price: float = 100.0, **overrides: float) -> dict:
    values = {
        "timestamp": to_candle_time(open_time),
        "open_time": open_time,
        "open": price,
        "high": price + 0.1,
        "low": price - 0.1,
        "close": price,
        "volume": 100.0,
    }
    values.update(overrides)
    return values


def make_snapshot(price: float = 100.0) -> dict:
    now_ms = int(time.time() * 1000)
    last_1m_open = (now_ms // 60_000 - 2) * 60_000
    last_5m_open = (now_ms // 300_000 - 1) * 300_000
    candles_1m = [
        candle(last_1m_open - index * 60_000, 100.0 + index * 0.01)
        for index in range(39, -1, -1)
    ]
    candles_5m = [
        candle(last_5m_open - index * 300_000, 100.0 + index * 0.02)
        for index in range(39, -1, -1)
    ]
    candles_1m[-1]["volume"] = 200.0
    return {
        "price": price,
        "bid": price - 0.01,
        "ask": price + 0.01,
        "candles1m": candles_1m,
        "candles5m": candles_5m,
        "updatedAt": iso_utc(),
        "stale": False,
    }


class FakeMarketData:
    def __init__(self, snapshot: dict):
        self.snapshot = snapshot

    def get_snapshot(self) -> dict:
        return self.snapshot


class SolEngineTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp_dir = tempfile.TemporaryDirectory()
        self.db_path = Path(self.temp_dir.name) / "paper.sqlite3"
        self.engine = PaperTradingEngine(self.db_path)
        self.market = FakeMarketData(make_snapshot())
        self.engine.market_data = self.market

    def tearDown(self) -> None:
        self.temp_dir.cleanup()

    def set_running_for_test(self) -> None:
        with closing(sqlite3.connect(self.db_path)) as connection, connection:
            connection.execute("UPDATE account SET running = 1 WHERE id = 1")

    def seed_open_trade(self) -> dict:
        with patch("sol_engine.build_signal", return_value=BUY):
            self.engine.evaluate()  # Stores market data and signal while OFF.
            self.engine.set_running(True)
            self.engine.evaluate()
        with closing(self.engine._connect()) as connection, connection:
            trade = self.engine._open_trade(connection)
            self.assertIsNotNone(trade)
            return dict(trade)

    def test_signal_generation_buy_sell_and_hold(self) -> None:
        bullish_1m = {
            "trend": "bullish",
            "rsi": 52.0,
            "volumeRatio": 1.2,
        }
        bullish_5m = {"trend": "bullish"}
        self.assertEqual(
            build_signal(bullish_1m, bullish_5m, 100)["direction"], "BUY"
        )
        self.assertEqual(
            build_signal(
                {"trend": "bearish", "rsi": 45.0, "volumeRatio": 2.0},
                {"trend": "bearish"},
                100,
            )["direction"],
            "SELL",
        )
        self.assertEqual(
            build_signal(
                {"trend": "bullish", "rsi": 52.0, "volumeRatio": 0.5},
                bullish_5m,
                100,
            )["direction"],
            "HOLD",
        )

    def test_public_ticker_parses_last_bid_and_ask(self) -> None:
        market = KrakenPublicMarketData()
        response = {
            "error": [],
            "result": {
                "SOLUSDC": {
                    "a": ["100.2", "1", "1"],
                    "b": ["100.0", "1", "1"],
                    "c": ["100.1", "0.5"],
                }
            },
        }
        with patch.object(market, "_get_json", return_value=response):
            self.assertEqual(
                market._ticker(),
                {"price": 100.1, "bid": 100.0, "ask": 100.2},
            )

    def test_new_account_and_startup_are_off(self) -> None:
        with closing(sqlite3.connect(self.db_path)) as connection, connection:
            self.assertEqual(
                connection.execute("SELECT running FROM account WHERE id=1").fetchone()[0],
                0,
            )
            connection.execute("UPDATE account SET running = 1 WHERE id = 1")
        self.assertFalse(self.engine.startup()["running"])
        with closing(sqlite3.connect(self.db_path)) as connection, connection:
            self.assertEqual(
                connection.execute("SELECT running FROM account WHERE id=1").fetchone()[0],
                0,
            )

    def test_dashboard_is_read_only(self) -> None:
        with patch("sol_engine.build_signal", return_value=BUY):
            self.engine.evaluate()
        self.set_running_for_test()
        before = self.engine.list_trades()
        reader = PaperTradingEngine(self.db_path, read_only=True)
        reader.dashboard()
        self.assertEqual(reader.list_trades(), before)
        with closing(sqlite3.connect(self.db_path)) as connection, connection:
            self.assertEqual(
                connection.execute("SELECT running FROM account WHERE id=1").fetchone()[0],
                1,
            )

    def test_one_buy_signal_cannot_reenter_on_same_candle(self) -> None:
        self.seed_open_trade()
        with closing(self.engine._connect()) as connection, connection:
            trade = self.engine._open_trade(connection)
            self.assertIsNotNone(trade)
            target = float(trade["take_profit_price"])

        # Exit at target while the same completed-candle BUY signal remains active.
        self.market.snapshot = make_snapshot(target + 0.2)
        with patch("sol_engine.build_signal", return_value=BUY):
            self.engine.evaluate()
            self.engine.evaluate()

        trades = self.engine.list_trades()
        self.assertEqual(len(trades), 1)
        self.assertEqual(trades[0]["status"], "CLOSED")
        self.assertEqual(trades[0]["exitReason"], "Take-profit")

    def test_database_constraint_rejects_a_second_open_position(self) -> None:
        self.seed_open_trade()
        with closing(self.engine._connect()) as connection, connection:
            trade = self.engine._open_trade(connection)
            self.assertIsNotNone(trade)
            with self.assertRaises(sqlite3.IntegrityError):
                connection.execute(
                    """
                    INSERT INTO trades (
                        opened_at, entry_price, quantity, fees_usdc, slippage_usdc,
                        status, fee_percent, slippage_percent, stop_loss_price,
                        take_profit_price
                    ) VALUES (?, ?, ?, ?, ?, 'OPEN', ?, ?, ?, ?)
                    """,
                    (
                        trade["opened_at"],
                        trade["entry_price"],
                        trade["quantity"],
                        trade["fees_usdc"],
                        trade["slippage_usdc"],
                        trade["fee_percent"],
                        trade["slippage_percent"],
                        trade["stop_loss_price"],
                        trade["take_profit_price"],
                    ),
                )

    def test_open_and_realized_pnl_include_all_fees_and_slippage(self) -> None:
        trade = self.seed_open_trade()
        mark_price = 100.2
        with closing(self.engine._connect()) as connection, connection:
            row = self.engine._open_trade(connection)
            self.assertIsNotNone(row)
            unrealized, _, _ = self.engine._position_pnl(row, mark_price)
            entry_price = float(row["entry_price"])
            entry_fee = float(row["fees_usdc"])
            quantity = float(row["quantity"])
            fee_rate = float(row["fee_percent"]) / 100.0
            slip_rate = float(row["slippage_percent"]) / 100.0
            exit_value = quantity * mark_price * (1.0 - slip_rate)
            expected_open = (
                exit_value
                - exit_value * fee_rate
                - quantity * entry_price
                - entry_fee
            )
            self.assertAlmostEqual(unrealized, expected_open, places=8)
            self.engine._close_trade(connection, row, mark_price, "test close")

        closed = self.engine.list_trades()[0]
        self.assertEqual(closed["status"], "CLOSED")
        self.assertGreater(closed["feesUsdc"], trade["fees_usdc"])
        self.assertGreater(closed["slippageUsdc"], 0)
        expected_realized = (
            closed["quantity"] * closed["exitPrice"]
            - closed["feesUsdc"]
            - closed["quantity"] * closed["entryPrice"]
        )
        self.assertAlmostEqual(
            closed["realizedPnlUsdc"], expected_realized, places=8
        )

    def test_stop_loss_uses_adverse_market_fill(self) -> None:
        self.seed_open_trade()
        with closing(self.engine._connect()) as connection, connection:
            trade = self.engine._open_trade(connection)
            stop = float(trade["stop_loss_price"])
        market_price = stop - 0.5
        self.market.snapshot = make_snapshot(market_price)
        with patch("sol_engine.build_signal", return_value=HOLD):
            self.engine.evaluate()
        closed = self.engine.list_trades()[0]
        self.assertEqual(closed["exitReason"], "Stop-loss")
        self.assertLess(closed["exitPrice"], stop)
        self.assertAlmostEqual(
            closed["exitPrice"],
            (market_price - 0.01)
            * (1.0 - DEFAULTS["slippagePercent"] / 100.0),
            places=8,
        )

    def test_candle_gap_fills_stop_at_gap_open_not_stop_level(self) -> None:
        trade = self.seed_open_trade()
        stop = float(trade["stop_loss_price"])
        opened_ms = int(
            datetime.fromisoformat(
                trade["opened_at"].replace("Z", "+00:00")
            ).timestamp()
            * 1000
        )
        gap_open = stop - 5.0
        snapshot = make_snapshot(100.0)
        snapshot["candles1m"].append(
            candle(
                opened_ms + 1,
                gap_open,
                high=gap_open + 0.5,
                low=gap_open - 2.0,
                close=gap_open + 0.2,
            )
        )
        self.market.snapshot = snapshot
        with patch("sol_engine.build_signal", return_value=SELL):
            self.engine.evaluate()
        closed = self.engine.list_trades()[0]
        self.assertEqual(closed["exitReason"], "Stop-loss")
        self.assertAlmostEqual(
            closed["exitPrice"],
            gap_open * (1.0 - DEFAULTS["slippagePercent"] / 100.0),
            places=8,
        )

    def test_ambiguous_candle_that_hits_both_brackets_uses_stop_first(self) -> None:
        trade = self.seed_open_trade()
        stop = float(trade["stop_loss_price"])
        target = float(trade["take_profit_price"])
        opened_ms = int(
            datetime.fromisoformat(
                trade["opened_at"].replace("Z", "+00:00")
            ).timestamp()
            * 1000
        )
        snapshot = make_snapshot(100.0)
        snapshot["candles1m"].append(
            candle(
                opened_ms + 1,
                (stop + target) / 2,
                high=target + 1.0,
                low=stop - 1.0,
                close=100.0,
            )
        )
        self.market.snapshot = snapshot
        with patch("sol_engine.build_signal", return_value=HOLD):
            self.engine.evaluate()
        closed = self.engine.list_trades()[0]
        self.assertEqual(closed["exitReason"], "Stop-loss")
        self.assertLess(closed["exitPrice"], stop)

    def test_take_profit_executes_when_trigger_is_crossed(self) -> None:
        self.seed_open_trade()
        with closing(self.engine._connect()) as connection, connection:
            trade = self.engine._open_trade(connection)
            target = float(trade["take_profit_price"])
        market_price = target + 0.2
        self.market.snapshot = make_snapshot(market_price)
        with patch("sol_engine.build_signal", return_value=HOLD):
            self.engine.evaluate()
        closed = self.engine.list_trades()[0]
        self.assertEqual(closed["exitReason"], "Take-profit")
        self.assertAlmostEqual(closed["exitPrice"], target, places=8)
        self.assertAlmostEqual(closed["slippageUsdc"], trade["slippage_usdc"], places=8)

    def test_take_profit_gap_gets_open_price_improvement(self) -> None:
        trade = self.seed_open_trade()
        target = float(trade["take_profit_price"])
        opened_ms = int(
            datetime.fromisoformat(
                trade["opened_at"].replace("Z", "+00:00")
            ).timestamp()
            * 1000
        )
        gap_open = target + 4.0
        snapshot = make_snapshot(100.0)
        snapshot["candles1m"].append(
            candle(
                opened_ms + 1,
                gap_open,
                high=gap_open + 1.0,
                low=gap_open - 0.2,
                close=gap_open + 0.3,
            )
        )
        self.market.snapshot = snapshot
        with patch("sol_engine.build_signal", return_value=HOLD):
            self.engine.evaluate()
        closed = self.engine.list_trades()[0]
        self.assertEqual(closed["exitReason"], "Take-profit")
        self.assertAlmostEqual(closed["exitPrice"], gap_open, places=8)

    def test_daily_loss_limit_closes_position_and_blocks_entries(self) -> None:
        settings = dict(DEFAULTS)
        settings["maxDailyLossUsdc"] = 5.0
        self.engine.update_settings(settings)
        self.seed_open_trade()
        self.market.snapshot = make_snapshot(90.0)
        with patch("sol_engine.build_signal", return_value=BUY):
            result = self.engine.evaluate()
            self.engine.evaluate()

        self.assertTrue(result["status"]["dailyLossLimitReached"])
        self.assertFalse(result["status"]["running"])
        trades = self.engine.list_trades()
        self.assertEqual(len(trades), 1)
        self.assertEqual(trades[0]["status"], "CLOSED")
        with closing(sqlite3.connect(self.db_path)) as connection, connection:
            account = connection.execute(
                "SELECT running, daily_loss_limit_reached FROM account WHERE id=1"
            ).fetchone()
        self.assertEqual(account, (0, 1))

    def test_entry_costs_cannot_cross_daily_limit_unchecked(self) -> None:
        settings = dict(DEFAULTS)
        settings["maxDailyLossUsdc"] = 1.0
        self.engine.update_settings(settings)
        self.market.snapshot = make_snapshot()
        with patch("sol_engine.build_signal", return_value=BUY):
            self.engine.evaluate()
            self.engine.set_running(True)
            result = self.engine.evaluate()
        self.assertTrue(result["status"]["dailyLossLimitReached"])
        self.assertFalse(result["status"]["running"])
        trades = self.engine.list_trades()
        self.assertEqual(len(trades), 1)
        self.assertEqual(trades[0]["status"], "CLOSED")


if __name__ == "__main__":
    unittest.main()
