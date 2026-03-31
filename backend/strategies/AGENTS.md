# Trading strategy backend

This project is a trading strategy backend. It is used to backtest trading strategies and produce outputs needed to visualise relevant stats and charts.
Important: do not run src/strategy.py, it will be run by the user instead!

## Building the scaffold

Source files must be stored under src folder. 
Entry point is src/strategy.py 
This must support the following options:
--ticker TICKER
--candlestick-period TIMEFRAME  # optional; Alpaca bar timeframe (e.g. 1Day, 1Hour); default 1Day
--start-test-date DATE  # optional, by default 2y from today
--end-test-date DATE  # optional, by default today. 
--backtest  # this runs the backtest and produces output/data.json with stats and chart data. Additionally this should print out parameters and stats to stdout

Following parameters are optional and only need to be implemented if asked by the user:
--hyperopt  # this runs hyperopt optimization of strategy parameters. Parameters should be printed out to stdout and stored into output/params.json and automatically picked up by src/strategy.py 
--start-train-date # optional, by default 7 years from today. This is start date for --hypeopt data
--end-train-date # optional, by default 2 years from today. This is end date for --hypeopt data

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