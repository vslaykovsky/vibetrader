import { useCallback, useEffect, useLayoutEffect, useMemo, useRef, useState } from 'react';
import { DateTimePickerModal } from './DateTimePickerModal.jsx';
import { SimulationCharts } from './SimulationCharts.jsx';
import {
  bucketStart,
  DISPLAY_TF_OPTIONS,
  equityOnCandleCloseTimes,
  normalizeTimeframe,
  timeframeRank,
  TF_SECONDS,
} from '../lib/ohlcResample.js';

/** Hard caps for in-memory buffers. */
const MAX_OHLC_BARS = 50_000;
const MAX_TRADES = 5_000;
const MAX_LOG_LINES = 400;
/** Initial window: ``rightBarUnix`` + 20 bars to the left (21 total). */
const LEFT_WINDOW_BARS = 20;
/** Per-tick prefetch chunk in bars: ~``liveBps * SPEED_PREFETCH_MULT``. */
const SPEED_PREFETCH_MULT = 5;
/** Debounce for visible-range driven older-history fetches. */
const PAN_HISTORY_DEBOUNCE_MS = 250;
/** Whitespace (in bars) to the left of the oldest loaded bar that triggers a history fetch. */
const HISTORY_TRIGGER_BARS = 1;
/** Extra older bars requested beyond the visible whitespace (TradingView-style small look-back). */
const HISTORY_PREFETCH_PAD_BARS = 10;
/** Hard ceiling on the number of bars to request in a single pan-history call. */
const HISTORY_REQUEST_MAX_BARS = 500;

const DISPLAY_TF_NATIVE = 'native';

const SPEED_OPTIONS = [
  { id: '0.5', bps: 0.5, label: '0.5x' },
  { id: '1', bps: 1, label: '1x' },
  { id: '2', bps: 2, label: '2x' },
  { id: '4', bps: 4, label: '4x' },
  { id: '16', bps: 16, label: '16x' },
  { id: 'max', bps: 1_000_000, label: 'Max' },
];

function isoDateLocal(d) {
  const yyyy = d.getFullYear();
  const mm = String(d.getMonth() + 1).padStart(2, '0');
  const dd = String(d.getDate()).padStart(2, '0');
  return `${yyyy}-${mm}-${dd}`;
}

function unixToIsoDateUTC(sec) {
  const d = new Date(sec * 1000);
  const yyyy = d.getUTCFullYear();
  const mm = String(d.getUTCMonth() + 1).padStart(2, '0');
  const dd = String(d.getUTCDate()).padStart(2, '0');
  return `${yyyy}-${mm}-${dd}`;
}

function startNoonUnix(isoDate) {
  return Math.floor(new Date(`${isoDate}T12:00:00Z`).getTime() / 1000);
}

/** Sorted unique by ``unixtime`` (ascending). */
function mergeBars(prev, next) {
  const map = new Map();
  for (const b of prev) map.set(b.unixtime, b);
  for (const b of next) map.set(b.unixtime, b);
  const out = [...map.values()].sort((a, b) => a.unixtime - b.unixtime);
  if (out.length > MAX_OHLC_BARS) return out.slice(-MAX_OHLC_BARS);
  return out;
}

export function SimulationPanel({ threadId, apiBaseUrl, authFetch, getAccessToken }) {
  // ── User input / session ──────────────────────────────────────────────
  const [startDate, setStartDate] = useState('');
  const [datePickerOpen, setDatePickerOpen] = useState(false);
  const [speedPresetId, setSpeedPresetId] = useState('2');
  const [displayTfMode, setDisplayTfMode] = useState(DISPLAY_TF_NATIVE);
  const [sessionReady, setSessionReady] = useState(false);
  const [initLoading, setInitLoading] = useState(false);
  const [initEpoch, setInitEpoch] = useState(0);
  const [busy, setBusy] = useState(false);
  const [paused, setPaused] = useState(false);
  const [error, setError] = useState('');
  const [logLines, setLogLines] = useState([]);
  const [strategyTfFromInit, setStrategyTfFromInit] = useState('');
  /** Strategy-declared indicator-series catalog (from /simulation/stream) — passed to chart for tooltips. */
  const [indicatorSeriesCatalog, setIndicatorSeriesCatalog] = useState([]);

  // ── Chart bars (display only, comes from /simulation/display_bars) ────
  const [bars, setBars] = useState([]);
  const [barsScale, setBarsScale] = useState('');
  const [barsLoading, setBarsLoading] = useState(false);
  const [barsError, setBarsError] = useState('');

  // ── Right-side pointer (bar open unix in current chartTf). Left side is implicit (= bars[0]).
  const [rightBarUnix, setRightBarUnix] = useState(0);

  // ── Trades / equity (from /simulation/stream) ─────────────────────────
  const [trades, setTrades] = useState([]);
  const [equityPts, setEquityPts] = useState([]);

  // ── Refs ──────────────────────────────────────────────────────────────
  const esRef = useRef(null);
  const logEndRef = useRef(null);
  const authFetchRef = useRef(authFetch);
  authFetchRef.current = authFetch;
  const busyRef = useRef(false);
  busyRef.current = busy;
  const pausedRef = useRef(false);
  pausedRef.current = paused;
  const barsRef = useRef([]);
  useEffect(() => { barsRef.current = bars; }, [bars]);
  const rightBarUnixRef = useRef(0);
  useEffect(() => { rightBarUnixRef.current = rightBarUnix; }, [rightBarUnix]);
  const liveBpsRef = useRef(2);
  const playbackAccumRef = useRef(0);
  const prefetchInFlightRef = useRef(false);
  const historyInFlightRef = useRef(false);
  const panDebounceRef = useRef(0);
  const tfFetchSeqRef = useRef(0);
  const forwardExhaustedRef = useRef(false);
  const prefetchEmptyStreakRef = useRef(0);
  // Set when the *backend* finishes the simulation (``status:done``/``stopped``).
  // The frontend playback loop must keep revealing buffered bars at the chosen
  // speed until the buffer is exhausted; only this combined condition ends the
  // run from the user's perspective.
  const streamDoneRef = useRef(false);
  // Last known "playback cursor" captured BEFORE we wipe the OHLC buffer on a
  // chart-TF switch. We store the *end* of that bucket (open-time + tf-seconds)
  // so the new TF anchors at the latest sub-bar that fits inside the previous
  // bar — e.g. switching 1d→4h on Thursday lands on Thursday's 20:00 4h bar,
  // not Wednesday's last one.
  const lastCursorEndUnixRef = useRef(0);

  // ── Derived ───────────────────────────────────────────────────────────
  const knownSourceTf = normalizeTimeframe(strategyTfFromInit);
  const chartTf = useMemo(() => {
    if (displayTfMode === DISPLAY_TF_NATIVE) return knownSourceTf || '1d';
    return normalizeTimeframe(displayTfMode) || knownSourceTf || '1d';
  }, [displayTfMode, knownSourceTf]);
  const tfSec = TF_SECONDS[chartTf] || TF_SECONDS['1d'];
  const chartTfRef = useRef(chartTf);
  const prevTfSecRef = useRef(tfSec);

  const selectedBps = useMemo(() => {
    const opt = SPEED_OPTIONS.find((o) => o.id === speedPresetId);
    return opt ? opt.bps : 2;
  }, [speedPresetId]);
  liveBpsRef.current = selectedBps;

  const chartTfChoices = useMemo(() => {
    const merged = [...DISPLAY_TF_OPTIONS, ...(knownSourceTf && !DISPLAY_TF_OPTIONS.includes(knownSourceTf) ? [knownSourceTf] : [])];
    return [...new Set(merged)].sort((a, b) => (timeframeRank(a) ?? 99) - (timeframeRank(b) ?? 99));
  }, [knownSourceTf]);

  // ── Helpers ───────────────────────────────────────────────────────────
  const appendLog = useCallback((line) => {
    setLogLines((prev) => [...prev.slice(-MAX_LOG_LINES), line]);
  }, []);
  const appendLogRef = useRef(appendLog);
  appendLogRef.current = appendLog;

  const stopStream = useCallback(() => {
    esRef.current?.close();
    esRef.current = null;
  }, []);

  const callDisplayBars = useCallback(
    async (apiStart, apiEnd, signal) => {
      const tid = String(threadId || '').trim();
      const qs = new URLSearchParams({
        thread_id: tid,
        scale: chartTf,
        start_date: apiStart,
        end_date: apiEnd,
      });
      if (busyRef.current && rightBarUnixRef.current > 0) {
        qs.set('chart_last_bar_unixtime', String(rightBarUnixRef.current));
      }
      const res = await authFetchRef.current(`${apiBaseUrl}/simulation/display_bars?${qs}`, {
        method: 'GET',
        signal,
      });
      const data = await res.json().catch(() => ({}));
      if (!res.ok) {
        throw new Error(typeof data.error === 'string' ? data.error : 'Failed to load chart OHLC');
      }
      const out = Array.isArray(data.bars) ? data.bars : [];
      out.sort((a, b) => a.unixtime - b.unixtime);
      return out;
    },
    [apiBaseUrl, chartTf, threadId],
  );

  // ── Reset chart buffers when the chart TF changes ──────────────────────
  useLayoutEffect(() => {
    // Remember where the user was *before* we wipe the buffer. We anchor the
    // new TF to the *end* of the previous right-most bucket (open + tfSec_prev)
    // so the new init lands on the latest sub-bar that fits inside that bucket.
    if (rightBarUnixRef.current > 0) {
      const prevTfSec = prevTfSecRef.current || tfSec;
      lastCursorEndUnixRef.current = rightBarUnixRef.current + prevTfSec;
    }
    chartTfRef.current = chartTf;
    prevTfSecRef.current = tfSec;
    tfFetchSeqRef.current += 1;
    setBars([]);
    setBarsScale('');
    setBarsError('');
    setBarsLoading(false);
    setRightBarUnix(0);
    barsRef.current = [];
    rightBarUnixRef.current = 0;
    playbackAccumRef.current = 0;
    prefetchInFlightRef.current = false;
    forwardExhaustedRef.current = false;
    streamDoneRef.current = false;
    prefetchEmptyStreakRef.current = 0;
    historyInFlightRef.current = false;
    window.clearTimeout(panDebounceRef.current);
  }, [chartTf]);

  // ── Initial load: 21 bars ending at the temporal anchor in the current TF.
  // The anchor is either:
  //  • ``lastCursorEndUnixRef`` — end of the right-most bucket from the
  //    previous chart TF, so switching TF keeps the same point in time on
  //    screen and lands on the latest sub-bar inside the previous bucket;
  //  • or the noon-bucket of ``start_date`` — the very first init.
  useEffect(() => {
    if (!sessionReady) return undefined;
    const sd = startDate.trim();
    const tid = String(threadId || '').trim();
    if (!/^\d{4}-\d{2}-\d{2}$/.test(sd) || !tid) return undefined;
    if (bars.length > 0) return undefined;

    const seq = tfFetchSeqRef.current;
    const ac = new AbortController();
    let alive = true;

    const cursorEndUnix = lastCursorEndUnixRef.current;
    // ``cursorEndUnix`` is the *end* of the previous bucket; subtract 1s so we
    // bucket-start strictly inside it (avoids landing on the next bucket).
    const baseUnix =
      cursorEndUnix > 0 ? cursorEndUnix - 1 : startNoonUnix(sd);
    const anchorBucket = bucketStart(baseUnix, chartTf);
    // Use the same trading-day inflation we apply on pan-history: intraday TFs
    // see ~6.5h of trading per calendar day with weekends fully closed, so a
    // naive ``padBack=N*tfSec`` for 1m would only span 25 *real-time* minutes
    // (often deep in after-hours) and the provider would only return a handful
    // of sparse bars sprinkled across the closing minutes of multiple days.
    const want = LEFT_WINDOW_BARS + 1;
    const tradingFractionPerDay = tfSec >= 86400 ? 1 : Math.min(1, (6.5 * 3600) / 86400);
    const weekendInflation = tfSec >= 86400 ? 1 : 7 / 5;
    const wantSec = want * tfSec;
    const inflatedSec = Math.ceil((wantSec / tradingFractionPerDay) * weekendInflation);
    const minPadSec = tfSec >= 86400 ? 0 : 2 * 86400;
    const padBack = Math.max(inflatedSec + minPadSec, wantSec);
    // ``apiEnd`` anchors at the moment *just before* the previous bucket's end
    // (or anchorBucket + tfSec for the very first init); it bounds the search
    // and avoids landing on the next-bucket sub-bars.
    const upperUnix = cursorEndUnix > 0 ? cursorEndUnix - 1 : anchorBucket + tfSec;
    const apiStart = unixToIsoDateUTC(upperUnix - padBack);
    // Add a 1-day cushion so providers (which often treat ``end_date`` as
    // exclusive) still include the anchor bucket itself.
    const apiEnd = unixToIsoDateUTC(upperUnix + 86400);

    appendLog(
      `[init] start_date=${sd} chartTf=${chartTf} cursorEnd=${cursorEndUnix} anchorBucket=${anchorBucket} (${new Date(
        anchorBucket * 1000,
      ).toISOString()}) apiStart=${apiStart} apiEnd=${apiEnd} padBackSec=${padBack}`,
    );
    setBarsLoading(true);
    setBarsError('');
    forwardExhaustedRef.current = false;
    streamDoneRef.current = false;
    void (async () => {
      try {
        const fetched = await callDisplayBars(apiStart, apiEnd, ac.signal);
        if (!alive || ac.signal.aborted || seq !== tfFetchSeqRef.current) return;
        // Filter STRICTLY before ``upperUnix`` (or ``<=`` anchorBucket on first
        // init): when switching coarse→fine, the provider returns whole calendar
        // days and we don't want to show any sub-bars *after* the previous
        // bucket's end. Take only the last ``want`` rows that survive — which
        // for intraday TFs naturally clusters near the latest trading minute
        // before ``upperUnix`` rather than scattering across multiple sessions.
        const upTo =
          cursorEndUnix > 0
            ? fetched.filter((b) => b.unixtime < cursorEndUnix)
            : fetched.filter((b) => b.unixtime <= anchorBucket);
        const initial = (upTo.length > 0 ? upTo : fetched).slice(-want);
        barsRef.current = initial;
        setBars(initial);
        setBarsScale(chartTf);
        if (initial.length > 0) {
          rightBarUnixRef.current = initial[initial.length - 1].unixtime;
          setRightBarUnix(initial[initial.length - 1].unixtime);
          const firstIso = new Date(initial[0].unixtime * 1000).toISOString();
          const lastIso = new Date(
            initial[initial.length - 1].unixtime * 1000,
          ).toISOString();
          // Bar-times in HH:MM UTC, comma-joined: makes it obvious whether the
          // chart actually has 4 intraday 4h bars per session or just one.
          const stamps = initial
            .map((b) => {
              const d = new Date(b.unixtime * 1000);
              const hh = String(d.getUTCHours()).padStart(2, '0');
              const mm = String(d.getUTCMinutes()).padStart(2, '0');
              const dd = String(d.getUTCDate()).padStart(2, '0');
              return `${dd}/${hh}:${mm}`;
            })
            .join(',');
          appendLog(
            `[init] loaded ${initial.length} bars: first=${initial[0].unixtime}(${firstIso}) last=${initial[initial.length - 1].unixtime}(${lastIso})`,
          );
          appendLog(`[init] stamps=${stamps}`);
        }
      } catch (err) {
        if (!alive || ac.signal.aborted) return;
        if (err instanceof DOMException && err.name === 'AbortError') return;
        setBarsError(err instanceof Error ? err.message : String(err));
      } finally {
        if (alive) setBarsLoading(false);
      }
    })();
    return () => {
      alive = false;
      ac.abort();
    };
  }, [sessionReady, startDate, threadId, chartTf, tfSec, callDisplayBars, bars.length, appendLog]);

  // ── Playback loop: rAF reveals next bar at ``liveBps``; prefetches more when buffer is low. ─
  // Effect identity is stable across ``bars`` updates so the rAF clock never resets.
  useEffect(() => {
    if (!busy || paused) return undefined;
    const sd = startDate.trim();
    const tid = String(threadId || '').trim();
    if (!sessionReady || !/^\d{4}-\d{2}-\d{2}$/.test(sd) || !tid) return undefined;

    let timer = 0;
    let lastTs = performance.now();
    let cancelled = false;
    let lastWatchdogLog = lastTs;
    let frameCount = 0;
    // ~30 Hz; ``setInterval`` keeps ticking when the tab is in the background
    // or DevTools steal focus, unlike ``requestAnimationFrame`` which Chrome
    // throttles to ~1 Hz (and sometimes pauses entirely) for inactive tabs.
    const TICK_INTERVAL_MS = 1000 / 30;
    appendLog('[playback] loop started');

    const triggerPrefetch = () => {
      if (prefetchInFlightRef.current) return;
      if (forwardExhaustedRef.current) return;
      const sorted = barsRef.current;
      if (sorted.length === 0) return;
      const chunkBars = Math.max(2, Math.ceil(liveBpsRef.current * SPEED_PREFETCH_MULT));
      const lastOpen = sorted[sorted.length - 1].unixtime;
      const padSec = Math.max(tfSec * 2, 120);
      // Widen the look-ahead window each time we don't get fresh bars; helps when the
      // provider's bar timestamps are sparse / lag behind the calendar.
      const widen = Math.min(8, 1 << prefetchEmptyStreakRef.current);
      const apiStart = unixToIsoDateUTC(Math.floor(lastOpen + 1));
      const apiEnd = unixToIsoDateUTC(
        Math.ceil(lastOpen + ((chunkBars + 5) * tfSec + padSec) * widen),
      );
      const seq = tfFetchSeqRef.current;
      prefetchInFlightRef.current = true;
      void (async () => {
        try {
          const fetched = await callDisplayBars(apiStart, apiEnd);
          if (seq !== tfFetchSeqRef.current) return;
          const prev = barsRef.current;
          const known = new Set(prev.map((b) => b.unixtime));
          const fresh = fetched.filter((b) => !known.has(b.unixtime));
          if (fresh.length > 0) {
            const merged = mergeBars(prev, fetched);
            barsRef.current = merged;
            setBars(merged);
            prefetchEmptyStreakRef.current = 0;
            appendLog(
              `[prefetch] +${fresh.length} new (got=${fetched.length}) buf ${prev.length}→${merged.length} widen=${widen}`,
            );
          } else {
            prefetchEmptyStreakRef.current += 1;
            appendLog(
              `[prefetch] no new bars (got=${fetched.length} dup-streak=${prefetchEmptyStreakRef.current} widen=${widen})`,
            );
            if (prefetchEmptyStreakRef.current >= 3) {
              forwardExhaustedRef.current = true;
              appendLog('[prefetch] giving up — provider seems exhausted');
              setBarsError('No more historical bars available from provider.');
            }
          }
        } catch (err) {
          if (err instanceof DOMException && err.name === 'AbortError') return;
          setBarsError(err instanceof Error ? err.message : String(err));
          appendLog(`[prefetch error] ${err instanceof Error ? err.message : String(err)}`);
        } finally {
          prefetchInFlightRef.current = false;
        }
      })();
    };

    const tick = () => {
      if (cancelled) return;
      try {
        runTick(performance.now());
      } catch (err) {
        appendLog(
          `[tick error] ${err instanceof Error ? err.message : String(err)}`,
        );
      }
    };

    const runTick = (now) => {
      frameCount += 1;
      const dt = Math.min(0.25, (now - lastTs) / 1000);
      lastTs = now;
      playbackAccumRef.current += Math.max(0.05, liveBpsRef.current) * dt;
      const steps = Math.floor(playbackAccumRef.current);
      const sorted = barsRef.current;
      if (sorted.length > 0) {
        const cur = rightBarUnixRef.current;
        const ix = cur > 0 ? sorted.findIndex((b) => b.unixtime === cur) : sorted.length - 1;
        if (steps > 0) {
          playbackAccumRef.current -= steps;
          const baseIx = ix < 0 ? sorted.length - 1 : ix;
          const next = Math.min(sorted.length - 1, baseIx + steps);
          if (next >= 0 && next > baseIx) {
            rightBarUnixRef.current = sorted[next].unixtime;
            setRightBarUnix(sorted[next].unixtime);
          }
        }
        const refIx = ix < 0 ? sorted.length - 1 : ix;
        const tail = sorted.length - 1 - refIx;
        const lowMark = Math.max(2, Math.ceil(liveBpsRef.current * SPEED_PREFETCH_MULT * 0.5));
        if (tail <= lowMark && !forwardExhaustedRef.current) {
          triggerPrefetch();
        }
        if (now - lastWatchdogLog > 1000) {
          const lastBar = sorted[sorted.length - 1]?.unixtime;
          appendLog(
            `[tick] frames/s≈${frameCount} buf=${sorted.length} ix=${refIx} tail=${tail} lowMark=${lowMark} cur=${cur} lastBar=${lastBar} accum=${playbackAccumRef.current.toFixed(2)} pref=${prefetchInFlightRef.current ? 'Y' : 'N'} exh=${forwardExhaustedRef.current ? 'Y' : 'N'}`,
          );
          lastWatchdogLog = now;
          frameCount = 0;
        }
        // Halt the loop only when there is genuinely nothing left to reveal:
        //  * we're at the rightmost loaded bar (``tail<=0``), AND
        //  * either the provider is exhausted OR the backend already announced
        //    that the simulation finished (so no more bars / trades will arrive).
        if (
          tail <= 0 &&
          (forwardExhaustedRef.current || streamDoneRef.current)
        ) {
          appendLog('[playback] paused at end of available data');
          setBusy(false);
          setPaused(false);
          cancelled = true;
          return;
        }
      } else if (now - lastWatchdogLog > 1000) {
        appendLog('[tick] empty bars buffer');
        lastWatchdogLog = now;
      }
    };
    timer = window.setInterval(tick, TICK_INTERVAL_MS);
    return () => {
      cancelled = true;
      window.clearInterval(timer);
      appendLog('[playback] loop stopped');
    };
  }, [busy, paused, sessionReady, startDate, threadId, tfSec, callDisplayBars, appendLog]);

  // ── Pan-history (left): TradingView-style — fetch only the whitespace + small pad. ─────
  const handleVisibleTimeRange = useCallback((range) => {
    window.clearTimeout(panDebounceRef.current);
    panDebounceRef.current = window.setTimeout(() => {
      if (!range || !Number.isFinite(range.from)) return;
      if (historyInFlightRef.current) return;
      const sd = startDate.trim();
      const tid = String(threadId || '').trim();
      if (!sessionReady || !/^\d{4}-\d{2}-\d{2}$/.test(sd) || !tid) return;

      const sorted = barsRef.current;
      if (sorted.length === 0) return;
      const oldest = sorted[0].unixtime;
      const whitespaceBars =
        typeof range.barsBefore === 'number' && range.barsBefore < 0 ? -range.barsBefore : 0;
      if (whitespaceBars < HISTORY_TRIGGER_BARS) return;

      const want = Math.min(
        HISTORY_REQUEST_MAX_BARS,
        Math.max(HISTORY_TRIGGER_BARS + HISTORY_PREFETCH_PAD_BARS, Math.ceil(whitespaceBars) + HISTORY_PREFETCH_PAD_BARS),
      );
      // Intraday TFs only see ~6.5h of trading per calendar day, plus weekends
      // are fully closed. Inflate the wall-clock window so ``want`` bars are
      // actually reachable, but bound the inflation so daily/weekly TFs don't
      // pull in months of extra data for a small whitespace.
      const tradingFractionPerDay = tfSec >= 86400 ? 1 : Math.min(1, (6.5 * 3600) / 86400);
      const weekendInflation = tfSec >= 86400 ? 1 : 7 / 5;
      const wantSec = want * tfSec;
      const inflatedSec = Math.ceil((wantSec / tradingFractionPerDay) * weekendInflation);
      const minPadSec = tfSec >= 86400 ? 0 : 86400; // ensure apiStart < apiEnd
      const apiEnd = unixToIsoDateUTC(Math.floor(oldest - 1));
      const apiStart = unixToIsoDateUTC(
        Math.floor(oldest - Math.max(inflatedSec + minPadSec, wantSec)),
      );
      historyInFlightRef.current = true;
      const seq = tfFetchSeqRef.current;
      const ac = new AbortController();
      appendLogRef.current?.(
        `[history] pan-left whitespaceBars=${whitespaceBars.toFixed(1)} want=${want} apiStart=${apiStart} apiEnd=${apiEnd}`,
      );
      void (async () => {
        try {
          const fetched = await callDisplayBars(apiStart, apiEnd, ac.signal);
          if (ac.signal.aborted || seq !== tfFetchSeqRef.current) return;
          if (fetched.length === 0) {
            appendLogRef.current?.('[history] empty response from display_bars');
            return;
          }
          // The provider returns whole calendar days, so for intraday TFs the
          // response can be massively larger than ``want``. Keep only the
          // ``want`` newest bars strictly older than ``oldest`` so we extend
          // the buffer left by exactly the visible whitespace + small pad.
          const trimmed = fetched
            .filter((b) => b.unixtime < oldest)
            .slice(-want);
          if (trimmed.length === 0) {
            appendLogRef.current?.(
              `[history] no bars older than ${oldest} in response (got=${fetched.length})`,
            );
            return;
          }
          const before = barsRef.current.length;
          const merged = mergeBars(barsRef.current, trimmed);
          const added = merged.length - before;
          appendLogRef.current?.(
            `[history] +${added} bars (got=${fetched.length} kept=${trimmed.length} buf ${before}→${merged.length})`,
          );
          if (trimmed.length > 0) {
            const stamps = trimmed
              .map((b) => {
                const d = new Date(b.unixtime * 1000);
                const hh = String(d.getUTCHours()).padStart(2, '0');
                const mm = String(d.getUTCMinutes()).padStart(2, '0');
                const dd = String(d.getUTCDate()).padStart(2, '0');
                return `${dd}/${hh}:${mm}`;
              })
              .join(',');
            appendLogRef.current?.(`[history] kept-stamps=${stamps}`);
          }
          barsRef.current = merged;
          setBars(merged);
        } catch (err) {
          appendLogRef.current?.(
            `[history] error: ${err instanceof Error ? err.message : String(err)}`,
          );
        } finally {
          historyInFlightRef.current = false;
        }
      })();
    }, PAN_HISTORY_DEBOUNCE_MS);
  }, [sessionReady, startDate, threadId, tfSec, callDisplayBars]);

  // ── Stream: trades / equity / status (no bars consumed for chart) ────
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
        if (payload?.kind === 'trade') {
          const tu = Number(payload.unixtime) || 0;
          const tIso = tu > 0 ? new Date(tu * 1000).toISOString() : '?';
          const bucket = tu > 0 ? bucketStart(tu, chartTfRef.current) : 0;
          const bIso = bucket > 0 ? new Date(bucket * 1000).toISOString() : '?';
          appendLog(
            `[trade-event] dir=${payload.direction} price=${payload.price} ` +
              `unixtime=${tu} (${tIso}) bucket@${chartTfRef.current}=${bucket} (${bIso})`,
          );
          setTrades((prev) => {
            const row = {
              unixtime: payload.unixtime,
              direction: payload.direction,
              price: payload.price,
              deposit_ratio: payload.deposit_ratio,
            };
            const next = [...prev, row];
            return next.length > MAX_TRADES ? next.slice(-MAX_TRADES) : next;
          });
        } else if (payload?.kind === 'pnl' && typeof payload.equity === 'number') {
          setEquityPts((prev) => {
            const row = { unixtime: payload.unixtime, equity: payload.equity };
            const next = [...prev, row];
            return next.length > MAX_OHLC_BARS ? next.slice(-MAX_OHLC_BARS) : next;
          });
        } else if (payload?.kind === 'indicator_series_catalog' && Array.isArray(payload.series)) {
          setIndicatorSeriesCatalog(
            payload.series
              .filter((row) => row && typeof row.name === 'string')
              .map((row) => ({
                name: row.name,
                description: typeof row.description === 'string' ? row.description : '',
              })),
          );
        } else if (payload?.kind === 'status' && typeof payload.status === 'string') {
          const rawSc =
            (typeof payload.strategy_scale === 'string' && payload.strategy_scale) ||
            (typeof payload.strategyScale === 'string' && payload.strategyScale) ||
            '';
          if (rawSc) {
            const st = normalizeTimeframe(rawSc.trim());
            if (st) setStrategyTfFromInit(st);
          }
          if (payload.status === 'ready') setSessionReady(true);
          if (payload.status === 'paused') setPaused(true);
          if (payload.status === 'running' || payload.status === 'starting') setPaused(false);
          // ``done`` / ``stopped`` mean the *backend* finished — but the chart
          // typically still has buffered bars that the user has not yet seen,
          // because the playback loop reveals them at the chosen speed (bps).
          // Close the stream, but keep ``busy`` true: the playback loop will
          // self-terminate once it shows the last buffered bar AND prefetch is
          // exhausted (``streamDoneRef && tail<=0`` branch in the tick fn).
          if (payload.status === 'done' || payload.status === 'stopped') {
            streamDoneRef.current = true;
            stopStream();
          }
          if (payload.status === 'error') {
            const msg =
              typeof payload.message === 'string' && payload.message
                ? payload.message
                : 'Trade stream reported an error; chart playback continues.';
            setError(msg);
            stopStream();
          }
        }
      } catch {
        appendLog(event.data || '');
      }
    };
    es.onerror = () => {
      const state = es.readyState; // 0 connecting, 1 open, 2 closed
      appendLog(`[stream closed or error] readyState=${state}`);
      if (state === 2) {
        stopStream();
      }
      // Don't kill playback on transport errors — chart bars come from a separate
      // HTTP endpoint. The user can press Stop to fully end the session.
    };
    es.onopen = () => {
      appendLog('[stream open]');
    };
  }, [apiBaseUrl, threadId, getAccessToken, appendLog, stopStream]);
  const openStreamRef = useRef(openStream);
  openStreamRef.current = openStream;

  // ── Init session when start date / thread / epoch changes ─────────────
  useEffect(() => {
    setSessionReady(false);
    setBusy(false);
    setPaused(false);
    stopStream();
  }, [threadId, stopStream]);

  useEffect(() => {
    const sd = startDate.trim();
    if (!/^\d{4}-\d{2}-\d{2}$/.test(sd)) {
      setSessionReady(false);
      setBusy(false);
      setInitLoading(false);
      stopStream();
      return undefined;
    }
    const tid = String(threadId || '').trim();
    if (!tid) return undefined;

    let cancelled = false;
    setError('');
    setInitLoading(true);
    setSessionReady(false);
    setBusy(false);
    setPaused(false);
    setLogLines([]);
    setTrades([]);
    setEquityPts([]);
    setStrategyTfFromInit('');
    setIndicatorSeriesCatalog([]);
    setDisplayTfMode(DISPLAY_TF_NATIVE);
    setBars([]);
    setBarsScale('');
    setBarsError('');
    setBarsLoading(false);
    setRightBarUnix(0);
    barsRef.current = [];
    rightBarUnixRef.current = 0;
    lastCursorEndUnixRef.current = 0;
    playbackAccumRef.current = 0;
    prefetchInFlightRef.current = false;
    historyInFlightRef.current = false;
    forwardExhaustedRef.current = false;
    streamDoneRef.current = false;
    prefetchEmptyStreakRef.current = 0;
    stopStream();

    void (async () => {
      try {
        const response = await authFetchRef.current(`${apiBaseUrl}/simulation/init`, {
          method: 'POST',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({
            thread_id: tid,
            start_date: sd,
            initial_speed_bps: liveBpsRef.current,
          }),
        });
        const data = await response.json().catch(() => ({}));
        if (cancelled) return;
        if (!response.ok) {
          throw new Error(typeof data.error === 'string' ? data.error : 'Failed to init simulation');
        }
        setSessionReady(true);
        const rawSc =
          (typeof data.strategy_scale === 'string' && data.strategy_scale) ||
          (typeof data.strategyScale === 'string' && data.strategyScale) ||
          '';
        if (rawSc) {
          const st = normalizeTimeframe(rawSc.trim());
          if (st) setStrategyTfFromInit(st);
        }
        await openStreamRef.current();
      } catch (err) {
        if (!cancelled) {
          setError(err instanceof Error ? err.message : String(err));
          setSessionReady(false);
        }
      } finally {
        if (!cancelled) setInitLoading(false);
      }
    })();
    return () => {
      cancelled = true;
    };
  }, [threadId, startDate, initEpoch, apiBaseUrl, stopStream]);

  // ── Controls ─────────────────────────────────────────────────────────
  async function handlePlay() {
    const tid = String(threadId || '').trim();
    if (!tid || !sessionReady) return;
    setError('');
    try {
      if (!esRef.current) await openStreamRef.current();
      forwardExhaustedRef.current = false;
      streamDoneRef.current = false;
      prefetchEmptyStreakRef.current = 0;
      playbackAccumRef.current = 0;
      setBusy(true);
      const res = await authFetchRef.current(`${apiBaseUrl}/simulation/play`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ thread_id: tid }),
      });
      if (!res.ok) {
        const data = await res.json().catch(() => ({}));
        throw new Error(typeof data.error === 'string' ? data.error : 'Play failed');
      }
    } catch (err) {
      setError(err instanceof Error ? err.message : String(err));
      setBusy(false);
    }
  }

  async function handlePause() {
    await authFetchRef.current(`${apiBaseUrl}/simulation/pause`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ thread_id: threadId }),
    });
  }

  async function handleStop() {
    setBusy(false);
    setPaused(false);
    setSessionReady(false);
    setStrategyTfFromInit('');
    stopStream();
    try {
      await authFetchRef.current(`${apiBaseUrl}/simulation/stop`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ thread_id: threadId }),
      });
    } catch {
      /* ignore */
    }
    if (String(startDate || '').trim()) setInitEpoch((n) => n + 1);
  }

  async function handleSpeedPresetChange(nextId) {
    setSpeedPresetId(nextId);
    if (!busy) return;
    const opt = SPEED_OPTIONS.find((o) => o.id === nextId);
    if (!opt) return;
    try {
      const res = await authFetchRef.current(`${apiBaseUrl}/simulation/speed`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ thread_id: threadId, bps: opt.bps }),
      });
      if (!res.ok) {
        const data = await res.json().catch(() => ({}));
        throw new Error(typeof data.error === 'string' ? data.error : 'Speed change failed');
      }
    } catch (err) {
      setError(err instanceof Error ? err.message : String(err));
    }
  }

  // ── Derived: candles from buffer start up to rightBarUnix (left side is open). ─────────
  const candles = useMemo(() => {
    if (!bars.length || normalizeTimeframe(barsScale) !== chartTf) return [];
    const hi = rightBarUnix > 0 ? rightBarUnix : bars[bars.length - 1].unixtime;
    return bars
      .filter((b) => b.unixtime <= hi)
      .map((b) => {
        const c = {
          time: b.unixtime,
          open: b.ohlc.open,
          high: b.ohlc.high,
          low: b.ohlc.low,
          close: b.ohlc.close,
        };
        if (typeof b.ohlc.volume === 'number' && Number.isFinite(b.ohlc.volume)) {
          c.volume = b.ohlc.volume;
        }
        return c;
      });
  }, [bars, barsScale, chartTf, rightBarUnix]);

  const equity = useMemo(() => {
    if (!equityPts.length || candles.length === 0) return [];
    return equityOnCandleCloseTimes(candles, equityPts);
  }, [equityPts, candles]);

  const markers = useMemo(() => {
    if (!trades.length || !rightBarUnix) return [];
    // Trades are emitted at the strategy's native timeframe. Only show their
    // markers when the user is looking at *that* timeframe; otherwise the
    // bucketing across TFs would visually shift each trade away from its real
    // bar.
    if (knownSourceTf && chartTf !== knownSourceTf) return [];
    // Hide a marker until its *own* bucket is the right-most rendered bar — or
    // earlier. Otherwise lightweight-charts would snap the marker to the
    // nearest existing bar, which makes it visually appear one bar to the left
    // and "jump" right when the next bar finally opens.
    const visible = trades.filter(
      (tr) => bucketStart(tr.unixtime, chartTf) <= rightBarUnix,
    );
    if (visible.length > 0) {
      const first = visible[0];
      const last = visible[visible.length - 1];
      const isoFirst = new Date((first.unixtime || 0) * 1000).toISOString();
      const isoLast = new Date((last.unixtime || 0) * 1000).toISOString();
      appendLogRef.current?.(
        `[markers] count=${visible.length} first=${first.unixtime}(${isoFirst}) last=${last.unixtime}(${isoLast}) right=${rightBarUnix}`,
      );
    }
    return visible
      .map((tr) => {
        const buy = String(tr.direction || '').toLowerCase() === 'buy';
        const dep =
          typeof tr.deposit_ratio === 'number' && Number.isFinite(tr.deposit_ratio)
            ? `${Math.round(tr.deposit_ratio * 100)}`
            : '?';
        const pr =
          typeof tr.price === 'number' && Number.isFinite(tr.price) ? tr.price.toFixed(2) : '?';
        return {
          time: bucketStart(tr.unixtime, chartTf),
          position: buy ? 'belowBar' : 'aboveBar',
          color: buy ? '#26a69a' : '#ef5350',
          shape: buy ? 'arrowUp' : 'arrowDown',
          text: `${buy ? 'BUY' : 'SELL'} @ ${pr} (${dep}% dep.)`,
        };
      })
      .sort((a, b) => a.time - b.time);
  }, [trades, rightBarUnix, chartTf, knownSourceTf]);

  const livePlayback = busy && !paused;
  const showChartArea = sessionReady || initLoading || candles.length > 0 || barsLoading || Boolean(barsError);
  const chartInstanceKey = `${String(threadId || 'sim')}:${chartTf}`;

  return (
    <div className="simulation-panel">
      <p className="simulation-intro muted">
        Pick a <strong>Start</strong> date to prepare the session (backend <code>POST /simulation/init</code>); then press{' '}
        <strong>Play</strong> to stream bars. Chart OHLC always comes from <code>GET /simulation/display_bars</code>.
        Trades are streamed from <code>/simulation/stream</code>.
      </p>
      <div className="simulation-controls-row">
        <label className="simulation-field">
          <span>Start</span>
          <button
            type="button"
            className="simulation-date-trigger"
            disabled={busy || initLoading}
            onClick={() => setDatePickerOpen(true)}
          >
            {startDate.trim() ? startDate : 'Выберите дату'}
          </button>
        </label>
        <DateTimePickerModal
          open={datePickerOpen}
          value={startDate}
          onClose={() => setDatePickerOpen(false)}
          onConfirm={(iso) => {
            setStartDate(iso);
            setDatePickerOpen(false);
          }}
          disabled={busy || initLoading}
          maxDate={isoDateLocal(new Date())}
        />
        <label className="simulation-field">
          <span>Speed</span>
          <select
            value={speedPresetId}
            onChange={(e) => void handleSpeedPresetChange(e.target.value)}
            title="Chart bar reveal rate (bars/second)"
          >
            {SPEED_OPTIONS.map((o) => (
              <option key={o.id} value={o.id}>
                {o.label}
              </option>
            ))}
          </select>
        </label>
        <button
          type="button"
          className="simulation-btn"
          disabled={!sessionReady || (busy && !paused)}
          onClick={() => void handlePlay()}
        >
          Play
        </button>
        <button type="button" className="simulation-btn" disabled={!busy} onClick={() => void handlePause()}>
          Pause
        </button>
        <button type="button" className="simulation-btn" onClick={() => void handleStop()}>
          Stop
        </button>
      </div>
      {error ? <p className="simulation-error">{error}</p> : null}
      {showChartArea ? (
        <div className="simulation-chart-section">
          <div className="simulation-chart-toolbar">
            <label className="simulation-field simulation-field--inline">
              <span>Chart TF</span>
              <select
                value={chartTf}
                onChange={(e) => {
                  const tf = normalizeTimeframe(e.target.value);
                  if (!tf) return;
                  if (knownSourceTf && knownSourceTf === tf) {
                    setDisplayTfMode(DISPLAY_TF_NATIVE);
                  } else {
                    setDisplayTfMode(tf);
                  }
                }}
                title="Timeframe of the candles on this chart."
              >
                {chartTfChoices.map((tf) => (
                  <option key={tf} value={tf}>
                    {tf}
                    {knownSourceTf === tf ? ' (strategy timeframe)' : ''}
                  </option>
                ))}
              </select>
            </label>
            {barsError ? (
              <span className="simulation-error simulation-error--inline">{barsError}</span>
            ) : null}
            {barsLoading ? (
              <span className="simulation-fine-loading muted">Loading chart OHLC…</span>
            ) : null}
          </div>
          {candles.length > 0 ? (
            <SimulationCharts
              key={chartInstanceKey}
              candles={candles}
              equity={equity}
              markers={markers}
              chartTf={chartTf}
              indicatorSeriesCatalog={indicatorSeriesCatalog}
              lockedVisibleRange={null}
              livePlayback={livePlayback}
              viewportCapped={false}
              onVisibleTimeRangeChange={handleVisibleTimeRange}
            />
          ) : (
            <p className="simulation-charts-placeholder muted">
              {barsLoading ? 'Loading chart OHLC…' : barsError || 'Pick a Start date and press Play to stream bars.'}
            </p>
          )}
        </div>
      ) : (
        <p className="simulation-charts-placeholder muted">
          Pick a Start date to prepare the session, then Play to stream bars.
        </p>
      )}
      <details className="simulation-log-details">
        <summary>Event log</summary>
        <div className="simulation-log" aria-label="Simulation event log">
          {logLines.length === 0 ? (
            <p className="muted">No events yet. Choose Start, then Play.</p>
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
