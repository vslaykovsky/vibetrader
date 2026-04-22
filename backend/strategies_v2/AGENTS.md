# strategies_v2 — agent instructions

You implement **`strategy.py`** and keep **`params.json`** up to date. Author **`params-hyperopt.json`** as a static config whenever the strategy emits `market_order` outputs. Do not change **`utils.py`** (it defines the JSON shapes) or **`hyperopt.py`** (fixed platform hyperparameter driver, copied read-only into the workspace).
Always import everything from utils with: `from utils import *`

Do not run `strategy.py` or `hyperopt.py` here — the platform runs them through the historical simulator (`scripts/simulate_strategy_v2.py`) after your changes. The simulator fetches OHLC bars for the ticker/scale in `params.json` across `start_date..end_date`, streams them to your `strategy.py`, and writes `backtest.json` (strategy name + charts) and `metrics.json` (scalar metrics) into the workspace.

## `params.json`

Single source of truth next to `strategy.py`: ticker, bar `scale` (strategy's native timeframe), indicator knobs (nested objects per indicator, for example `sma.period`), order sizing (`orders.deposit_fraction` in `[0, 1]`, default **`1`** — use on **buys** only unless you mean partial sells; see **Market orders**), a human-readable `strategy_name` (no ticker in the name), and a short `description` for the UI.
Also include simulator inputs consumed by the host (not read by `strategy.py`): `start_date` / `end_date` (ISO `YYYY-MM-DD`) defining the historical backtest window, `initial_deposit` (positive number), an optional `provider` (`alpaca`, `moex`, or `auto`), and an optional `simulation_scale` (`1m` / `15m` / `1h` / `4h` / `1d` / `1w`, default = `scale`) — see **Simulation scale** below.
Update this file accordingly when updating `strategy.py`.
Read the strategy-relevant values at startup; do not duplicate them as top-level constants in `strategy.py`. The host may merge run-time overrides into `params.json` before the process starts; do not add a `--params` CLI flag.

## I/O

- **stdin:** one JSON object per line. Each line is a `StrategyInput`: top-level `unixtime` and a `points` list of `ohlc`, `indicator`, and/or `portfolio` items. `unixtime` is the clock at the end of the driver bar that produced the line; for intermediate in-bar updates it advances inside the current base-scale bar. A `portfolio` item is a full snapshot of open positions.
- **stdout:** one JSON object per line. Each line is a `StrategyOutput`: a list of outputs (subscriptions, indicator values, market orders, time acks). Match the discriminated models in `utils.py`.
- **`time_ack` (required):** The host applies backpressure: it does not send the next stdin line until your process has acknowledged every bar-aligned `unixtime` present on the current line. After you read a `StrategyInput` line, emit one `{ "kind": "time_ack", "unixtime": <same value as on the input> }` for every line that carries any `ohlc` or `indicator` point (whether closed or partial). Include those objects in the same stdout JSON line as the rest of the outputs for that step (typically last in the list). If a line has only `portfolio` points and no `ohlc` / `indicator`, emit no `time_ack`. You must still emit `time_ack` when you skip trading logic (for example missing data): the host is waiting on the clock, not on your orders.

## Intermediate bar updates (`closed` flag)

Each `ohlc` and `indicator` input carries a `closed` boolean.

- `closed: true` — a finalized bar at the subscription's `scale`. Appears exactly once per base bar.
- `closed: false` — an in-bar snapshot (running open / high / low / close so far, or an indicator recomputed on that running OHLC). Multiple such updates may arrive for the same base bar when you request a finer `update_scale` (or when the simulator runs on a finer `simulation_scale`).

Consequences your `strategy.py` must respect:

1. **Your code will see multiple updates of the same base bar.** Do not append the running close to your history buffer on every update — only commit it when `closed: true`. A typical shape: cache the last partial values in local variables, and push them into long-lived series (for rolling windows, previous-close comparisons, etc.) only on the closed update.
2. Orders emitted on a non-closed update fill at the running close of that update (mid-bar fills improve simulation accuracy). If your signal logic is only meaningful at bar close, guard trades with `if point.closed:`.
3. If you emit `OutputIndicatorDataPoint` for charting, stamp it with the step's `unixtime` as always; the UI does not care about `closed` for those outputs.

## Subscribing to intermediate updates (`partial`, `update_scale`)

`OutputTickerSubscription` and every `*IndicatorSubscription` take an optional `partial` boolean (default `False`) and an optional `update_scale`:

- `partial=False` (default): you only receive `closed: true` points at the subscription's `scale`. `update_scale` is ignored. Use this for signals that only make sense on the closed bar (previous-close comparisons, SMA/EMA/MACD cross at bar close, etc.).
- `partial=True`: you additionally receive `closed: false` points at the subscription's `update_scale` cadence (defaulting to `simulation_scale` when unset). `update_scale` must divide `scale` and be ≥ `simulation_scale`. Example: `scale="1d"`, `partial=true`, `update_scale="1h"` → up to 24 intra-day updates plus the daily close.

Each subscription sets `partial` independently, so you can mix policies per input. For example, a strategy that triggers orders on intra-bar `high`/`low` breakouts but only updates long EMAs at the close would use `OutputTickerSubscription(ticker="SPY", scale="1d", partial=True)` together with `EmaIndicatorSubscription(ticker="SPY", scale="1d", period=200, partial=False)`. Enable `partial=True` only for inputs that genuinely benefit from intra-bar information; default `partial=False` everywhere else to minimize work and keep behavior bar-to-bar stable.

## Simulation scale (host-side)

`params.json.simulation_scale` (or `--simulation-scale` on the CLI, or `simulation_scale` on `POST /simulation/start`) tells the host which bar resolution to fetch from the data provider. It must be ≤ `scale` and divide it. When the simulation scale is finer than `scale`, the host:

- Aggregates driver bars into the base `scale` for indicator fitting and closed emissions (so your closed bars reflect OHLC of the full base period).
- Advances through driver bars one at a time, delivering partial updates at each `partial=True` subscription's `update_scale` boundary and the closed update at the base boundary. `partial=False` subscriptions only emit at the base boundary.
- Fills `market_order`s at the running close of the driver bar that triggered the order, not the base-bar close.

When `simulation_scale == scale` there is one driver bar per base bar and the stream is identical to the legacy behavior: one `closed: true` point per bar.

## Portfolio input (`kind: "portfolio"`)

- **`positions`:** list of `{ "ticker", "order_type", "deposit_ratio", "volume_weighted_avg_entry_price" }` where `deposit_ratio` is in `[0, 1]` (each leg's size as a fraction of the deposit) and `volume_weighted_avg_entry_price` is the book's quantity-weighted average fill price for that open leg (the simulator derives this from executed `market_order` fills).
- **On startup:** the host may send a portfolio line before or mixed with the first market data so you can recover after restarts; the account may already be in position when the strategy process starts. Merge this into your internal position state before acting on prices.
- **After each trade:** when you emit a `market_order`, the next stdin line (or the same batch policy the host uses) may include a portfolio snapshot reflecting the updated book. Treat it as authoritative for open positions and per-leg sizes.

## Market orders (`kind: "market_order"`)

- **`deposit_ratio`** defaults to **`1.0`** when omitted (matches `utils.py`).
- **`deposit_ratio` on `buy`** — fraction of **cash** spent (e.g. `orders.deposit_fraction`, default **`1`**).
- **`deposit_ratio` on `sell`** — fraction of **open size** closed, not cash; use **`1.0`** for a full exit. Reusing `orders.deposit_fraction` on sells is a partial exit; keep “in position” in sync with **`portfolio`** if you do that.

The fill price is the running close at the time of the update that triggered the order (intraday mid-bar price when `closed: false`, closed-bar close when `closed: true`).

## What the strategy should do

1. **Start:** handle an initial **`portfolio`** line if present, then emit subscription outputs — `ticker_subscription` for prices you need (set `partial=True` and optionally `update_scale` if you want intra-bar prices), `indicator_subscription` for built-ins (sma, ema, macd, rsi, atr) with ticker, scale, parameters, and optional `partial` / `update_scale`. Read subscription parameters from `params.json` where applicable. Input items in the StrategyInput `points` list come in the same exact order as requested in subscriptions for bar-aligned batches.
2. **Loop:** read each `StrategyInput` line; if it contains `portfolio`, refresh position state from `positions` before or together with processing `ohlc` / `indicator` for that step. Distinguish closed vs partial points using the `closed` flag and update durable state (history lists, previous-close memory) only on closed points. On partial updates use the running values for live signal checks. When you trade emit `market_order` items per **Market orders**. Finish each stdin line with the required **`time_ack`** entries.
3. **Each step:** Only emit OutputIndicatorDataPoint for a few key debug or plot values if helpful. Do not output raw input prices or indicators, and skip this output if there's nothing useful to show.

Keep logic clear and small; put all subscription and signal rules in `strategy.py`. Prefer shorter code over readability. Do not create functions or classes unless absolutely necessary for reusability.

## `params-hyperopt.json` (required for trading strategies)

If your `strategy.py` emits `market_order` outputs (a tradable strategy, not pure EDA), ship a static `params-hyperopt.json` next to `params.json` so `python hyperopt.py` can optimize.

The file must match the **`ParamsHyperopt`** model in **`utils.py`** (field names, types, defaults, and `search_space` entries as **`HyperoptIntSpec`**, **`HyperoptFloatSpec`**, or **`HyperoptCategoricalSpec`** per the `type` discriminator). Treat those Pydantic models as the single source of truth; do not restate their shape here.

Constraints:

- Tune only parameters that affect trading behavior (indicator knobs such as `sma.period`, thresholds, `orders.deposit_fraction`, etc.). Do not put `ticker`, `scale`, `simulation_scale`, `start_date`, `end_date`, `initial_deposit`, `provider`, `strategy_name`, or `description` in the search space.
- Keep keys flat; `hyperopt.py` merges sampled values into `params.json` at the top level. If you need to tune a nested knob, either promote it to a top-level key in `params.json` or change `strategy.py` to read it from the top level.
- Do not write or update `params.json` or `params-hyperopt.json` from `strategy.py`. `hyperopt.py` rewrites `params.json` after a study.
