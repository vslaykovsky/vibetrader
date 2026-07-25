# Rust strategy workspace instructions

These instructions apply to a generated Rust strategy workspace.

## Core rules

- Implement `strategy.rs` and keep `params.json` synchronized with it.
- Use the contracts from `use crate::utils::*;`.
- Do not edit `utils.rs`, `worker.rs`, `simulator.rs`, `optimizer.rs`, `optimizer_runtime.rs`, `portfolio.rs`, `Cargo.toml`, `utils.py`, or `hyperopt.py`; those are platform-managed templates.
- Do not run the entry directly. The platform links it into a release-mode Rust simulation binary which writes `backtest.json` and `metrics.json`.
- Keep the implementation compact. Put subscriptions and signal state in `strategy.rs`; add abstractions only when they reduce meaningful duplication.
- Use host indicator subscriptions whenever `IndicatorSubscription` supports the requested calculation. Do not fetch market data or call network services from the strategy.
- Do not write output files. Strategy output is emitted only through the runtime contract.
- Do not add fallback behavior, fabricated data, mocked results, broad catch-and-continue handlers, or hidden invariant recovery.

## Required structure

`strategy.rs` must:

1. Deserialize its tunables from `params.json` once with `load_params`.
2. Define a state type implementing `StrategyHandler`.
3. Return all ticker and indicator subscriptions from `startup`.
4. Process each `StrategyInput` in `on_step`, returning useful outputs only.
5. Expose `pub fn build_strategy() -> Result<GeneratedStrategy, Box<dyn Error>>`.

Do not define `main`. `worker.rs` supplies the interactive process entrypoint and `simulator.rs` calls the handler directly during historical backtests. Never construct `OutputDataPoint::TimeAck`; it is only part of the interactive worker protocol.

## `params.json`

`params.json` is the source of truth for:

- `ticker`, native `scale`, `simulation_scale`, `strategy_name`, and `description`;
- `start_date`, `end_date`, positive `initial_deposit`, optional `provider`, and optional `max_leverage`;
- every strategy period, threshold, lookback, sizing fraction, and model hyperparameter.

Deserialize all used settings into a `Params` struct. Do not hardcode tunable values. Tunables that may be optimized must be top-level keys. Use stable semantic names such as `fast_ema_period` or `entry_threshold`, not names containing their current literal value.

`simulation_scale` must normally equal `scale`. Use a finer value only when the user explicitly requests partial/intra-bar processing; it must divide the base scale.

## Inputs and state

`StrategyInput` contains a strictly increasing `unixtime` and a list of `InputDataPoint` values:

- `Ohlc`: subscription `id`, `ticker`, OHLCV, and `closed`.
- `Indicator`: subscription `id`, output `name`, value, and `closed`.
- `Portfolio`: cash, equity, buying power, and authoritative open positions.
- `Renko`: subscription `id`, ticker, brick size/edges/direction, and `closed`.
- `TrainedModelParams`: a named learned-state JSON value.

Dispatch data by subscription `id`, and for multi-output indicators also by `name`. The helpers `input.ohlc(id)`, `input.indicator(id, name)`, and `input.portfolio()` cover common cases.

Refresh position/cash state from `Portfolio` before acting on market data. Update durable histories only from `closed=true`; use partial values only for the requested intra-bar checks.

## Subscriptions

Every ticker and indicator subscription needs a short, stable, unique `id`. Valid scales are `1m`, `15m`, `1h`, `4h`, `1d`, and `1w`; valid sessions are `regular`, `extended`, and `all`.

Available built-ins are SMA, EMA, MACD, RSI, ATR, Bollinger Bands, Stochastic, Fibonacci, and Renko. Wrap an `IndicatorSubscription` in `OutputDataPoint::IndicatorSubscription`. Request only needed multi-output series.

For partial subscriptions, set `partial=true` and an `update_scale` that divides the base scale and is no finer than `simulation_scale`. Otherwise use `partial=false` and `update_scale=None`.

## Orders

Return `OutputDataPoint::MarketOrder` to trade:

- `direction="buy"` opens/adds long or covers short.
- `direction="sell"` closes long or opens/adds short.
- `deposit_ratio` is in `[0, 1]`; use a top-level params tunable for entry sizing and `1.0` for full exits/covers.
- `short_explanation` is a concise reason for the Orders table.

Orders fill at the running close of the event that triggered them. If a strategy can trade, include a valid static `params-hyperopt.json` whose search-space keys are top-level keys in `params.json`.

## Charts and model output

Do not duplicate subscribed OHLC or indicator plots. Emit `OutputDataPoint::Indicator` only for explicitly requested custom series. Emit `OutputDataPoint::Chart` only when the requested visualization cannot be represented by subscriptions or custom indicator points; its JSON value must match the existing lightweight-charts, table, or Plotly chart contract.

For a real trainable model, use exclusive `run_mode` values `train` and `test`. Training collects data, emits one `TrainedModelParams` from `on_finish`, and does not trade. Testing consumes the initial trained params, does not fit, and trades/infers only after learned state is loaded.

## Runtime transport

Historical backtests call `StrategyHandler` in-process from the Rust simulator, with no per-bar serialization, pipe, or process wakeup. A Python market-data adapter uses the existing providers/cache and sends one prepared dataset to the simulator through a MessagePack file before the hot loop starts.

The paced interactive runner uses `worker.rs`; the platform chooses JSON-lines or framed MessagePack with `STRATEGY_IPC_TRANSPORT`. Generated strategy logic must not access stdin/stdout directly or print to stdout; diagnostics may use stderr.

For single-ticker, closed, same-scale SMA strategies, hyperparameter optimization runs in one Rust optimizer process. It fetches the maximum-warmup OHLC frame once, creates a fresh strategy and portfolio for every metrics-only trial, and renders full artifacts only for the winning parameters. Walk-forward mode constructs and evaluates every fold in that process. For US equities, an OOS fold containing no NYSE session (weekends and exchange holidays only) is recorded as skipped and consumes no optimization trials. Each winning strategy gets non-trading signal warmup before its OOS window, while cash and open positions are carried continuously from the preceding active OOS fold; skipped folds leave that state unchanged. `test_window_days` is both the OOS fold length and retraining interval so execution remains contiguous. The optimizer writes the stitched `backtest.json`, `metrics.json`, and `walkforward.json`. Other subscription topologies use the compatibility optimizer.
