# Trading strategy workspace

Backtest strategies and emit JSON the frontend uses for charts and metrics.


**Do not run `strategy.py` here** — the platform runs it after your changes.

## Layout

- **`strategy.py`** — entry point; all authored strategy and backtest logic lives here.
- **`utils.py`** — shared helpers (Alpaca, paths, JSON). Import only; never edit, replace, or delete.
- **No other `.py` files** in this folder (only `strategy.py` and `utils.py`).

## `strategy.py` CLI

Ticker, timeframe, and dates are **not** separate flags; they come from `output/params.json`.

Implementing **`--backtest`**, **`--eda`**, and **`--hyperopt`** is optional; ship only what the task needs. **`--hyperopt`** must not be added until **`--backtest`** is implemented.

- **`--backtest`** — Read `output/params.json`, run the backtest, write `output/data.json` (stats + chart data), print performance to stdout.
- **`--eda`** — Read `output/params.json`, run **exploratory analysis** only (distributions, correlations, volatility, seasonality, liquidity, regimes, etc.) — **not** strategy rules or walk-forward performance. Write `output/data.json` with a `charts` array and `strategy_name` from params. Omit `metrics` unless you have meaningful backtest-style numbers. Print concise findings. Put analysis-only knobs in `output/params.json` with the same static-params rules as below.
- **`--help`** — `ArgumentParser(description=...)` — one short paragraph on strategy logic; leave empty or minimal until there is behavior to describe.

**Mode choice:** `--eda` for data analysis or market research; `--backtest` for entries/exits, sizing, or walk-forward results. **`--hyperopt`** only when the user asks to **train or optimize parameters**, **`--backtest`** already exists, and you are not doing that search inside **`--eda`**. After `update_strategy`, refresh with `python strategy.py --eda` or `--backtest` as appropriate.

**`--hyperopt`:** Only when requested and **`--backtest`** already exists. Optimize using ranges and training window from `output/params.json` (or equivalent in code), print results, write tuned params back to `output/params.json` for the next `--backtest`. Prefer Optuna or similar. **Hard cap: 120s** runtime.

## Data

- [Alpaca Market Data (Python)](https://alpaca.markets/sdks/python/market_data.html#market-data)
- Credentials: `ALPACA_API_KEY`, `ALPACA_SECRET_KEY`
- Drop bars where `(high - low) > 0.30 * high` (broken prints).
- Make sure to use dotted versions of ticker symbols where applicable. E.g. for BRK-B use BRK.B when fetching data from Alpaca. 

## `output/params.json`

Single source of truth: ticker, bar timeframe (e.g. Alpaca `1Day`, `1Hour`), backtest window (`start_test_date` / `end_test_date` or equivalent), every fixed numeric/boolean input, and hyperopt config when applicable (train window, search ranges, etc.). No duplicates in code — read this file, do not hardcode those constants.

Ship a valid file with sensible defaults so a fresh workspace runs. **Do not** use in-code defaults to paper over missing keys. **Do not** write or update this file from `strategy.py` — keep it static.

Always include **`strategy_name`**: human-readable, no ticker in the name.

## `output/data.json`

**Only these top-level keys are rendered by the frontend.** Any other key (e.g. `summary_table`, `top10`, `chart`) is **silently ignored** — never emit them.

```json
{
  "strategy_name": "...",
  "charts": [],
  "table": [],
  "metrics": {}
}
```

- **`strategy_name`** (`string`) — always include; copy from params.
- **`charts`** (`array`) — always include; at least one chart for anything to render.
- **`table`** (`array`) — optional; tabular data (rankings, holdings, stats rows).
- **`metrics`** (`object`) — `--backtest` only; omit for `--eda`.

At least one of `charts` (non-empty) or `table` (non-empty) **must** be present or the frontend shows nothing.

### `charts`

Ordered array. The frontend renders each item with **lightweight-charts** (time series) or **Plotly.js** (everything else). **No JS chart code** — data only.

Each chart object must have:

- **`type`**: `"lightweight-charts"` or `"plotly"` (any other value is skipped)
- **`title`**: string (displayed as section heading; use `""` if none)

#### lightweight-charts

Time-indexed OHLC, equity, overlays, etc.

```json
{
  "type": "lightweight-charts",
  "title": "Price Chart with SMA Crossover",
  "series": [
    {
      "type": "Candlestick",
      "options": {"upColor": "#26a69a", "downColor": "#ef5350"},
      "data": [{"time": "2024-01-02", "open": 100, "high": 105, "low": 99, "close": 103}],
      "markers": [
        {"time": "2024-01-15", "position": "belowBar", "color": "#26a69a", "shape": "arrowUp", "text": "BUY"},
        {"time": "2024-02-10", "position": "aboveBar", "color": "#ef5350", "shape": "arrowDown", "text": "SELL"}
      ]
    },
    {
      "type": "Line",
      "options": {"color": "#f6c90e", "lineWidth": 2, "title": "SMA 20"},
      "data": [{"time": "2024-01-02", "value": 101.5}]
    }
  ]
}
```

- **`series`**: array (default `[]`). Each item:
  - **`type`**: one of `Candlestick`, `Line`, `Area`, `Histogram`, `Baseline`, `Bar` (unknown type → series skipped)
  - **`options`**: object passed to `chart.addSeries()` (optional)
  - **`data`**: array passed to `series.setData()` (optional)
  - **`markers`**: array passed to `createSeriesMarkers()` — sorted by `time` automatically (optional)

#### Plotly

Distributions, scatter, bars, heatmaps, subplots, etc.

```json
{
  "type": "plotly",
  "title": "PnL Distribution",
  "data": [
    {"type": "histogram", "x": [1.2, -0.5, 3.1], "marker": {"color": "#26a69a"}}
  ],
  "layout": {"xaxis": {"title": "Return %"}, "yaxis": {"title": "Count"}}
}
```

- **`data`**: array of Plotly traces (default `[]`) — passed to `Plotly.newPlot`. [Plotly.js reference](https://plotly.com/javascript/reference/).
- **`layout`**: object shallow-merged with dark-theme defaults — passed to `Plotly.newPlot`.

### `table`

Optional array of row objects. Column headers are derived from `Object.keys(rows[0])`. Underscores in column names are replaced with spaces and the name is capitalized.

```json
{
  "table": [
    {"Ticker": "AAPL", "Close": 189.50, "SMA50": 185.20, "Distance_pct": 2.32, "Side": "above"},
    {"Ticker": "MSFT", "Close": 410.30, "SMA50": 412.10, "Distance_pct": -0.44, "Side": "below"}
  ]
}
```

- Numbers are auto-formatted; null/undefined render as empty.
- Use this for rankings, holdings, scan results, or any tabular data you want displayed.

### `metrics`

`--backtest` only. Object with these recognized keys:

- **`total_return`** — number, shown as `…%`, green/red by sign (e.g. `12.5`)
- **`sharpe_ratio`** — number, `.toFixed(3)` (e.g. `1.234`)
- **`max_drawdown`** — number, shown as `…%`, always red (e.g. `-8.2`)
- **`win_rate`** — number, shown as `…%` (e.g. `62.5`)
- **`num_trades`** — number (e.g. `47`)
- **`final_equity`** — number, shown as `$…` with locale formatting (e.g. `112500`)

Omit `metrics` entirely for `--eda` to hide the panel.

### Chart rules

- Do not use matplotlib to render charts, only plotly or lightweight-charts are allowed.
- `strategy.py` must **not** write PNG, JPEG, WebP, SVG, or any other image or standalone chart file. All visuals go only in **`output/data.json`** under the top-level **`charts`** array, using **`lightweight-charts`** or **`plotly`** objects exactly as documented above (no `savefig`, no chart exports under `output/` or elsewhere).
- Shared time axis across lightweight-charts so scroll/zoom stays in sync.
- Equity curves: include buy-and-hold benchmark on the same chart.
- Signals: use `markers` on the right series.
- Readable contrast: make sure colors are set so text is readable on the background.
- Every series/bar/line clearly labeled.
