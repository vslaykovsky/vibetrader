import { useEffect, useRef } from 'react';
import { createChart, CandlestickSeries, LineSeries, createSeriesMarkers } from 'lightweight-charts';
import { CHART_THEME } from '../lib/chartTheme.js';
import { attachSyncedCrosshair, attachSyncedTimeScales } from '../lib/lwcSync.js';

/**
 * @param {{
 *   candles: { time: number; open: number; high: number; low: number; close: number }[];
 *   equity: { time: number; value: number }[];
 *   markers: { time: number; position?: string; color?: string; shape?: string; text?: string }[];
 * }} props
 */
export function SimulationCharts({ candles, equity, markers }) {
  const priceHostRef = useRef(null);
  const equityHostRef = useRef(null);
  const chartsRef = useRef(null);
  const candleSeriesRef = useRef(null);
  const lineSeriesRef = useRef(null);
  const markersPluginRef = useRef(null);
  const prevLenRef = useRef(0);
  const prevEqLenRef = useRef(0);
  const lastCandleSigRef = useRef('');
  const lastEqSigRef = useRef('');

  useEffect(() => {
    const topEl = priceHostRef.current;
    const botEl = equityHostRef.current;
    if (!topEl || !botEl) {
      return undefined;
    }

    const hPrice = topEl.clientHeight || 280;
    const hEq = botEl.clientHeight || 160;
    const chartPrice = createChart(topEl, {
      ...CHART_THEME,
      width: topEl.clientWidth,
      height: hPrice,
    });
    const chartEq = createChart(botEl, {
      ...CHART_THEME,
      width: botEl.clientWidth,
      height: hEq,
    });

    const candleSeries = chartPrice.addSeries(CandlestickSeries, {
      upColor: '#26a69a',
      downColor: '#ef5350',
      borderVisible: false,
      wickUpColor: '#26a69a',
      wickDownColor: '#ef5350',
    });
    const lineSeries = chartEq.addSeries(LineSeries, {
      color: '#2962ff',
      lineWidth: 2,
      priceLineVisible: false,
    });

    markersPluginRef.current = createSeriesMarkers(candleSeries, []);

    const detachTs = attachSyncedTimeScales([chartPrice, chartEq]);
    const detachXh = attachSyncedCrosshair([
      { chart: chartPrice, series: candleSeries },
      { chart: chartEq, series: lineSeries },
    ]);

    chartsRef.current = {
      chartPrice,
      chartEq,
      destroy() {
        detachTs?.();
        detachXh?.();
        chartPrice.remove();
        chartEq.remove();
      },
    };
    candleSeriesRef.current = candleSeries;
    lineSeriesRef.current = lineSeries;
    prevLenRef.current = 0;
    prevEqLenRef.current = 0;
    lastCandleSigRef.current = '';
    lastEqSigRef.current = '';

    const ro = new ResizeObserver(() => {
      chartPrice.resize(topEl.clientWidth, topEl.clientHeight || 280);
      chartEq.resize(botEl.clientWidth, botEl.clientHeight || 160);
    });
    ro.observe(topEl);
    ro.observe(botEl);

    return () => {
      ro.disconnect();
      chartsRef.current?.destroy();
      chartsRef.current = null;
      candleSeriesRef.current = null;
      lineSeriesRef.current = null;
      markersPluginRef.current = null;
    };
  }, []);

  useEffect(() => {
    const candleSeries = candleSeriesRef.current;
    const lineSeries = lineSeriesRef.current;
    const chartPrice = chartsRef.current?.chartPrice;
    const chartEq = chartsRef.current?.chartEq;
    if (!candleSeries || !lineSeries || !chartPrice || !chartEq) {
      return;
    }

    const len = candles.length;
    const sig =
      len > 0
        ? `${candles[len - 1].time}|${candles[len - 1].open}|${candles[len - 1].high}|${candles[len - 1].low}|${candles[len - 1].close}`
        : '';

    if (len === 0) {
      candleSeries.setData([]);
      prevLenRef.current = 0;
      lastCandleSigRef.current = '';
    } else if (len < prevLenRef.current || prevLenRef.current === 0) {
      candleSeries.setData(candles);
      chartPrice.timeScale().fitContent();
      prevLenRef.current = len;
      lastCandleSigRef.current = sig;
    } else if (len > prevLenRef.current) {
      if (len - prevLenRef.current > 1) {
        candleSeries.setData(candles);
        chartPrice.timeScale().fitContent();
      } else {
        candleSeries.update(candles[len - 1]);
      }
      prevLenRef.current = len;
      lastCandleSigRef.current = sig;
    } else if (sig !== lastCandleSigRef.current) {
      candleSeries.update(candles[len - 1]);
      lastCandleSigRef.current = sig;
    }

    const eqLen = equity.length;
    const eqSig = eqLen > 0 ? `${equity[eqLen - 1].time}|${equity[eqLen - 1].value}` : '';

    if (eqLen === 0) {
      lineSeries.setData([]);
      prevEqLenRef.current = 0;
      lastEqSigRef.current = '';
    } else if (eqLen < prevEqLenRef.current || prevEqLenRef.current === 0) {
      lineSeries.setData(equity);
      prevEqLenRef.current = eqLen;
      lastEqSigRef.current = eqSig;
    } else if (eqLen > prevEqLenRef.current) {
      if (eqLen - prevEqLenRef.current > 1) {
        lineSeries.setData(equity);
      } else {
        lineSeries.update(equity[eqLen - 1]);
      }
      prevEqLenRef.current = eqLen;
      lastEqSigRef.current = eqSig;
    } else if (eqSig !== lastEqSigRef.current) {
      lineSeries.update(equity[eqLen - 1]);
      lastEqSigRef.current = eqSig;
    }

    const sortedMarkers =
      Array.isArray(markers) && markers.length > 0
        ? [...markers].sort((a, b) => (a.time < b.time ? -1 : a.time > b.time ? 1 : 0))
        : [];
    markersPluginRef.current?.setMarkers(sortedMarkers);
  }, [candles, equity, markers]);

  return (
    <div className="simulation-charts-stack" aria-label="Simulation price and equity charts">
      <div className="simulation-chart-host simulation-chart-host--price" ref={priceHostRef} />
      <div className="simulation-chart-host simulation-chart-host--equity" ref={equityHostRef} />
    </div>
  );
}
