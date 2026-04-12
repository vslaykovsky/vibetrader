import { useCallback, useEffect, useMemo, useRef, useState } from 'react';
import { Link, useNavigate, useParams } from 'react-router-dom';
import ReactMarkdown from 'react-markdown';
import remarkGfm from 'remark-gfm';
import { randomUUID } from '../randomUUID.js';
import { renderCharts } from '../strategyChartRenderer.js';
import { useAuth } from '../AuthContext';

function ChatProcessingSpinner({ label }) {
  const text =
    typeof label === 'string' && label.trim().length > 0 ? label.trim() : 'Working…';
  return (
    <div
      className="message message-assistant chat-processing"
      role="status"
      aria-live="polite"
      aria-label="Agent is responding"
    >
      <div className="message-header-row">
        <span className="message-role">Agent</span>
      </div>
      <div className="chat-spinner-row">
        <span className="chat-spinner" aria-hidden />
        <span className="chat-processing-label">{text}</span>
      </div>
    </div>
  );
}

const API_BASE_URL =
  import.meta.env.VITE_API_BASE_URL ||
  (import.meta.env.PROD ? '/api' : 'http://localhost:8080');

function formatThreadLabel(threadId) {
  const t = String(threadId || '').trim();
  if (!t) return 'Unknown thread';
  return t.length > 12 ? `${t.slice(0, 8)}…${t.slice(-4)}` : t;
}

function threadDisplayName(thread) {
  const n = typeof thread?.strategy_name === 'string' ? thread.strategy_name.trim() : '';
  if (n) return n;
  return 'Untitled strategy';
}

function parseIsoTime(value) {
  if (typeof value !== 'string') return null;
  let t = value.trim();
  if (!t) return null;
  if (/^\d{4}-\d{2}-\d{2}T/.test(t) && !/[zZ]|[+-]\d{2}:\d{2}$/.test(t)) {
    t = `${t}Z`;
  }
  t = t.replace(/(\.\d{3})\d+([zZ]|[+-]\d{2}:\d{2})$/, '$1$2');
  const ms = Date.parse(t);
  return Number.isFinite(ms) ? ms : null;
}

function dateKeyFromIso(value) {
  if (typeof value !== 'string') return null;
  const t = value.trim();
  if (!t) return null;
  const m = t.match(/^(\d{4}-\d{2}-\d{2})/);
  return m ? m[1] : null;
}

function isoDateTodayKey() {
  const d = new Date();
  const yyyy = d.getFullYear();
  const mm = String(d.getMonth() + 1).padStart(2, '0');
  const dd = String(d.getDate()).padStart(2, '0');
  return `${yyyy}-${mm}-${dd}`;
}

function formatReplyDurationMs(ms) {
  if (typeof ms !== 'number' || !Number.isFinite(ms) || ms < 0) {
    return '';
  }
  if (ms < 1000) {
    return `${Math.round(ms)}ms`;
  }
  const s = ms / 1000;
  if (s < 60) {
    return `${s < 10 ? s.toFixed(1) : Math.round(s)}s`;
  }
  const m = Math.floor(s / 60);
  const rs = Math.round(s - m * 60);
  return `${m}m ${rs}s`;
}

function MessageBubble({
  message,
  activeRunId,
  onViewRun,
  onRevertRun,
  revertDisabled,
  showViewStrategy,
  onViewStrategy,
}) {
  const isAssistant = message.role === 'assistant';
  const hasRunId = isAssistant && message.run_id;
  const isActive = hasRunId && message.run_id === activeRunId;
  const replyMs =
    isAssistant &&
    typeof message.reply_duration_ms === 'number' &&
    Number.isFinite(message.reply_duration_ms) &&
    message.reply_duration_ms >= 0
      ? message.reply_duration_ms
      : null;
  const handleClick = hasRunId
    ? () => {
        if (showViewStrategy) {
          onViewStrategy?.(message.run_id);
        } else {
          onViewRun(message.run_id);
        }
      }
    : undefined;
  return (
    <div
      className={`message message-${message.role}${hasRunId ? ' message-clickable' : ''}${isActive ? ' message-active-run' : ''}`}
      title={hasRunId ? (showViewStrategy ? 'Tap to view strategy' : 'Click to view strategy output') : undefined}
      onClick={handleClick}
      role={hasRunId ? 'button' : undefined}
      tabIndex={hasRunId ? 0 : undefined}
      onKeyDown={
        hasRunId
          ? (e) => {
              if (e.key === 'Enter' || e.key === ' ') {
                e.preventDefault();
                handleClick?.();
              }
            }
          : undefined
      }
    >
      <div className="message-header-row">
        <span className="message-role">{isAssistant ? 'Agent' : 'You'}</span>
        {replyMs != null || hasRunId ? (
          <div className="message-header-end">
            {replyMs != null ? (
              <span
                className="message-role-reply-time"
                title={`${replyMs} ms`}
                aria-label={`Reply took ${formatReplyDurationMs(replyMs)}`}
              >
                {formatReplyDurationMs(replyMs)}
              </span>
            ) : null}
            {hasRunId ? (
              <button
                type="button"
                className="message-revert-button"
                disabled={Boolean(revertDisabled)}
                onClick={(e) => {
                  e.preventDefault();
                  e.stopPropagation();
                  onRevertRun?.(message.run_id);
                }}
                title="Revert thread to this point"
                aria-label="Revert thread to this point"
              >
                <svg viewBox="0 0 24 24" fill="none" aria-hidden="true">
                  <path
                    d="M8.5 12l-4.5-4.5L8.5 3"
                    stroke="currentColor"
                    strokeWidth="2.8"
                    strokeLinecap="round"
                    strokeLinejoin="round"
                  />
                  <path
                    d="M4 7.5h11.5c3.9 0 7 3.1 7 7s-3.1 7-7 7H12"
                    stroke="currentColor"
                    strokeWidth="2.8"
                    strokeLinecap="round"
                    strokeLinejoin="round"
                  />
                </svg>
              </button>
            ) : null}
          </div>
        ) : null}
      </div>
      {isAssistant ? (
        <div className="message-markdown">
          <ReactMarkdown remarkPlugins={[remarkGfm]}>{message.content || ''}</ReactMarkdown>
        </div>
      ) : (
        <p>{message.content}</p>
      )}
      {showViewStrategy && hasRunId ? (
        <div className="message-view-strategy-row">
          <button
            type="button"
            className="message-view-strategy-button"
            onClick={(e) => {
              e.preventDefault();
              e.stopPropagation();
              onViewStrategy?.(message.run_id);
            }}
          >
            View strategy
          </button>
        </div>
      ) : null}
    </div>
  );
}

function hasRenderableChartOutput(output) {
  if (!output || typeof output !== 'object') {
    return false;
  }
  let chartData = output['data.json'];
  if (typeof chartData === 'string') {
    try {
      chartData = JSON.parse(chartData);
    } catch {
      return false;
    }
  }
  return (
    chartData != null &&
    typeof chartData === 'object' &&
    Array.isArray(chartData.charts) &&
    chartData.charts.length > 0
  );
}

function strategyCliDescriptionFromOutput(output) {
  if (!output || typeof output !== 'object') {
    return undefined;
  }
  const raw = output.strategy_cli_description;
  if (typeof raw !== 'string') {
    return undefined;
  }
  const t = raw.trim();
  return t.length ? t : undefined;
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

function strategyNameFromOutput(output) {
  if (!output || typeof output !== 'object') {
    return '';
  }
  let data = output['data.json'];
  if (data == null) {
    return '';
  }
  if (typeof data === 'string') {
    try {
      data = JSON.parse(data);
    } catch {
      return '';
    }
  }
  if (typeof data !== 'object' || data === null) {
    return '';
  }
  const name = data.strategy_name ?? data.params?.strategy_name;
  return typeof name === 'string' && name.trim() ? name.trim() : '';
}


function attachSyncedTimeScales(charts) {
  if (charts.length < 2) {
    return undefined;
  }
  let syncing = false;
  const subscriptions = charts.map((chart) => {
    const handler = (logicalRange) => {
      if (syncing || logicalRange === null) {
        return;
      }
      syncing = true;
      try {
        for (const other of charts) {
          if (other !== chart) {
            other.timeScale().setVisibleLogicalRange(logicalRange);
          }
        }
      } finally {
        syncing = false;
      }
    };
    chart.timeScale().subscribeVisibleLogicalRangeChange(handler);
    return { chart, handler };
  });
  return () => {
    for (const { chart, handler } of subscriptions) {
      chart.timeScale().unsubscribeVisibleLogicalRangeChange(handler);
    }
  };
}

export function StrategyPage() {
  const { threadId } = useParams();
  const navigate = useNavigate();
  const { user, signOut, getAccessToken } = useAuth();
  const signedInUserId = user?.id ?? null;
  const [messages, setMessages] = useState([]);
  const [canvas, setCanvas] = useState({});
  const [draft, setDraft] = useState('');
  const [loading, setLoading] = useState(true);
  const [submitting, setSubmitting] = useState(false);
  const [serverJob, setServerJob] = useState({ status: null, statusText: '' });
  const [error, setError] = useState('');
  const [threads, setThreads] = useState([]);
  const [threadsError, setThreadsError] = useState('');
  const [sidebarOpen, setSidebarOpen] = useState(false);
  const [deletingThread, setDeletingThread] = useState(false);
  const chatEndRef = useRef(null);
  const chatFormRef = useRef(null);
  const emptyThreadPrompts = useMemo(
    () => [
      'What can you do?',
      "Let's create a SMA-based strategy for SPY",
      'What are ways to account for volatility changes?',
    ],
    [],
  );
  const optimisticUserContentRef = useRef(null);
  const chartsMountRef = useRef(null);
  const [chartError, setChartError] = useState('');
  const [viewingRunId, setViewingRunId] = useState(null);
  const [historicalCanvas, setHistoricalCanvas] = useState(null);
  const [reverting, setReverting] = useState(false);
  const [isNarrow, setIsNarrow] = useState(false);
  const [mobileCanvasOpen, setMobileCanvasOpen] = useState(false);

  const authFetch = useCallback(async (url, options = {}) => {
    const token = await getAccessToken();
    const headers = { ...options.headers };
    if (token) headers['Authorization'] = `Bearer ${token}`;
    return fetch(url, { ...options, headers });
  }, [getAccessToken]);

  async function handleViewRun(runId) {
    if (runId === viewingRunId) {
      setViewingRunId(null);
      setHistoricalCanvas(null);
      return;
    }
    try {
      const response = await authFetch(
        `${API_BASE_URL}/strategy?id=${encodeURIComponent(runId)}`,
      );
      if (!response.ok) throw new Error('Failed to load strategy run');
      const payload = await response.json();
      setHistoricalCanvas(payload.canvas || {});
      setViewingRunId(runId);
    } catch (err) {
      setError(err.message);
    }
  }

  async function handleViewStrategy(runId) {
    await handleViewRun(runId);
    setMobileCanvasOpen(true);
  }

  async function handleRevertRun(runId) {
    if (!threadId || !runId || reverting) {
      return;
    }
    setViewingRunId(null);
    setHistoricalCanvas(null);
    const ok = window.confirm(
      'Revert this thread to this agent message? This will delete all later strategy runs for this thread.',
    );
    if (!ok) return;
    setReverting(true);
    setError('');
    try {
      const response = await authFetch(
        `${API_BASE_URL}/threads/${encodeURIComponent(threadId)}/revert`,
        {
          method: 'POST',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({ run_id: runId }),
        },
      );
      const payload = await response.json().catch(() => ({}));
      if (!response.ok) {
        throw new Error(payload.error || 'Failed to revert thread');
      }

      const refreshed = await authFetch(
        `${API_BASE_URL}/strategy?thread_id=${encodeURIComponent(threadId)}`,
      );
      if (!refreshed.ok) {
        throw new Error('Reverted, but failed to reload thread');
      }
      const next = await refreshed.json();
      setMessages(next.messages || []);
      setCanvas(next.canvas || {});
      setServerJob({
        status: next.status ?? null,
        statusText: next.status_text || '',
      });
    } catch (err) {
      setError(err instanceof Error ? err.message : String(err));
    } finally {
      setReverting(false);
    }
  }

  async function refreshThreads(signal) {
    setThreadsError('');
    const response = await authFetch(`${API_BASE_URL}/threads`, signal ? { signal } : undefined);
    if (!response.ok) {
      throw new Error(`Failed to load threads (${response.status})`);
    }
    const payload = await response.json().catch(() => ({}));
    const list = Array.isArray(payload.threads) ? payload.threads : [];
    setThreads(list);
    return list;
  }

  async function handleDeleteThread() {
    if (!threadId || deletingThread) {
      return;
    }
    if (viewingRunId) {
      return;
    }
    const ok = window.confirm('Delete this strategy thread? This cannot be undone.');
    if (!ok) return;

    setDeletingThread(true);
    setError('');
    try {
      const response = await authFetch(
        `${API_BASE_URL}/threads/${encodeURIComponent(threadId)}`,
        { method: 'DELETE' },
      );
      const payload = await response.json().catch(() => ({}));
      if (!response.ok) {
        throw new Error(payload.error || 'Failed to delete strategy');
      }
      const list = await refreshThreads();
      const sorted = [...list].sort((a, b) => {
        const at = parseIsoTime(a?.latest_created_at) ?? -1;
        const bt = parseIsoTime(b?.latest_created_at) ?? -1;
        return bt - at;
      });
      const next = sorted.find((t) => t?.thread_id && t.thread_id !== threadId)?.thread_id;
      navigate(`/strategy/${next || randomUUID()}`);
    } catch (err) {
      setError(err instanceof Error ? err.message : String(err));
    } finally {
      setDeletingThread(false);
    }
  }

  useEffect(() => {
    if (!signedInUserId) {
      setThreads([]);
      setThreadsError('');
      return undefined;
    }
    const controller = new AbortController();

    async function loadThreads() {
      try {
        await refreshThreads(controller.signal);
      } catch (loadError) {
        if (loadError.name !== 'AbortError') {
          setThreadsError(loadError instanceof Error ? loadError.message : String(loadError));
        }
      }
    }

    async function loadThread() {
      try {
        setLoading(true);
        setError('');
        const response = await authFetch(
          `${API_BASE_URL}/strategy?thread_id=${encodeURIComponent(threadId)}`,
          { signal: controller.signal },
        );
        if (!response.ok) {
          throw new Error(`Failed to load thread ${threadId}`);
        }
        const payload = await response.json();
        setMessages(payload.messages || []);
        setCanvas(payload.canvas || {});
        setServerJob({
          status: payload.status ?? null,
          statusText: payload.status_text || '',
        });
      } catch (loadError) {
        if (loadError.name !== 'AbortError') {
          setError(loadError.message);
        }
      } finally {
        setLoading(false);
      }
    }

    loadThreads();
    loadThread();
    return () => controller.abort();
  }, [threadId, signedInUserId]);

  useEffect(() => {
    if (typeof window === 'undefined' || typeof window.matchMedia !== 'function') {
      return undefined;
    }
    const mq = window.matchMedia('(max-width: 980px)');
    const update = () => {
      const narrow = Boolean(mq.matches);
      setIsNarrow(narrow);
      if (!narrow) {
        setMobileCanvasOpen(false);
      }
    };
    update();
    if (typeof mq.addEventListener === 'function') {
      mq.addEventListener('change', update);
      return () => mq.removeEventListener('change', update);
    }
    mq.addListener(update);
    return () => mq.removeListener(update);
  }, []);

  useEffect(() => {
    if (!sidebarOpen || !signedInUserId) {
      return undefined;
    }
    const controller = new AbortController();
    (async () => {
      try {
        await refreshThreads(controller.signal);
      } catch (loadError) {
        if (loadError.name !== 'AbortError') {
          setThreadsError(loadError instanceof Error ? loadError.message : String(loadError));
        }
      }
    })();
    return () => controller.abort();
  }, [sidebarOpen, signedInUserId]);

  useEffect(() => {
    if (serverJob.status !== 'running') {
      return undefined;
    }

    let evtSource;
    let cancelled = false;

    (async () => {
      const token = await getAccessToken();
      if (cancelled) return;
      const url = new URL(`${API_BASE_URL}/strategy/stream`, window.location.origin);
      url.searchParams.set('thread_id', threadId);
      if (token) url.searchParams.set('access_token', token);

      evtSource = new EventSource(url.toString());

      evtSource.onmessage = (event) => {
        try {
          const payload = JSON.parse(event.data);
          setMessages(payload.messages || []);
          setCanvas(payload.canvas || {});
          setServerJob({
            status: payload.status ?? null,
            statusText: payload.status_text || '',
          });
          if (payload.status !== 'running') {
            setSubmitting(false);
            evtSource.close();
          }
        } catch {
          /* ignore malformed events */
        }
      };

      evtSource.onerror = () => {
        evtSource.close();
        setSubmitting(false);
      };
    })();

    return () => {
      cancelled = true;
      evtSource?.close();
    };
  }, [threadId, serverJob.status, getAccessToken]);

  useEffect(() => {
    chatEndRef.current?.scrollIntoView({ behavior: 'smooth' });
  }, [messages, submitting, serverJob.status]);

  const displayCanvas = viewingRunId ? historicalCanvas : canvas;
  const displayOutput = displayCanvas ? displayCanvas.output : undefined;

  useEffect(() => {
    const mount = chartsMountRef.current;
    const output = displayOutput;
    setChartError('');
    if (!mount || !output) {
      if (mount) {
        mount.innerHTML = '';
      }
      return undefined;
    }
    let chartData = output['data.json'];
    if (typeof chartData === 'string') {
      try {
        chartData = JSON.parse(chartData);
      } catch {
        setChartError('Could not parse data.json');
        mount.innerHTML = '';
        return undefined;
      }
    }
    if (chartData === null || typeof chartData !== 'object') {
      mount.innerHTML = '';
      return undefined;
    }
    if (!Array.isArray(chartData.charts) || chartData.charts.length === 0) {
      mount.innerHTML = '';
      return undefined;
    }
    mount.innerHTML = '';
    const root = document.createElement('div');
    root.className = 'strategy-charts-root';
    mount.appendChild(root);

    let detachSync;
    try {
      const { lwCharts } = renderCharts(root, chartData);
      detachSync = attachSyncedTimeScales(lwCharts);
    } catch (err) {
      setChartError(err instanceof Error ? err.message : String(err));
    }

    return () => {
      detachSync?.();
      mount.innerHTML = '';
    };
  }, [displayOutput]);

  async function handleSubmit(event) {
    event.preventDefault();
    const message = draft.trim();
    if (!message || submitting || serverJob.status === 'running') {
      return;
    }

    setSubmitting(true);
    setError('');
    setViewingRunId(null);
    setHistoricalCanvas(null);
    optimisticUserContentRef.current = message;
    setMessages((prev) => [...prev, { role: 'user', content: message }]);
    setDraft('');

    let payload = {};
    try {
      const response = await authFetch(`${API_BASE_URL}/strategy`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ thread_id: threadId, message }),
      });

      payload = await response.json().catch(() => ({}));
      if (!response.ok) {
        if (payload.messages) {
          optimisticUserContentRef.current = null;
          setMessages(payload.messages);
        } else {
          const sent = optimisticUserContentRef.current;
          optimisticUserContentRef.current = null;
          if (sent) {
            setMessages((prev) => {
              const last = prev[prev.length - 1];
              if (last?.role === 'user' && last?.content === sent) {
                return prev.slice(0, -1);
              }
              return prev;
            });
            setDraft(sent);
          }
        }
        if (payload.canvas) {
          setCanvas(payload.canvas || {});
        }
        if (payload.status != null || payload.status_text != null) {
          setServerJob({
            status: payload.status ?? null,
            statusText: payload.status_text || '',
          });
        }
        setSubmitting(false);
        throw new Error(payload.error || 'Failed to send message');
      }

      optimisticUserContentRef.current = null;
      setMessages(payload.messages || []);
      setCanvas(payload.canvas || {});
      setServerJob({
        status: payload.status ?? null,
        statusText: payload.status_text || '',
      });
      if (payload.status !== 'running') {
        setSubmitting(false);
      }
    } catch (submitError) {
      setSubmitting(false);
      const sent = optimisticUserContentRef.current;
      if (sent) {
        optimisticUserContentRef.current = null;
        setMessages((prev) => {
          const last = prev[prev.length - 1];
          if (last?.role === 'user' && last?.content === sent) {
            return prev.slice(0, -1);
          }
          return prev;
        });
        setDraft(sent);
      }
      setError(submitError.message);
    }
  }

  const showProcessing = submitting || serverJob.status === 'running';
  const processingLabel =
    serverJob.status === 'running'
      ? serverJob.statusText?.trim() || 'Working…'
      : submitting
        ? 'Sending…'
        : 'Working…';

  const sortedThreads = [...threads].sort((a, b) => {
    const at = parseIsoTime(a?.latest_created_at) ?? -1;
    const bt = parseIsoTime(b?.latest_created_at) ?? -1;
    return bt - at;
  });

  const todayKey = isoDateTodayKey();
  const groupedThreads = sortedThreads.reduce((acc, t) => {
    const key = dateKeyFromIso(t?.latest_created_at) || 'Unknown date';
    if (!acc[key]) {
      acc[key] = [];
    }
    acc[key].push(t);
    return acc;
  }, {});
  const groupKeys = Object.keys(groupedThreads).sort((a, b) => {
    if (a === 'Unknown date') return 1;
    if (b === 'Unknown date') return -1;
    return b.localeCompare(a);
  });

  const output = displayOutput;
  const strategyName = strategyNameFromOutput(output);
  const cliDescriptionText = strategyCliDescriptionFromOutput(output);
  const showCliDescription = cliDescriptionText != null;
  const paramsJsonText = paramsJsonFromOutput(output);
  const showParamsPanel = paramsJsonText != null;
  const hasAnyCanvasData =
    showCliDescription ||
    showParamsPanel ||
    hasRenderableChartOutput(output);
  const currentThreadMeta = useMemo(
    () => threads.find((t) => t?.thread_id && t.thread_id === threadId) || null,
    [threads, threadId],
  );
  const strategyAvailable =
    (!loading && Array.isArray(messages) && messages.length > 0) ||
    (Number.isFinite(Number(currentThreadMeta?.message_count)) &&
      Number(currentThreadMeta?.message_count) > 0);
  const deployDisabled = loading || showProcessing || !strategyAvailable;
  const deployTitle = deployDisabled ? 'Strategy not available yet' : 'Deploy live';

  return (
    <main className={`layout${isNarrow ? ' layout-narrow' : ''}${mobileCanvasOpen ? ' is-mobile-canvas-open' : ''}`}>
      {sidebarOpen ? (
        <div
          className="sidebar-backdrop"
          role="presentation"
          onClick={() => setSidebarOpen(false)}
        />
      ) : null}
      <aside className={`sidebar-drawer${sidebarOpen ? ' is-open' : ''}`} aria-label="Strategies">
        <div className="sidebar-header">
          <div className="sidebar-title">Strategies</div>
          <button
            type="button"
            className="sidebar-close"
            onClick={() => setSidebarOpen(false)}
            aria-label="Close sidebar"
          >
            ×
          </button>
        </div>
        <nav className="sidebar-list" aria-label="Thread list">
          {threadsError ? <div className="sidebar-empty muted">{threadsError}</div> : null}
          {!threadsError && sortedThreads.length === 0 ? (
            <div className="sidebar-empty muted">No threads yet.</div>
          ) : null}
          {!threadsError && sortedThreads.length > 0 ? (
            groupKeys.map((key) => {
              const label = key === todayKey ? 'Today' : key;
              return (
                <div key={key} className="sidebar-group">
                  <div className="sidebar-group-title">{label}</div>
                  <div className="sidebar-group-items">
                    {groupedThreads[key].map((t) => {
                      const tid = t?.thread_id;
                      const active = typeof tid === 'string' && tid === threadId;
                      return (
                        <button
                          key={tid}
                          type="button"
                          className={`sidebar-item${active ? ' is-active' : ''}`}
                          onClick={() => {
                            if (typeof tid === 'string' && tid.trim()) {
                              setSidebarOpen(false);
                              navigate(`/strategy/${tid}`);
                            }
                          }}
                          title={typeof tid === 'string' ? tid : undefined}
                        >
                          <div className="sidebar-item-badge" aria-label="Message count">
                            <span className="sidebar-item-badge-icon" aria-hidden>
                              <svg viewBox="0 0 24 24" fill="none">
                                <path
                                  d="M7.5 18.25v2.6c0 .38.43.6.74.38l3.07-2.16h5.9c2.5 0 4.54-2.04 4.54-4.54V8.55c0-2.5-2.04-4.54-4.54-4.54H7.74C5.24 4.01 3.2 6.05 3.2 8.55v5.98c0 2.18 1.55 4 3.6 4.4Z"
                                  stroke="currentColor"
                                  strokeWidth="1.8"
                                  strokeLinejoin="round"
                                />
                              </svg>
                            </span>
                            <span className="sidebar-item-badge-count">
                              {Number.isFinite(Number(t?.message_count)) ? Number(t?.message_count) : 0}
                            </span>
                          </div>
                          <div className="sidebar-item-title">{threadDisplayName(t)}</div>
                          <div className="sidebar-item-subtitle">
                            {t?.status === 'running'
                              ? (t?.status_text?.trim() || 'Running…')
                              : ''}
                          </div>
                        </button>
                      );
                    })}
                  </div>
                </div>
              );
            })
          ) : null}
        </nav>
      </aside>
      <section className="chat-panel">
        <header className="chat-header">
          <div className="chat-header-top">
            <div>
              <div className="chat-brand">
                <button
                  type="button"
                  className="sidebar-toggle"
                  onClick={() => setSidebarOpen(true)}
                  aria-label="Open sidebar"
                >
                  ☰
                </button>
                <Link to="/" className="app-home-link" aria-label="Go to homepage">
                  <span className="app-logo">TraderChat</span>
                  <span className="app-beta-badge" aria-label="Beta">
                    Beta
                  </span>
                </Link>
              </div>
            </div>
            <div className="chat-header-actions">
              <button
                type="button"
                className="button-new-thread"
                onClick={() => navigate(`/strategy/${randomUUID()}`)}
                aria-label="New strategy"
                title="New strategy"
              >
                <svg viewBox="0 0 24 24" fill="none" aria-hidden>
                  <path
                    d="M12 5v14M5 12h14"
                    stroke="currentColor"
                    strokeWidth="2"
                    strokeLinecap="round"
                  />
                </svg>
                <span className="sr-only">New strategy</span>
              </button>
              {user && (
                <div className="auth-user-area">
                  {user.user_metadata?.avatar_url && (
                    <img className="auth-avatar" src={user.user_metadata.avatar_url} alt="" />
                  )}
                  <button type="button" className="auth-btn auth-btn-secondary" onClick={signOut}>
                    Sign out
                  </button>
                </div>
              )}
            </div>
          </div>
        </header>

        <div className="chat-stream">
          {loading ? <p className="status">Loading thread…</p> : null}
          {messages.map((message, index) => (
            <MessageBubble
              key={`${message.role}-${index}`}
              message={message}
              activeRunId={viewingRunId}
              onViewRun={handleViewRun}
              onViewStrategy={handleViewStrategy}
              onRevertRun={handleRevertRun}
              revertDisabled={reverting || showProcessing}
              showViewStrategy={isNarrow && !mobileCanvasOpen && hasAnyCanvasData}
            />
          ))}
          {showProcessing ? <ChatProcessingSpinner label={processingLabel} /> : null}
          <div ref={chatEndRef} />
        </div>

        <form ref={chatFormRef} className="chat-input" onSubmit={handleSubmit}>
          {!loading && messages.length === 0 ? (
            <section className="home-prompts chat-suggested-prompts" aria-label="Suggested prompts">
              <ul className="home-prompt-list">
                {emptyThreadPrompts.map((p) => (
                  <li key={p} className="home-prompt-item">
                    <button
                      type="button"
                      className="home-prompt"
                      disabled={showProcessing}
                      onClick={() => {
                        setDraft(p);
                        setTimeout(() => chatFormRef.current?.requestSubmit(), 0);
                      }}
                    >
                      {p}
                    </button>
                  </li>
                ))}
              </ul>
            </section>
          ) : null}
          <label htmlFor="message" className="sr-only">
            Message
          </label>
          <div className="chat-compose">
            <textarea
              id="message"
              placeholder="Describe your strategy in your own words..."
              value={draft}
              onChange={(event) => setDraft(event.target.value)}
              onKeyDown={(event) => {
                if (event.key === 'Enter' && !event.shiftKey) {
                  event.preventDefault();
                  chatFormRef.current?.requestSubmit();
                }
              }}
              rows={4}
            />
            <button
              type="submit"
              className="chat-send-button"
              disabled={showProcessing}
              aria-label="Send message"
              title="Send"
            >
              <svg
                xmlns="http://www.w3.org/2000/svg"
                viewBox="0 0 20 20"
                fill="currentColor"
                style={{ transform: 'rotate(90deg)' }}
                aria-hidden
              >
                <path d="M10.894 2.553a1 1 0 00-1.788 0l-7 14a1 1 0 001.169 1.409l5-1.429A1 1 0 009 15.571V11a1 1 0 112 0v4.571a1 1 0 00.725.962l5 1.428a1 1 0 001.17-1.408l-7-14z" />
              </svg>
            </button>
          </div>
          <div className="chat-actions">
            <span className="status chat-actions-status"></span>
          </div>
        </form>
      </section>

      <section className="canvas-panel canvas-panel-charts">
        <header className="canvas-hero">
          <div className="canvas-hero-actions">
            {viewingRunId ? (
              <button
                type="button"
                className="button-back-to-current"
                onClick={() => { setViewingRunId(null); setHistoricalCanvas(null); }}
              >
                Back to current
              </button>
            ) : null}
            <button
              type="button"
              className="button-deploy-live"
              disabled={deployDisabled}
              onClick={() => window.alert('Live trading is not yet available. Stay tuned for updates!')}
              aria-label="Deploy live"
              aria-disabled={deployDisabled}
              title={deployTitle}
            >
              <span aria-hidden>🚀</span>
            </button>
            <button
              type="button"
              className="button-delete-thread"
              onClick={handleDeleteThread}
              disabled={deletingThread || showProcessing || Boolean(viewingRunId)}
              aria-label="Delete strategy"
              title={viewingRunId ? 'Return to current thread to delete' : 'Delete strategy'}
            >
              <svg viewBox="0 0 24 24" fill="none" aria-hidden>
                <path
                  d="M14.74 9l-.346 9m-4.788 0L9.26 9m9.968-3.21c.342.052.682.107 1.022.166m-1.022-.165L18.16 19.673a2.25 2.25 0 0 1-2.244 2.077H8.084a2.25 2.25 0 0 1-2.244-2.077L4.772 5.79m14.456 0a48.108 48.108 0 0 0-3.478-.397m-12 .562c.34-.059.68-.114 1.022-.165m0 0a48.11 48.11 0 0 1 3.478-.397m7.5 0v-.916c0-1.18-.91-2.164-2.09-2.201a51.964 51.964 0 0 0-3.32 0c-1.18.037-2.09 1.022-2.09 2.201v.916m7.5 0a48.667 48.667 0 0 0-7.5 0"
                  stroke="currentColor"
                  strokeWidth="1.7"
                  strokeLinecap="round"
                  strokeLinejoin="round"
                />
              </svg>
            </button>
            {isNarrow ? (
              <button
                type="button"
                className="button-close-canvas"
                onClick={() => setMobileCanvasOpen(false)}
                aria-label="Close strategy view"
                title="Close"
              >
                <svg viewBox="0 0 24 24" fill="none" aria-hidden="true">
                  <path
                    d="M6 6l12 12M18 6L6 18"
                    stroke="currentColor"
                    strokeWidth="2.6"
                    strokeLinecap="round"
                  />
                </svg>
              </button>
            ) : null}
          </div>
          <h2 className="canvas-hero-title">{strategyName || 'Strategy'}</h2>
        </header>
        {showCliDescription ? (
          <article className="canvas-text-block" aria-label="Strategy description">
            <h3 className="canvas-text-block-title">Description</h3>
            <div className="canvas-text-block-body">{cliDescriptionText}</div>
          </article>
        ) : null}
        {showParamsPanel ? (
          <details className="canvas-text-block canvas-text-block-pseudocode canvas-pseudocode-details">
            <summary className="canvas-pseudocode-summary">Strategy parameters</summary>
            <pre className="canvas-pseudocode">{paramsJsonText}</pre>
          </details>
        ) : null}
        {chartError ? <p className="canvas-chart-error">{chartError}</p> : null}
        {!hasRenderableChartOutput(output) && !chartError ? (
          <p className="canvas-charts-placeholder muted">No chart data yet. Send a message to refresh the strategy run.</p>
        ) : null}
        <div
          ref={chartsMountRef}
          className="canvas-charts-mount"
          aria-label="Strategy backtest charts"
        />
      </section>
    </main>
  );
}
