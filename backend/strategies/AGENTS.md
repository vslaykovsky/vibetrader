# Trading strategy backend

This project is a trading strategy backend. It is used to backtest trading strategies and produce outputs needed to visualise relevant stats and charts.
Important: do not run src/strategy.py, it will be run by the user instead!

## Building the scaffold

Source files must be stored under `src/`. Entry point is `src/strategy.py`.

### `output/params.json`

All constant parameters for the strategy must live in `output/params.json`. That includes everything needed to run the strategy and the backtest: for example ticker, candlestick/bar timeframe (e.g. Alpaca `1Day`, `1Hour`), backtest window (`start_test_date`, `end_test_date` or equivalent), every fixed numeric or boolean strategy input, and—when hyperopt is implemented—the hyperopt training window (`start_train_date`, `end_train_date` or equivalent) and any hyperopt configuration. There must be no duplicate sources of truth: the script reads this file instead of hardcoding those constants elsewhere. Ship or generate a valid `output/params.json` with sensible defaults so a fresh workspace can run.

### CLI

`src/strategy.py` must support only these flags (no separate flags for ticker, timeframe, or dates):

- `--backtest` — Load `output/params.json`, apply optional `--params` overrides for this run only, run the backtest, write `output/data.json` with stats and chart data, and print parameters and stats to stdout.

- `--hyperopt` — Run hyperparameter optimization (using ranges and training window defined in `output/params.json` or strategy code as appropriate). Print results to stdout and write the optimized parameters to `output/params.json` so a subsequent `python src/strategy.py --backtest` picks them up. 

- `--params JSON` — Optional; use together with `--backtest`. `JSON` is a single JSON object (quote the argument for the shell). Merge it over the object loaded from `output/params.json` for this run only; do not write merged values back into `output/params.json`. Top-level keys in `--params` override the same keys from the file. Invalid JSON must fail with a clear error.

## Data sources

Use Alpaca market data api: https://docs.alpaca.markets/docs/getting-started-with-alpaca-market-data
endpoint: https://paper-api.alpaca.markets/v2
key: PK2U3BR2HWAKQX2FTPMQUFK4IK
secret: BuoVH6LRX4NGpvyT5NZAEEd9wPjJ9YT7JW3KjTf4coV5

## Charts

Produce all necessary charts using lightweight-charts 5.1: https://tradingview.github.io/lightweight-charts/docs  

Use the following approach:
1. src/strategy.py --backtest must produce output/data.json with all input data required to build the chart. This data will be sent back to the web frontend. Apart from other keys it should include 'strategy_name' and 'ticker' keys. Do not run src/strategy.py, it will be run by the user. 
2. additionally generate JS code into output/charts.js with `render_charts(node_id, data)` function that uses lightweight-charts version 5.1 to render charts into `node_id` node using `data` parameter that contains json data from output/data.json. output/charts.js must be produced statically, not dynamically from src/strategy.py! Don't use outdated functions like addCandlestickSeries. 

- When showing buy/sell signals, use createSeriesMarkers to add markers to a chart. Don't render header with strategy name, only render charts with their titles.
- When showing equity curve, add benchmark as well. 
- IMPORTANT: output/charts.js is loaded as an ES module on the frontend. It MUST start with a named import from 'lightweight-charts', for example:
```
import { createChart } from 'lightweight-charts';
```
The frontend rewires this import at runtime. Do NOT use default imports, do NOT use `import *`, do NOT skip the import. Always use named imports like `import { createChart, LineSeries, CandlestickSeries, AreaSeries, HistogramSeries, BaselineSeries } from 'lightweight-charts';` (only import what you need). The function `render_charts` must be exported: `export function render_charts(container, data) { ... }`.


## Summary

- output/summary.txt Additionally update summary of the strategy in output/summary.txt This should be a concise single paragraph description of logic used by the strategy. Mention exact values of strategy parameters in the summary. If the user prompt is in non-english language, use the same language to produce the summary and strategy_name

- output/pseudocode.txt Store high level concise pseudocode of the strategy into output/pseudocode.txt. No comments, only pseudocode itself. If the user prompt is in non-english language, use the same language to produce the pseudocode