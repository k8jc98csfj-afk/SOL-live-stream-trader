SOL Scalping Bot

Objective

Build a SOL-only scalping trading bot with a path to live trading after thorough testing.

Market

* Trade SOL against USDC only.
* Do not trade other tokens.
* Use a reputable exchange API for market data and order execution.
* Confirm the selected exchange supports SOL/USDC and is available to the account.

Trading strategy

* Analyse short-term price movements using configurable moving averages, RSI, momentum and volume.
* Generate buy, sell and hold signals.
* Account for fees, spread and slippage before assessing profitability.
* Avoid trading when market data is stale or incomplete.

Development stages

1. Market data collection.
2. Backtesting with historical data.
3. Paper trading using simulated funds.
4. Risk-control and performance testing.
5. Live trading only after explicit approval and successful testing.

Mandatory risk controls

* Maximum position size and maximum daily loss.
* Stop-loss and emergency shutdown controls.
* No duplicate orders or uncontrolled retry loops.
* Never trade when risk limits or data checks fail.
* Never store API secrets in source code or GitHub.
* Use read-only API credentials during initial development.
* Require explicit confirmation before enabling live order execution.

Initial operating mode

Paper trading only. Live order execution must remain disabled.

Reporting

Log prices, signals, simulated orders, fees, slippage, profit and loss, and risk-limit events.

Important

The bot must not assume a strategy is profitable. Backtest results must account for trading costs and avoid look-ahead bias.
