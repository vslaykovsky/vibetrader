import { useCallback, useEffect, useMemo, useRef, useState } from 'react';
import { Link, useParams } from 'react-router-dom';
import { formatDistanceStrict } from 'date-fns';
import { useAuth } from '../AuthContext';
import { useTheme } from '../ThemeContext';
import { ProfileMenu } from '../ProfileMenu';
import { renderCharts } from '../strategyChartRenderer.js';
import { attachSyncedCrosshair, attachSyncedTimeScales } from '../lib/lwcSync.js';

const API_BASE_URL =
  import.meta.env.VITE_API_BASE_URL ||
  (import.meta.env.PROD ? '/api' : 'http://localhost:8080');

const LIVE_SSE_KINDS = [
  'output',
  'chart',
  'order_signal',
  'bar',
  'indicator_in',
  'indicator_out',
  'portfolio',
  'renko',
  'startup',
  'status',
  'input',
];

function parseRunDate(iso) {
  if (typeof iso !== 'string' || !iso.trim()) return null;
  const ms = Date.parse(iso);
  if (!Number.isFinite(ms)) return null;
  return new Date(ms);
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

function chartDedupeKey(spec, idx) {
  if (!spec || typeof spec !== 'object') return `anon:${idx}`;
  const t = typeof spec.title === 'string' ? spec.title.trim() : '';
  const ty = typeof spec.type === 'string' ? spec.type : '';
  if (t) return `${ty}:${t}`;
  return `${ty}:i:${idx}`;
}

function fmtUnixTime(u) {
  if (u == null || !Number.isFinite(Number(u))) return '—';
  const ms = Number(u) > 2e10 ? Number(u) : Number(u) * 1000;
  return new Date(ms).toLocaleString();
}

function liveRunCanStop(status) {
  const s = String(status || '').toLowerCase();
  return s !== 'stopping' && s !== 'stopped';
}

function liveOrderIdLabel(t) {
  const a = typeof t?.alpaca_order_id === 'string' ? t.alpaca_order_id.trim() : '';
  if (a) return a;
  const c = typeof t?.client_order_id === 'string' ? t.client_order_id.trim() : '';
  if (c) return c;
  return '—';
}

export function LiveRunStreamPage() {
  const { runId = '' } = useParams();
  const { user, signOut, getAccessToken } = useAuth();
  const { theme, toggleTheme } = useTheme();
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

  const chartsMountRef = useRef(null);
  const chartMapRef = useRef(new Map());
  const lastEventIdRef = useRef(0);

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
      const eid = Number(parsed?.id);
      if (Number.isFinite(eid) && eid > lastEventIdRef.current) lastEventIdRef.current = eid;
      const kind = parsed?.kind;
      const payload = parsed?.payload && typeof parsed.payload === 'object' ? parsed.payload : {};

      if (kind === 'chart' && payload.chart) {
        const k = chartDedupeKey(payload.chart, chartMapRef.current.size);
        chartMapRef.current.set(k, payload.chart);
        bumpCharts();
        return;
      }
      if (kind === 'output' && Array.isArray(payload.output)) {
        payload.output.forEach((item, i) => {
          if (item && item.kind === 'chart' && item.chart) {
            chartMapRef.current.set(chartDedupeKey(item.chart, i), item.chart);
          }
        });
        bumpCharts();
        return;
      }
      if (kind === 'order_signal') {
        setTrades((prev) => [
          ...prev,
          {
            rowKey: typeof parsed?.id === 'number' ? parsed.id : `${Date.now()}-${prev.length}`,
            unixtime: parsed?.unixtime,
            ticker: payload.ticker,
            direction: payload.direction,
            deposit_ratio: payload.deposit_ratio,
            alpaca_order_id: payload.alpaca_order_id,
            client_order_id: payload.client_order_id,
          },
        ]);
        return;
      }
      if (kind === 'status' && typeof payload.status === 'string' && payload.status.trim()) {
        setDbRow((prev) => (prev && typeof prev === 'object' ? { ...prev, status: payload.status.trim() } : prev));
      }
    },
    [bumpCharts],
  );

  useEffect(() => {
    let evtSource;
    let cancelled = false;
    lastEventIdRef.current = 0;
    chartMapRef.current = new Map();
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
    const charts = [...chartMapRef.current.values()];
    if (charts.length === 0) {
      mount.innerHTML = '';
      setChartError('');
      return undefined;
    }
    const dataJson = { charts };
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
  }, [chartEpoch, runId]);

  const displayStatus = dbRow?.status ?? runMeta?.status ?? '';
  const createdAt = dbRow?.created_at ?? runMeta?.created_at;
  const updatedAt = dbRow?.updated_at ?? runMeta?.updated_at;

  const backtestHref = useMemo(() => {
    const tid = String(runMeta?.thread_id || '').trim();
    const dep = String(runMeta?.deployed_from_run_id || '').trim();
    if (tid && dep) return `/strategy/${tid}#${dep}`;
    if (tid) return `/strategy/${tid}`;
    return '';
  }, [runMeta]);

  const stopLive = useCallback(async () => {
    const rid = String(runId || '').trim();
    if (!rid) return;
    const ok = window.confirm(
      'Stop live trading for this deployment? Open positions will not be closed automatically; only the live runner stops.',
    );
    if (!ok) return;
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
      await loadMeta();
    } catch (e) {
      setActionError(e instanceof Error ? e.message : String(e));
    } finally {
      setStopping(false);
    }
  }, [authFetch, runId, loadMeta]);

  const title = strategyTitleFromRun(runMeta);
  const accountDisp = liveAccountLabel(runMeta || {});
  const showStop = liveRunCanStop(displayStatus);
  const isLocal = String(runMeta?.runner_backend || '').toLowerCase() === 'local';

  return (
    <div className="dashboard-page live-run-page">
      <header className="dashboard-topbar">
        <div className="dashboard-topbar-left">
          <Link to="/dashboard" className="app-home-link" aria-label="Go to dashboard">
            <span className="app-logo">TraderChat</span>
          </Link>
          <span className="dashboard-topbar-sep" aria-hidden>
            /
          </span>
          <span className="dashboard-topbar-crumb">Live stream</span>
        </div>
        <div className="dashboard-topbar-right">
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
            <Link className="dashboard-btn-ghost" to="/dashboard#live-deployments">
              <span className="home-ms dashboard-btn-icon" aria-hidden>
                arrow_back
              </span>
              Dashboard
            </Link>
            {backtestHref ? (
              <Link className="dashboard-link-btn" to={backtestHref}>
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
                onClick={() => void stopLive()}
              >
                {stopping ? 'Stopping…' : 'Stop live'}
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
            <h2 className="dashboard-panel-title">Trades</h2>
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
                    <th>Side</th>
                    <th>Fraction</th>
                    <th>Order ID</th>
                  </tr>
                </thead>
                <tbody>
                  {[...trades].reverse().map((t) => (
                    <tr key={t.rowKey}>
                      <td>{fmtUnixTime(t.unixtime)}</td>
                      <td>{t.ticker ?? '—'}</td>
                      <td>{t.direction ?? '—'}</td>
                      <td>{t.deposit_ratio != null ? String(t.deposit_ratio) : '—'}</td>
                      <td className="live-run-order-id-cell">{liveOrderIdLabel(t)}</td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          )}
        </section>
      </div>
    </div>
  );
}
