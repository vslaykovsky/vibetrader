# strategies_v2 — agent instructions

You implement **`strategy.py`** and keep **`params.json`** up to date. Do not embed tunable values (ticker, scale, periods, thresholds, sizing, renko brick size, etc.) as literals in `strategy.py` — define them in `params.json` and read them after load (see **`params.json`**). Author **`params-hyperopt.json`** as a static config whenever the strategy emits `market_order` outputs. Do not change **`utils.py`** (it defines the JSON shapes) or **`hyperopt.py`** (fixed platform hyperparameter driver, copied read-only into the workspace).
Always import everything from utils with: `from utils import *`

Two workspace flavours are supported:

- **Trading strategy** — `strategy.py` emits `market_order` outputs; the host auto-generates a price chart with buy/sell markers, an equity curve vs. buy-and-hold, a trades table, and `metrics.json`. You may additionally emit `OutputChart` items (see **EDA / custom analytics** below) to prepend custom analytics before those host charts.
- **EDA strategy** — `strategy.py` emits no `market_order` outputs. It subscribes to inputs, accumulates them during the stream, runs its analysis at stdin EOF, and emits a final batch of `OutputChart` items. The host writes only those charts into `backtest.json`, skips `metrics.json`, and you skip `params-hyperopt.json`.

Do not run `strategy.py` or `hyperopt.py` here — the platform runs them through the historical simulator (`scripts/simulate_strategy_v2.py`) after your changes. The simulator fetches OHLC bars for the ticker/scale in `params.json` across `start_date..end_date`, streams them to your `strategy.py`, and writes `backtest.json` (strategy name + charts) and, for trading strategies, `metrics.json` (scalar metrics) into the workspace.

## `params.json`

Single source of truth next to `strategy.py`: ticker, bar `scale` (strategy's native timeframe), a human-readable `strategy_name` (no ticker in the name), and a short `description` for the UI.

Also include simulator inputs consumed by the host (not read by `strategy.py`): `start_date` / `end_date` (ISO `YYYY-MM-DD`) defining the historical backtest window, `initial_deposit` (positive number), an optional `provider` (`alpaca`, `moex`, or `auto`), and an optional `simulation_scale` (`1m` / `15m` / `1h` / `4h` / `1d` / `1w`, default = `scale`) — see **Simulation scale** below.

**No hardcoded strategy parameters in `strategy.py`:** Anything a user, the UI, or hyperopt might change must live in `params.json`, not as literals in `strategy.py`. Load `params.json` once at startup, bind tunables to variables, and use those everywhere — subscriptions (`ticker`, `scale`, `period`, `brick_size`, MACD lengths, BB multipliers, etc.), thresholds, lookbacks, min bars, and buy sizing keys read by **Market orders**. Do not write `ticker="SPY"`, `period=14`, `brick_size=1.0`, or `if rsi > 70` when `70` should be a knob unless `70` is only the default in `params.json` and the code compares against `params["rsi_overbought"]` (or equivalent). Literals are only for values that are fixed by contract and not mirrored in `params.json` (for example structural `0`/`1`, required `utils.py` enums, or a `default` inside `params.get` that matches the default in `params.json`).

**Tunable parameters (strategy knobs, sizing, thresholds, indicator periods, etc.):** define every such value as a **top-level key** in `params.json` (flat JSON). `strategy.py` must read each tunable with `params["<key>"]` (or `params.get("<key>", default)`), using **the same key string** you will use in `params-hyperopt.json` `search_space`. Do not tuck tunables only under nested objects (for example `rsi.period` as the sole path while hyperopt writes a sibling key `rsi.period` at the root): `hyperopt.py` shallow-merges sampled values onto the **root** object only, so nested fields are never updated by a study. Nested objects are fine only for **fixed** blobs the host or strategy reads as a whole when those inner fields are not individually tuned or overridden.

**Consistency with `params-hyperopt.json`:** every `search_space` key must already exist as a top-level entry in `params.json` with a valid default. Do not list a key in `search_space` that the strategy does not read from the root of `params.json`. Do not read a tunable from nesting if that tunable is listed in `search_space` — the study will not change nested copies.

Update this file accordingly when updating `strategy.py`. Read the strategy-relevant values at startup; do not duplicate them as unrelated constants in `strategy.py`. The host may merge run-time overrides into `params.json` before the process starts; do not add a `--params` CLI flag.

Order sizing for buys: use a top-level tunable (for example `deposit_fraction` in `[0, 1]`, default **`1`**) and pass it as `deposit_ratio` on buy `market_order` outputs; see **Market orders**.

## I/O

- **stdin:** one JSON object per line. Each line is a `StrategyInput`: top-level `unixtime` and a `points` list of `ohlc`, `indicator`, `portfolio`, and/or `renko` items. `unixtime` is strictly monotonic across lines. Most lines correspond to a driver bar at its natural clock; a driver bar that produces sub-bar events (e.g. renko bricks) fans out into multiple lines, each with its own nudged `unixtime` so the stream stays strictly increasing.
- **stdout:** one JSON object per line. Each line is a `StrategyOutput`: a list of outputs (subscriptions, indicator values, market orders, time acks). Match the discriminated models in `utils.py`.
- **`time_ack` (required):** The host applies strict backpressure: **it will not write the next stdin line until your process prints a stdout line that includes an `OutputTimeAck` (`"kind": "time_ack"`) for the current input line's `unixtime`.** Emitting that ack is what unblocks the next `read` / next `for raw in sys.stdin` iteration. If you buffer many stdin lines before printing any ack, or omit the ack while still reading, you deadlock: the host waits for stdout while you wait for more stdin.
  **Unconditional rule:** For **every** `StrategyInput` line you read from stdin (any mixture of `portfolio`, `ohlc`, `indicator`, `renko` in `points`), print **exactly one** stdout line for that line that includes **exactly one** `time_ack` with `unixtime` equal to that input's top-level `unixtime`. No exceptions by point kind: portfolio-only lines, warm-up bars with no indicator values, partial vs closed, and lines where you emit no trades or charts all use the same rule. If you have nothing else to send, use a one-item `StrategyOutput` containing only the `time_ack`. Put `time_ack` on that stdout line with any other outputs for the step (typically last in the list). Omitting `time_ack` for any read line causes the host to time out waiting for stdout.

## Subscription `id` (preferred dispatch key)

Every `OutputTickerSubscription` and every `*IndicatorSubscription` accepts an optional `id: str`. Set it to a short, readable, stable handle that describes the **role** of the subscription in your strategy (e.g. `"price"`, `"fast_ema"`, `"slow_ema"`, `"trend_rsi"`, `"renko_2"`). Ids must be unique across all subscriptions in the same startup batch — if you do not provide one, the host auto-assigns a deterministic `f"{kind}_{n}"` (e.g. `"ema_0"`, `"ema_1"`), but explicit ids make the strategy code self-documenting.

**Every input data point the host produces echoes the originating subscription's id**:

- `InputOhlcDataPoint.id` — id of the `OutputTickerSubscription` that produced this bar.
- `InputIndicatorDataPoint.id` — id of the indicator subscription that produced this value.
- `InputIndicatorDataPoint.name` — which output column for that subscription (e.g. `bb_lower` vs `bb_upper` vs `bb_middle`, or `macd` / `signal` / `histogram` for MACD). Allowed names depend on the subscription type and on your `outputs` list (see below). Indicator points do **not** repeat `ticker` or subscription `kind` on stdin: after you print subscriptions at startup, keep a map `id →` your subscription object (or ticker + `kind` + `outputs`) and branch in the stdin loop on **`id` + `name`** only.
- `InputRenkoDataPoint.id` — id of the `RenkoIndicatorSubscription` that produced this brick.

**`outputs` on multi-line subscriptions (`utils.py`):** `MacdIndicatorSubscription`, `BollingerBandsIndicatorSubscription`, `StochasticIndicatorSubscription`, and `FibonacciIndicatorSubscription` each include an `outputs` field listing which series the host computes and streams (defaults: all lines). Request only what you need (e.g. MACD `signal` alone). For Fibonacci, each entry is a string key such as `fib_0p618` (same value as `InputIndicatorDataPoint.name` on stdin); allowed keys are the `FibonacciOutputKey` literals in `utils.py`.

Match input points to subscriptions by `point.id` rather than by `(ticker, name)` heuristics or by the order of items in `points`. This keeps your code obviously correct when you have **two of the same kind** (two SMAs, two tickers, fast/slow EMAs, etc.):

```python
import json
params = json.load(open("params.json"))
ticker, scale = params["ticker"], params["scale"]
fast_p, slow_p = int(params["fast_ema_period"]), int(params["slow_ema_period"])
outs = [
    OutputTickerSubscription(id="price", ticker=ticker, scale=scale),
    OutputIndicatorSubscriptionOrder(
        indicator=EmaIndicatorSubscription(id="fast_ema", ticker=ticker, scale=scale, period=fast_p)
    ),
    OutputIndicatorSubscriptionOrder(
        indicator=EmaIndicatorSubscription(id="slow_ema", ticker=ticker, scale=scale, period=slow_p)
    ),
]
print(StrategyOutput(outs).model_dump_json(), flush=True)

last_fast = last_slow = None
for raw in sys.stdin:
    inp = StrategyInput.model_validate_json(raw)
    out = []
    for pt in inp.points:
        if pt.kind == "indicator" and pt.id == "fast_ema":
            last_fast = pt.value
        elif pt.kind == "indicator" and pt.id == "slow_ema":
            last_slow = pt.value
        elif pt.kind == "ohlc" and pt.id == "price" and pt.closed:
            ...
    out.append(OutputTimeAck(unixtime=inp.unixtime))
    print(StrategyOutput(out).model_dump_json(), flush=True)
```

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

## Renko subscriptions (`kind: "renko"`)

Ask for renko bricks by emitting an `OutputIndicatorSubscriptionOrder` wrapping a `RenkoIndicatorSubscription(ticker, scale, brick_size, partial=True, update_scale=...)`. Bricks are close-based (a new brick prints whenever the running close crosses `anchor ± brick_size`; the first firing bar only seeds the anchor and emits no brick). The anchor persists across bars; reversals require one full `brick_size` move (no 2× rule). Set `partial=True` if you want bricks as soon as they form at the `update_scale` cadence; `partial=False` limits detection to base-scale closes.

**Stream semantics — line-per-event.** Renko bricks are emitted on their own `StrategyInput` lines, not bundled with the regular ticker/indicator update:

1. On a driver bar where regular ticker/indicator subscriptions fire, you first receive the usual line at the driver bar's `unixtime` with those points.
2. For each brick produced on that driver bar you receive an additional line at `unixtime + 1`, `unixtime + 2`, … Each brick line contains: the single `InputRenkoDataPoint`, plus a snapshot of every `partial=True` ticker/indicator subscription (all with `closed: false`) so you see the current running OHLC and partial indicators at the brick moment. `partial=False` subscriptions are **not** re-sent on brick lines — by contract they only fire at their own `scale` close.
3. Each line requires its own `time_ack` echoing the line's `unixtime`.

Consequences your `strategy.py` must respect:

- Treat `InputRenkoDataPoint` as a fully-formed, final event (`closed: true` always). There is no partial brick.
- Within one driver bar, brick lines arrive in strict formation order (`up` bricks from low to high anchor, `down` bricks from high to low); their `unixtime`s are strictly increasing. Across bars, the contract "next line's `unixtime` > current line's `unixtime`" is preserved.
- On an `InputRenkoDataPoint`, `open` and `close` are the brick's edges (`direction="up"` ⇒ `close == open + brick_size`, `"down"` ⇒ `close == open - brick_size`); `brick_size` matches `params.json`'s renko config.
- If you render bricks on a `LightweightChartsChart`, feed each brick as a candlestick with `time = unixtime_of_that_line` (seconds, intraday). Because the host already guarantees distinct unixtimes per brick, Lightweight-Charts accepts them directly and spaces them on a non-uniform x-axis that still synchronises with price / equity panes via shared time.
- Market orders emitted on a brick line fill at the running close of the originating driver bar (same rule as mid-bar fills on `closed: false` points).

Renko subscriptions are not supported in multi-ticker simulations yet — use a single ticker if your strategy subscribes to bricks.

## Portfolio input (`kind: "portfolio"`)

- **`positions`:** list of `{ "ticker", "order_type", "deposit_ratio", "volume_weighted_avg_entry_price" }` where `deposit_ratio` is in `[0, 1]` (each leg's size as a fraction of the deposit) and `volume_weighted_avg_entry_price` is the book's quantity-weighted average fill price for that open leg (the simulator derives this from executed `market_order` fills).
- **On startup:** the host may send a portfolio line before or mixed with the first market data so you can recover after restarts; the account may already be in position when the strategy process starts. Merge this into your internal position state before acting on prices.
- **After each trade:** when you emit a `market_order`, the next stdin line (or the same batch policy the host uses) may include a portfolio snapshot reflecting the updated book. Treat it as authoritative for open positions and per-leg sizes.

## Market orders (`kind: "market_order"`)

- **`deposit_ratio`** defaults to **`1.0`** when omitted (matches `utils.py`).
- **`deposit_ratio` on `buy`** — fraction of **cash** spent (read the fraction from your top-level buy-size key in `params.json`, default **`1`**).
- **`deposit_ratio` on `sell`** — fraction of **open size** closed, not cash; use **`1.0`** for a full exit. Reusing the same buy-fraction top-level tunable on sells is a partial exit; keep “in position” in sync with **`portfolio`** if you do that.

The fill price is the running close at the time of the update that triggered the order (intraday mid-bar price when `closed: false`, closed-bar close when `closed: true`).

## What the strategy should do

1. **Start:** emit subscription outputs first — `ticker_subscription` for prices you need (set `partial=True` and optionally `update_scale` if you want intra-bar prices), `indicator_subscription` for built-ins (sma, ema, macd, rsi, atr, bb, stochastic, renko) with ticker, scale, parameters, and optional `partial` / `update_scale`. **Set a readable `id` on every subscription** (see **Subscription `id`**) — each input point will carry that id so you can dispatch by role. Read subscription parameters from `params.json` where applicable. Print that startup `StrategyOutput` before you read stdin so an initial **`portfolio`** line the host buffered is handled in the main loop with the same **`time_ack`** rule as every other line (**I/O**).
   You must emit all subscriptions **before** reading or acting on any `ohlc` / `indicator` / `renko` points from stdin.
2. **Loop:** read each `StrategyInput` line; if it contains `portfolio`, refresh position state from `positions` before or together with processing `ohlc` / `indicator` for that step. **Match each `ohlc` / `indicator` / `renko` point to your subscription via `point.id`.** Distinguish closed vs partial points using the `closed` flag and update durable state (history lists, previous-close memory) only on closed points. On partial updates use the running values for live signal checks. When you trade emit `market_order` items per **Market orders**. On **every** stdin line, print stdout with the **`time_ack`** for that line's `unixtime` (see **I/O**); the host will not send the next stdin line until that line is printed.
3. **Each step:** Only emit OutputIndicatorDataPoint for a few key debug or plot values if helpful. Do not output raw input prices or indicators, and skip this output if there's nothing useful to show.

Keep logic clear and small; put all subscription and signal rules in `strategy.py`. Prefer shorter code over readability. Do not create functions or classes unless absolutely necessary for reusability.

## EDA / custom analytics (`kind: "chart"`)

An EDA strategy (or a trading strategy that wants extra analytics alongside the host's auto-generated charts) emits final charts to stdout as `OutputChart` items wrapping one of `LightweightChartsChart`, `PlotlyChart`, or `TableChart` defined in `utils.py`. The host validates them and appends them to `backtest.json` under `charts`.

Pattern for pure EDA:

1. In startup, emit subscription outputs for every input you need (`ticker_subscription`, `indicator_subscription`). Same as a trading strategy.
2. Only after you have emitted subscriptions, read stdin with `for raw in sys.stdin:` — when the host closes stdin, the loop exits.
3. On each step, if `point.closed`, push the values you need into long-lived lists (returns, indicator series, labels, etc.). On partial points do nothing unless you specifically need intra-bar stats. On every stdin line, emit the required `time_ack` (same rule as trading strategies; see **I/O**).
4. After the loop, run your analysis, build a list of `OutputChart` items, print `StrategyOutput(items).model_dump_json()` on a single line, flush, and exit.

A trading strategy can emit `OutputChart` items in the same line as `market_order` / `time_ack`, at any step. All collected `OutputChart` items are rendered before the host's price / equity / trades charts.

Constraints:

- No matplotlib, PNG, SVG, or any other image format. Use `lightweight-charts` for timeseries (so the zoom/pan axis stays in sync with the host charts), `plotly` for arbitrary figures (histograms, heatmaps, scatter), and `TableChart` for tabular summaries.
- Do not write `backtest.json`, `metrics.json`, or any standalone chart file from `strategy.py`. The host owns those files; your only output channel is stdout.
- Do not use matplotlib, yfinance, or any market-data fetch from within `strategy.py`. All bars arrive through stdin subscriptions.
- For lightweight-charts `time` values follow the same rule as v1: `"YYYY-MM-DD"` for daily/weekly bars, ISO 8601 UTC datetime or unix epoch seconds for intraday. Pick one format per chart and use it for every series point and every marker in that chart.
- Every series / bar / line must be clearly labeled. Use readable-contrast colors.
- Pure EDA strategies must not ship `params-hyperopt.json` and must not emit `market_order` outputs (the host decides EDA vs. trading by the presence of any executed trade).

## `params-hyperopt.json` (required for trading strategies)

If your `strategy.py` emits `market_order` outputs (a tradable strategy, not pure EDA), ship a static `params-hyperopt.json` next to `params.json` so `python hyperopt.py` can optimize.

The file must match the **`ParamsHyperopt`** model in **`utils.py`** (field names, types, defaults, and `search_space` entries as **`HyperoptIntSpec`**, **`HyperoptFloatSpec`**, or **`HyperoptCategoricalSpec`** per the `type` discriminator). Treat those Pydantic models as the single source of truth; do not restate their shape here.

**Contract with `params.json`:** `search_space` keys are **top-level** names. Each key must match a **top-level** tunable in `params.json` (same string, same type after coercion) that `strategy.py` reads from the merged root object. Dotted names in examples (e.g. `rsi.period`) are only a naming convention for flat keys — they are still a single JSON property name at the root, not a path into a nested `rsi` object unless you also defined nested merging (the platform does not). Keep `params.json` and `search_space` in lockstep: add or rename a tunable in both files together.

Constraints:

- Tune only parameters that affect trading behavior (indicator periods, thresholds, buy fractions, etc.). Do not put `ticker`, `scale`, `simulation_scale`, `start_date`, `end_date`, `initial_deposit`, `provider`, `strategy_name`, or `description` in the search space.
- `hyperopt.py` shallow-merges sampled values onto the root of `params.json`; all optimized knobs must therefore live as top-level keys there and be read as such in `strategy.py` (see **`params.json`**).
- Do not write or update `params.json` or `params-hyperopt.json` from `strategy.py`. `hyperopt.py` rewrites `params.json` after a study.
