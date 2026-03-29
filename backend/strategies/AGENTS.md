# Trading strategy backend

This project is a trading strategy backend. It is used to backtest trading strategies and produce outputs needed to visualise relevant stats and charts.

## Building the scaffold

Source files must be stored under src folder. 
Entry point is src/strategy.py 
This must support the following options:
--ticker TICKER
--backtest  # this runs the backtest and produces output/data.json with stats and chart data. 


## Data sources

Use Alpaca market data api: https://docs.alpaca.markets/docs/getting-started-with-alpaca-market-data
endpoint: https://paper-api.alpaca.markets/v2
key: PK2U3BR2HWAKQX2FTPMQUFK4IK
secret: BuoVH6LRX4NGpvyT5NZAEEd9wPjJ9YT7JW3KjTf4coV5

## Charts

Produce all necessary charts using lightweight-charts library: https://tradingview.github.io/lightweight-charts/docs 

Use the following approach:
1. src/strategy.py --backtest must produce output/data.json with all input data required to build the chart. This data will be sent back to the web frontend. 
2. additionally generate JS code into output/charts.js with `render_charts(node_id, data)` function that uses lightweight-charts to render charts into `node_id` node using `data` parameter that contains json data from output/data.json. output/charts.js must be produced statically, not dynamically from src/strategy.py!


## Summary

Additionally update summary of the strategy in output/summary.txt
This should be a concise single paragraph description of logic used by the strategy. 
Mention exact values of strategy parameters in the summary. 

Store high level concise pseudocode of the strategy into output/pseudocode.txt. No comments, only pseudocode itself