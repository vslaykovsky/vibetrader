import { useCallback, useEffect, useMemo, useState } from 'react';
import { formatDistanceStrict } from 'date-fns';
import { Link, useLocation, useNavigate } from 'react-router-dom';
import { randomUUID } from '../randomUUID.js';
import { useAuth } from '../AuthContext';
import { useTheme } from '../ThemeContext';
import { ProfileMenu } from '../ProfileMenu';

const API_BASE_URL =
  import.meta.env.VITE_API_BASE_URL ||
    (import.meta.env.PROD ? '/api' : 'http://localhost:8080');

const THREAD_PREVIEW_LIMIT = 10;

function fmtTime(iso) {
  if (typeof iso !== 'string' || !iso.trim()) return '';
  const ms = Date.parse(iso);
  if (!Number.isFinite(ms)) return iso;
  return new Date(ms).toLocaleString();
}

function threadTitle(t) {
  const n = typeof t?.strategy_name === 'string' ? t.strategy_name.trim() : '';
  if (n && n !== 'unknown strategy') return n;
  return 'Untitled strategy';
}

function liveStrategyTitle(r, threadsByThreadId) {
  const n = typeof r?.strategy_name === 'string' ? r.strategy_name.trim() : '';
  if (n && n !== 'unknown strategy') return n;
  const tid = String(r?.thread_id || '').trim();
  const t = tid && threadsByThreadId ? threadsByThreadId.get(tid) : null;
  if (t) return threadTitle(t);
  return 'Untitled strategy';
}

function liveDeploymentStopped(status) {
  const s = String(status || '').toLowerCase();
  return s === 'stopped' || s === 'failure' || s === 'error' || s === 'failed';
}

function LiveDeploymentStatusIcon({ status }) {
  const stopped = liveDeploymentStopped(status);
  const icon = stopped ? 'stop_circle' : 'motion_play';
  const label = stopped ? 'Stopped' : 'Running';
  return (
    <span
      className={`dashboard-live-status dashboard-live-status--${stopped ? 'stopped' : 'running'}`}
      aria-label={label}
      title={label}
    >
      <span className="home-ms" aria-hidden>
        {icon}
      </span>
    </span>
  );
}

function liveAccountLabel(r) {
  const lab = typeof r?.alpaca_account_label === 'string' ? r.alpaca_account_label.trim() : '';
  if (lab) return lab;
  const mode = String(r?.mode || '').toLowerCase();
  if (mode === 'paper') return 'Paper';
  if (mode === 'live') return 'Live';
  return '—';
}

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

function LiveRunDuration({ status, createdAt, updatedAt }) {
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
    <span className="dashboard-live-duration muted">
      {prefix} {text}
    </span>
  );
}

export function DashboardPage() {
  const navigate = useNavigate();
  const location = useLocation();
  const { user, signOut, getAccessToken } = useAuth();
  const { theme, toggleTheme } = useTheme();
  const [threads, setThreads] = useState([]);
  const [runs, setRuns] = useState([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState('');
  const [threadsExpanded, setThreadsExpanded] = useState(false);

  const authFetch = useCallback(
    async (url, options = {}) => {
      const token = await getAccessToken();
      const headers = { ...options.headers };
      if (token) headers['Authorization'] = `Bearer ${token}`;
      return fetch(url, { ...options, headers });
    },
    [getAccessToken],
  );

  const load = useCallback(async () => {
    setLoading(true);
    setError('');
    try {
      const [tRes, rRes] = await Promise.all([
        authFetch(`${API_BASE_URL}/threads`),
        authFetch(`${API_BASE_URL}/live/runs?limit=100`),
      ]);
      const tPayload = await tRes.json().catch(() => ({}));
      const rPayload = await rRes.json().catch(() => ({}));
      if (!tRes.ok) throw new Error(tPayload.error || `Threads failed (${tRes.status})`);
      if (!rRes.ok) throw new Error(rPayload.error || `Live runs failed (${rRes.status})`);
      setThreads(Array.isArray(tPayload.threads) ? tPayload.threads : []);
      setRuns(Array.isArray(rPayload.runs) ? rPayload.runs : []);
      setThreadsExpanded(false);
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e));
    } finally {
      setLoading(false);
    }
  }, [authFetch]);

  useEffect(() => {
    load();
  }, [load]);

  useEffect(() => {
    const id = String(location.hash || '').replace(/^#/, '').trim();
    if (!id) return undefined;
    const t = window.setTimeout(() => {
      document.getElementById(id)?.scrollIntoView({ behavior: 'smooth', block: 'start' });
    }, 100);
    return () => window.clearTimeout(t);
  }, [location.hash, loading, threads.length, runs.length]);

  const startNewThread = useMemo(
    () => () => navigate(`/strategy/${randomUUID()}`, { state: {} }),
    [navigate],
  );

  const visibleThreads = useMemo(() => {
    if (threadsExpanded || threads.length <= THREAD_PREVIEW_LIMIT) return threads;
    return threads.slice(0, THREAD_PREVIEW_LIMIT);
  }, [threads, threadsExpanded]);

  const threadsByThreadId = useMemo(() => {
    const m = new Map();
    for (const t of threads) {
      const id = String(t.thread_id || '').trim();
      if (id) m.set(id, t);
    }
    return m;
  }, [threads]);

  const email = user?.email || '';

  return (
    <div className="dashboard-page">
      <header className="dashboard-topbar">
        <div className="dashboard-topbar-left">
          <Link to="/" className="app-home-link" aria-label="Go to home page">
            <span className="app-logo">TraderChat</span>
          </Link>
          <span className="dashboard-topbar-sep" aria-hidden>
            /
          </span>
          <span className="dashboard-topbar-crumb">Dashboard</span>
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
        <section className="dashboard-hero">
          <div className="dashboard-hero-copy">
            <h1 className="dashboard-hero-title">Your workspace</h1>
            <p className="dashboard-hero-lead">
              Continue a research thread, watch live deployments, or spin up a fresh strategy chat.
            </p>
          </div>
          <div className="dashboard-hero-actions">
            <Link to="/dashboard/settings" className="dashboard-btn-ghost dashboard-btn-ghost--settings">
              <span className="home-ms dashboard-btn-icon" aria-hidden>
                tune
              </span>
              Settings
            </Link>
            <button type="button" className="dashboard-btn-primary" onClick={startNewThread}>
              <span className="home-ms dashboard-btn-icon" aria-hidden>
                add
              </span>
              Build new strategy
            </button>
          </div>
        </section>

        {error ? <p className="dashboard-banner-error">{error}</p> : null}
        {loading ? <p className="dashboard-loading muted">Loading…</p> : null}

        <div className="dashboard-grid">
          <section className="dashboard-panel" id="your-threads" aria-labelledby="dash-threads-heading">
            <div className="dashboard-panel-head">
              <h2 id="dash-threads-heading" className="dashboard-panel-title">
                Recent threads
              </h2>
              <span className="dashboard-panel-count">{threads.length}</span>
            </div>
            {!loading && threads.length === 0 ? (
              <div className="dashboard-empty">
                <span className="home-ms dashboard-empty-icon" aria-hidden>
                  forum
                </span>
                <p className="dashboard-empty-title">No threads yet</p>
                <p className="dashboard-empty-text muted">Start a strategy thread to see it listed here.</p>
                <button type="button" className="dashboard-btn-primary dashboard-btn-primary--sm" onClick={startNewThread}>
                  Build new strategy
                </button>
              </div>
            ) : null}
            {!loading && threads.length > 0 ? (
              <>
                <ul className="dashboard-thread-list">
                  {visibleThreads.map((t) => {
                    const tid = String(t.thread_id || '').trim();
                    if (!tid) return null;
                    return (
                      <li key={tid}>
                        <Link className="dashboard-thread-row" to={`/strategy/${tid}`}>
                          <div className="dashboard-card-main">
                            <h3 className="dashboard-card-title">{threadTitle(t)}</h3>
                            <p className="dashboard-card-meta muted">
                              Updated {fmtTime(t.latest_created_at)}
                              {Number.isFinite(Number(t.message_count)) ? ` · ${t.message_count} messages` : ''}
                            </p>
                          </div>
                          <span className="dashboard-card-chevron" aria-hidden>
                            →
                          </span>
                        </Link>
                      </li>
                    );
                  })}
                </ul>
                {threads.length > THREAD_PREVIEW_LIMIT ? (
                  <button
                    type="button"
                    className="dashboard-thread-expand"
                    onClick={() => setThreadsExpanded((v) => !v)}
                  >
                    {threadsExpanded ? 'Show less' : 'Show more'}
                  </button>
                ) : null}
              </>
            ) : null}
          </section>

          <section className="dashboard-panel" id="live-deployments" aria-labelledby="dash-live-heading">
            <div className="dashboard-panel-head">
              <h2 id="dash-live-heading" className="dashboard-panel-title">
                Live deployments
              </h2>
              <span className="dashboard-panel-count">{runs.length}</span>
            </div>
            {!loading && runs.length === 0 ? (
              <div className="dashboard-empty">
                <span className="home-ms dashboard-empty-icon" aria-hidden>
                  sensors
                </span>
                <p className="dashboard-empty-title">Nothing live</p>
                <p className="dashboard-empty-text muted">
                  Deploy from a strategy thread when you are ready. Deployments will show up here.
                </p>
              </div>
            ) : null}
            {!loading && runs.length > 0 ? (
              <ul className="dashboard-live-deploy-list">
                {runs.map((r) => {
                  const threadId = String(r.thread_id || '').trim();
                  const runId = String(r.run_id || '').trim();
                  const inner = (
                    <>
                      <LiveDeploymentStatusIcon status={r.status} />
                      <div className="dashboard-live-row-text">
                        <div className="dashboard-live-name-line">
                          <span className="dashboard-live-name">{liveStrategyTitle(r, threadsByThreadId)}</span>
                          {String(r.runner_backend || '').toLowerCase() === 'local' ? (
                            <span className="dashboard-pill dashboard-pill--local">local</span>
                          ) : null}
                        </div>
                        <LiveRunDuration status={r.status} createdAt={r.created_at} updatedAt={r.updated_at} />
                      </div>
                      <span className="dashboard-live-account">
                        <span className="dashboard-live-account-label muted">Account</span>
                        <span className="dashboard-live-account-value">{liveAccountLabel(r)}</span>
                      </span>
                    </>
                  );
                  return (
                    <li key={runId || `${threadId}:${r.created_at}`}>
                      {runId ? (
                        <Link className="dashboard-live-row dashboard-live-row-link" to={`/live/${runId}`}>
                          {inner}
                        </Link>
                      ) : (
                        <div className="dashboard-live-row">{inner}</div>
                      )}
                    </li>
                  );
                })}
              </ul>
            ) : null}
          </section>
        </div>
      </div>
    </div>
  );
}
