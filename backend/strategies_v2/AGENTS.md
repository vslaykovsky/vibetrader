# strategies_v2 agent instructions

These instructions apply inside each strategy workspace copied from `backend/strategies_v2/`.

## Core Rules

- Implement `strategy.py` and keep `params.json` synchronized with it.
- Always import contracts with `from utils import *`.
- Do not replace or hand-edit `utils.py` or `hyperopt.py` in a thread workspace; update the templates under `backend/strategies_v2/` when contracts change.
- Do not run `strategy.py` or `hyperopt.py` here. The platform runs `scripts/simulate_strategy_v2.py`, streams historical OHLC to stdin, and writes `backtest.json` plus `metrics.json` when applicable.
- Implement requested behavior directly. Do not add alternative behavior, fallback behavior, broad catch-and-continue handlers, fabricated data, mocked results, or hidden invariant recovery.
- Keep strategy code small and direct. Put subscription and signal logic in `strategy.py`; add functions or classes only when they reduce real duplication or complexity.
- Prefer platform subscriptions for market data and built-in indicators whenever they can provide the requested data. Do not reimplement subscribed indicators in `strategy.py` unless the request needs a genuinely custom calculation that the platform cannot provide.
- Prefer compact code over maintainability. 
- Do not validate params from params.json! Do not use ValueError to validate input parameters. Input parameter validation is not needed!

## Strategy Outputs

- `market_order`: creates host-managed trades, markers, equity vs. buy-and-hold, trades table, and `metrics.json`. A run with no executed orders still completes with `num_trades: 0`.
- `chart`: custom analytics via `OutputChart`. Charts may be emitted during the stream or once after stdin EOF and are appended to `backtest.json`.
- Trainable model strategies may emit `trained_model_params.json` only through `OutputTrainedModelParams`.

## `params.json`

`params.json` is the single source of truth for strategy configuration. Include:

- Strategy metadata: `ticker`, native bar `scale`, `strategy_name` without ticker text, and short UI `description`.
- Host inputs: `start_date`, `end_date`, positive `initial_deposit`, optional `provider` (`alpaca`, `moex`, `auto`), optional `simulation_scale` (`1m`, `15m`, `1h`, `4h`, `1d`, `1w`; must be the same as scale `scale`, unless explicitly asked by user for lower value), and optional `max_leverage` (defaults to `1.0`, no margin).
- Strategy tunables: periods, thresholds, lookbacks, sizing, Renko brick settings, model hyperparameters chosen before fitting, and similar knobs.
- `run_mode: "train" | "test"` only for real trainable model strategies like boosted trees and ANNs, not simple indicator or rule strategies. Each process must use exactly one exclusive `run_mode` selected at startup.

Do not hardcode tunables in `strategy.py`. Load `params.json` once at startup, bind values from it, and use those variables for subscriptions, thresholds, lookbacks, sizing, and signal logic. Fixed structural literals are fine when they are not knobs.

All tunables that may be optimized must be top-level keys in `params.json`. `hyperopt.py` shallow-merges sampled values into the root object, so nested tunables are not updated. Nested objects are acceptable only for fixed blobs read as a whole.

The host may merge runtime overrides into `params.json` before process start. Do not add a `--params` flag.

## Runtime I/O

- stdin is exactly one JSON `StrategyInput` object per line after startup: top-level `unixtime` plus `points` containing `ohlc`, `indicator`, `portfolio`, `renko`, and/or `trained_model_params`. `unixtime` is a real simulation/event timestamp and is strictly increasing. The host does not send a synthetic `unixtime=0` bootstrap input, command object, sentinel, or any other stdin shape.
- stdout is exactly one JSON `StrategyOutput` object per line, including the startup line. Each `StrategyOutput` may contain subscription outputs, optional `indicator_series_catalog`, indicator values, market orders, charts, trained model params, and/or `time_ack`. Do not write raw output items, standalone chart/metric JSON, logs, prints, or any other object/text to stdout.
- The only runtime boundary objects are `StrategyInput` read from stdin and `StrategyOutput` written to stdout.
- Match all shapes to the Pydantic contracts in `utils.py`.

For every stdin line read, print exactly one stdout line containing exactly one `OutputTimeAck` with the same `unixtime`. This is required even for portfolio-only lines, early bars with no indicator values, partial updates, lines with no trades, and Renko event lines. If there is nothing else to emit, output only the ack. Missing or delayed acks deadlock the host.

## Standard Strategy Flow

1. Before reading stdin, emit all `ticker_subscription` and `indicator_subscription` outputs, optionally followed by one `OutputIndicatorSeriesCatalog`. The first stdout line is the startup contract; do not read stdin before printing it.
2. After startup, read stdin in a loop. The first stdin line is the first timestamped simulation/event input at or after `params.json.start_date`; it includes the current `portfolio` snapshot before market data. If saved `trained_model_params` are available, they are included on that first timestamped input before market data.
3. In the stdin loop, process `portfolio` and `trained_model_params` before acting on market data for that step.
4. Dispatch `ohlc`, `indicator`, and `renko` points by `point.id`, not by order, ticker/name heuristics, or repeated subscription kind.
5. Update durable histories only from `closed: true` data. Use `closed: false` data only for live intra-bar checks.
6. Emit `market_order`, custom `OutputIndicatorDataPoint`, or `OutputChart` items only when useful, then include the required `time_ack`.

Do not re-emit raw subscribed OHLC or built-in indicator values as custom chart data; the UI already renders subscribed prices and indicators.

## Subscriptions

Set a short, stable, unique `id` on every `OutputTickerSubscription` and `*IndicatorSubscription` (`price`, `fast_ema`, `trend_rsi`, `renko_2`, etc.). Input points echo this id. For multi-output indicators, also use `InputIndicatorDataPoint.name` to distinguish lines such as MACD `signal`, Bollinger `bb_upper`, or Fibonacci keys.

Before calculating an indicator from raw OHLC in `strategy.py`, check whether `utils.py` already exposes a matching subscription. For SMA, EMA, MACD, RSI, ATR, Bollinger Bands, Stochastic, Fibonacci, and Renko, subscribe and consume the returned `indicator` or `renko` points instead of recomputing the same values from OHLC. Local calculations are appropriate only for derived/custom series that are not available through a subscription.

Subscriptions support `session="regular" | "extended" | "all"` and default to `all`. Daily and weekly bars are regular regardless of this value.

`MacdIndicatorSubscription`, `BollingerBandsIndicatorSubscription`, `StochasticIndicatorSubscription`, and `FibonacciIndicatorSubscription` support an `outputs` list. Request only the series the strategy needs.

If emitting `OutputIndicatorDataPoint`, optionally emit one `OutputIndicatorSeriesCatalog` on the startup stdout line after subscriptions. Each catalog entry is `{ "name", "description" }`; names must be unique and must exactly match emitted indicator data point names. Do not emit later catalogs or repeat long descriptions on every data point.

## Bars, Partials, and Simulation Scale

Each `ohlc` and `indicator` input has `closed`:

- `closed: true`: finalized base-scale bar, delivered once.
- `closed: false`: running intra-bar snapshot, delivered only when the subscription uses `partial=True`.

`partial=False` is the default and emits only closed base-scale points. `partial=True` emits additional running points at `update_scale`, defaulting to `simulation_scale`. `update_scale` must divide `scale` and be no finer than `simulation_scale`.

`params.json.simulation_scale` controls the host fetch/driver resolution. It must be the same as or finer than `scale`, and must divide it. When finer than `scale`, the host aggregates driver bars into base bars, emits partial updates where requested, and fills orders at the running close that triggered them. Make it always THE SAME as scale, unless asked explicitly by the user for a finer value. 

## Renko

Subscribe with `OutputIndicatorSubscriptionOrder` wrapping `RenkoIndicatorSubscription`. Renko is close-based: a brick forms when the running close crosses `anchor +/- brick_size`; the first firing bar seeds the anchor. Reversals require one full current brick size move. Use `partial=True` for intra-bar brick detection; `partial=False` limits detection to base-scale closes.

Do not manually build Renko bricks from OHLC bars when `RenkoIndicatorSubscription` can express the requested brick-size mode, scale, session, and partial/update behavior. For ATR Renko, use `brick_size_mode="atr"` with `atr_period` and `atr_multiplier`, then handle incoming `InputRenkoDataPoint` objects by subscription `id`.

Brick-size modes:

- `brick_size_mode="fixed"`: set positive `brick_size`.
- `brick_size_mode="atr"`: use `atr_period` and `atr_multiplier`; brick size is current base-scale ATR times multiplier, and bricks wait until ATR is available.

Renko bricks arrive on separate stdin lines after the regular driver-bar line, with nudged increasing `unixtime` values. Each brick line contains one `InputRenkoDataPoint` and snapshots of `partial=True` subscriptions. Ack every line. A Renko point is final (`closed: true`), has edge prices in `open`/`close`, and fills market orders at the originating driver bar's running close.

Renko is not supported in multi-ticker simulations; use a single ticker when subscribing to bricks.

## Portfolio

`portfolio` points contain account state plus authoritative open positions. Account fields are `cash`, `equity`, and `buying_power`; open positions are `{ "ticker", "order_type", "deposit_ratio", "volume_weighted_avg_entry_price" }`. Position `deposit_ratio` is that leg's current marked exposure as a fraction of account equity. Cash is never represented as a synthetic position. The host includes a portfolio point on every timestamped input it sends to the strategy, including the first market input. Refresh internal position and cash state from it before making price-based decisions.

## Market Orders

- `deposit_ratio` defaults to `1.0`.
- `direction="buy"` opens/adds long when flat or long, spending `deposit_ratio` of available cash; when covering short, `deposit_ratio` is the fraction of open short size to cover. In live trading, the host also caps submitted buy notional by broker-reported buying power.
- `direction="sell"` closes long or opens/adds short; when closing long, `deposit_ratio` is the fraction of open long size to close; when opening short, it sizes exposure as a fraction of account equity.
- For buy sizing, use a top-level tunable such as `deposit_fraction` in `[0, 1]` and pass it as `deposit_ratio`.
- Use `1.0` for full exits/covers.
- Optional `short_explanation` should be a concise trade reason for the Orders table.

Orders fill at the running close of the update that triggered them: intra-bar close for partial/Renko lines, closed-bar close for closed bars.

## Trainable Models

Use `trained_model_params` only for strategies with a real fitted model whose learned state is needed for inference. Examples are boosted trees and ANNs. Do not use it for rule-only strategies.

- `InputTrainedModelParams`: stdin `kind: "trained_model_params"`, `name`, `data`.
- `OutputTrainedModelParams`: stdout `kind: "trained_model_params"`, `name`, `data`.
- `run_mode="train"`: collect data, fit the model, emit exactly one `OutputTrainedModelParams` after stdin EOF, and do not trade.
- `run_mode="test"`: load `InputTrainedModelParams` from the initial input when available, trade/infer only after it is loaded, and raise if required learned params are absent. Do not train or emit trained params in test mode.

Training and testing are separate simulator runs with different `params.json` date windows. Learned weights, scalers, encoders, fitted trees, and calibration values belong in `OutputTrainedModelParams.data`, not `params.json`.

## Charts and Output Data

What the user can see in both the backtest canvas page and live trading canvas:
- All indicators the startegy subscribed to using OutputIndicatorSubscriptionOrder 
- Candlestick diagrams of ticker pricedata for all tickers in OutputTickerSubscription
- All data that the strategy emits with OutputIndicatorDataPoint
- Positions over time chart
- Equity curve vs benchmark chart
- Orders table

What the user can see in backtest canvas only:
- Custom charts produced by the strategy using LightweightChartsChart | PlotlyChart | TableChart

Charts rules:
- Use subscriptions for all market data. Do not fetch data with yfinance or any other source inside `strategy.py`.
- Do not produce duplicate charts. Check what is already emitted automatically and only produce new unique charts.
- Only output charts, OutputIndicatorDataPoint items for data that was explicitly mentioned as chart/visual data in the user request! 
- Prefer OutputIndicatorDataPoint when producing new charts as they are accessible in both backtest and live trading. 
- Only use LightweightChartsChart, PlotlyChart, TableChart if the data cannot be rendered and is not already rendered by using OutputIndicatorSubscriptionOrder, OutputTickerSubscription and OutputIndicatorDataPoint
- Use `PlotlyChart` only as a last resort; it is backtest-only, not interactive, and not synchronized with lightweight charts. When deciding between LightweightChartsChart and PlotlyChart choose LightweightChartsChart if possible. 
- Do not use matplotlib, PNG, SVG, or standalone chart files.
- Do not write `backtest.json` or `metrics.json`; stdout is the only strategy output channel.
- For lightweight-charts time values, use `YYYY-MM-DD` for daily/weekly bars and ISO 8601 UTC datetime or Unix seconds for intraday. Pick one format per chart.
- Label every series, bar, and line clearly with readable-contrast colors.
- Chart-only analysis should not ship `params-hyperopt.json` and should not emit `market_order`.

## `params-hyperopt.json`

If `strategy.py` can emit `market_order`, ship a static `params-hyperopt.json` next to `params.json`.

The file must match `ParamsHyperopt` in `utils.py`; treat that model as the source of truth for field names and search-space spec shapes.

`search_space` keys must be top-level keys already present in `params.json`, with compatible defaults, and `strategy.py` must read those same root keys. Dotted names are just flat JSON property names, not nested paths.

Tune only parameters that affect trading behavior, such as indicator periods, thresholds, buy fractions, and model hyperparameters chosen before fitting. Do not tune `ticker`, `scale`, `simulation_scale`, dates, `initial_deposit`, `provider`, metadata, `run_mode`, or learned model artifacts. Do not write or update params files from `strategy.py`; `hyperopt.py` owns that after a study.
