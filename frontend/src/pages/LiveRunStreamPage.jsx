import { useCallback, useEffect, useMemo, useRef, useState } from 'react';
import { Link, useNavigate, useParams } from 'react-router-dom';
import { formatDistanceStrict } from 'date-fns';
import hljs from 'highlight.js/lib/core';
import python from 'highlight.js/lib/languages/python';
import ReactMarkdown from 'react-markdown';
import remarkGfm from 'remark-gfm';
import { useAuth } from '../AuthContext';
import { useTheme } from '../ThemeContext';
import { useTimeZone } from '../TimeZoneContext.jsx';
import { formatUnixDateTime, parseIsoInstant } from '../lib/dateTime.js';
import { ProfileMenu } from '../ProfileMenu';
import { ConfirmDialog } from '../components/ConfirmDialog.jsx';
import { renderCharts } from '../strategyChartRenderer.js';
import { attachSyncedCrosshair, attachSyncedTimeScales } from '../lib/lwcSync.js';
import {
  applyLiveStreamEvent,
  createLiveChartState,
  liveChartsDataJson,
  liveTrades,
} from '../lib/liveChartStream.js';

hljs.registerLanguage('python', python);

const API_BASE_URL =
  import.meta.env.VITE_API_BASE_URL ||
  (import.meta.env.PROD ? '/api' : 'http://localhost:8080');

const LIVE_SSE_KINDS = [
  'snapshot',
  'bar',
  'indicator',
  'position',
  'trade',
  'status',
  'annotation',
];

function parseRunDate(iso) {
  const ms = parseIsoInstant(iso);
  return ms == null ? null : new Date(ms);
}

function liveRunDurationLabel(status, createdAt, updatedAt, now) {
  const start = parseRunDate(createdAt);
  if (!start) return { prefix: 'Running for', text: '—' };
  const s = String(status || '').toLowerCase();
  const ended = s === 'stopped' || s === 'failure' || s === 'error' || s === 'failed';
  const end = ended ? parseRunDate(updatedAt) ?? now : now;
  const text = formatDistanceStrict(end, start, { addSuffix: false });
  return { prefix: ended ? 'Ran for' : 'Running for', text };
}

function LiveRunDurationLine({ status, createdAt, updatedAt }) {
  const s = String(status || '').toLowerCase();
  const ended = s === 'stopped' || s === 'failure' || s === 'error' || s === 'failed';
  const [tick, setTick] = useState(0);
  useEffect(() => {
    if (ended) return undefined;
    const id = window.setInterval(() => setTick((n) => n + 1), 15000);
    return () => window.clearInterval(id);
  }, [ended]);
  const { prefix, text } = useMemo(
    () => liveRunDurationLabel(status, createdAt, updatedAt, new Date()),
    [status, createdAt, updatedAt, tick],
  );
  return (
    <span className="live-run-meta-item muted">
      {prefix} {text}
    </span>
  );
}

function strategyTitleFromRun(r) {
  const n = typeof r?.strategy_name === 'string' ? r.strategy_name.trim() : '';
  if (n && n !== 'unknown strategy') return n;
  return 'Untitled strategy';
}

function liveAccountLabel(r) {
  const lab = typeof r?.alpaca_account_label === 'string' ? r.alpaca_account_label.trim() : '';
  if (lab) return lab;
  const mode = String(r?.mode || '').toLowerCase();
  if (mode === 'paper') return 'Paper';
  if (mode === 'live') return 'Live';
  return '—';
}

function fmtUnixTime(u, timeZone) {
  return formatUnixDateTime(u, timeZone);
}

function liveRunCanStop(status) {
  const s = String(status || '').toLowerCase();
  return Boolean(s) && s !== 'stopping' && !liveRunCanDelete(s);
}

function liveRunCanDelete(status) {
  const s = String(status || '').toLowerCase();
  return s === 'stopped' || s === 'failure' || s === 'error' || s === 'failed';
}

function liveOrderIdLabel(t) {
  const a = typeof t?.alpaca_order_id === 'string' ? t.alpaca_order_id.trim() : '';
  if (a) return a;
  const c = typeof t?.client_order_id === 'string' ? t.client_order_id.trim() : '';
  if (c) return c;
  return '—';
}

function liveAlpacaOrderHref(t) {
  const a = typeof t?.alpaca_order_id === 'string' ? t.alpaca_order_id.trim() : '';
  if (!a) return '';
  return `https://app.alpaca.markets/dashboard/order/${encodeURIComponent(a)}`;
}

function fmtTradeNumber(v) {
  if (v == null || v === '') return '—';
  const n = Number(v);
  if (!Number.isFinite(n)) return '—';
  return n.toLocaleString('en-US', { maximumFractionDigits: 6 });
}

function fmtUsdNumber(v) {
  if (v == null || v === '') return '—';
  const n = Number(v);
  if (!Number.isFinite(n)) return '—';
  return n.toLocaleString('en-US', {
    style: 'currency',
    currency: 'USD',
    minimumFractionDigits: 2,
    maximumFractionDigits: 2,
  });
}

function optionalTradeNumber(v) {
  if (v == null || v === '') return null;
  const n = Number(v);
  return Number.isFinite(n) ? n : null;
}

function liveTradeValueUsd(t) {
  const value = optionalTradeNumber(t?.value_usd);
  if (value != null) return Math.abs(value);
  const price = optionalTradeNumber(t?.price);
  const qty = optionalTradeNumber(t?.qty);
  if (price != null && qty != null) return Math.abs(price * qty);
  return null;
}

function escapeCsvField(value) {
  const s = value == null ? '' : String(value);
  if (/[",\n\r]/.test(s)) {
    return `"${s.replace(/"/g, '""')}"`;
  }
  return s;
}

function liveOrdersToCsv(trades, timeZone) {
  const columns = [
    'Time',
    'Ticker',
    'Direction',
    'Price',
    'Qty',
    'Value USD',
    'Fraction',
    'Position Before',
    'Position After',
    'Status',
    'Comment',
    'Order ID',
  ];
  const lines = [columns.map(escapeCsvField).join(',')];
  for (const t of [...trades].reverse()) {
    const valueUsd = liveTradeValueUsd(t);
    const row = [
      fmtUnixTime(t.unixtime, timeZone),
      t.ticker ?? '',
      t.direction ?? '',
      fmtTradeNumber(t.price),
      fmtTradeNumber(t.qty),
      fmtUsdNumber(valueUsd),
      fmtTradeNumber(t.deposit_ratio),
      fmtTradeNumber(t.position_before_order),
      fmtTradeNumber(t.position_after_order_filled),
      t.status || '',
      t.comment || '',
      liveOrderIdLabel(t) === '—' ? '' : liveOrderIdLabel(t),
    ];
    lines.push(row.map(escapeCsvField).join(','));
  }
  return lines.join('\r\n');
}

function triggerCsvDownload(filename, csvText) {
  const blob = new Blob([csvText], { type: 'text/csv;charset=utf-8' });
  const url = URL.createObjectURL(blob);
  const a = document.createElement('a');
  a.href = url;
  a.download = filename;
  a.rel = 'noopener';
  document.body.appendChild(a);
  a.click();
  document.body.removeChild(a);
  URL.revokeObjectURL(url);
}

function CanvasPanelCopyButton({ text, ariaLabel, disabled }) {
  const [copied, setCopied] = useState(false);
  const payload = typeof text === 'string' ? text : '';
  const isDisabled = Boolean(disabled) || !payload;

  async function handleCopy(event) {
    event.preventDefault();
    event.stopPropagation();
    if (isDisabled) return;
    try {
      await navigator.clipboard.writeText(payload);
      setCopied(true);
      window.setTimeout(() => setCopied(false), 1600);
    } catch {
      setCopied(false);
    }
  }

  return (
    <button
      type="button"
      className={`canvas-panel-copy-btn${copied ? ' is-copied' : ''}`}
      aria-label={ariaLabel}
      title={copied ? 'Copied' : 'Copy to clipboard'}
      disabled={isDisabled}
      onClick={handleCopy}
    >
      {copied ? (
        <svg viewBox="0 0 24 24" fill="none" aria-hidden>
          <path
            d="M5 13l4 4L19 7"
            stroke="currentColor"
            strokeWidth="2"
            strokeLinecap="round"
            strokeLinejoin="round"
          />
        </svg>
      ) : (
        <svg viewBox="0 0 24 24" fill="none" aria-hidden>
          <rect
            x="8.25"
            y="8.25"
            width="11.5"
            height="11.5"
            rx="2"
            stroke="currentColor"
            strokeWidth="1.75"
          />
          <rect
            x="4.25"
            y="4.25"
            width="11.5"
            height="11.5"
            rx="2"
            stroke="currentColor"
            strokeWidth="1.75"
          />
        </svg>
      )}
    </button>
  );
}

function escapeHtml(code) {
  const source = typeof code === 'string' ? code : '';
  return source
    .replace(/&/g, '&amp;')
    .replace(/</g, '&lt;')
    .replace(/>/g, '&gt;')
    .replace(/"/g, '&quot;')
    .replace(/'/g, '&#39;');
}

function highlightedPythonHtml(code) {
  const source = typeof code === 'string' ? code : '';
  try {
    return hljs.highlight(source, { language: 'python', ignoreIllegals: true }).value;
  } catch {
    return escapeHtml(source);
  }
}

function PythonSourceCode({ code }) {
  const highlighted = useMemo(() => highlightedPythonHtml(code), [code]);
  return (
    <pre className="canvas-pseudocode canvas-python-code">
      <code dangerouslySetInnerHTML={{ __html: highlighted }} />
    </pre>
  );
}

function paramsJsonFromOutput(output) {
  if (!output || typeof output !== 'object') {
    return null;
  }
  const raw = output['params.json'];
  if (raw == null) {
    return null;
  }
  if (typeof raw === 'string') {
    const t = raw.trim();
    return t.length ? t : null;
  }
  try {
    const s = JSON.stringify(raw, null, 2);
    return s.trim().length ? s : null;
  } catch {
    return null;
  }
}

function paramsHyperoptJsonFromOutput(output) {
  if (!output || typeof output !== 'object') {
    return null;
  }
  const raw = output['params-hyperopt.json'];
  if (raw == null) {
    return null;
  }
  if (typeof raw === 'string') {
    const t = raw.trim();
    return t.length ? t : null;
  }
  try {
    const s = JSON.stringify(raw, null, 2);
    return s.trim().length ? s : null;
  } catch {
    return null;
  }
}

export function LiveRunStreamPage() {
  const { runId = '' } = useParams();
  const navigate = useNavigate();
  const { user, signOut, getAccessToken } = useAuth();
  const { theme, toggleTheme } = useTheme();
  const { timeZone } = useTimeZone();
  const [runMeta, setRunMeta] = useState(null);
  const [dbRow, setDbRow] = useState(null);
  const [metaLoading, setMetaLoading] = useState(true);
  const [metaError, setMetaError] = useState('');
  const [streamConn, setStreamConn] = useState('connecting');
  const [chartEpoch, setChartEpoch] = useState(0);
  const [trades, setTrades] = useState([]);
  const [chartError, setChartError] = useState('');
  const [actionError, setActionError] = useState('');
  const [stopping, setStopping] = useState(false);
  const [stopDialogOpen, setStopDialogOpen] = useState(false);
  const [deleting, setDeleting] = useState(false);
  const [deleteDialogOpen, setDeleteDialogOpen] = useState(false);
  const [strategyDetails, setStrategyDetails] = useState(null);
  const [strategyError, setStrategyError] = useState('');
  const [algorithmLoading, setAlgorithmLoading] = useState(false);

  const chartsMountRef = useRef(null);
  const liveChartStateRef = useRef(createLiveChartState());
  const lastEventIdRef = useRef(0);
  const strategyFetchAbortRef = useRef(null);

  const authFetch = useCallback(
    async (url, options = {}) => {
      const token = await getAccessToken();
      const headers = { ...options.headers };
      if (token) headers.Authorization = `Bearer ${token}`;
      return fetch(url, { ...options, headers });
    },
    [getAccessToken],
  );

  const bumpCharts = useCallback(() => setChartEpoch((n) => n + 1), []);

  const handleDownloadOrdersCsv = useCallback(() => {
    if (!trades.length) return;
    const rid = String(runId || 'live-run').trim() || 'live-run';
    triggerCsvDownload(`live-orders-${rid}.csv`, liveOrdersToCsv(trades, timeZone));
  }, [runId, timeZone, trades]);

  const loadMeta = useCallback(async () => {
    const rid = String(runId || '').trim();
    if (!rid) {
      setMetaError('Missing run id');
      setMetaLoading(false);
      return;
    }
    setMetaLoading(true);
    setMetaError('');
    try {
      const [runsRes, statusRes] = await Promise.all([
        authFetch(`${API_BASE_URL}/live/runs?run_id=${encodeURIComponent(rid)}&limit=1`),
        authFetch(`${API_BASE_URL}/live/status?run_id=${encodeURIComponent(rid)}`),
      ]);
      const runsPayload = await runsRes.json().catch(() => ({}));
      const statusPayload = await statusRes.json().catch(() => ({}));
      if (!runsRes.ok) throw new Error(runsPayload.error || `Runs failed (${runsRes.status})`);
      if (!statusRes.ok) throw new Error(statusPayload.error || `Status failed (${statusRes.status})`);
      const runs = Array.isArray(runsPayload.runs) ? runsPayload.runs : [];
      setRunMeta(runs[0] || null);
      setDbRow(statusPayload.db && typeof statusPayload.db === 'object' ? statusPayload.db : null);
    } catch (e) {
      setMetaError(e instanceof Error ? e.message : String(e));
      setRunMeta(null);
      setDbRow(null);
    } finally {
      setMetaLoading(false);
    }
  }, [authFetch, runId]);

  useEffect(() => {
    void loadMeta();
  }, [loadMeta]);

  useEffect(() => {
    const id = window.setInterval(() => void loadMeta(), 20000);
    return () => window.clearInterval(id);
  }, [loadMeta]);

  const ingestParsed = useCallback(
    (parsed) => {
      const eid = Number(parsed?.seq);
      if (Number.isFinite(eid) && eid > lastEventIdRef.current) lastEventIdRef.current = eid;
      const result = applyLiveStreamEvent(liveChartStateRef.current, parsed);
      if (result.tradesChanged) {
        setTrades(liveTrades(liveChartStateRef.current));
      }
      if (result.statusChanged) {
        const status = liveChartStateRef.current.status?.status;
        if (typeof status === 'string' && status.trim()) {
          setDbRow((prev) => (prev && typeof prev === 'object' ? { ...prev, status: status.trim() } : { status: status.trim() }));
        }
      }
      if (result.changed) {
        bumpCharts();
      }
    },
    [bumpCharts],
  );

  useEffect(() => {
    let evtSource;
    let cancelled = false;
    lastEventIdRef.current = 0;
    liveChartStateRef.current = createLiveChartState();
    setTrades([]);
    setChartError('');
    setStreamConn('connecting');

    (async () => {
      const rid = String(runId || '').trim();
      if (!rid) return;
      const token = await getAccessToken();
      if (cancelled) return;
      const url = new URL(`${API_BASE_URL}/live/stream`, window.location.origin);
      url.searchParams.set('run_id', rid);
      url.searchParams.set('after_id', '0');
      if (token) url.searchParams.set('access_token', token);
      evtSource = new EventSource(url.toString());

      const onChunk = (event) => {
        try {
          const parsed = JSON.parse(event.data);
          ingestParsed(parsed);
        } catch {
          /* ignore */
        }
      };

      evtSource.onopen = () => setStreamConn('connected');
      for (const k of LIVE_SSE_KINDS) {
        evtSource.addEventListener(k, onChunk);
      }
      evtSource.onerror = () => {
        setStreamConn('disconnected');
        evtSource?.close();
      };
    })();

    return () => {
      cancelled = true;
      evtSource?.close();
    };
  }, [runId, getAccessToken, ingestParsed]);

  useEffect(() => {
    const mount = chartsMountRef.current;
    if (!mount) return undefined;
    const dataJson = liveChartsDataJson(liveChartStateRef.current);
    const charts = Array.isArray(dataJson.charts) ? dataJson.charts : [];
    if (charts.length === 0) {
      mount.innerHTML = '';
      setChartError('');
      return undefined;
    }
    mount.innerHTML = '';
    const root = document.createElement('div');
    root.className = 'strategy-charts-root';
    mount.appendChild(root);
    let detachSync;
    let detachCrosshair;
    let detachChartDnD;
    try {
      const rid = String(runId || '').trim();
      const rendered = renderCharts(root, dataJson, {
        chartOrderStorageBase: rid ? `live:${rid}` : 'live',
        alignRightEdge: true,
        timeZone,
      });
      detachChartDnD = rendered.detachChartDnD;
      detachSync = attachSyncedTimeScales(rendered.lwCharts);
      detachCrosshair = attachSyncedCrosshair(rendered.lwCrosshairBindings);
      setChartError('');
    } catch (err) {
      setChartError(err instanceof Error ? err.message : String(err));
    }
    return () => {
      detachChartDnD?.();
      detachSync?.();
      detachCrosshair?.();
      mount.innerHTML = '';
    };
  }, [chartEpoch, runId, timeZone]);

  const displayStatus = dbRow?.status ?? runMeta?.status ?? '';
  const createdAt = dbRow?.created_at ?? runMeta?.created_at;
  const updatedAt = dbRow?.updated_at ?? runMeta?.updated_at;

  const backtestHref = useMemo(() => {
    const tid = String(runMeta?.thread_id || '').trim();
    const dep = String(runMeta?.strategy_id || runMeta?.deployed_from_run_id || '').trim();
    if (tid && dep) return `/strategy/${tid}#${dep}`;
    if (tid) return `/strategy/${tid}`;
    return '';
  }, [runMeta]);

  const deployedStrategyId = useMemo(() => {
    const fromRuns = String(runMeta?.strategy_id || runMeta?.deployed_from_run_id || '').trim();
    if (fromRuns) return fromRuns;
    return String(dbRow?.strategy_id || dbRow?.deployed_from_run_id || '').trim();
  }, [runMeta, dbRow]);

  useEffect(() => {
    strategyFetchAbortRef.current?.abort();
    strategyFetchAbortRef.current = null;
    setStrategyDetails(null);
    setStrategyError('');
    setAlgorithmLoading(false);

    const sid = String(deployedStrategyId || '').trim();
    if (!sid) return undefined;

    const ac = new AbortController();
    strategyFetchAbortRef.current = ac;
    (async () => {
      try {
        const res = await authFetch(`${API_BASE_URL}/strategy?id=${encodeURIComponent(sid)}`, {
          signal: ac.signal,
        });
        const payload = await res.json().catch(() => ({}));
        if (ac.signal.aborted) return;
        if (!res.ok) {
          throw new Error(payload.error || `Strategy details failed (${res.status})`);
        }
        setStrategyDetails(payload && typeof payload === 'object' ? payload : null);
      } catch (e) {
        if (e?.name === 'AbortError') return;
        setStrategyError(e instanceof Error ? e.message : String(e));
      } finally {
        if (strategyFetchAbortRef.current === ac) {
          strategyFetchAbortRef.current = null;
        }
      }
    })();

    return () => {
      ac.abort();
      if (strategyFetchAbortRef.current === ac) {
        strategyFetchAbortRef.current = null;
      }
    };
  }, [authFetch, deployedStrategyId]);

  const fetchStrategyAlgorithm = useCallback(async () => {
    const sid = String(deployedStrategyId || strategyDetails?.id || '').trim();
    if (!sid || algorithmLoading) return;
    if (String(strategyDetails?.algorithm || '').trim()) return;
    setAlgorithmLoading(true);
    setStrategyError('');
    try {
      const response = await authFetch(`${API_BASE_URL}/strategy/algorithm`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ id: sid }),
      });
      const data = await response.json().catch(() => ({}));
      if (!response.ok) {
        setStrategyError(typeof data.error === 'string' ? data.error : 'Failed to load strategy algorithm');
        return;
      }
      const text = typeof data.algorithm === 'string' ? data.algorithm : '';
      setStrategyDetails((prev) => {
        if (!prev || String(prev.id || '').trim() !== sid) return prev;
        return { ...prev, algorithm: text };
      });
    } catch (err) {
      setStrategyError(err instanceof Error ? err.message : String(err));
    } finally {
      setAlgorithmLoading(false);
    }
  }, [authFetch, deployedStrategyId, strategyDetails, algorithmLoading]);

  const stopLive = useCallback(async () => {
    const rid = String(runId || '').trim();
    if (!rid) return;
    setStopping(true);
    setActionError('');
    try {
      const res = await authFetch(`${API_BASE_URL}/live/stop`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ run_id: rid }),
      });
      const payload = await res.json().catch(() => ({}));
      if (!res.ok) throw new Error(payload.error || `Stop failed (${res.status})`);
      setStopDialogOpen(false);
      await loadMeta();
    } catch (e) {
      setActionError(e instanceof Error ? e.message : String(e));
    } finally {
      setStopping(false);
    }
  }, [authFetch, runId, loadMeta]);

  const deleteLiveRun = useCallback(async () => {
    const rid = String(runId || '').trim();
    if (!rid || deleting) return;
    setDeleting(true);
    setActionError('');
    try {
      const res = await authFetch(`${API_BASE_URL}/live/runs/${encodeURIComponent(rid)}`, {
        method: 'DELETE',
      });
      const payload = await res.json().catch(() => ({}));
      if (!res.ok) throw new Error(payload.error || `Delete failed (${res.status})`);
      navigate('/dashboard#live-deployments');
    } catch (e) {
      setActionError(e instanceof Error ? e.message : String(e));
    } finally {
      setDeleting(false);
    }
  }, [authFetch, runId, navigate, deleting]);

  const title = strategyTitleFromRun(runMeta);
  const accountDisp = liveAccountLabel(runMeta || {});
  const showStop = liveRunCanStop(displayStatus);
  const showDelete = Boolean(runMeta) && liveRunCanDelete(displayStatus);
  const isLocal = String(runMeta?.runner_backend || '').toLowerCase() === 'local';
  const strategyOutput = strategyDetails?.canvas?.output;
  const paramsJsonText = paramsJsonFromOutput(strategyOutput);
  const showParamsPanel = paramsJsonText != null;
  const paramsHyperoptJsonText = paramsHyperoptJsonFromOutput(strategyOutput);
  const showHyperoptParamsPanel = paramsHyperoptJsonText != null;
  const strategyAlgorithmText = String(strategyDetails?.algorithm || '').trim();
  const showAlgorithmPanel = Boolean(deployedStrategyId && strategyDetails);
  const pythonCodeText = typeof strategyDetails?.python_code === 'string' ? strategyDetails.python_code : '';
  const showPythonCodePanel = pythonCodeText.trim().length > 0;
  const showStrategyArtifactPanels =
    showParamsPanel ||
    showHyperoptParamsPanel ||
    showAlgorithmPanel ||
    showPythonCodePanel ||
    Boolean(strategyError);

  return (
    <div className="dashboard-page live-run-page">
      <header className="dashboard-topbar">
        <div className="dashboard-topbar-left">
          <Link to="/" className="app-home-link" aria-label="Go to home page">
            <span className="app-logo">TraderChat</span>
          </Link>
          <span className="dashboard-topbar-sep" aria-hidden>
            /
          </span>
          <span className="dashboard-topbar-crumb">Live stream</span>
        </div>
        <div className="dashboard-topbar-right">
          <Link className="dashboard-topbar-crumb dashboard-topbar-link" to="/dashboard#live-deployments">
            Dashboard
          </Link>
          <button
            type="button"
            className="theme-toggle"
            onClick={toggleTheme}
            aria-label={theme === 'dark' ? 'Switch to light theme' : 'Switch to dark theme'}
            title={theme === 'dark' ? 'Light theme' : 'Dark theme'}
          >
            <span className="home-ms" aria-hidden>
              {theme === 'light' ? 'dark_mode' : 'light_mode'}
            </span>
          </button>
          {user ? (
            <div className="auth-user-area">
              <ProfileMenu user={user} signOut={signOut} surface="strategy" />
            </div>
          ) : null}
        </div>
      </header>

      <div className="dashboard-inner">
        <section className="dashboard-hero live-run-hero">
          <div className="dashboard-hero-copy live-run-hero-copy">
            <h1 className="dashboard-hero-title">{metaLoading ? 'Loading…' : title}</h1>
            <div className="live-run-hero-meta">
              {runMeta ? (
                <>
                  <span className="live-run-meta-item muted">
                    Stream: {streamConn === 'connected' ? 'live' : streamConn}
                  </span>
                  <span className="live-run-meta-sep muted" aria-hidden>
                    ·
                  </span>
                  <span className="live-run-meta-item muted">Run status: {displayStatus || '—'}</span>
                  <span className="live-run-meta-sep muted" aria-hidden>
                    ·
                  </span>
                  <LiveRunDurationLine status={displayStatus} createdAt={createdAt} updatedAt={updatedAt} />
                  <span className="live-run-meta-sep muted" aria-hidden>
                    ·
                  </span>
                  <span className="live-run-meta-item muted">
                    Account: <strong className="live-run-meta-strong">{accountDisp}</strong>
                  </span>
                  {isLocal ? (
                    <>
                      <span className="live-run-meta-sep muted" aria-hidden>
                        ·
                      </span>
                      <span className="dashboard-pill dashboard-pill--local">local</span>
                    </>
                  ) : null}
                </>
              ) : null}
            </div>
          </div>
          <div className="dashboard-hero-actions live-run-hero-actions">
            {backtestHref ? (
              <Link className="live-run-action-link" to={backtestHref}>
                <span className="home-ms dashboard-btn-icon" aria-hidden>
                  analytics
                </span>
                Backtest canvas
              </Link>
            ) : null}
            {showStop ? (
              <button
                type="button"
                className="dashboard-btn-primary live-run-stop-btn"
                disabled={stopping}
                onClick={() => setStopDialogOpen(true)}
              >
                {stopping ? 'Stopping…' : 'Stop live'}
              </button>
            ) : null}
            {showDelete ? (
              <button
                type="button"
                className="live-run-action-link live-run-delete-btn"
                disabled={deleting}
                aria-label="Delete live deployment"
                title="Delete live deployment"
                onClick={() => setDeleteDialogOpen(true)}
              >
                <span className="home-ms dashboard-btn-icon" aria-hidden>
                  {deleting ? 'hourglass_top' : 'delete'}
                </span>
              </button>
            ) : null}
          </div>
        </section>

        {metaError ? <p className="dashboard-banner-error">{metaError}</p> : null}
        {actionError ? <p className="dashboard-banner-error">{actionError}</p> : null}

        {!metaLoading && !runMeta && !metaError ? (
          <p className="dashboard-banner-error">This live run was not found or you do not have access.</p>
        ) : null}

        <section className="canvas-panel canvas-panel-charts live-run-canvas-panel">
          <header className="canvas-hero live-run-canvas-heading">
            <h2 className="dashboard-panel-title live-run-canvas-title">Charts</h2>
            <p className="muted live-run-canvas-lead">Updates from the live strategy runner stream.</p>
          </header>
          {chartError ? <p className="canvas-chart-error">{chartError}</p> : null}
          {chartEpoch === 0 && !chartError && streamConn === 'connected' ? (
            <p className="canvas-charts-placeholder muted">Waiting for chart output from the runner…</p>
          ) : null}
          <div ref={chartsMountRef} className="canvas-charts-mount" aria-label="Live strategy charts" />
        </section>

        <section className="dashboard-panel live-run-trades-panel">
          <div className="dashboard-panel-head">
            <div className="live-run-orders-heading">
              <h2 className="dashboard-panel-title">Orders</h2>
              <a
                className="live-run-orders-link"
                href="https://app.alpaca.markets/account/orders"
                target="_blank"
                rel="noopener noreferrer"
              >
                Alpaca orders
              </a>
              <button
                type="button"
                className="live-run-orders-download-btn"
                disabled={trades.length === 0}
                onClick={handleDownloadOrdersCsv}
              >
                Download CSV
              </button>
            </div>
            <span className="dashboard-panel-count">{trades.length}</span>
          </div>
          {trades.length === 0 ? (
            <p className="muted live-run-trades-empty">No orders yet.</p>
          ) : (
            <div className="live-run-trades-scroll">
              <table className="live-run-trades-table">
                <thead>
                  <tr>
                    <th>Time</th>
                    <th>Ticker</th>
                    <th>Direction</th>
                    <th>Price</th>
                    <th>Qty</th>
                    <th>Value USD</th>
                    <th>Fraction</th>
                    <th>Position Before</th>
                    <th>Position After</th>
                    <th>Status</th>
                    <th>Comment</th>
                    <th>Order ID</th>
                  </tr>
                </thead>
                <tbody>
                  {[...trades].reverse().map((t) => {
                    const orderIdLabel = liveOrderIdLabel(t);
                    const alpacaOrderHref = liveAlpacaOrderHref(t);
                    const valueUsd = liveTradeValueUsd(t);
                    return (
                      <tr key={t.rowKey}>
                        <td>{fmtUnixTime(t.unixtime, timeZone)}</td>
                        <td>{t.ticker ?? '—'}</td>
                        <td>{t.direction ?? '—'}</td>
                        <td>{fmtTradeNumber(t.price)}</td>
                        <td>{fmtTradeNumber(t.qty)}</td>
                        <td>{fmtUsdNumber(valueUsd)}</td>
                        <td>{fmtTradeNumber(t.deposit_ratio)}</td>
                        <td>{fmtTradeNumber(t.position_before_order)}</td>
                        <td>{fmtTradeNumber(t.position_after_order_filled)}</td>
                        <td>{t.status || '—'}</td>
                        <td>{t.comment || '—'}</td>
                        <td className="live-run-order-id-cell">
                          {alpacaOrderHref ? (
                            <a
                              className="live-run-order-id-link"
                              href={alpacaOrderHref}
                              target="_blank"
                              rel="noopener noreferrer"
                            >
                              {orderIdLabel}
                            </a>
                          ) : (
                            orderIdLabel
                          )}
                        </td>
                      </tr>
                    );
                  })}
                </tbody>
              </table>
            </div>
          )}
        </section>

        {showStrategyArtifactPanels ? (
          <section className="live-run-artifacts" aria-label="Live strategy artifacts">
            {strategyError ? <p className="dashboard-banner-error">{strategyError}</p> : null}
            {showParamsPanel ? (
              <details className="canvas-text-block canvas-text-block-pseudocode canvas-pseudocode-details">
                <summary className="canvas-pseudocode-summary">Strategy parameters</summary>
                <CanvasPanelCopyButton
                  text={paramsJsonText}
                  ariaLabel="Copy strategy parameters JSON"
                />
                <pre className="canvas-pseudocode">{paramsJsonText}</pre>
              </details>
            ) : null}
            {showHyperoptParamsPanel ? (
              <details className="canvas-text-block canvas-text-block-pseudocode canvas-pseudocode-details">
                <summary className="canvas-pseudocode-summary">Hyperopt parameters</summary>
                <CanvasPanelCopyButton
                  text={paramsHyperoptJsonText}
                  ariaLabel="Copy hyperopt parameters JSON"
                />
                <pre className="canvas-pseudocode">{paramsHyperoptJsonText}</pre>
              </details>
            ) : null}
            {showAlgorithmPanel ? (
              <details
                key={`algorithm:${deployedStrategyId}`}
                className="canvas-text-block canvas-text-block-pseudocode canvas-pseudocode-details"
                onToggle={(e) => {
                  if (!e.currentTarget.open) return;
                  if (!strategyAlgorithmText) {
                    void fetchStrategyAlgorithm();
                  }
                }}
              >
                <summary className="canvas-pseudocode-summary">Strategy Algorithm</summary>
                <CanvasPanelCopyButton
                  text={strategyAlgorithmText}
                  ariaLabel="Copy strategy algorithm overview"
                  disabled={algorithmLoading}
                />
                {algorithmLoading ? (
                  <div className="chat-spinner-row canvas-algorithm-spinner" role="status" aria-live="polite">
                    <span className="chat-spinner" aria-hidden />
                    <span className="chat-processing-label">Generating overview…</span>
                  </div>
                ) : (
                  <div className="canvas-algorithm-markdown message-markdown">
                    <ReactMarkdown remarkPlugins={[remarkGfm]}>
                      {strategyAlgorithmText}
                    </ReactMarkdown>
                  </div>
                )}
              </details>
            ) : null}
            {showPythonCodePanel ? (
              <details
                key={`source:${deployedStrategyId}`}
                className="canvas-text-block canvas-text-block-pseudocode canvas-pseudocode-details"
              >
                <summary className="canvas-pseudocode-summary">Python Source Code</summary>
                <CanvasPanelCopyButton
                  text={pythonCodeText}
                  ariaLabel="Copy Python source code"
                />
                <PythonSourceCode code={pythonCodeText} />
              </details>
            ) : null}
          </section>
        ) : null}
      </div>
      <ConfirmDialog
        open={stopDialogOpen}
        title="Stop live trading?"
        message="Open positions will not be closed automatically; only the live runner stops."
        confirmLabel={stopping ? 'Stopping…' : 'Stop live'}
        icon="stop_circle"
        busy={stopping}
        danger
        onCancel={() => {
          if (!stopping) setStopDialogOpen(false);
        }}
        onConfirm={() => void stopLive()}
      />
      <ConfirmDialog
        open={deleteDialogOpen}
        title="Delete live deployment?"
        message={`Delete "${title}"? This removes its saved stream and orders.`}
        confirmLabel={deleting ? 'Deleting…' : 'Delete'}
        busy={deleting}
        danger
        onCancel={() => {
          if (!deleting) setDeleteDialogOpen(false);
        }}
        onConfirm={() => void deleteLiveRun()}
      />
    </div>
  );
}
