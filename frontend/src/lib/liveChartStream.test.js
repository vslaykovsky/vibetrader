import { describe, expect, it } from 'vitest';
import {
  applyLiveStreamEvent,
  createLiveChartState,
  liveChartsDataJson,
  liveTrades,
} from './liveChartStream.js';

describe('applyLiveStreamEvent', () => {
  it('builds chart data from snapshots and patches', () => {
    const state = createLiveChartState();
    const snapshotResult = applyLiveStreamEvent(state, {
      kind: 'snapshot',
      seq: 2,
      run_id: 'run-1',
      unixtime: 1000,
      data: {
        last_seq: 2,
        series: [
          {
            chart_id: 'ohlcv',
            series_id: 'ohlcv:price',
            source: 'ohlcv',
            label: 'SPY',
            ticker: 'SPY',
            scale: '1m',
          },
          {
            chart_id: 'output_indicators',
            series_id: 'output:edge',
            source: 'output_indicator',
            label: 'output:edge',
            name: 'edge',
            description: 'model edge',
          },
        ],
        bars: [
          {
            chart_id: 'ohlcv',
            series_id: 'ohlcv:price',
            label: 'SPY',
            ticker: 'SPY',
            time: 1000,
            open: 10,
            high: 12,
            low: 9,
            close: 11,
            volume: 100,
            closed: true,
          },
        ],
        indicators: [
          {
            chart_id: 'output_indicators',
            series_id: 'output:edge',
            source: 'output',
            label: 'output:edge',
            name: 'edge',
            time: 1000,
            value: 0.4,
            description: 'model edge',
          },
        ],
        positions: [
          {
            chart_id: 'positions',
            time: 1000,
            equity: 12000,
            positions: [
              {
                ticker: 'SPY',
                order_type: 'long',
                deposit_ratio: 0.25,
                value: 3000,
              },
            ],
          },
        ],
        trades: [],
        annotations: [
          {
            time: 1050,
            kind: 'live_start',
            label: 'Live trading starts',
          },
        ],
        status: { status: 'running' },
      },
    });
    const barResult = applyLiveStreamEvent(state, {
      kind: 'bar',
      seq: 3,
      run_id: 'run-1',
      unixtime: 1060,
      data: {
        chart_id: 'ohlcv',
        series_id: 'ohlcv:price',
        label: 'SPY',
        ticker: 'SPY',
        time: 1060,
        open: 11,
        high: 13,
        low: 10,
        close: 12,
        volume: 120,
      },
    });
    const tradeResult = applyLiveStreamEvent(state, {
      kind: 'trade',
      seq: 4,
      run_id: 'run-1',
      unixtime: 1060,
      data: {
        time: 1060,
        ticker: 'SPY',
        direction: 'buy',
        value_usd: null,
        deposit_ratio: 0.5,
        client_order_id: 'client-1',
        status: 'submitted',
        comment: 'crossed above EMA',
      },
    });
    const orderUpdateResult = applyLiveStreamEvent(state, {
      kind: 'trade',
      seq: 5,
      run_id: 'run-1',
      unixtime: 1061,
      data: {
        time: 1061,
        ticker: 'SPY',
        direction: 'buy',
        price: 12.25,
        qty: 3,
        alpaca_order_id: 'alpaca-1',
        client_order_id: 'client-1',
        status: 'filled',
        comment: 'Alpaca fill',
      },
    });

    expect(snapshotResult).toEqual({ changed: true, tradesChanged: true, statusChanged: true });
    expect(barResult).toEqual({ changed: true, tradesChanged: false, statusChanged: false });
    expect(tradeResult).toEqual({ changed: true, tradesChanged: true, statusChanged: false });
    expect(orderUpdateResult).toEqual({ changed: true, tradesChanged: true, statusChanged: false });
    expect(liveTrades(state)).toEqual([
      {
        rowKey: 4,
        time: 1060,
        unixtime: 1060,
        ticker: 'SPY',
        direction: 'buy',
        action: '',
        label: '',
        price: 12.25,
        qty: 3,
        value_usd: 36.75,
        deposit_ratio: 0.5,
        position_before_order: null,
        position_after_order_filled: null,
        alpaca_order_id: 'alpaca-1',
        client_order_id: 'client-1',
        status: 'filled',
        comment: 'crossed above EMA; Alpaca fill',
      },
    ]);
    expect(liveChartsDataJson(state)).toEqual({
      indicator_series_catalog: [{ name: 'edge', description: 'model edge' }],
      charts: [
        {
          type: 'lightweight-charts',
          title: 'Live Price',
          series: [
            {
              type: 'Candlestick',
              label: 'SPY',
              data: [
                { time: 1000, open: 10, high: 12, low: 9, close: 11 },
                { time: 1050 },
                { time: 1060, open: 11, high: 13, low: 10, close: 12 },
              ],
              markers: [
                {
                  time: 1060,
                  position: 'belowBar',
                  color: '#26a69a',
                  shape: 'arrowUp',
                  text: 'buy filled',
                },
              ],
            },
          ],
          verticalMarkers: [
            {
              time: 1050,
              label: 'Live trading starts',
              kind: 'live_start',
              color: '#f59e0b',
            },
          ],
        },
        {
          type: 'lightweight-charts',
          title: 'Equity curve',
          series: [
            {
              type: 'Line',
              label: 'Strategy equity',
              options: { color: '#2962ff', lineWidth: 2 },
              data: [{ time: 1000, value: 12000 }, { time: 1050 }],
              markers: [
                {
                  time: 1060,
                  position: 'belowBar',
                  color: '#26a69a',
                  shape: 'arrowUp',
                  text: 'buy filled',
                },
              ],
            },
          ],
          verticalMarkers: [
            {
              time: 1050,
              label: 'Live trading starts',
              kind: 'live_start',
              color: '#f59e0b',
            },
          ],
        },
        {
          type: 'lightweight-charts',
          title: 'Current position value',
          series: [
            {
              type: 'Line',
              label: 'SPY position value',
              data: [{ time: 1000, value: 3000 }, { time: 1050 }],
            },
          ],
          verticalMarkers: [
            {
              time: 1050,
              label: 'Live trading starts',
              kind: 'live_start',
              color: '#f59e0b',
            },
          ],
        },
        {
          type: 'lightweight-charts',
          title: 'Live Output Indicators',
          series: [
            {
              type: 'Line',
              label: 'output:edge',
              data: [{ time: 1000, value: 0.4 }, { time: 1050 }],
            },
          ],
          verticalMarkers: [
            {
              time: 1050,
              label: 'Live trading starts',
              kind: 'live_start',
              color: '#f59e0b',
            },
          ],
        },
      ],
    });
  });
});
