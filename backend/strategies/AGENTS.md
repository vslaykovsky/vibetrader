# Trading strategy backend

This project is a trading strategy backend. It is used to backtest trading strategies and produce outputs needed to visualise relevant stats and charts.
Important: do not run src/strategy.py, it will be run by the user instead. 

## Strategy CLI script src/strategy.py

Source files must be stored under `src/`. Entry point is `src/strategy.py`. Put all strategy and backtest logic you author in `src/strategy.py`. The file `src/utils.py` is read-only platform-managed shared helpers (Alpaca fetch, paths, JSON helpers); import from it when useful but never edit, replace, or delete it. Do not add other `.py` files under `src/`.

`src/strategy.py` must support these flags (no separate flags for ticker, timeframe, or dates):
- `--backtest` — Load `output/params.json`,  runs the backtest, write `output/data.json` with performance stats and all data necessary to render charts, prints performance stats to stdout.
- `--eda` — Load `output/params.json`, run **exploratory data analysis** the user asked for (distributions, correlations, volatility, seasonality, liquidity, regime splits, etc.). This path does **not** implement or evaluate a trading strategy. Write `output/data.json` with a `charts` array (and `strategy_name` from params). Omit `metrics` unless you have meaningful backtest-style numbers; the metrics panel is for strategy runs. Print concise findings to stdout. Put any analysis-only knobs (what to plot, horizons, cohorts) in `output/params.json` alongside shared fields (ticker, timeframe, dates), same static-params rules as below.
- `--help` — help message that describes the logic of the strategy in one short paragraph using ArgumentParser(description='...'). Leave empty if --backtest is not implemented yet!

Use **`--eda`** when the user wants **data analysis or market research**, not when they want rules for entries/exits, position sizing, or walk-forward performance—in those cases use **`--backtest`**. Use **`--hyperopt`** only when they ask to **train or optimize strategy parameters**; do not use `--eda` for optimization loops. `update_strategy` applies to all of these: implement the right mode for the user’s intent, then refresh outputs with `python src/strategy.py --eda` or `--backtest` as appropriate (not always `--backtest`).

Optional: implement `--hyperopt` only when the user asks to train or optimize parameters. It runs hyperparameter optimization (using ranges and training window defined in `output/params.json` or strategy code as appropriate), prints results to stdout, and writes optimized parameters to `output/params.json` so a subsequent `python src/strategy.py --backtest` picks them up. Use optuna or other appropriate tool. Set hardstop timeout of 120 seconds when running --hyperopt.  

## Data sources

Use Alpaca market data api. Use python API: https://alpaca.markets/sdks/python/market_data.html#market-data
Use environment variables to access Alpaca: ALPACA_API_KEY, ALPACA_SECRET_KEY
Make sure remove broken bars with high-low > 30% * high

## Special files

### output/params.json

All parameters for the strategy must live in `output/params.json`. That includes everything needed to run the strategy and the backtest: for example ticker, candlestick/bar timeframe (e.g. Alpaca `1Day`, `1Hour`), backtest window (`start_test_date`, `end_test_date` or equivalent), and every fixed numeric or boolean strategy input. When the user asks to train or optimize parameters, also store hyperopt fields there (e.g. training window `start_train_date`, `end_train_date` or equivalent, search ranges, and any other hyperopt configuration) and implement `--hyperopt` as above. There must be no duplicate sources of truth: the script reads this file instead of hardcoding those constants elsewhere. Generate a valid `output/params.json` with sensible defaults so a fresh workspace can run. Do not introduce "default values of parameter" in the code to substitue missing values. All values must be stored in output/params.json. Produce output/params.json statically, do not write/update it dynamically from src/strategy.py!

Special parameters to always include in output/params.json:
- strategy_name - name of the strategy that shouldn't include specific tickers. 

### output/data.json chart data

`output/data.json` must contain a top-level `"charts"` array. The frontend renders each element automatically using **lightweight-charts** (time-series) or **Plotly.js** (everything else). Do NOT generate any JS rendering code — the frontend handles it.

Each chart object in the `"charts"` array has:
- `"type"` — rendering library: `"lightweight-charts"` or `"plotly"`
- `"title"` — chart title string (use the language of the user's prompt)


#### lightweight-charts objects

Use for all time-indexed financial data: price charts with OHLC candles, equity curves, indicator overlays, etc.

```json
{
  "type": "lightweight-charts",
  "title": "Price Chart with SMA Crossover",
  "series": [
    {
      "type": "Candlestick",
      "options": {"upColor": "#26a69a", "downColor": "#ef5350"},
      "data": [{"time": "2024-01-02", "open": 100, "high": 105, "low": 99, "close": 103}, ...],
      "markers": [
        {"time": "2024-01-15", "position": "belowBar", "color": "#26a69a", "shape": "arrowUp", "text": "BUY"},
        {"time": "2024-02-10", "position": "aboveBar", "color": "#ef5350", "shape": "arrowDown", "text": "SELL"}
      ]
    },
    {
      "type": "Line",
      "options": {"color": "#f6c90e", "lineWidth": 2, "title": "SMA 20"},
      "data": [{"time": "2024-01-02", "value": 101.5}, ...]
    }
  ]
}
```

Supported series types: `Candlestick`, `Line`, `Area`, `Histogram`, `Baseline`, `Bar`.
- `options` — passed to `addSeries(SeriesType, options)`.
- `data` — array of data points passed to `series.setData(data)`.
- `markers` (optional) — array of marker objects passed to `createSeriesMarkers(series, markers)`.


#### plotly objects

Use for all non-time-series analytical data: distributions, scatter plots, bar charts, tables, heatmaps, etc.

```json
{
  "type": "plotly",
  "title": "PnL Distribution",
  "data": [
    {"type": "histogram", "x": [1.2, -0.5, 3.1, ...], "marker": {"color": "#26a69a"}}
  ],
  "layout": {"xaxis": {"title": "Return %"}, "yaxis": {"title": "Count"}}
}
```

`data` and `layout` follow the Plotly.js schema: https://plotly.com/javascript/reference/
The frontend passes them directly to `Plotly.newPlot(element, data, layout, config)`.

#### Rules
- All lightweight-charts charts share the same time axis range so the frontend can synchronize scroll/zoom.
- When showing an equity curve, include a benchmark (buy & hold) series on the same chart.
- When showing buy/sell signals, add `markers` to the appropriate series.
- Do NOT produce `output/charts.js`. All chart rendering is handled by the frontend.
- Generate chart titles in the language of the user's prompt.
- When picking text and background color make sure text is readable. 
- Make sure that for every component of the chart (bars/lines/etc) there is a clear text label indicating what it is. 


Additionally, `output/data.json` must include a top-level `"strategy_name"` field (copied from params). For **`--backtest`** output, include a `"metrics"` object with backtest performance stats (total_return, sharpe_ratio, max_drawdown, win_rate, num_trades, final_equity, initial_capital); the frontend renders it as a summary panel. For **`--eda`** output you may omit `"metrics"` so that panel stays hidden.
