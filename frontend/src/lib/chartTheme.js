import { LineStyle } from 'lightweight-charts';

/** Shared TradingView-style theme for LWC (strategy backtest + simulation). */
export const CHART_THEME = {
  layout: {
    background: { color: '#131722' },
    textColor: '#d1d4dc',
    attributionLogo: false,
  },
  grid: {
    vertLines: { color: '#1e2130' },
    horzLines: { color: '#1e2130' },
  },
  crosshair: {
    mode: 1,
    vertLine: { style: LineStyle.Dashed },
  },
  timeScale: {
    borderColor: '#363a45',
    timeVisible: true,
    /** Do not scroll the viewport when a new bar is appended (simulation / history merge). */
    shiftVisibleRangeOnNewBar: false,
  },
  rightPriceScale: { borderColor: '#363a45' },
};
