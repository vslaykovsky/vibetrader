# Trading strategy backend

This project is a trading strategy backend. It is used to backtest trading strategies and produce outputs needed to visualise relevant stats and charts.
Important: do not run src/strategy.py, it will be run by the user instead. 

## Strategy CLI script src/strategy.py

Source files must be stored under `src/`. Entry point is `src/strategy.py`. All strategy Python code must be stored in the single file `src/strategy.py` only. Do not produce additional `.py` files.

`src/strategy.py` must support these flags (no separate flags for ticker, timeframe, or dates):
- `--backtest` — Load `output/params.json`,  runs the backtest, write `output/data.json` with performance stats and all data necessary to render charts, prints performance stats to stdout.
- `--help` — help message

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

### output/charts.js

Produce all necessary charts using lightweight-charts 5.1: https://tradingview.github.io/lightweight-charts/docs 
Generate js code into output/charts.js with `render_charts(node_id, data)` function, where:
- node_id - js node ID to render charts into. 
- data - JSON parameter with contents of output/data.json

Notes:
- Produce output/charts.js statically, not dynamically from src/strategy.py!
- Don't use outdated functions like addCandlestickSeries. 
- When showing buy/sell signals, use createSeriesMarkers to add markers to a chart. 
- Don't render header with strategy name, only render charts with their titles. Generate chart titles in the language of user's prompt. 
- When showing equity curve, add benchmark curve as well. 
- All time-based charts must use the same time scale and the same logical time range on the x-axis (same min/max over the backtest window) so the frontend can keep scroll and zoom synchronized across charts.
- For non time-based charts (histograms, bar plots, scatter plots etc) use chart.js charts: https://www.chartjs.org/docs/latest/getting-started/
- output/charts.js is loaded as an ES module on the frontend. It MUST start with a named import from 'lightweight-charts', for example:
```
import { createChart } from 'lightweight-charts';
```
The frontend rewires this import at runtime. Do NOT use default imports, do NOT use `import *`, do NOT skip the import. Always use named imports like `import { createChart, LineSeries, CandlestickSeries, AreaSeries, HistogramSeries, BaselineSeries } from 'lightweight-charts';` (only import what you need). The function `render_charts` must be exported: `export function render_charts(container, data) { ... }`.

### output/summary.txt

Additionally update summary of the strategy in output/summary.txt This should be a concise single paragraph description of logic used by the strategy. Mention exact values of strategy parameters in the summary. If the user prompt is in non-english language, use the same language to produce the summary and strategy_name. 
Produce output/summary.txt statically, not dynamically from src/strateg.py!

### output/pseudocode.txt

Store high level concise pseudocode of the strategy into output/pseudocode.txt. No comments, only pseudocode itself. If the user prompt is in non-english language, use the same language to produce the pseudocode
Produce output/pseudocode.txt statically, not dynamically from src/strateg.py!