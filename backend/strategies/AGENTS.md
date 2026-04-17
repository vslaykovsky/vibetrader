# Trading strategy workspace

Backtest strategies and emit JSON the frontend uses for charts and metrics.

Do not run `strategy.py` or `hyperopt.py` here — the platform runs it after your changes.

## Layout

- `strategy.py` — entry point; all authored strategy and analysis logic lives here.
- `utils.py` — shared helpers (Alpaca, moexalgo algopack, paths, JSON). Import only; never edit, replace, or delete.
- `hyperopt.py` — fixed platform hyperparameter driver (random search). Copied read-only into the workspace; do not edit or replace.

## Running `strategy.py`

Invoked as `python strategy.py` only. Ticker, timeframe, and dates are not separate flags; they come from `params.json`. Do not add a `--params` CLI argument; the platform merges run-time overrides into `params.json` before the run.

The chat UI shows a short summary from `params.json["description"]` (not from `strategy.py --help`).

EDA-style workspace: exploratory analysis only (no tradable rules backtest). Read `params.json`, write `backtest.json` with `strategy_name` and `charts` (tables go inline in `charts` as `TableChart` entries). Omit the `metrics` key from `backtest.json`. Do not write `metrics.json` or `params-hyperopt.json`.

Strategy workspace: read `params.json`, write `backtest.json` (do not include `metrics`), and write `metrics.json` for tooling. `params-hyperopt.json` is authored by the agent as a static file (same as `params.json`) to describe the hyperparameter study (`search_space`, `n_trials`, `timeout_seconds`, `direction`, `objective_metric`, etc.) so `python hyperopt.py` can optimize. Do not write or update `params.json` or `params-hyperopt.json` from `strategy.py`.

## Data

- [Alpaca Market Data (Python)](https://alpaca.markets/sdks/python/market_data.html#market-data)
  - Credentials: `ALPACA_API_KEY`, `ALPACA_SECRET_KEY`
- [MOEX Market Data](https://moexalgo.github.io/docs/api) 
  - Credentials: `MOEX_API_KEY` for Algopack access.
- Provider choice rule:
  - Use Alpaca for all markets except Russia.
  - Use MOEX for Russian instruments/markets.
  - Auto mode is allowed: try providers in this order Alpaca -> MOEX.
- Keep the same OHLCV dataframe format from `utils.py` (`open`, `high`, `low`, `close`, `volume` with datetime index).
- Drop bars where `(high - low) > 0.30 * high` (broken prints).
- Make sure to use dotted versions of ticker symbols where applicable. E.g. for BRK-B use BRK.B when fetching data from Alpaca. 
- Do NOT use yfinance. Use only helpers from `utils.py` to access market data (Alpaca/MOEX).
- Today's data is not available, use only past data for analysis and strategies. 

## `params.json`

Single source of truth: ticker, bar timeframe (e.g. Alpaca `1Day`, `1Hour`), backtest window (`start_test_date` / `end_test_date` or equivalent), and every fixed numeric/boolean input the run needs. No duplicates in code — read this file, do not hardcode those constants.

Ship a valid file with sensible defaults so a fresh workspace runs. Do not use in-code defaults to paper over missing keys. Do not write or update this file from `strategy.py`; `hyperopt.py` may rewrite it after a study.

Always include `strategy_name`: human-readable, no ticker in the name.

## `params-hyperopt.json` (strategy only)

For strategy backtests (not EDA), create a `params-hyperopt.json` file as a static config matching the `ParamsHyperopt` model in `utils.py`. Do not generate or update this file from `strategy.py`.
This file should contain hyperparameters from params.json that are worth optimizing. 

## `metrics.json` (strategy only)

When the workspace is a strategy backtest (not EDA-only) strategy.py should produce `metrics.json` — free-form JSON object (the frontend recognizes a standard set of scalar keys, but extra keys are fine).

## `backtest.json`

`backtest.json` is validated by the Pydantic model `DataJson` in `utils.py` (ordered `charts` list plus optional `metrics`). Use `save_backtest_json(...)` to validate and write it.

- The frontend renders only the supported chart/table shapes; stick to the models in `utils.py` as the contract.
- The `charts` list is rendered **in order**; pick that order deliberately based on the user's request and sensible defaults. Include at least one `charts` entry or the frontend shows nothing.
- Tables live inside `charts` as `TableChart` entries (`type: "table"`, `title`, `rows`) so each table sits exactly where it belongs in the narrative. There is no top-level `table` field.
- You may include multiple `TableChart` entries in `charts` (e.g. a trades table, a holdings table, a per-ticker stats table). Give each a distinct `title` so the reader knows what it is.
- When producing a trades table, include a `comment` column with a short explanation for each trade.
- Prefer `lightweight-charts` when the schema above supports the figure; otherwise use `plotly`.
- Do not use matplotlib to render charts, only plotly or lightweight-charts are allowed.
- `strategy.py` must not write PNG, JPEG, WebP, SVG, or any other image or standalone chart file. All visuals go only in `backtest.json` under the top-level `charts` array, using `lightweight-charts` or `plotly` objects exactly as documented above (no `savefig`, no chart exports).
- `time` values on lightweight-charts series points and markers must be one of:
  - `"YYYY-MM-DD"` for daily bars (business-day format);
  - ISO 8601 datetime (e.g. `"2020-01-02T09:30:00Z"`) or unix epoch seconds (int) for intraday bars or intraday markers.
  Pick one format per chart and use it for every series point and every marker in that chart (don't mix daily strings with intraday datetimes in the same chart). For intraday data, use UTC.
- Use shared time axis across lightweight-charts charts so scroll/zoom stays in sync.
- Equity curves: when strategy.py is a strategy with trades, then include equity curve as a separate chart, include buy-and-hold benchmark to compare to. 
- Signals: use `markers` on the right series.
- Readable contrast: make sure colors are set so text is readable on the background.
- Every series/bar/line clearly labeled.
- When rendering raw prices default to using candlesticks. 
