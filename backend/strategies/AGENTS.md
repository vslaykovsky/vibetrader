# Trading strategy workspace

Backtest strategies and emit JSON the frontend uses for charts and metrics.

**Do not run `src/strategy.py` here** — the platform runs it after your changes.

## Layout

- **`src/strategy.py`** — entry point; all authored strategy and backtest logic lives here.
- **`src/utils.py`** — shared helpers (Alpaca, paths, JSON). Import only; never edit, replace, or delete.
- **No other `.py` files under `src/`.**

## `src/strategy.py` CLI

Ticker, timeframe, and dates are **not** separate flags; they come from `output/params.json`.

Implementing **`--backtest`**, **`--eda`**, and **`--hyperopt`** is optional; ship only what the task needs. **`--hyperopt`** must not be added until **`--backtest`** is implemented.

- **`--backtest`** — Read `output/params.json`, run the backtest, write `output/data.json` (stats + chart data), print performance to stdout.
- **`--eda`** — Read `output/params.json`, run **exploratory analysis** only (distributions, correlations, volatility, seasonality, liquidity, regimes, etc.) — **not** strategy rules or walk-forward performance. Write `output/data.json` with a `charts` array and `strategy_name` from params. Omit `metrics` unless you have meaningful backtest-style numbers. Print concise findings. Put analysis-only knobs in `output/params.json` with the same static-params rules as below.
- **`--help`** — `ArgumentParser(description=...)` — one short paragraph on strategy logic; leave empty or minimal until there is behavior to describe.

**Mode choice:** `--eda` for data analysis or market research; `--backtest` for entries/exits, sizing, or walk-forward results. **`--hyperopt`** only when the user asks to **train or optimize parameters**, **`--backtest`** already exists, and you are not doing that search inside **`--eda`**. After `update_strategy`, refresh with `python src/strategy.py --eda` or `--backtest` as appropriate.

**`--hyperopt`:** Only when requested and **`--backtest`** already exists. Optimize using ranges and training window from `output/params.json` (or equivalent in code), print results, write tuned params back to `output/params.json` for the next `--backtest`. Prefer Optuna or similar. **Hard cap: 120s** runtime.

## Data

- [Alpaca Market Data (Python)](https://alpaca.markets/sdks/python/market_data.html#market-data)
- Credentials: `ALPACA_API_KEY`, `ALPACA_SECRET_KEY`
- Drop bars where `(high - low) > 0.30 * high` (broken prints).

## `output/params.json`

Single source of truth: ticker, bar timeframe (e.g. Alpaca `1Day`, `1Hour`), backtest window (`start_test_date` / `end_test_date` or equivalent), every fixed numeric/boolean input, and hyperopt config when applicable (train window, search ranges, etc.). No duplicates in code — read this file, do not hardcode those constants.

Ship a valid file with sensible defaults so a fresh workspace runs. **Do not** use in-code defaults to paper over missing keys. **Do not** write or update this file from `src/strategy.py` — keep it static.

Always include **`strategy_name`**: human-readable, no ticker in the name.

## `output/data.json`

### Charts

Top-level **`charts`** array. The frontend renders each item with **lightweight-charts** (time series) or **Plotly.js** (everything else). **No JS chart code** — data only.

Each chart object:

- **`type`**: `"lightweight-charts"` or `"plotly"`
- **`title`** - has title

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

Series types: `Candlestick`, `Line`, `Area`, `Histogram`, `Baseline`, `Bar`. `options` → `addSeries`; `data` → `setData`; optional `markers` → `createSeriesMarkers`.

#### Plotly

Distributions, scatter, bars, tables, heatmaps, etc.

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

`data` / `layout`: [Plotly.js reference](https://plotly.com/javascript/reference/) — passed to `Plotly.newPlot`.

### Chart rules

- Shared time axis across lightweight-charts so scroll/zoom stays in sync.
- Equity curves: include buy-and-hold benchmark on the same chart.
- Signals: use `markers` on the right series.
- readable contrast; 
- every series/bar/line clearly labeled.

### Other top-level fields

- **`strategy_name`**: copy from params.
- **`metrics`** (`--backtest` only): e.g. `total_return`, `sharpe_ratio`, `max_drawdown`, `win_rate`, `num_trades`, `final_equity`, `initial_capital` — summary panel. Omit for `--eda` when you want that panel hidden.
