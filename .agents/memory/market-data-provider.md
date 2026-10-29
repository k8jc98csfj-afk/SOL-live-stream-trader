---
name: SOL public market data
description: Public exchange API access constraints observed for SOL/USDC in this project environment.
---

Use Kraken's unauthenticated SOL/USDC ticker and OHLC endpoints in this runtime. Binance's public API returned HTTP 451, and Coinbase Exchange did not expose the checked SOL-USDC product path. Kraken OHLC requests require numeric interval values `1` and `5`, and the final OHLC item is still forming, so omit it from closed-candle indicators.

**Why:** Two plausible public providers failed from this environment, while Kraken served the required live pair and candle intervals without credentials.

**How to apply:** Keep market-data access behind a provider boundary. If changing providers, verify real ticker and both candle intervals from the running environment, and preserve closed-candle-only signal evaluation.
