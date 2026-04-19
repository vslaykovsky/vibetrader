# strategies_v2 — agent instructions

You implement **`strategy.py` only**. Do not change **`utils.py`** (it defines the JSON shapes).
Always import everything from utils with: `from utils import *`

## `params.json`

Single source of truth next to `strategy.py`: ticker, bar `scale`, indicator knobs (nested objects per indicator, for example `sma.period`), order sizing (`orders.deposit_fraction` in `[0, 1]`, fraction of deposit per `market_order`), and a short `description` for the UI. 
Update this file accordingly when updating the strategy.py. 
Read this file at startup; do not duplicate those values as top-level constants in `strategy.py`. The host may merge run-time overrides into `params.json` before the process starts; do not add a `--params` CLI flag.

## I/O

- **stdin:** one JSON object per line. Each line is a `StrategyInput`: top-level `unixtime` and a `points` list of `ohlc`, `indicator`, and/or `portfolio` items. When a line arrives, treat the strategy clock from `unixtime` for bar-aligned batches (`ohlc` / `indicator`; end of bar for OHLC; same idea for indicators). A `portfolio` item is a full snapshot of open positions, not tied to a bar `unixtime` in the same way.
- **stdout:** one JSON object per line. Each line is a `StrategyOutput`: a list of outputs (subscriptions, indicator values, market orders, time acks). Match the discriminated models in `utils.py`.
- **`time_ack` (required):** The host applies backpressure: it does not send the next stdin line until your process has acknowledged every bar-aligned `unixtime` present on the current line. After you read a `StrategyInput` line, emit one `{ "kind": "time_ack", "unixtime": <same value as on the input> }` for each **distinct** bar-aligned `unixtime` on that line (preserve the order of first appearance; typically the line’s single top-level `unixtime` when `points` include `ohlc` or `indicator`). Include those objects in the same stdout JSON line as the rest of the outputs for that step (typically last in the list). If a line has only `portfolio` points and no `ohlc` / `indicator`, emit no `time_ack`. You must still emit `time_ack` when you skip trading logic (for example missing bar data): the host is waiting on the clock, not on your orders.

## Portfolio input (`kind: "portfolio"`)

- **`positions`:** list of `{ "ticker", "order_type", "deposit_ratio" }` where `deposit_ratio` is in `[0, 1]` (each leg’s size as a fraction of the deposit).
- **On startup:** the host may send a portfolio line before or mixed with the first market data so you can recover after restarts; the account may already be in position when the strategy process starts. Merge this into your internal position state before acting on prices.
- **After each trade:** when you emit a `market_order`, the next stdin line (or the same batch policy the host uses) may include a portfolio snapshot reflecting the updated book. Treat it as authoritative for open positions and per-leg sizes.

## Market orders (`kind: "market_order"`)

- Include **`deposit_ratio`** in `[0, 1]`: fraction of the deposit for that order (order size). Omit only if you rely on the schema default in `utils.py` (full deposit); prefer emitting an explicit `deposit_ratio` when sizing matters.

## What the strategy should do

1. **Start:** handle an initial **`portfolio`** line if present, then emit subscription outputs — `ticker_subscription` for prices you need, `indicator_subscription` for built-ins (sma, ema, macd, rsi, atr) with ticker, scale, and parameters taken from `params.json` where applicable. Input items in the StrategyInput `points` list come in the same exact order as requested in subscriptions for bar-aligned batches.
2. **Loop:** read each `StrategyInput` line; if it contains `portfolio`, refresh position state from `positions` before or together with processing `ohlc` / `indicator` for that step. Update internal state, decide entries/exits, and when you trade emit `market_order` items (`ticker`, `direction`, `deposit_ratio` from params when you size orders as a deposit fraction). Finish each stdin line with the required **`time_ack`** list entries for that line’s bar-aligned `unixtime` values.
3. **Each step:** Only emit OutputIndicatorDataPoint for a few key debug or plot values if helpful. Do not output raw input prices or indicators, and skip this output if there’s nothing useful to show.

Keep logic clear and small; put all subscription and signal rules in `strategy.py`. Prefer shorter code over readability. Do not create functions or classes unless absolutely necessary for reusability. 
