#!/usr/bin/env python3
"""Paper-only SOL/USDC signal and simulation engine.

The market-data provider is intentionally separate from the paper engine so a
future exchange adapter can be tested without changing simulation or risk logic.
This module has no wallet, credential, or order-execution code.
"""

from __future__ import annotations

import json
import math
import os
import sqlite3
import sys
import time
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
from urllib.error import HTTPError, URLError
from urllib.parse import urlencode
from urllib.request import Request, urlopen


SYMBOL = "SOLUSDC"
SOURCE = "Kraken public market data"
DEFAULTS = {
    "startingBalanceUsdc": 10000.0,
    "positionSizePercent": 10.0,
    "stopLossPercent": 0.6,
    "takeProfitPercent": 0.9,
    "maxDailyLossUsdc": 200.0,
    "feePercent": 0.1,
    "slippagePercent": 0.05,
}
SETTINGS_COLUMNS = {
    "startingBalanceUsdc": "starting_balance_usdc",
    "positionSizePercent": "position_size_percent",
    "stopLossPercent": "stop_loss_percent",
    "takeProfitPercent": "take_profit_percent",
    "maxDailyLossUsdc": "max_daily_loss_usdc",
    "feePercent": "fee_percent",
    "slippagePercent": "slippage_percent",
}


class EngineError(Exception):
    status = 500


class MarketDataUnavailable(EngineError):
    status = 503


class InvalidRequest(EngineError):
    status = 400


def utc_now() -> datetime:
    return datetime.now(timezone.utc)


def iso_utc(value: datetime | None = None) -> str:
    return (value or utc_now()).isoformat(timespec="milliseconds").replace("+00:00", "Z")


def to_candle_time(milliseconds: int) -> str:
    return iso_utc(datetime.fromtimestamp(milliseconds / 1000, timezone.utc))


def finite_number(value: Any, name: str) -> float:
    try:
        number = float(value)
    except (TypeError, ValueError) as exc:
        raise InvalidRequest(f"{name} must be a number.") from exc
    if not math.isfinite(number):
        raise InvalidRequest(f"{name} must be finite.")
    return number


class KrakenPublicMarketData:
    """Read-only public REST client; it never sends authenticated requests."""

    base_url = "https://api.kraken.com/0/public"

    def _get_json(self, path: str, params: dict[str, str]) -> Any:
        url = f"{self.base_url}/{path}?{urlencode(params)}"
        request = Request(url, headers={"User-Agent": "SOL-Scalper-Paper/1.0"})
        try:
            with urlopen(request, timeout=8) as response:
                payload = json.loads(response.read().decode("utf-8"))
                errors = payload.get("error", [])
                if errors:
                    raise MarketDataUnavailable(
                        "Kraken could not return SOL/USDC market data: "
                        + ", ".join(str(error) for error in errors)
                    )
                return payload
        except MarketDataUnavailable:
            raise
        except HTTPError as exc:
            raise MarketDataUnavailable(
                f"Kraken public market data returned HTTP {exc.code}."
            ) from exc
        except (URLError, TimeoutError, json.JSONDecodeError) as exc:
            raise MarketDataUnavailable(
                "Could not retrieve live SOL/USDC market data. Check the public market-data connection and try again."
            ) from exc

    def _price(self) -> float:
        payload = self._get_json("Ticker", {"pair": SYMBOL}).get("result", {})
        ticker = payload.get(SYMBOL) or next(iter(payload.values()), {})
        close = ticker.get("c", [])
        if not close:
            raise MarketDataUnavailable("Kraken did not return a SOL/USDC ticker price.")
        return finite_number(close[0], "SOL/USDC price")

    def _candles(self, interval: str) -> list[dict[str, Any]]:
        result = self._get_json(
            "OHLC", {"pair": SYMBOL, "interval": interval}
        ).get("result", {})
        rows = result.get(SYMBOL)
        if rows is None:
            rows = next(
                (value for key, value in result.items() if key != "last"), []
            )
        # Kraken always includes an unfinished candle as the last element.
        rows = rows[:-1]
        candles: list[dict[str, Any]] = []
        for row in rows:
            if len(row) < 7:
                continue
            open_time = int(row[0]) * 1000
            candles.append(
                {
                    "timestamp": to_candle_time(open_time),
                    "open_time": open_time,
                    "open": float(row[1]),
                    "high": float(row[2]),
                    "low": float(row[3]),
                    "close": float(row[4]),
                    "volume": float(row[6]),
                }
            )
        if len(candles) < 30:
            raise MarketDataUnavailable(
                f"Not enough closed {interval}-minute SOL/USDC candles were returned to calculate indicators."
            )
        return candles

    def get_snapshot(self) -> dict[str, Any]:
        with ThreadPoolExecutor(max_workers=3) as executor:
            price_job = executor.submit(self._price)
            one_minute_job = executor.submit(self._candles, "1")
            five_minute_job = executor.submit(self._candles, "5")
            return {
                "price": price_job.result(),
                "candles1m": one_minute_job.result(),
                "candles5m": five_minute_job.result(),
                "updatedAt": iso_utc(),
                "stale": False,
            }


def ema(values: list[float], period: int) -> float:
    if len(values) < period:
        raise MarketDataUnavailable(f"At least {period} closes are required for EMA.")
    alpha = 2.0 / (period + 1)
    result = values[0]
    for value in values[1:]:
        result = alpha * value + (1.0 - alpha) * result
    return result


def rsi(values: list[float], period: int = 14) -> float:
    if len(values) <= period:
        raise MarketDataUnavailable(f"At least {period + 1} closes are required for RSI.")
    changes = [values[index] - values[index - 1] for index in range(1, len(values))]
    gains = [max(change, 0.0) for change in changes]
    losses = [max(-change, 0.0) for change in changes]
    average_gain = sum(gains[:period]) / period
    average_loss = sum(losses[:period]) / period
    for index in range(period, len(changes)):
        average_gain = (average_gain * (period - 1) + gains[index]) / period
        average_loss = (average_loss * (period - 1) + losses[index]) / period
    if average_loss == 0:
        return 100.0 if average_gain > 0 else 50.0
    relative_strength = average_gain / average_loss
    return 100.0 - (100.0 / (1.0 + relative_strength))


def calculate_indicators(candles: list[dict[str, Any]], timeframe: str) -> dict[str, Any]:
    closes = [float(candle["close"]) for candle in candles]
    volumes = [float(candle["volume"]) for candle in candles]
    ema9 = ema(closes, 9)
    ema21 = ema(closes, 21)
    recent_volumes = volumes[-21:-1]
    average_volume = sum(recent_volumes) / len(recent_volumes) if recent_volumes else 0.0
    current_volume = volumes[-1]
    ratio = current_volume / average_volume if average_volume > 0 else 0.0
    if ema9 > ema21:
        trend = "bullish"
    elif ema9 < ema21:
        trend = "bearish"
    else:
        trend = "neutral"
    return {
        "timeframe": timeframe,
        "candleTime": candles[-1]["timestamp"],
        "close": closes[-1],
        "rsi": rsi(closes),
        "ema9": ema9,
        "ema21": ema21,
        "volume": current_volume,
        "averageVolume": average_volume,
        "volumeRatio": ratio,
        "trend": trend,
    }


def build_signal(
    one_minute: dict[str, Any], five_minute: dict[str, Any], price: float
) -> dict[str, Any]:
    one_bull = one_minute["trend"] == "bullish"
    five_bull = five_minute["trend"] == "bullish"
    one_bear = one_minute["trend"] == "bearish"
    five_bear = five_minute["trend"] == "bearish"
    rsi_value = one_minute["rsi"]
    volume_ratio = one_minute["volumeRatio"]

    if one_bull and five_bull and 40 <= rsi_value <= 68 and volume_ratio >= 1.0:
        return {
            "direction": "BUY",
            "confidence": min(96.0, 68.0 + min((volume_ratio - 1.0) * 8.0, 18.0)),
            "reason": (
                "1m and 5m EMA9 are above EMA21; 1m RSI is in the entry range "
                f"({rsi_value:.1f}) and volume is {volume_ratio:.2f}× its recent average."
            ),
        }
    if one_bear and five_bear and rsi_value <= 55:
        return {
            "direction": "SELL",
            "confidence": min(94.0, 68.0 + max(0.0, 50.0 - rsi_value) * 0.5),
            "reason": (
                "1m and 5m EMA9 are below EMA21 with weakening 1m momentum "
                f"(RSI {rsi_value:.1f})."
            ),
        }
    reason_parts = []
    if one_bull != five_bull:
        reason_parts.append("1m and 5m trends are not aligned")
    if not 40 <= rsi_value <= 68:
        reason_parts.append(f"1m RSI {rsi_value:.1f} is outside the entry range")
    if volume_ratio < 1.0:
        reason_parts.append(f"volume is {volume_ratio:.2f}× its recent average")
    if not reason_parts:
        reason_parts.append("the entry conditions are not strong enough")
    return {
        "direction": "HOLD",
        "confidence": 0.0,
        "reason": "Hold: " + "; ".join(reason_parts) + ".",
    }


class PaperTradingEngine:
    """SQLite-backed, long-only paper simulator; it has no live order adapter."""

    def __init__(self, db_path: Path | None = None):
        default_path = Path(__file__).resolve().parents[2] / "data" / "sol-scalper.sqlite3"
        self.db_path = db_path or Path(os.environ.get("SOL_SCALPER_DB_PATH", default_path))
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        self.market_data = KrakenPublicMarketData()
        self._initialize()

    def _connect(self) -> sqlite3.Connection:
        connection = sqlite3.connect(self.db_path, timeout=10)
        connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA busy_timeout = 10000")
        return connection

    def _initialize(self) -> None:
        with self._connect() as connection:
            connection.execute(
                """
                CREATE TABLE IF NOT EXISTS settings (
                    id INTEGER PRIMARY KEY CHECK (id = 1),
                    starting_balance_usdc REAL NOT NULL,
                    position_size_percent REAL NOT NULL,
                    stop_loss_percent REAL NOT NULL,
                    take_profit_percent REAL NOT NULL,
                    max_daily_loss_usdc REAL NOT NULL,
                    fee_percent REAL NOT NULL,
                    slippage_percent REAL NOT NULL
                )
                """
            )
            connection.execute(
                """
                CREATE TABLE IF NOT EXISTS account (
                    id INTEGER PRIMARY KEY CHECK (id = 1),
                    cash_usdc REAL NOT NULL,
                    realized_pnl_usdc REAL NOT NULL DEFAULT 0,
                    running INTEGER NOT NULL DEFAULT 1,
                    daily_loss_limit_reached INTEGER NOT NULL DEFAULT 0,
                    utc_day TEXT NOT NULL,
                    day_start_equity REAL NOT NULL
                )
                """
            )
            connection.execute(
                """
                CREATE TABLE IF NOT EXISTS signals (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    candle_open_ms INTEGER NOT NULL UNIQUE,
                    timestamp TEXT NOT NULL,
                    direction TEXT NOT NULL,
                    timeframe TEXT NOT NULL,
                    price REAL NOT NULL,
                    confidence REAL NOT NULL,
                    reason TEXT NOT NULL
                )
                """
            )
            connection.execute(
                """
                CREATE TABLE IF NOT EXISTS trades (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    opened_at TEXT NOT NULL,
                    closed_at TEXT,
                    entry_price REAL NOT NULL,
                    exit_price REAL,
                    quantity REAL NOT NULL,
                    fees_usdc REAL NOT NULL DEFAULT 0,
                    slippage_usdc REAL NOT NULL DEFAULT 0,
                    realized_pnl_usdc REAL,
                    exit_reason TEXT,
                    status TEXT NOT NULL,
                    fee_percent REAL NOT NULL,
                    slippage_percent REAL NOT NULL,
                    stop_loss_price REAL NOT NULL,
                    take_profit_price REAL NOT NULL
                )
                """
            )
            connection.execute(
                """
                CREATE TABLE IF NOT EXISTS market_cache (
                    id INTEGER PRIMARY KEY CHECK (id = 1),
                    fetched_at REAL NOT NULL,
                    snapshot_json TEXT NOT NULL
                )
                """
            )
            connection.execute(
                """
                INSERT OR IGNORE INTO settings (
                    id, starting_balance_usdc, position_size_percent,
                    stop_loss_percent, take_profit_percent, max_daily_loss_usdc,
                    fee_percent, slippage_percent
                ) VALUES (1, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    DEFAULTS["startingBalanceUsdc"],
                    DEFAULTS["positionSizePercent"],
                    DEFAULTS["stopLossPercent"],
                    DEFAULTS["takeProfitPercent"],
                    DEFAULTS["maxDailyLossUsdc"],
                    DEFAULTS["feePercent"],
                    DEFAULTS["slippagePercent"],
                ),
            )
            starting_balance = DEFAULTS["startingBalanceUsdc"]
            connection.execute(
                """
                INSERT OR IGNORE INTO account (
                    id, cash_usdc, realized_pnl_usdc, running,
                    daily_loss_limit_reached, utc_day, day_start_equity
                ) VALUES (1, ?, 0, 1, 0, ?, ?)
                """,
                (starting_balance, utc_now().date().isoformat(), starting_balance),
            )

    def _settings(self, connection: sqlite3.Connection) -> dict[str, float]:
        row = connection.execute("SELECT * FROM settings WHERE id = 1").fetchone()
        return {
            key: float(row[column])
            for key, column in SETTINGS_COLUMNS.items()
        }

    def _market_snapshot(self) -> dict[str, Any]:
        with self._connect() as connection:
            cached = connection.execute(
                "SELECT fetched_at, snapshot_json FROM market_cache WHERE id = 1"
            ).fetchone()
            if cached and time.time() - float(cached["fetched_at"]) < 5:
                return json.loads(cached["snapshot_json"])

        snapshot = self.market_data.get_snapshot()
        with self._connect() as connection:
            connection.execute(
                """
                INSERT INTO market_cache (id, fetched_at, snapshot_json)
                VALUES (1, ?, ?)
                ON CONFLICT(id) DO UPDATE SET
                  fetched_at = excluded.fetched_at,
                  snapshot_json = excluded.snapshot_json
                """,
                (time.time(), json.dumps(snapshot, separators=(",", ":"))),
            )
        return snapshot

    def _open_trade(self, connection: sqlite3.Connection) -> sqlite3.Row | None:
        return connection.execute(
            "SELECT * FROM trades WHERE status = 'OPEN' ORDER BY id DESC LIMIT 1"
        ).fetchone()

    def _position_pnl(
        self, trade: sqlite3.Row | None, price: float
    ) -> tuple[float, float, float]:
        if not trade:
            return 0.0, 0.0, 0.0
        quantity = float(trade["quantity"])
        slippage_rate = float(trade["slippage_percent"]) / 100.0
        fee_rate = float(trade["fee_percent"]) / 100.0
        exit_price = price * (1.0 - slippage_rate)
        exit_fee = quantity * exit_price * fee_rate
        entry_cost = quantity * float(trade["entry_price"]) + (
            float(trade["fees_usdc"]) - quantity * float(trade["entry_price"]) * fee_rate
        )
        net_pnl = quantity * exit_price - exit_fee - entry_cost
        return net_pnl, exit_price, exit_fee

    def _equity(
        self, connection: sqlite3.Connection, price: float
    ) -> tuple[float, float, sqlite3.Row | None]:
        account = connection.execute("SELECT * FROM account WHERE id = 1").fetchone()
        trade = self._open_trade(connection)
        unrealized, exit_price, exit_fee = self._position_pnl(trade, price)
        equity = float(account["cash_usdc"])
        if trade:
            equity += float(trade["quantity"]) * exit_price - exit_fee
        return equity, unrealized, trade

    def _reset_day_if_needed(self, connection: sqlite3.Connection, price: float) -> None:
        account = connection.execute("SELECT * FROM account WHERE id = 1").fetchone()
        today = utc_now().date().isoformat()
        if account["utc_day"] != today:
            equity, _, _ = self._equity(connection, price)
            connection.execute(
                """
                UPDATE account SET utc_day = ?, day_start_equity = ?,
                  daily_loss_limit_reached = 0 WHERE id = 1
                """,
                (today, equity),
            )

    def _record_signal(
        self,
        connection: sqlite3.Connection,
        signal: dict[str, Any],
        candle: dict[str, Any],
        price: float,
    ) -> sqlite3.Row:
        connection.execute(
            """
            INSERT OR IGNORE INTO signals (
                candle_open_ms, timestamp, direction, timeframe, price, confidence, reason
            ) VALUES (?, ?, ?, '1m', ?, ?, ?)
            """,
            (
                int(candle["open_time"]),
                iso_utc(),
                signal["direction"],
                price,
                signal["confidence"],
                signal["reason"],
            ),
        )
        return connection.execute(
            "SELECT * FROM signals WHERE candle_open_ms = ?",
            (int(candle["open_time"]),),
        ).fetchone()

    def _close_trade(
        self,
        connection: sqlite3.Connection,
        trade: sqlite3.Row,
        market_price: float,
        reason: str,
    ) -> None:
        quantity = float(trade["quantity"])
        slippage_rate = float(trade["slippage_percent"]) / 100.0
        fee_rate = float(trade["fee_percent"]) / 100.0
        exit_price = market_price * (1.0 - slippage_rate)
        exit_notional = quantity * exit_price
        exit_fee = exit_notional * fee_rate
        entry_price = float(trade["entry_price"])
        entry_fee = float(trade["fees_usdc"])
        entry_market_estimate = entry_price / (1.0 + slippage_rate)
        entry_slippage = quantity * max(0.0, entry_price - entry_market_estimate)
        exit_slippage = quantity * max(0.0, market_price - exit_price)
        net_pnl = exit_notional - exit_fee - quantity * entry_price - entry_fee
        connection.execute(
            """
            UPDATE trades SET closed_at = ?, exit_price = ?, fees_usdc = ?,
              slippage_usdc = ?, realized_pnl_usdc = ?, exit_reason = ?, status = 'CLOSED'
            WHERE id = ?
            """,
            (
                iso_utc(),
                exit_price,
                entry_fee + exit_fee,
                entry_slippage + exit_slippage,
                net_pnl,
                reason,
                int(trade["id"]),
            ),
        )
        connection.execute(
            """
            UPDATE account SET cash_usdc = cash_usdc + ?,
              realized_pnl_usdc = realized_pnl_usdc + ? WHERE id = 1
            """,
            (exit_notional - exit_fee, net_pnl),
        )

    def _open_position(
        self,
        connection: sqlite3.Connection,
        settings: dict[str, float],
        price: float,
    ) -> None:
        account = connection.execute("SELECT * FROM account WHERE id = 1").fetchone()
        equity, _, current_trade = self._equity(connection, price)
        if current_trade:
            return
        budget = min(
            equity * settings["positionSizePercent"] / 100.0,
            float(account["cash_usdc"]),
        )
        slippage_rate = settings["slippagePercent"] / 100.0
        fee_rate = settings["feePercent"] / 100.0
        entry_price = price * (1.0 + slippage_rate)
        quantity = budget / (entry_price * (1.0 + fee_rate)) if entry_price > 0 else 0.0
        if quantity <= 0:
            return
        entry_notional = quantity * entry_price
        entry_fee = entry_notional * fee_rate
        slippage_cost = quantity * (entry_price - price)
        stop_price = entry_price * (1.0 - settings["stopLossPercent"] / 100.0)
        target_price = entry_price * (1.0 + settings["takeProfitPercent"] / 100.0)
        connection.execute(
            """
            INSERT INTO trades (
                opened_at, entry_price, quantity, fees_usdc, slippage_usdc,
                status, fee_percent, slippage_percent, stop_loss_price, take_profit_price
            ) VALUES (?, ?, ?, ?, ?, 'OPEN', ?, ?, ?, ?)
            """,
            (
                iso_utc(),
                entry_price,
                quantity,
                entry_fee,
                slippage_cost,
                settings["feePercent"],
                settings["slippagePercent"],
                stop_price,
                target_price,
            ),
        )
        connection.execute(
            "UPDATE account SET cash_usdc = cash_usdc - ? WHERE id = 1",
            (entry_notional + entry_fee,),
        )

    def _status(self, connection: sqlite3.Connection, price: float) -> dict[str, Any]:
        account = connection.execute("SELECT * FROM account WHERE id = 1").fetchone()
        settings = self._settings(connection)
        equity, unrealized, trade = self._equity(connection, price)
        daily_pnl = equity - float(account["day_start_equity"])
        position = None
        if trade:
            position = {
                "quantity": float(trade["quantity"]),
                "entryPrice": float(trade["entry_price"]),
                "stopLossPrice": float(trade["stop_loss_price"]),
                "takeProfitPrice": float(trade["take_profit_price"]),
                "openedAt": trade["opened_at"],
                "unrealizedPnlUsdc": unrealized,
            }
        return {
            "running": bool(account["running"]),
            "mode": "PAPER",
            "dailyLossLimitReached": bool(account["daily_loss_limit_reached"]),
            "dailyPnlUsdc": daily_pnl,
            "cashUsdc": float(account["cash_usdc"]),
            "equityUsdc": equity,
            "realizedPnlUsdc": float(account["realized_pnl_usdc"]),
            "unrealizedPnlUsdc": unrealized,
            "openPosition": position,
        }

    @staticmethod
    def _signal_dict(row: sqlite3.Row) -> dict[str, Any]:
        return {
            "id": int(row["id"]),
            "timestamp": row["timestamp"],
            "direction": row["direction"],
            "timeframe": row["timeframe"],
            "price": float(row["price"]),
            "confidence": float(row["confidence"]),
            "reason": row["reason"],
        }

    @staticmethod
    def _trade_dict(row: sqlite3.Row) -> dict[str, Any]:
        return {
            "id": int(row["id"]),
            "openedAt": row["opened_at"],
            "closedAt": row["closed_at"],
            "side": "LONG",
            "status": row["status"],
            "entryPrice": float(row["entry_price"]),
            "exitPrice": float(row["exit_price"]) if row["exit_price"] is not None else None,
            "quantity": float(row["quantity"]),
            "feesUsdc": float(row["fees_usdc"]),
            "slippageUsdc": float(row["slippage_usdc"]),
            "realizedPnlUsdc": (
                float(row["realized_pnl_usdc"])
                if row["realized_pnl_usdc"] is not None
                else None
            ),
            "exitReason": row["exit_reason"],
        }

    def dashboard(self) -> dict[str, Any]:
        snapshot = self._market_snapshot()
        indicators = [
            calculate_indicators(snapshot["candles1m"], "1m"),
            calculate_indicators(snapshot["candles5m"], "5m"),
        ]
        signal_data = build_signal(indicators[0], indicators[1], snapshot["price"])
        last_candle = snapshot["candles1m"][-1]

        with self._connect() as connection:
            self._reset_day_if_needed(connection, snapshot["price"])
            signal_row = self._record_signal(
                connection, signal_data, last_candle, snapshot["price"]
            )
            settings = self._settings(connection)
            account = connection.execute("SELECT * FROM account WHERE id = 1").fetchone()
            trade = self._open_trade(connection)

            if trade:
                exit_reason = None
                if snapshot["price"] <= float(trade["stop_loss_price"]):
                    exit_reason = "Stop-loss"
                elif snapshot["price"] >= float(trade["take_profit_price"]):
                    exit_reason = "Take-profit"
                elif signal_data["direction"] == "SELL":
                    exit_reason = "Bearish signal"
                if exit_reason:
                    self._close_trade(connection, trade, snapshot["price"], exit_reason)

            current_status = self._status(connection, snapshot["price"])
            loss_reached = (
                current_status["dailyPnlUsdc"] <= -settings["maxDailyLossUsdc"]
            )
            if loss_reached:
                trade = self._open_trade(connection)
                if trade:
                    self._close_trade(
                        connection, trade, snapshot["price"], "Maximum daily loss"
                    )
                connection.execute(
                    "UPDATE account SET running = 0, daily_loss_limit_reached = 1 WHERE id = 1"
                )
            elif (
                bool(account["running"])
                and not bool(account["daily_loss_limit_reached"])
                and signal_data["direction"] == "BUY"
                and self._open_trade(connection) is None
            ):
                self._open_position(connection, settings, snapshot["price"])

            status = self._status(connection, snapshot["price"])
            today = utc_now().date().isoformat()
            today_start = f"{today}T00:00:00.000Z"
            today_trade_count = int(
                connection.execute(
                    "SELECT COUNT(*) AS count FROM trades WHERE opened_at >= ?",
                    (today_start,),
                ).fetchone()["count"]
            )
            closed_count = connection.execute(
                "SELECT COUNT(*) AS count FROM trades WHERE status = 'CLOSED'"
            ).fetchone()["count"]
            winning_count = connection.execute(
                """
                SELECT COUNT(*) AS count FROM trades
                WHERE status = 'CLOSED' AND realized_pnl_usdc > 0
                """
            ).fetchone()["count"]
            win_rate = (winning_count / closed_count * 100.0) if closed_count else 0.0

        return {
            "market": {
                "symbol": "SOL/USDC",
                "price": float(snapshot["price"]),
                "source": SOURCE,
                "updatedAt": snapshot["updatedAt"],
                "stale": bool(snapshot["stale"]),
                "candles1m": [
                    {
                        "timestamp": candle["timestamp"],
                        "open": float(candle["open"]),
                        "high": float(candle["high"]),
                        "low": float(candle["low"]),
                        "close": float(candle["close"]),
                        "volume": float(candle["volume"]),
                    }
                    for candle in snapshot["candles1m"][-50:]
                ],
            },
            "indicators": indicators,
            "signal": self._signal_dict(signal_row),
            "status": status,
            "settings": settings,
            "todayTradeCount": today_trade_count,
            "winRatePercent": win_rate,
        }

    def get_settings(self) -> dict[str, float]:
        with self._connect() as connection:
            return self._settings(connection)

    def update_settings(self, payload: dict[str, Any]) -> dict[str, float]:
        values = {
            key: finite_number(payload.get(key), key)
            for key in SETTINGS_COLUMNS
        }
        bounds = {
            "startingBalanceUsdc": (100.0, 10_000_000.0),
            "positionSizePercent": (1.0, 100.0),
            "stopLossPercent": (0.1, 20.0),
            "takeProfitPercent": (0.1, 50.0),
            "maxDailyLossUsdc": (1.0, 10_000_000.0),
            "feePercent": (0.0, 5.0),
            "slippagePercent": (0.0, 5.0),
        }
        for name, value in values.items():
            minimum, maximum = bounds[name]
            if not minimum <= value <= maximum:
                raise InvalidRequest(f"{name} must be between {minimum:g} and {maximum:g}.")

        with self._connect() as connection:
            current = self._settings(connection)
            if values["startingBalanceUsdc"] != current["startingBalanceUsdc"]:
                trades_exist = connection.execute(
                    "SELECT EXISTS(SELECT 1 FROM trades) AS exists_flag"
                ).fetchone()["exists_flag"]
                if trades_exist:
                    raise InvalidRequest(
                        "Starting balance can only be changed before the first simulated trade."
                    )
                connection.execute(
                    """
                    UPDATE account SET cash_usdc = ?, realized_pnl_usdc = 0,
                      day_start_equity = ? WHERE id = 1
                    """,
                    (values["startingBalanceUsdc"], values["startingBalanceUsdc"]),
                )
            assignments = ", ".join(
                f"{SETTINGS_COLUMNS[key]} = ?" for key in SETTINGS_COLUMNS
            )
            connection.execute(
                f"UPDATE settings SET {assignments} WHERE id = 1",
                tuple(values[key] for key in SETTINGS_COLUMNS),
            )
            return self._settings(connection)

    def set_running(self, running: bool) -> dict[str, Any]:
        price = self._market_snapshot()["price"]
        with self._connect() as connection:
            account = connection.execute("SELECT * FROM account WHERE id = 1").fetchone()
            if running and bool(account["daily_loss_limit_reached"]):
                raise InvalidRequest(
                    "The daily loss limit was reached. The simulator can resume after the next UTC day begins."
                )
            connection.execute("UPDATE account SET running = ? WHERE id = 1", (int(running),))
            return self._status(connection, price)

    def list_signals(self) -> list[dict[str, Any]]:
        with self._connect() as connection:
            rows = connection.execute(
                "SELECT * FROM signals ORDER BY id DESC LIMIT 30"
            ).fetchall()
            return [self._signal_dict(row) for row in rows]

    def list_trades(self) -> list[dict[str, Any]]:
        with self._connect() as connection:
            rows = connection.execute(
                "SELECT * FROM trades ORDER BY id DESC LIMIT 30"
            ).fetchall()
            return [self._trade_dict(row) for row in rows]


def dispatch(action: str, payload: dict[str, Any]) -> Any:
    engine = PaperTradingEngine()
    if action == "dashboard":
        return engine.dashboard()
    if action == "settings":
        return engine.get_settings()
    if action == "update_settings":
        return engine.update_settings(payload)
    if action == "control":
        running = payload.get("running")
        if not isinstance(running, bool):
            raise InvalidRequest("running must be a boolean.")
        return engine.set_running(running)
    if action == "signals":
        return engine.list_signals()
    if action == "trades":
        return engine.list_trades()
    raise InvalidRequest("Unknown paper-trading operation.")


def main() -> None:
    action = sys.argv[1] if len(sys.argv) > 1 else ""
    try:
        raw_input = sys.stdin.read()
        payload = json.loads(raw_input) if raw_input else {}
        if not isinstance(payload, dict):
            raise InvalidRequest("Request body must be an object.")
        result = dispatch(action, payload)
        print(json.dumps(result, separators=(",", ":"), allow_nan=False))
    except EngineError as exc:
        print(json.dumps({"error": str(exc), "status": exc.status}))
    except (OSError, sqlite3.Error) as exc:
        print(json.dumps({"error": "Paper-trading storage is unavailable.", "status": 500}))
        print(f"Storage error: {exc}", file=sys.stderr)
    except Exception as exc:  # Keep API errors explicit while preserving diagnostic logs.
        print(json.dumps({"error": "The paper-trading engine encountered an unexpected error.", "status": 500}))
        print(f"Engine error: {type(exc).__name__}: {exc}", file=sys.stderr)


if __name__ == "__main__":
    main()
