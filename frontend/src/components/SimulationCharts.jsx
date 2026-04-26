import { useEffect, useMemo, useRef } from 'react';
import {
  MismatchDirection,
  createChart,
  CandlestickSeries,
  LineSeries,
  createSeriesMarkers,
} from 'lightweight-charts';
import { CHART_THEME } from '../lib/chartTheme.js';
import { coerceChartTimeSeconds, visibleTimeRangeToUnix } from '../lib/chartHistory.js';
import { attachSyncedCrosshair, attachSyncedTimeScales } from '../lib/lwcSync.js';

/** Empty fraction on the right when ``lockedVisibleRange`` is set (LWC cannot extend ``setVisibleRange`` past last bar). */
const PREVIEW_RIGHT_EMPTY_FRACTION = 0.3;
/** During live playback: auto-follow new bars only if empty space from last bar to viewport right is at least this fraction.
 *  Slightly under 30% so the auto-follow stays armed at exactly the initial layout (which is exactly 30%) — float noise
 *  in ``logicalToCoordinate`` would otherwise drop us below the threshold on the very first bar after Play. */
const LIVE_EDGE_FOLLOW_MIN_RIGHT_GAP = 0.28;
/** Fraction of the visible width left empty on the right when the chart is first populated (initial load / TF switch). */
const INITIAL_RIGHT_EMPTY_FRACTION = 0.3;

/**
 * Preview lock: when ``lockedVisibleRange`` is set, horizontal padding uses ``timeScale.rightOffset``
 * plus ``fitContent`` — LWC v5 ``setVisibleRange`` cannot extend past the last data point.
 *
 * @param {{
 *   candles: { time: number; open: number; high: number; low: number; close: number }[];
 *   equity: { time: number; value: number }[];
 *   markers: { time: number; position?: string; color?: string; shape?: string; text?: string }[];
 *   indicatorSeriesCatalog?: { name: string; description: string }[];
 *   lockedVisibleRange?: { from: number; to: number } | null;
 *   livePlayback?: boolean — sim running; enables auto-follow when the user is parked on the right edge.
 *   viewportCapped?: boolean;
 *   onVisibleTimeRangeChange?: (range: {
 *     from: number;
 *     to: number;
 *     barsBefore?: number;
 *     logicalFrom?: number;
 *     logicalTo?: number;
 *     leftEdgeUnix?: number | null — unix at viewport left edge (incl. empty margin), from LWC time scale.
 *     firstBarUnix?: number | null — unix open of bar at logical index 0 (oldest loaded); matches display_bars merge.
 *   } | null) => void;
 * }} props
 */
export function SimulationCharts({
  candles,
  equity,
  markers,
  chartTf = '1d',
  indicatorSeriesCatalog = [],
  lockedVisibleRange = null,
  livePlayback = false,
  viewportCapped = false,
  onVisibleTimeRangeChange = null,
}) {
  const priceHostRef = useRef(null);
  const equityHostRef = useRef(null);
  const chartsRef = useRef(null);
  const candleSeriesRef = useRef(null);
  const lineSeriesRef = useRef(null);
  const markersPluginRef = useRef(null);
  const prevLenRef = useRef(0);
  /** First candle time before last commit — detect prepended OHLC (older rows added before previous head). */
  const prevFirstBarTimeRef = useRef(null);
  const prevEqLenRef = useRef(0);
  const lastCandleSigRef = useRef('');
  const lastEqSigRef = useRef('');
  const prevLockedRangeRef = useRef(null);
  /** Frozen ``rightOffset`` (bar widths) for preview lock — must not grow with prepended history or the scale jumps. */
  const lockPreviewRightOffsetBarsRef = useRef(null);
  const onVisibleRef = useRef(onVisibleTimeRangeChange);
  onVisibleRef.current = onVisibleTimeRangeChange;
  const livePlaybackRef = useRef(false);
  livePlaybackRef.current = livePlayback;
  const lastCandleLenRef = useRef(0);

  const catalogHelpText = useMemo(() => {
    if (!indicatorSeriesCatalog.length) return '';
    return indicatorSeriesCatalog
      .map((r) => `${r.name}\n${typeof r.description === 'string' ? r.description : ''}`)
      .join('\n\n');
  }, [indicatorSeriesCatalog]);

  useEffect(() => {
    const topEl = priceHostRef.current;
    const botEl = equityHostRef.current;
    if (!topEl || !botEl) {
      return undefined;
    }

    const hPrice = topEl.clientHeight || 280;
    const hEq = botEl.clientHeight || 160;
    // Force a deterministic axis label per bar so intraday TFs always show the
    // bar's HH:MM (LWC's default sometimes collapses 4h sessions into a single
    // day-of-month label, which makes the chart look like it's missing bars).
    const tfKey = String(chartTf || '').trim().toLowerCase();
    const isIntraday = tfKey === '1m' || tfKey === '15m' || tfKey === '1h' || tfKey === '4h';
    const tickMarkFormatter = (timeSec) => {
      const u = typeof timeSec === 'number'
        ? timeSec
        : (timeSec && typeof timeSec === 'object' && 'timestamp' in timeSec)
          ? Number(timeSec.timestamp)
          : NaN;
      if (!Number.isFinite(u)) return '';
      const d = new Date(u * 1000);
      const dd = String(d.getUTCDate()).padStart(2, '0');
      const mon = d.toLocaleString('en-US', { month: 'short', timeZone: 'UTC' });
      const hh = String(d.getUTCHours()).padStart(2, '0');
      const mm = String(d.getUTCMinutes()).padStart(2, '0');
      if (!isIntraday) return `${dd} ${mon}`;
      // For 1m show HH:MM at midnight too so the date is implied by the hour.
      if (hh === '00' && mm === '00') return `${dd} ${mon}`;
      return `${hh}:${mm}`;
    };
    const chartPrice = createChart(topEl, {
      ...CHART_THEME,
      width: topEl.clientWidth,
      height: hPrice,
      timeScale: {
        ...(CHART_THEME.timeScale || {}),
        tickMarkFormatter,
      },
    });
    const chartEq = createChart(botEl, {
      ...CHART_THEME,
      width: botEl.clientWidth,
      height: hEq,
      timeScale: {
        ...(CHART_THEME.timeScale || {}),
        tickMarkFormatter,
      },
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

    const detachTs = attachSyncedTimeScales([chartPrice, chartEq], { mode: 'leader' });
    const detachXh = attachSyncedCrosshair([
      { chart: chartPrice, series: candleSeries },
      { chart: chartEq, series: lineSeries },
    ]);

    /** Max zoom-out: visible logical span must not exceed this many bar-widths (LWC logical range). */
    const emitViewport = () => {
      const cb = onVisibleRef.current;
      if (typeof cb !== 'function') return;
      const ts = chartPrice.timeScale();
      const lr = ts.getVisibleLogicalRange?.();
      if (!lr || !Number.isFinite(lr.from) || !Number.isFinite(lr.to)) {
        cb(null);
        return;
      }
      let barsBefore = 0;
      const bi = candleSeries.barsInLogicalRange?.(lr);
      if (bi && typeof bi.barsBefore === 'number' && Number.isFinite(bi.barsBefore)) {
        barsBefore = bi.barsBefore;
      }

      let firstBarUnix = null;
      if (typeof candleSeries.dataByIndex === 'function') {
        const b0 = candleSeries.dataByIndex(0, MismatchDirection.NearestRight);
        if (b0?.time != null) firstBarUnix = coerceChartTimeSeconds(b0.time);
      }

      let leftEdgeUnix = null;
      const xAtLogicalFrom =
        typeof ts.logicalToCoordinate === 'function' ? ts.logicalToCoordinate(lr.from) : null;
      if (
        xAtLogicalFrom != null &&
        Number.isFinite(xAtLogicalFrom) &&
        typeof ts.coordinateToTime === 'function'
      ) {
        const tRaw = ts.coordinateToTime(xAtLogicalFrom);
        if (tRaw != null) leftEdgeUnix = coerceChartTimeSeconds(tRaw);
      }
      if (leftEdgeUnix == null && firstBarUnix != null && typeof candleSeries.dataByIndex === 'function') {
        const b1 = candleSeries.dataByIndex(1, MismatchDirection.NearestRight);
        const u1 = b1?.time != null ? coerceChartTimeSeconds(b1.time) : null;
        const perBar = u1 != null && u1 > firstBarUnix ? u1 - firstBarUnix : 86400;
        leftEdgeUnix = Math.floor(firstBarUnix + lr.from * perBar);
      }

      const tr = ts.getVisibleRange?.();
      let unix = tr ? visibleTimeRangeToUnix(tr.from, tr.to) : null;
      if (!unix && typeof candleSeries.dataByIndex === 'function') {
        const lo = Math.floor(Math.min(lr.from, lr.to));
        const hi = Math.ceil(Math.max(lr.from, lr.to));
        const bLo = candleSeries.dataByIndex(lo, MismatchDirection.NearestRight);
        const bHi = candleSeries.dataByIndex(hi, MismatchDirection.NearestLeft);
        const tf = bLo?.time != null ? coerceChartTimeSeconds(bLo.time) : null;
        const tt = bHi?.time != null ? coerceChartTimeSeconds(bHi.time) : null;
        if (tf != null && tt != null) {
          unix = { from: Math.min(tf, tt), to: Math.max(tf, tt) };
        }
      }
      if (!unix && firstBarUnix != null && leftEdgeUnix != null) {
        unix = { from: Math.min(leftEdgeUnix, firstBarUnix), to: Math.max(leftEdgeUnix, firstBarUnix) };
      } else if (!unix && firstBarUnix != null) {
        unix = { from: firstBarUnix, to: firstBarUnix };
      }
      if (!unix) {
        cb(null);
        return;
      }
      cb({
        from: unix.from,
        to: unix.to,
        barsBefore,
        logicalFrom: lr.from,
        logicalTo: lr.to,
        leftEdgeUnix,
        firstBarUnix,
      });
    };

    /** Auto-follow active if the supplied bar (default: latest known) was on
     *  screen AND empty space to right edge ≥ 30% **before** the new bar was
     *  appended. Pass ``probeIdx`` to test the previous-tail position when
     *  called right after ``candleSeries.update``. */
    const isAutoFollowActive = (probeIdx) => {
      if (!livePlaybackRef.current) return false;
      const len = lastCandleLenRef.current;
      if (len < 1) return false;
      const ts = chartPrice.timeScale();
      const lr = ts.getVisibleLogicalRange?.();
      if (!lr || !Number.isFinite(lr.from) || !Number.isFinite(lr.to)) return false;
      const idx = Number.isFinite(probeIdx) ? probeIdx : len - 1;
      if (idx < 0) return false;
      // Allow the probe bar to sit slightly past the current right edge —
      // when called immediately after appending a new bar the viewport hasn't
      // yet scrolled and ``lr.to`` may equal ``idx - 1``.
      if (idx < lr.from - 1e-6) return false;
      const w = topEl.clientWidth;
      if (!(w > 0)) return false;
      let barRightX =
        typeof ts.logicalToCoordinate === 'function' ? ts.logicalToCoordinate(idx + 1) : null;
      if (barRightX == null || !Number.isFinite(barRightX)) {
        const xLast = ts.logicalToCoordinate?.(idx);
        const xPrev = idx > 0 ? ts.logicalToCoordinate?.(idx - 1) : null;
        if (xLast != null && xPrev != null && Number.isFinite(xLast) && Number.isFinite(xPrev)) {
          barRightX = xLast + Math.max(8, xLast - xPrev);
        } else {
          barRightX = xLast != null && Number.isFinite(xLast) ? xLast + 40 : w * 0.65;
        }
      }
      const gapFrac = Math.max(0, Math.min(1, (w - barRightX) / w));
      return gapFrac >= LIVE_EDGE_FOLLOW_MIN_RIGHT_GAP;
    };

    const onLogicalRangeChanged = () => {
      emitViewport();
    };
    chartPrice.timeScale().subscribeVisibleLogicalRangeChange(onLogicalRangeChanged);

    chartsRef.current = {
      chartPrice,
      chartEq,
      emitViewport,
      isAutoFollowActive,
      destroy() {
        chartPrice.timeScale().unsubscribeVisibleLogicalRangeChange(onLogicalRangeChanged);
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
    lastCandleLenRef.current = len;
    const sig =
      len > 0
        ? `${candles[len - 1].time}|${candles[len - 1].open}|${candles[len - 1].high}|${candles[len - 1].low}|${candles[len - 1].close}`
        : '';

    const prevLock = prevLockedRangeRef.current;
    const hadLocked =
      prevLock &&
      typeof prevLock.from === 'number' &&
      typeof prevLock.to === 'number' &&
      prevLock.to > prevLock.from;
    const hasLocked =
      lockedVisibleRange &&
      typeof lockedVisibleRange.from === 'number' &&
      typeof lockedVisibleRange.to === 'number' &&
      lockedVisibleRange.to > lockedVisibleRange.from;
    const unlocking = hadLocked && !hasLocked;
    prevLockedRangeRef.current = lockedVisibleRange;

    if (!hasLocked) {
      lockPreviewRightOffsetBarsRef.current = null;
    }

    const prevLen = prevLenRef.current;
    const prevHeadTime = prevFirstBarTimeRef.current;
    let prependedBars = 0;
    if (
      len > prevLen &&
      prevLen > 0 &&
      typeof prevHeadTime === 'number' &&
      Number.isFinite(prevHeadTime) &&
      typeof candles[0]?.time === 'number'
    ) {
      let i = 0;
      while (i < len && candles[i].time < prevHeadTime) {
        i += 1;
      }
      prependedBars = i;
    }
    /** Before ``setData`` when prepending history: same visible **time** window after update (no snap to latest bar). */
    let preservedVisibleTimeRange = null;

    const resetTimeScaleScrollOptions = () => {
      chartPrice.timeScale().applyOptions({
        rightOffset: 0,
        shiftVisibleRangeOnNewBar: false,
      });
      chartEq.timeScale().applyOptions({
        rightOffset: 0,
        shiftVisibleRangeOnNewBar: false,
      });
    };

    const applyShiftOffWithoutResizing = () => {
      chartPrice.timeScale().applyOptions({ shiftVisibleRangeOnNewBar: false });
      chartEq.timeScale().applyOptions({ shiftVisibleRangeOnNewBar: false });
    };

    const applyFreeFitBoth = () => {
      resetTimeScaleScrollOptions();
      chartPrice.timeScale().fitContent();
      chartEq.timeScale().fitContent();
    };

    /** Auto-follow: shift the current logical range right by ``n`` bars so the
     *  user-chosen "distance from last bar to right edge" is preserved as new
     *  bars open. ``setVisibleLogicalRange`` is the only LWC primitive that can
     *  position the viewport without changing zoom. */
    const shiftVisibleRangeRight = (n) => {
      if (!Number.isFinite(n) || n <= 0) return;
      try {
        const ts = chartPrice.timeScale();
        const lr = ts.getVisibleLogicalRange?.();
        if (!lr || !Number.isFinite(lr.from) || !Number.isFinite(lr.to)) return;
        const next = { from: lr.from + n, to: lr.to + n };
        ts.setVisibleLogicalRange(next);
        chartEq.timeScale().setVisibleLogicalRange(next);
      } catch {
        /* ignore */
      }
    };

    /** First populate: leave ~``INITIAL_RIGHT_EMPTY_FRACTION`` empty on the right. */
    const applyInitialRightPaddingFit = () => {
      const f = INITIAL_RIGHT_EMPTY_FRACTION;
      const rightBars = Math.max(1, Math.ceil((candles.length * f) / Math.max(0.05, 1 - f)));
      chartPrice.timeScale().applyOptions({
        rightOffset: rightBars,
        shiftVisibleRangeOnNewBar: false,
      });
      chartEq.timeScale().applyOptions({
        rightOffset: rightBars,
        shiftVisibleRangeOnNewBar: false,
      });
      chartPrice.timeScale().fitContent();
      chartEq.timeScale().fitContent();
    };

    if (unlocking) {
      lockPreviewRightOffsetBarsRef.current = null;
      const vrPrice = chartPrice.timeScale().getVisibleRange?.();
      const vrEq = chartEq.timeScale().getVisibleRange?.();
      resetTimeScaleScrollOptions();
      candleSeries.setData(candles);
      if (
        vrPrice &&
        vrPrice.from != null &&
        vrPrice.to != null &&
        vrEq &&
        vrEq.from != null &&
        vrEq.to != null
      ) {
        try {
          chartPrice.timeScale().setVisibleRange(vrPrice);
          chartEq.timeScale().setVisibleRange(vrEq);
        } catch {
          chartPrice.timeScale().fitContent();
          chartEq.timeScale().fitContent();
        }
      } else {
        chartPrice.timeScale().fitContent();
        chartEq.timeScale().fitContent();
      }
      prevLenRef.current = len;
      lastCandleSigRef.current = sig;
    } else if (len === 0) {
      candleSeries.setData([]);
      prevLenRef.current = 0;
      prevFirstBarTimeRef.current = null;
      lastCandleSigRef.current = '';
    } else if (viewportCapped) {
      candleSeries.setData(candles);
      if (!hasLocked) {
        applyShiftOffWithoutResizing();
      }
      prevLenRef.current = len;
      lastCandleSigRef.current = sig;
    } else if (prevLenRef.current === 0) {
      candleSeries.setData(candles);
      if (!hasLocked) {
        applyInitialRightPaddingFit();
      }
      prevLenRef.current = len;
      lastCandleSigRef.current = sig;
    } else if (len < prevLenRef.current) {
      candleSeries.setData(candles);
      if (!hasLocked) {
        applyShiftOffWithoutResizing();
      }
      prevLenRef.current = len;
      lastCandleSigRef.current = sig;
    } else if (len > prevLenRef.current) {
      const delta = len - prevLenRef.current;
      // Sample the auto-follow flag *before* mutating the series — we test
      // the previous tail bar against the current viewport.
      let followBeforeAppend = false;
      try {
        followBeforeAppend =
          prependedBars === 0 && livePlayback
            ? Boolean(
                chartsRef.current?.isAutoFollowActive?.(
                  prevLenRef.current - 1,
                ),
              )
            : false;
      } catch {
        followBeforeAppend = false;
      }
      if (delta > 1) {
        let preservedLogicalRange = null;
        if (prependedBars > 0) {
          const lr = chartPrice.timeScale().getVisibleLogicalRange?.();
          if (lr && Number.isFinite(lr.from) && Number.isFinite(lr.to)) {
            preservedLogicalRange = {
              from: lr.from + prependedBars,
              to: lr.to + prependedBars,
            };
          }
          const vr = chartPrice.timeScale().getVisibleRange?.();
          if (vr && vr.from != null && vr.to != null) {
            preservedVisibleTimeRange = { from: vr.from, to: vr.to };
          }
        }
        try {
          candleSeries.setData(candles);
        } catch {
          /* ignore */
        }
        if (!hasLocked) {
          applyShiftOffWithoutResizing();
        }
        if (preservedLogicalRange != null) {
          try {
            chartPrice.timeScale().setVisibleLogicalRange(preservedLogicalRange);
            chartEq.timeScale().setVisibleLogicalRange(preservedLogicalRange);
            preservedVisibleTimeRange = null;
          } catch {
            /* fall back to time-range restore below */
          }
        }
        if (followBeforeAppend) {
          requestAnimationFrame(() => {
            shiftVisibleRangeRight(delta);
          });
        }
      } else {
        try {
          candleSeries.update(candles[len - 1]);
        } catch {
          // fall back to setData if update rejects (out-of-order time, etc).
          try {
            candleSeries.setData(candles);
          } catch {
            /* ignore */
          }
        }
        if (followBeforeAppend) {
          requestAnimationFrame(() => {
            shiftVisibleRangeRight(1);
          });
        }
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
    } else if (viewportCapped) {
      lineSeries.setData(equity);
      prevEqLenRef.current = eqLen;
      lastEqSigRef.current = eqSig;
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

    if (hasLocked) {
      /** ``fitContent`` / right padding only when preview lock window changes — not on prepend (would reset pan/zoom). */
      const lockRangeChanged =
        lockedVisibleRange &&
        (!hadLocked ||
          prevLock.from !== lockedVisibleRange.from ||
          prevLock.to !== lockedVisibleRange.to);
      if (lockRangeChanged || lockPreviewRightOffsetBarsRef.current == null) {
        const n0 = Math.max(1, candles.length);
        lockPreviewRightOffsetBarsRef.current = Math.max(
          1,
          Math.ceil((n0 * PREVIEW_RIGHT_EMPTY_FRACTION) / (1 - PREVIEW_RIGHT_EMPTY_FRACTION)),
        );
        const rightBars = lockPreviewRightOffsetBarsRef.current;
        chartPrice.timeScale().applyOptions({
          rightOffset: rightBars,
          shiftVisibleRangeOnNewBar: false,
        });
        chartEq.timeScale().applyOptions({
          rightOffset: rightBars,
          shiftVisibleRangeOnNewBar: false,
        });
      }
      if (candles.length > 0 && lockRangeChanged) {
        chartPrice.timeScale().fitContent();
        chartEq.timeScale().fitContent();
      }
    }

    if (preservedVisibleTimeRange != null && !hasLocked) {
      try {
        chartPrice.timeScale().setVisibleRange(preservedVisibleTimeRange);
        chartEq.timeScale().setVisibleRange(preservedVisibleTimeRange);
      } catch {
        /* range may be invalid for the new series — ignore */
      }
    }

    if (len > 0 && typeof candles[0]?.time === 'number') {
      prevFirstBarTimeRef.current = candles[0].time;
    } else {
      prevFirstBarTimeRef.current = null;
    }

    if (len > 0 && typeof onVisibleTimeRangeChange === 'function' && chartsRef.current?.emitViewport) {
      requestAnimationFrame(() => {
        chartsRef.current?.emitViewport?.();
      });
    }
  }, [candles, equity, markers, lockedVisibleRange, livePlayback, viewportCapped, onVisibleTimeRangeChange]);

  return (
    <div className="simulation-charts-stack" aria-label="Simulation price and equity charts">
      <div className="simulation-chart-pane">
        <div className="simulation-chart-pane-caption">
          <span className="simulation-chart-pane-caption-text">Price</span>
          {catalogHelpText ? (
            <span className="strategy-chart-help-wrap">
              <button
                type="button"
                className="strategy-chart-help-btn"
                aria-label="Output series descriptions"
                onMouseDown={(e) => {
                  e.preventDefault();
                  e.stopPropagation();
                }}
                onClick={(e) => {
                  e.preventDefault();
                  e.stopPropagation();
                }}
              >
                ?
              </button>
              <div className="strategy-chart-help-tooltip" role="tooltip">
                {catalogHelpText}
              </div>
            </span>
          ) : null}
        </div>
        <div className="simulation-chart-host simulation-chart-host--price" ref={priceHostRef} />
      </div>
      <div className="simulation-chart-pane">
        <div className="simulation-chart-pane-caption">
          <span className="simulation-chart-pane-caption-text">Equity</span>
        </div>
        <div className="simulation-chart-host simulation-chart-host--equity" ref={equityHostRef} />
      </div>
    </div>
  );
}
