import { useCallback, useEffect, useMemo, useRef, useState } from 'react';
import { SimulationCharts } from './SimulationCharts.jsx';
import {
  bucketStart,
  coarserChartTimeframes,
  equityOnCandleCloseTimes,
  finerChartTimeframes,
  normalizeTimeframe,
  resampleEquity,
  resampleOhlc,
  timeframeRank,
} from '../lib/ohlcResample.js';

const MAX_SIM_BARS = 50_000;
const MAX_SIM_TRADES = 5_000;
const MAX_LOG_LINES = 400;
/** Max finer candles to release in one frame (very high bps / Max preset). */
const MAX_FINER_BARS_PER_FRAME = 800;

/** Chart follows streamed bars one-to-one (strategy native TF). */
const DISPLAY_TF_NATIVE = 'native';

const SPEED_OPTIONS = [
  { id: '0.5', bps: 0.5, label: '0.5x' },
  { id: '1', bps: 1, label: '1x' },
  { id: '2', bps: 2, label: '2x' },
  { id: '4', bps: 4, label: '4x' },
  { id: '16', bps: 16, label: '16x' },
  { id: 'max', bps: 1_000_000, label: 'Max' },
];

function presetIdFromBps(bps) {
  if (!Number.isFinite(bps)) return '2';
  if (bps >= 999_999) return 'max';
  const hit = SPEED_OPTIONS.find((o) => o.id !== 'max' && Math.abs(o.bps - bps) < 0.001);
  return hit ? hit.id : '2';
}

function defaultEndDateKey() {
  const d = new Date();
  const yyyy = d.getFullYear();
  const mm = String(d.getMonth() + 1).padStart(2, '0');
  const dd = String(d.getDate()).padStart(2, '0');
  return `${yyyy}-${mm}-${dd}`;
}

function defaultStartDateKey() {
  const d = new Date();
  d.setUTCDate(d.getUTCDate() - 30);
  const yyyy = d.getUTCFullYear();
  const mm = String(d.getUTCMonth() + 1).padStart(2, '0');
  const dd = String(d.getUTCDate()).padStart(2, '0');
  return `${yyyy}-${mm}-${dd}`;
}

export function SimulationPanel({ threadId, apiBaseUrl, authFetch, getAccessToken }) {
  const [startDate, setStartDate] = useState(defaultStartDateKey);
  const [endDate, setEndDate] = useState(defaultEndDateKey);
  const [speedPresetId, setSpeedPresetId] = useState('2');
  /** Bars per second for the *visible* chart TF when finer than strategy (see rAF playback). */
  const [liveBps, setLiveBps] = useState(2);
  const [displayTfMode, setDisplayTfMode] = useState(DISPLAY_TF_NATIVE);
  const [logLines, setLogLines] = useState([]);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState('');
  const [sourceScale, setSourceScale] = useState('');
  const [rawBars, setRawBars] = useState([]);
  const [rawTrades, setRawTrades] = useState([]);
  const [rawEquity, setRawEquity] = useState([]);
  const [fineBars, setFineBars] = useState([]);
  const [fineBarsLoading, setFineBarsLoading] = useState(false);
  const [fineBarsError, setFineBarsError] = useState('');
  /** True while we paused the backend to load finer OHLC (user switched chart TF during a run). */
  const [chartLoadHoldsSim, setChartLoadHoldsSim] = useState(false);
  /** Backend simulation clock paused (user Pause / chart load); finer-chart rAF playback freezes with it. */
  const [simWallClockPaused, setSimWallClockPaused] = useState(false);
  /** How many finer TF candles (from the head of finePool) are shown; paced by liveBps. */
  const [fineRevealCount, setFineRevealCount] = useState(0);
  /** Chart TF dropdown: show overlay so the UI does not look frozen (finer fetch or resample). */
  const [chartTfSwitching, setChartTfSwitching] = useState(false);
  const esRef = useRef(null);
  const logEndRef = useRef(null);
  const busyRef = useRef(false);
  busyRef.current = busy;
  const liveBpsRef = useRef(liveBps);
  liveBpsRef.current = liveBps;
  const fineBarsRef = useRef(fineBars);
  fineBarsRef.current = fineBars;
  const cursorUnixRef = useRef(0);
  const playbackAccumRef = useRef(0);
  const prevDisplayTfRef = useRef(displayTfMode);
  const prevFineBarsLoadingRef = useRef(false);

  const appendLog = useCallback((line) => {
    setLogLines((prev) => [...prev.slice(-MAX_LOG_LINES), line]);
  }, []);

  const stopStream = useCallback(() => {
    esRef.current?.close();
    esRef.current = null;
  }, []);

  useEffect(() => () => stopStream(), [stopStream]);

  useEffect(() => {
    logEndRef.current?.scrollIntoView({ behavior: 'smooth' });
  }, [logLines]);

  const knownSource = normalizeTimeframe(sourceScale);
  const sourceRankFallback = '1d';
  const rankBase = knownSource || sourceRankFallback;

  const chartTf =
    displayTfMode === DISPLAY_TF_NATIVE
      ? knownSource || sourceRankFallback
      : normalizeTimeframe(displayTfMode) || knownSource || sourceRankFallback;

  const srcRank = timeframeRank(knownSource);
  const chartRank = timeframeRank(chartTf);

  const isNativeChart =
    displayTfMode === DISPLAY_TF_NATIVE ||
    (knownSource !== '' && normalizeTimeframe(chartTf) === knownSource);

  const isFinerChart =
    !isNativeChart &&
    knownSource !== '' &&
    srcRank != null &&
    chartRank != null &&
    chartRank < srcRank;

  const isCoarserChart = !isNativeChart && !isFinerChart;

  const chartTfOptions = useMemo(() => {
    const nativeLabel = knownSource
      ? `Native (${knownSource}) — same as strategy`
      : 'Native (strategy timeframe)';
    const finer = finerChartTimeframes(rankBase);
    const coarse = coarserChartTimeframes(rankBase);
    return [
      { value: DISPLAY_TF_NATIVE, label: nativeLabel },
      ...finer.map((tf) => ({ value: tf, label: `View ${tf} (market OHLC)` })),
      ...coarse.map((tf) => ({ value: tf, label: `Aggregate → ${tf}` })),
    ];
  }, [knownSource, rankBase]);

  useEffect(() => {
    const valid = new Set(chartTfOptions.map((o) => o.value));
    if (!valid.has(displayTfMode)) {
      setDisplayTfMode(DISPLAY_TF_NATIVE);
    }
  }, [chartTfOptions, displayTfMode]);

  useEffect(() => {
    const prev = prevDisplayTfRef.current;
    if (prev !== displayTfMode) {
      prevDisplayTfRef.current = displayTfMode;
      setChartTfSwitching(true);
    }
  }, [displayTfMode]);

  useEffect(() => {
    if (!chartTfSwitching) return undefined;
    if (isFinerChart) {
      if (!fineBarsLoading) {
        setChartTfSwitching(false);
      }
      return undefined;
    }
    let cancelled = false;
    let id2 = 0;
    const id1 = requestAnimationFrame(() => {
      id2 = requestAnimationFrame(() => {
        if (!cancelled) setChartTfSwitching(false);
      });
    });
    return () => {
      cancelled = true;
      cancelAnimationFrame(id1);
      cancelAnimationFrame(id2);
    };
  }, [chartTfSwitching, isFinerChart, fineBarsLoading, displayTfMode]);

  useEffect(() => {
    if (!isFinerChart || !chartTf || !startDate || !endDate || !String(threadId || '').trim()) {
      setFineBars([]);
      setFineBarsError('');
      setFineBarsLoading(false);
      setChartLoadHoldsSim(false);
      return;
    }
    const ac = new AbortController();
    let alive = true;
    const tid = String(threadId).trim();
    const qs = new URLSearchParams({
      thread_id: tid,
      scale: chartTf,
      start_date: startDate,
      end_date: endDate,
    });
    let pausedForThisLoad = false;

    void (async () => {
      setFineBarsLoading(true);
      setFineBarsError('');
      try {
        if (busyRef.current) {
          setChartLoadHoldsSim(true);
          await authFetch(`${apiBaseUrl}/simulation/pause`, {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ thread_id: tid }),
          });
          if (!alive) {
            await authFetch(`${apiBaseUrl}/simulation/resume`, {
              method: 'POST',
              headers: { 'Content-Type': 'application/json' },
              body: JSON.stringify({ thread_id: tid }),
            });
            return;
          }
          pausedForThisLoad = true;
        }
        const res = await authFetch(`${apiBaseUrl}/simulation/display_bars?${qs}`, {
          method: 'GET',
          signal: ac.signal,
        });
        const data = await res.json().catch(() => ({}));
        if (!res.ok) {
          throw new Error(typeof data.error === 'string' ? data.error : 'Failed to load chart OHLC');
        }
        const bars = Array.isArray(data.bars) ? data.bars : [];
        if (alive) {
          setFineBars(bars);
        }
      } catch (err) {
        if (ac.signal.aborted || (err instanceof DOMException && err.name === 'AbortError')) {
          /* cancelled */
        } else if (alive) {
          setFineBars([]);
          setFineBarsError(err instanceof Error ? err.message : String(err));
        }
      } finally {
        if (pausedForThisLoad) {
          try {
            await authFetch(`${apiBaseUrl}/simulation/resume`, {
              method: 'POST',
              headers: { 'Content-Type': 'application/json' },
              body: JSON.stringify({ thread_id: tid }),
            });
          } catch {
            /* ignore */
          }
        }
        if (alive) {
          setFineBarsLoading(false);
          setChartLoadHoldsSim(false);
        }
      }
    })();

    return () => {
      alive = false;
      ac.abort();
    };
  }, [isFinerChart, chartTf, startDate, endDate, threadId, apiBaseUrl, authFetch]);

  const cursorUnix = useMemo(() => {
    if (!rawBars.length) return 0;
    return Math.max(...rawBars.map((b) => b.unixtime));
  }, [rawBars]);
  cursorUnixRef.current = cursorUnix;

  const finePool = useMemo(() => {
    if (!isFinerChart || !fineBars.length || cursorUnix <= 0) return [];
    return fineBars
      .filter((b) => b.unixtime <= cursorUnix)
      .sort((a, b) => a.unixtime - b.unixtime);
  }, [isFinerChart, fineBars, cursorUnix]);

  /** Reset finer playback only when the sim date range changes — not when switching chart TF. */
  useEffect(() => {
    setFineRevealCount(0);
    playbackAccumRef.current = 0;
  }, [startDate, endDate]);

  useEffect(() => {
    setFineRevealCount((c) => Math.min(c, finePool.length));
  }, [finePool.length]);

  /** After OHLC load for a finer TF (incl. switching 4h→1h mid-run), show all candles through current cursor. */
  useEffect(() => {
    if (!isFinerChart) {
      prevFineBarsLoadingRef.current = false;
      return;
    }
    const wasLoading = prevFineBarsLoadingRef.current;
    prevFineBarsLoadingRef.current = fineBarsLoading;
    if (wasLoading && !fineBarsLoading) {
      const pool = fineBarsRef.current
        .filter((b) => b.unixtime <= cursorUnixRef.current)
        .sort((a, b) => a.unixtime - b.unixtime);
      setFineRevealCount(pool.length);
      playbackAccumRef.current = 0;
    }
  }, [isFinerChart, fineBarsLoading]);

  useEffect(() => {
    if (
      !isFinerChart ||
      fineBarsLoading ||
      !fineBars.length ||
      !busy ||
      simWallClockPaused ||
      chartLoadHoldsSim
    ) {
      return undefined;
    }
    let raf = 0;
    let lastTs = performance.now();

    const tick = (now) => {
      const dt = Math.min((now - lastTs) / 1000, 0.25);
      lastTs = now;
      const bps = Math.max(0.05, liveBpsRef.current);
      playbackAccumRef.current += bps * dt;
      let steps = Math.floor(playbackAccumRef.current);
      if (steps > 0) {
        playbackAccumRef.current -= steps;
        steps = Math.min(steps, MAX_FINER_BARS_PER_FRAME);
        setFineRevealCount((c) => {
          const pool = fineBarsRef.current
            .filter((b) => b.unixtime <= cursorUnixRef.current)
            .sort((a, b) => a.unixtime - b.unixtime);
          const cap = pool.length;
          if (cap === 0) return 0;
          return Math.min(cap, c + steps);
        });
      }
      raf = requestAnimationFrame(tick);
    };
    raf = requestAnimationFrame(tick);
    return () => cancelAnimationFrame(raf);
  }, [
    isFinerChart,
    fineBarsLoading,
    fineBars.length,
    liveBps,
    busy,
    simWallClockPaused,
    chartLoadHoldsSim,
  ]);

  useEffect(() => {
    if (!busy && isFinerChart && finePool.length > 0) {
      setFineRevealCount(finePool.length);
    }
  }, [busy, isFinerChart, finePool.length]);

  const chartCandles = useMemo(() => {
    if (isNativeChart) {
      if (!rawBars.length) return [];
      return rawBars.map((b) => ({
        time: b.unixtime,
        open: b.ohlc.open,
        high: b.ohlc.high,
        low: b.ohlc.low,
        close: b.ohlc.close,
      }));
    }
    if (isFinerChart) {
      if (!finePool.length) return [];
      const n = Math.min(fineRevealCount, finePool.length);
      return finePool.slice(0, n).map((b) => ({
        time: b.unixtime,
        open: b.ohlc.open,
        high: b.ohlc.high,
        low: b.ohlc.low,
        close: b.ohlc.close,
      }));
    }
    if (!rawBars.length) return [];
    return resampleOhlc(rawBars, chartTf);
  }, [isNativeChart, isFinerChart, rawBars, finePool, fineRevealCount, chartTf]);

  const chartEquity = useMemo(() => {
    if (!rawEquity.length) return [];
    if (isNativeChart) {
      return rawEquity.map((p) => ({ time: p.unixtime, value: p.equity }));
    }
    if (isFinerChart) {
      return equityOnCandleCloseTimes(chartCandles, rawEquity);
    }
    return resampleEquity(rawEquity, chartTf);
  }, [rawEquity, isNativeChart, isFinerChart, chartCandles, chartTf]);

  const chartMarkers = useMemo(() => {
    let maxTradeUnix = Number.POSITIVE_INFINITY;
    if (isFinerChart) {
      const n = Math.min(fineRevealCount, finePool.length);
      maxTradeUnix = n > 0 ? finePool[n - 1].unixtime : 0;
    } else if (cursorUnix > 0) {
      maxTradeUnix = cursorUnix;
    }
    return rawTrades
      .filter((tr) => tr.unixtime <= maxTradeUnix)
      .map((tr) => {
        const buy = String(tr.direction || '').toLowerCase() === 'buy';
        const markerTime = isCoarserChart ? bucketStart(tr.unixtime, chartTf) : tr.unixtime;
        const dep =
          typeof tr.deposit_ratio === 'number' && Number.isFinite(tr.deposit_ratio)
            ? `${Math.round(tr.deposit_ratio * 100)}`
            : '?';
        const pr =
          typeof tr.price === 'number' && Number.isFinite(tr.price) ? tr.price.toFixed(2) : '?';
        return {
          time: markerTime,
          position: buy ? 'belowBar' : 'aboveBar',
          color: buy ? '#26a69a' : '#ef5350',
          shape: buy ? 'arrowUp' : 'arrowDown',
          text: `${buy ? 'BUY' : 'SELL'} @ ${pr} (${dep}% dep.)`,
        };
      })
      .sort((a, b) => a.time - b.time);
  }, [rawTrades, isCoarserChart, isFinerChart, chartTf, cursorUnix, finePool, fineRevealCount]);

  const openStream = useCallback(async () => {
    stopStream();
    const token = await getAccessToken();
    const url = new URL(`${apiBaseUrl}/simulation/stream`, window.location.origin);
    url.searchParams.set('thread_id', threadId);
    if (token) url.searchParams.set('access_token', token);
    const es = new EventSource(url.toString());
    esRef.current = es;
    es.onmessage = (event) => {
      try {
        const payload = JSON.parse(event.data);
        appendLog(JSON.stringify(payload));
        if (payload?.kind === 'speed' && typeof payload.bps === 'number') {
          setLiveBps(payload.bps);
          setSpeedPresetId(presetIdFromBps(payload.bps));
        }
        if (payload?.kind === 'bar' && payload.ohlc) {
          const sc = typeof payload.scale === 'string' ? normalizeTimeframe(payload.scale) : '';
          setSourceScale((s) => s || sc || '');
          setRawBars((prev) => {
            const row = { unixtime: payload.unixtime, ohlc: payload.ohlc };
            const next = [...prev, row];
            return next.length > MAX_SIM_BARS ? next.slice(-MAX_SIM_BARS) : next;
          });
        } else if (payload?.kind === 'trade') {
          setRawTrades((prev) => {
            const row = {
              unixtime: payload.unixtime,
              direction: payload.direction,
              price: payload.price,
              deposit_ratio: payload.deposit_ratio,
            };
            const next = [...prev, row];
            return next.length > MAX_SIM_TRADES ? next.slice(-MAX_SIM_TRADES) : next;
          });
        } else if (payload?.kind === 'pnl' && typeof payload.equity === 'number') {
          setRawEquity((prev) => {
            const row = { unixtime: payload.unixtime, equity: payload.equity };
            const next = [...prev, row];
            return next.length > MAX_SIM_BARS ? next.slice(-MAX_SIM_BARS) : next;
          });
        }
        if (payload?.kind === 'status' && typeof payload.status === 'string') {
          if (payload.status === 'paused') {
            setSimWallClockPaused(true);
          } else if (payload.status === 'running' || payload.status === 'starting') {
            setSimWallClockPaused(false);
          }
          if (
            payload.status === 'done' ||
            payload.status === 'error' ||
            payload.status === 'stopped'
          ) {
            setBusy(false);
            setSimWallClockPaused(false);
            stopStream();
          }
        }
      } catch {
        appendLog(event.data || '');
      }
    };
    es.onerror = () => {
      appendLog('[stream closed or error]');
      stopStream();
      setBusy(false);
      setSimWallClockPaused(false);
    };
  }, [apiBaseUrl, threadId, getAccessToken, appendLog, stopStream]);

  const selectedBps = useMemo(() => {
    const opt = SPEED_OPTIONS.find((o) => o.id === speedPresetId);
    return opt ? opt.bps : 2;
  }, [speedPresetId]);

  async function handleStart() {
    const tid = String(threadId || '').trim();
    if (!tid) return;
    setError('');
    setBusy(true);
    setSimWallClockPaused(false);
    setLogLines([]);
    setRawBars([]);
    setRawTrades([]);
    setRawEquity([]);
    setSourceScale('');
    setDisplayTfMode(DISPLAY_TF_NATIVE);
    setFineBars([]);
    setFineBarsError('');
    setChartLoadHoldsSim(false);
    setFineRevealCount(0);
    playbackAccumRef.current = 0;
    prevFineBarsLoadingRef.current = false;
    stopStream();
    const bpsVal = selectedBps;
    setLiveBps(bpsVal);
    try {
      const response = await authFetch(`${apiBaseUrl}/simulation/start`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({
          thread_id: tid,
          start_date: startDate,
          end_date: endDate,
          initial_speed_bps: bpsVal,
        }),
      });
      const data = await response.json().catch(() => ({}));
      if (!response.ok) {
        throw new Error(typeof data.error === 'string' ? data.error : 'Failed to start simulation');
      }
      await openStream();
    } catch (err) {
      setError(err instanceof Error ? err.message : String(err));
      setBusy(false);
    }
  }

  async function handlePause() {
    await authFetch(`${apiBaseUrl}/simulation/pause`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ thread_id: threadId }),
    });
  }

  async function handleResume() {
    await authFetch(`${apiBaseUrl}/simulation/resume`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ thread_id: threadId }),
    });
  }

  async function handleStop() {
    setBusy(false);
    stopStream();
    try {
      await authFetch(`${apiBaseUrl}/simulation/stop`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ thread_id: threadId }),
      });
    } catch {
      /* ignore */
    }
  }

  async function handleSpeedPresetChange(nextId) {
    setSpeedPresetId(nextId);
    if (!busy) return;
    const opt = SPEED_OPTIONS.find((o) => o.id === nextId);
    if (!opt) return;
    try {
      const response = await authFetch(`${apiBaseUrl}/simulation/speed`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ thread_id: threadId, bps: opt.bps }),
      });
      if (!response.ok) {
        const data = await response.json().catch(() => ({}));
        throw new Error(typeof data.error === 'string' ? data.error : 'Speed change failed');
      }
      setLiveBps(opt.bps);
    } catch (err) {
      setError(err instanceof Error ? err.message : String(err));
    }
  }

  const hasSimThread = Boolean(String(threadId || '').trim());
  /** Keep the chart block open on finer TF even when the OHLC request fails or returns [] (common for 1m + long ranges). */
  const showChartArea =
    chartCandles.length > 0 ||
    chartEquity.length > 0 ||
    (isFinerChart &&
      (fineBarsLoading ||
        fineBars.length > 0 ||
        Boolean(fineBarsError) ||
        rawBars.length > 0 ||
        hasSimThread));

  /** Stable chart instance across TF switches so history is not torn down; data updates in place. */
  const chartInstanceKey = String(threadId || '').trim() || 'sim';

  return (
    <div className="simulation-panel">
      <p className="simulation-intro muted">
        The simulation engine and SSE stream always use the strategy timeframe from <code>params.json</code>.{' '}
        <strong>Speed</strong> applies to <em>what you see on the chart</em>: on a finer chart TF (e.g. 4h) it is{' '}
        &quot;N {chartTf} bars per second&quot;; on native or aggregated TF it matches the strategy bar stream. For the
        chart you can stay <strong>native</strong>, load <strong>finer market OHLC</strong>, or <strong>aggregate</strong>{' '}
        to a coarser TF.
      </p>
      <div className="simulation-controls-row">
        <label className="simulation-field">
          <span>Start</span>
          <input type="date" value={startDate} onChange={(e) => setStartDate(e.target.value)} disabled={busy} />
        </label>
        <label className="simulation-field">
          <span>End</span>
          <input type="date" value={endDate} onChange={(e) => setEndDate(e.target.value)} disabled={busy} />
        </label>
        <label className="simulation-field">
          <span>Speed</span>
          <select
            value={speedPresetId}
            onChange={(e) => void handleSpeedPresetChange(e.target.value)}
            title={
              isFinerChart
                ? `Chart: ${liveBps} ${chartTf} bars per second (strategy stream may be a different TF)`
                : 'Strategy stream: bars per second (native / aggregated chart)'
            }
          >
            {SPEED_OPTIONS.map((o) => (
              <option key={o.id} value={o.id}>
                {o.label}
              </option>
            ))}
          </select>
        </label>
        <button type="button" className="simulation-btn-primary" disabled={busy} onClick={() => void handleStart()}>
          {busy ? 'Running…' : 'Run simulation'}
        </button>
        <button type="button" className="simulation-btn" disabled={!busy} onClick={() => void handlePause()}>
          Pause
        </button>
        <button type="button" className="simulation-btn" disabled={!busy} onClick={() => void handleResume()}>
          Resume
        </button>
        <button type="button" className="simulation-btn" onClick={() => void handleStop()}>
          Stop
        </button>
      </div>
      {error ? <p className="simulation-error">{error}</p> : null}
      {showChartArea ? (
        <div className="simulation-chart-section" aria-busy={chartTfSwitching}>
          <div className="simulation-chart-toolbar">
            <label className="simulation-field simulation-field--inline">
              <span>Chart TF</span>
              <select
                value={displayTfMode}
                onChange={(e) => setDisplayTfMode(e.target.value)}
                title="Native = strategy stream; finer = market OHLC for chart; coarser = aggregate buffered bars"
              >
                {chartTfOptions.map((o) => (
                  <option key={o.value} value={o.value}>
                    {o.label}
                  </option>
                ))}
              </select>
            </label>
            <span className="simulation-source-tf muted">
              Strategy stream: <code>{knownSource || '…'}</code>
              <span className="simulation-tf-mode">
                {' '}
                · chart:{' '}
                {isNativeChart ? (
                  'native'
                ) : isFinerChart ? (
                  <>
                    {chartTf} <span className="simulation-tf-sub">(market)</span>
                  </>
                ) : (
                  <>
                    {chartTf} <span className="simulation-tf-sub">(aggregated)</span>
                  </>
                )}
              </span>
            </span>
            {fineBarsError ? <span className="simulation-error simulation-error--inline">{fineBarsError}</span> : null}
            {fineBarsLoading ? (
              <span className="simulation-fine-loading muted">
                {chartLoadHoldsSim
                  ? 'Simulation paused — loading intraday OHLC, then resuming…'
                  : 'Loading intraday OHLC…'}
              </span>
            ) : null}
            {isFinerChart && fineBars.length > 0 && cursorUnix <= 0 ? (
              <span className="simulation-tf-hint muted">
                Intraday series loaded; candles appear as the simulation advances in time.
              </span>
            ) : null}
          </div>
          {chartCandles.length > 0 || chartEquity.length > 0 ? (
            <SimulationCharts
              key={chartInstanceKey}
              candles={chartCandles}
              equity={chartEquity}
              markers={chartMarkers}
            />
          ) : isFinerChart ? (
            <p className="simulation-charts-placeholder muted">
              {fineBarsLoading
                ? 'Fetching OHLC for the selected chart timeframe…'
                : fineBarsError
                  ? fineBarsError
                  : fineBars.length === 0
                    ? `No ${chartTf} OHLC for this date range (empty response). Check the market data provider or try a shorter range if the request times out.`
                    : 'No candles yet — run the simulation to reveal intraday bars up to the current strategy step.'}
            </p>
          ) : null}
          {chartTfSwitching ? (
            <div
              className="simulation-chart-tf-loader-overlay"
              role="status"
              aria-live="polite"
              aria-label="Switching chart timeframe"
            >
              <div className="simulation-chart-tf-loader-spinner" aria-hidden />
              <div className="simulation-chart-tf-loader-text">
                <span className="simulation-chart-tf-loader-title">Switching timeframe…</span>
                {isFinerChart ? (
                  <span className="simulation-chart-tf-loader-sub muted">
                    {chartLoadHoldsSim
                      ? 'Simulation is paused while intraday OHLC loads, then it resumes.'
                      : 'Loading market OHLC for this chart resolution.'}
                  </span>
                ) : (
                  <span className="simulation-chart-tf-loader-sub muted">Rebuilding the chart from streamed bars.</span>
                )}
              </div>
            </div>
          ) : null}
        </div>
      ) : (
        <p className="simulation-charts-placeholder muted">Charts appear after the first streamed bars.</p>
      )}
      <details className="simulation-log-details">
        <summary>Event log</summary>
        <div className="simulation-log" aria-label="Simulation event log">
          {logLines.length === 0 ? (
            <p className="muted">No events yet. Choose dates and run.</p>
          ) : (
            logLines.map((line, i) => (
              <div key={`${i}-${line.slice(0, 24)}`} className="simulation-log-line">
                {line}
              </div>
            ))
          )}
          <div ref={logEndRef} />
        </div>
      </details>
    </div>
  );
}
