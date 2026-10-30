# SOL Scalper

Paper-only SOL/USDC scalping dashboard with public market data, 1-minute and 5-minute indicators, configurable risk controls, and simulated trades. It never connects a wallet or submits exchange orders.

## Run & Operate

- `pnpm --filter @workspace/api-server run dev` — run the API server (port 5000)
- `pnpm --filter @workspace/sol-scalper run dev` — run the SOL Scalper dashboard
- `pnpm run typecheck` — full typecheck across all packages
- `pnpm run build` — typecheck + build all packages
- `pnpm --filter @workspace/api-spec run codegen` — regenerate API hooks and Zod schemas from the OpenAPI spec
- `pnpm --filter @workspace/sol-scalper run typecheck` — check the dashboard
- `pnpm --filter @workspace/api-server run test:python` — run the paper-engine unit tests
- Kraken's public SOL/USDC endpoints need no exchange credentials or API key.

## Stack

- pnpm workspaces, Node.js 24, TypeScript 5.9
- API: Express 5
- Shared database support: PostgreSQL + Drizzle ORM; paper-trading history: Python stdlib SQLite
- Validation: Zod (`zod/v4`), `drizzle-zod`
- API codegen: Orval (from OpenAPI spec)
- Build: esbuild (CJS bundle)

## Where things live

- `lib/api-spec/openapi.yaml` — source of truth for the SOL Scalper API contract
- `artifacts/api-server/src/python/sol_engine.py` — market data, indicators, signals, risk checks, and paper simulation
- `artifacts/api-server/src/lib/sol-scalper-worker.ts` — independent paper-evaluation loop; resets simulator OFF at startup
- `artifacts/api-server/src/routes/sol-scalper.ts` — Express bridge into the Python engine
- `artifacts/sol-scalper/src/` — React dashboard and interface

## Architecture decisions

- The engine starts OFF in PAPER mode after every API restart and has no wallet, credential, or live-order path.
- Public Kraken market data is used; the engine fails visibly if the feed is unavailable instead of fabricating fallback prices.
- Dashboard GETs are read-only. A server-owned worker evaluates paper signals and positions independently.
- SQLite constraints enforce one open paper position and at most one entry for a given signal candle.
- Live fills and equity marks use Kraken's public best bid/ask plus configured slippage; closed 1m candles catch missed bracket crossings, with stop-first handling when both levels trade in one candle.
- Take-profit exits model a limit fill at target or candle-open price improvement; order-book depth and partial fills are not modeled.
- Only closed candles create signals. Fees, slippage, position sizing, and the daily-loss lock apply to simulated positions.

## Product

Users can monitor SOL/USDC, review 1m/5m RSI, EMA9/EMA21 and volume, inspect BUY/SELL/HOLD signals, adjust paper risk settings, pause or resume the simulator, and review recorded signals and simulated trades.

## User preferences

Keep the product paper-only. Never add wallet connection, private-key handling, or real order execution without a new explicit request.

## Gotchas

- Kraken's OHLC API takes numeric interval values (`1` and `5`) and always includes an unfinished final candle; exclude it from signal calculations.
- Paper history is stored in a local SQLite file under `artifacts/api-server/data/` (ignored by Git).

## Pointers

- See the `pnpm-workspace` skill for workspace structure, TypeScript setup, and package details
