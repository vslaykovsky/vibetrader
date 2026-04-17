import { useCallback, useEffect, useLayoutEffect, useMemo, useRef, useState } from 'react';
import { createPortal } from 'react-dom';
import { Link, useLocation, useNavigate, useParams } from 'react-router-dom';
import ReactMarkdown from 'react-markdown';
import remarkGfm from 'remark-gfm';
import { randomUUID } from '../randomUUID.js';
import { attachSyncedCrosshair, renderCharts } from '../strategyChartRenderer.js';
import { useAuth } from '../AuthContext';
import { useTheme } from '../ThemeContext';
import { ProfileMenu } from '../ProfileMenu';

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

const API_BASE_URL =
  import.meta.env.VITE_API_BASE_URL ||
  (import.meta.env.PROD ? '/api' : 'http://localhost:8080');

const LAYOUT_SPLIT = {
  gapPx: 10,
  splitterPx: 8,
  minChatPx: 320,
  minCanvasPx: 440,
};

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

function agentAnswerElementId(runId) {
  return `answer-${runId}`;
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
  const answerDomId = hasRunId ? agentAnswerElementId(message.run_id) : undefined;
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
      id={answerDomId}
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
      <div className="message-markdown">
        <ReactMarkdown remarkPlugins={[remarkGfm]}>{message.content || ''}</ReactMarkdown>
      </div>
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
  let chartData = output['backtest.json'] ?? output['data.json'];
  if (typeof chartData === 'string') {
    try {
      chartData = JSON.parse(chartData);
    } catch {
      return false;
    }
  }
  if (chartData == null || typeof chartData !== 'object') {
    return false;
  }
  return Array.isArray(chartData.charts) && chartData.charts.length > 0;
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

function metricsJsonFromOutput(output) {
  if (!output || typeof output !== 'object') {
    return null;
  }
  const raw = output['metrics.json'];
  if (raw == null) {
    return null;
  }
  if (typeof raw === 'string') {
    const t = raw.trim();
    if (!t.length) return null;
    try {
      const parsed = JSON.parse(t);
      return parsed && typeof parsed === 'object' ? parsed : null;
    } catch {
      return null;
    }
  }
  return raw && typeof raw === 'object' ? raw : null;
}

function strategyNameFromOutput(output) {
  if (!output || typeof output !== 'object') {
    return '';
  }
  let data = output['backtest.json'] ?? output['data.json'];
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
  const location = useLocation();
  const { user, signOut, getAccessToken } = useAuth();
  const { theme, toggleTheme } = useTheme();
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
  const chatPanelRef = useRef(null);
  const messageTextareaRef = useRef(null);
  const homePromptAutoSubmitRef = useRef(false);
  const locationRef = useRef(location);
  locationRef.current = location;
  const emptyThreadPrompts = useMemo(
    () => [
      'What can you do?',
      "Let's create a SMA-based strategy for SPY",
      'What are ways to account for volatility changes?',
    ],
    [],
  );
  const optimisticUserContentRef = useRef(null);
  const viewingRunIdRef = useRef(null);
  const liveStrategyRunIdRef = useRef('');
  const algorithmFetchAbortRef = useRef(null);
  const chartsMountRef = useRef(null);
  const [chartError, setChartError] = useState('');
  const [viewingRunId, setViewingRunId] = useState(null);
  const [historicalCanvas, setHistoricalCanvas] = useState(null);
  const [liveStrategyRunId, setLiveStrategyRunId] = useState('');
  const [liveStrategyAlgorithm, setLiveStrategyAlgorithm] = useState('');
  const [historicalStrategyAlgorithm, setHistoricalStrategyAlgorithm] = useState('');
  const [algorithmLoading, setAlgorithmLoading] = useState(false);
  const [reverting, setReverting] = useState(false);
  const [isNarrow, setIsNarrow] = useState(false);
  const [mobileCanvasOpen, setMobileCanvasOpen] = useState(false);
  const [chatPanelWidthPx, setChatPanelWidthPx] = useState(null);
  const [composerExpanded, setComposerExpanded] = useState(false);
  const layoutDualRef = useRef(null);
  const composerExpandedTextareaRef = useRef(null);
  const hashHydratedRef = useRef(false);
  const appliedHashKeyRef = useRef('');
  const hydratingForRef = useRef('');
  const skipNextChatEndScrollRef = useRef(false);

  const authFetch = useCallback(async (url, options = {}) => {
    const token = await getAccessToken();
    const headers = { ...options.headers };
    if (token) headers['Authorization'] = `Bearer ${token}`;
    return fetch(url, { ...options, headers });
  }, [getAccessToken]);

  const syncLiveStrategyFromServer = useCallback(async () => {
    const tid = String(threadId || '').trim();
    if (!tid || !signedInUserId) {
      return;
    }
    if (viewingRunIdRef.current) {
      return;
    }
    try {
      const response = await authFetch(
        `${API_BASE_URL}/strategy?thread_id=${encodeURIComponent(tid)}`,
      );
      if (!response.ok) {
        return;
      }
      const payload = await response.json().catch(() => ({}));
      if (viewingRunIdRef.current) {
        return;
      }
      setMessages(payload.messages || []);
      setCanvas(payload.canvas || {});
      if (typeof payload.id === 'string') {
        setLiveStrategyRunId(payload.id);
      }
      if (typeof payload.algorithm === 'string') {
        setLiveStrategyAlgorithm(payload.algorithm);
      }
      setServerJob({
        status: payload.status ?? null,
        statusText: payload.status_text || '',
      });
    } catch {
    }
  }, [threadId, signedInUserId, authFetch]);

  const prevJobStatusRef = useRef(null);

  useEffect(() => {
    const prev = prevJobStatusRef.current;
    const cur = serverJob.status;
    const finished = prev === 'running' && cur !== 'running';
    prevJobStatusRef.current = cur;
    if (!finished) {
      return undefined;
    }
    let cancelled = false;
    void (async () => {
      await syncLiveStrategyFromServer();
      if (cancelled) {
        return;
      }
    })();
    return () => {
      cancelled = true;
    };
  }, [serverJob.status, syncLiveStrategyFromServer]);

  viewingRunIdRef.current = viewingRunId;
  liveStrategyRunIdRef.current = liveStrategyRunId;

  const displayStrategyRunId = useMemo(
    () => String(viewingRunId || liveStrategyRunId || '').trim(),
    [viewingRunId, liveStrategyRunId],
  );

  const displayAlgorithmText = useMemo(
    () => (viewingRunId ? historicalStrategyAlgorithm : liveStrategyAlgorithm) || '',
    [viewingRunId, historicalStrategyAlgorithm, liveStrategyAlgorithm],
  );

  useEffect(() => {
    algorithmFetchAbortRef.current?.abort();
    algorithmFetchAbortRef.current = null;
    setAlgorithmLoading(false);
  }, [displayStrategyRunId]);

  const handleViewRun = useCallback(
    async (runId) => {
      if (runId === viewingRunIdRef.current) {
        setViewingRunId(null);
        setHistoricalCanvas(null);
        setHistoricalStrategyAlgorithm('');
        appliedHashKeyRef.current = '';
        navigate(
          { pathname: location.pathname, search: location.search, hash: '' },
          { replace: true },
        );
        return;
      }
      try {
        const response = await authFetch(
          `${API_BASE_URL}/strategy?id=${encodeURIComponent(runId)}`,
        );
        if (!response.ok) throw new Error('Failed to load strategy run');
        const payload = await response.json();
        setHistoricalCanvas(payload.canvas || {});
        setHistoricalStrategyAlgorithm(
          typeof payload.algorithm === 'string' ? payload.algorithm : '',
        );
        setViewingRunId(runId);
      } catch (err) {
        setError(err.message);
      }
    },
    [authFetch, navigate, location.pathname, location.search],
  );

  const fetchStrategyAlgorithm = useCallback(
    async (runId) => {
      const rid = String(runId || '').trim();
      if (!rid) return;
      algorithmFetchAbortRef.current?.abort();
      const ac = new AbortController();
      algorithmFetchAbortRef.current = ac;
      setAlgorithmLoading(true);
      setError('');
      try {
        const response = await authFetch(`${API_BASE_URL}/strategy/algorithm`, {
          method: 'POST',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({ id: rid }),
          signal: ac.signal,
        });
        const data = await response.json().catch(() => ({}));
        if (ac.signal.aborted) return;
        if (!response.ok) {
          setError(typeof data.error === 'string' ? data.error : 'Failed to load strategy algorithm');
          return;
        }
        const text = typeof data.algorithm === 'string' ? data.algorithm : '';
        if (viewingRunIdRef.current && viewingRunIdRef.current === rid) {
          setHistoricalStrategyAlgorithm(text);
        } else if (!viewingRunIdRef.current && liveStrategyRunIdRef.current === rid) {
          setLiveStrategyAlgorithm(text);
        }
      } catch (err) {
        if (err?.name === 'AbortError') return;
        setError(err instanceof Error ? err.message : String(err));
      } finally {
        if (algorithmFetchAbortRef.current === ac) {
          setAlgorithmLoading(false);
          algorithmFetchAbortRef.current = null;
        }
      }
    },
    [authFetch],
  );

  const scrollToAnswerForRun = useCallback((runId) => {
    skipNextChatEndScrollRef.current = true;
    requestAnimationFrame(() => {
      requestAnimationFrame(() => {
        document.getElementById(agentAnswerElementId(runId))?.scrollIntoView({
          behavior: 'smooth',
          block: 'center',
        });
      });
    });
  }, []);

  async function handleViewStrategy(runId) {
    await handleViewRun(runId);
    setMobileCanvasOpen(true);
  }

  useEffect(() => {
    setViewingRunId(null);
    setHistoricalCanvas(null);
    setLiveStrategyRunId('');
    setLiveStrategyAlgorithm('');
    setHistoricalStrategyAlgorithm('');
    setAlgorithmLoading(false);
    algorithmFetchAbortRef.current?.abort();
    algorithmFetchAbortRef.current = null;
    setMobileCanvasOpen(false);
    setMessages([]);
    setLoading(true);
    hashHydratedRef.current = false;
    appliedHashKeyRef.current = '';
    hydratingForRef.current = '';
    prevJobStatusRef.current = null;
  }, [threadId]);

  useEffect(() => {
    if (loading) {
      return undefined;
    }
    const raw = (location.hash || '').replace(/^#/, '').trim();
    if (!raw) {
      appliedHashKeyRef.current = '';
      hashHydratedRef.current = true;
      return undefined;
    }
    const inThread = messages.some((m) => m.role === 'assistant' && m.run_id === raw);
    if (!inThread) {
      hashHydratedRef.current = true;
      if (location.hash) {
        navigate(
          { pathname: location.pathname, search: location.search, hash: '' },
          { replace: true },
        );
      }
      return undefined;
    }
    const key = `${threadId}:${raw}`;
    if (appliedHashKeyRef.current === key) {
      hashHydratedRef.current = true;
      return undefined;
    }
    if (viewingRunId === raw) {
      appliedHashKeyRef.current = key;
      scrollToAnswerForRun(raw);
      if (typeof window !== 'undefined' && window.matchMedia('(max-width: 980px)').matches) {
        setMobileCanvasOpen(true);
      }
      hashHydratedRef.current = true;
      return undefined;
    }
    if (hydratingForRef.current === key) {
      return undefined;
    }
    hydratingForRef.current = key;
    let cancelled = false;
    void (async () => {
      try {
        await handleViewRun(raw);
        if (cancelled) {
          return;
        }
        appliedHashKeyRef.current = key;
        scrollToAnswerForRun(raw);
        if (typeof window !== 'undefined' && window.matchMedia('(max-width: 980px)').matches) {
          setMobileCanvasOpen(true);
        }
      } finally {
        hydratingForRef.current = '';
        if (!cancelled) {
          hashHydratedRef.current = true;
        }
      }
    })();
    return () => {
      cancelled = true;
      hydratingForRef.current = '';
    };
  }, [
    loading,
    messages,
    location.hash,
    location.pathname,
    location.search,
    threadId,
    viewingRunId,
    handleViewRun,
    navigate,
    scrollToAnswerForRun,
  ]);

  useEffect(() => {
    if (!hashHydratedRef.current) {
      return undefined;
    }
    const desired = viewingRunId ? `#${viewingRunId}` : '';
    if ((location.hash || '') === desired) {
      return undefined;
    }
    navigate(
      { pathname: location.pathname, search: location.search, hash: desired },
      { replace: true },
    );
    return undefined;
  }, [viewingRunId, location.pathname, location.search, location.hash, navigate]);

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
      setLiveStrategyRunId(typeof next.id === 'string' ? next.id : '');
      setLiveStrategyAlgorithm(typeof next.algorithm === 'string' ? next.algorithm : '');
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
      navigate({ pathname: `/strategy/${next || randomUUID()}`, hash: '' });
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
        if (controller.signal.aborted) {
          return;
        }
        const msgs = payload.messages || [];
        setMessages(msgs);
        setCanvas(payload.canvas || {});
        setLiveStrategyRunId(typeof payload.id === 'string' ? payload.id : '');
        setLiveStrategyAlgorithm(typeof payload.algorithm === 'string' ? payload.algorithm : '');
        setServerJob({
          status: payload.status ?? null,
          statusText: payload.status_text || '',
        });
        const loc = locationRef.current;
        const rawDraft = loc?.state?.draft;
        const draftText = typeof rawDraft === 'string' ? rawDraft.trim() : '';
        if (msgs.length === 0 && draftText && !homePromptAutoSubmitRef.current) {
          homePromptAutoSubmitRef.current = true;
          navigate('.', { replace: true, state: {} });
          setDraft(draftText);
          setTimeout(() => {
            void handleSubmit({ preventDefault() {} }, draftText);
          }, 0);
        }
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
    homePromptAutoSubmitRef.current = false;
  }, [threadId]);

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
    if (isNarrow) {
      setChatPanelWidthPx(null);
    }
  }, [isNarrow]);

  useEffect(() => {
    if (!composerExpanded || typeof document === 'undefined') {
      return undefined;
    }
    const prev = document.body.style.overflow;
    document.body.style.overflow = 'hidden';
    return () => {
      document.body.style.overflow = prev;
    };
  }, [composerExpanded]);

  useEffect(() => {
    if (!composerExpanded) {
      return undefined;
    }
    const onKey = (event) => {
      if (event.key === 'Escape') {
        setComposerExpanded(false);
      }
    };
    window.addEventListener('keydown', onKey);
    return () => window.removeEventListener('keydown', onKey);
  }, [composerExpanded]);

  useLayoutEffect(() => {
    if (!composerExpanded) {
      return;
    }
    composerExpandedTextareaRef.current?.focus();
  }, [composerExpanded]);

  useEffect(() => {
    if (isNarrow || chatPanelWidthPx == null || typeof ResizeObserver === 'undefined') {
      return undefined;
    }
    const dual = layoutDualRef.current;
    if (!dual) {
      return undefined;
    }
    const clamp = () => {
      const el = layoutDualRef.current;
      if (!el) return;
      setChatPanelWidthPx((w) => {
        if (typeof w !== 'number') return w;
        const dualW = el.getBoundingClientRect().width;
        const { gapPx, splitterPx, minChatPx, minCanvasPx } = LAYOUT_SPLIT;
        const maxChat = dualW - 2 * gapPx - splitterPx - minCanvasPx;
        const upper = Math.max(minChatPx, maxChat);
        return Math.min(upper, Math.max(minChatPx, w));
      });
    };
    clamp();
    const ro = new ResizeObserver(() => {
      clamp();
    });
    ro.observe(dual);
    return () => ro.disconnect();
  }, [chatPanelWidthPx, isNarrow]);

  const fitMessageTextarea = useCallback(() => {
    const ta = messageTextareaRef.current;
    const panel = chatPanelRef.current;
    if (!ta || !panel) return;
    const maxH = panel.clientHeight * 0.5;
    if (!Number.isFinite(maxH) || maxH <= 0) return;
    ta.style.maxHeight = `${maxH}px`;
    ta.style.height = 'auto';
    ta.style.height = `${Math.min(ta.scrollHeight, maxH)}px`;
  }, []);

  useLayoutEffect(() => {
    fitMessageTextarea();
  }, [draft, loading, fitMessageTextarea]);

  useLayoutEffect(() => {
    if (typeof ResizeObserver === 'undefined') {
      return undefined;
    }
    const panel = chatPanelRef.current;
    if (!panel) {
      return undefined;
    }
    const ro = new ResizeObserver(() => {
      fitMessageTextarea();
    });
    ro.observe(panel);
    return () => ro.disconnect();
  }, [fitMessageTextarea]);

  const handleLayoutSplitterPointerDown = useCallback(
    (e) => {
      if (e.pointerType === 'mouse' && e.button !== 0) return;
      if (isNarrow) return;
      e.preventDefault();
      const dual = layoutDualRef.current;
      const chatEl = dual?.querySelector('.chat-panel');
      if (!dual || !chatEl) return;
      const { gapPx, splitterPx, minChatPx, minCanvasPx } = LAYOUT_SPLIT;
      const startX = e.clientX;
      const startW = chatEl.getBoundingClientRect().width;

      const maxForDual = (dualW) =>
        Math.max(minChatPx, dualW - 2 * gapPx - splitterPx - minCanvasPx);

      const move = (ev) => {
        const d = layoutDualRef.current;
        if (!d) return;
        const dualW = d.getBoundingClientRect().width;
        const upper = maxForDual(dualW);
        const dx = ev.clientX - startX;
        setChatPanelWidthPx(Math.min(upper, Math.max(minChatPx, startW + dx)));
      };

      const up = () => {
        document.removeEventListener('pointermove', move);
        document.removeEventListener('pointerup', up);
        document.removeEventListener('pointercancel', up);
        document.body.style.removeProperty('cursor');
        document.body.style.removeProperty('user-select');
      };

      document.body.style.cursor = 'col-resize';
      document.body.style.userSelect = 'none';
      document.addEventListener('pointermove', move);
      document.addEventListener('pointerup', up);
      document.addEventListener('pointercancel', up);
    },
    [isNarrow],
  );

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
          if (typeof payload.id === 'string') {
            setLiveStrategyRunId(payload.id);
          }
          if (typeof payload.algorithm === 'string') {
            setLiveStrategyAlgorithm(payload.algorithm);
          }
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
        void syncLiveStrategyFromServer();
      };
    })();

    return () => {
      cancelled = true;
      evtSource?.close();
    };
  }, [threadId, serverJob.status, getAccessToken, syncLiveStrategyFromServer]);

  useEffect(() => {
    if (skipNextChatEndScrollRef.current) {
      skipNextChatEndScrollRef.current = false;
      return undefined;
    }
    chatEndRef.current?.scrollIntoView({ behavior: 'smooth' });
    return undefined;
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
    let chartData = output['backtest.json'] ?? output['data.json'];
    if (typeof chartData === 'string') {
      try {
        chartData = JSON.parse(chartData);
      } catch {
        setChartError('Could not parse backtest.json');
        mount.innerHTML = '';
        return undefined;
      }
    }
    if (chartData === null || typeof chartData !== 'object') {
      mount.innerHTML = '';
      return undefined;
    }
    if (chartData.metrics == null) {
      const m = metricsJsonFromOutput(output);
      if (m) {
        chartData = { ...chartData, metrics: m };
      }
    }
    const hasCharts = Array.isArray(chartData.charts) && chartData.charts.length > 0;
    if (!hasCharts) {
      mount.innerHTML = '';
      return undefined;
    }
    mount.innerHTML = '';
    const root = document.createElement('div');
    root.className = 'strategy-charts-root';
    mount.appendChild(root);

    let detachSync;
    let detachCrosshair;
    try {
      const { lwCharts, lwCrosshairBindings } = renderCharts(root, chartData);
      detachSync = attachSyncedTimeScales(lwCharts);
      detachCrosshair = attachSyncedCrosshair(lwCrosshairBindings);
    } catch (err) {
      setChartError(err instanceof Error ? err.message : String(err));
    }

    return () => {
      detachSync?.();
      detachCrosshair?.();
      mount.innerHTML = '';
    };
  }, [displayOutput]);

  async function handleSubmit(event, messageFromField) {
    if (event && typeof event.preventDefault === 'function') {
      event.preventDefault();
    }
    const raw =
      messageFromField !== undefined && messageFromField !== null
        ? String(messageFromField)
        : messageTextareaRef.current?.value ??
          composerExpandedTextareaRef.current?.value ??
          draft;
    const message = raw.trim();
    if (!message || submitting || serverJob.status === 'running') {
      return;
    }

    setSubmitting(true);
    setError('');
    setViewingRunId(null);
    setHistoricalCanvas(null);
    navigate(
      { pathname: location.pathname, search: location.search, hash: '' },
      { replace: true },
    );
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
      if (typeof payload.id === 'string') {
        setLiveStrategyRunId(payload.id);
      }
      if (typeof payload.algorithm === 'string') {
        setLiveStrategyAlgorithm(payload.algorithm);
      }
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
  const paramsHyperoptJsonText = paramsHyperoptJsonFromOutput(output);
  const showHyperoptParamsPanel = paramsHyperoptJsonText != null;
  const showMetricsPanel = metricsJsonFromOutput(output) != null;
  const hasAnyCanvasData =
    showCliDescription ||
    showParamsPanel ||
    showHyperoptParamsPanel ||
    showMetricsPanel ||
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

  const chatPanelStyle = isNarrow
    ? undefined
    : chatPanelWidthPx != null
      ? {
          flex: '0 0 auto',
          width: chatPanelWidthPx,
          minWidth: LAYOUT_SPLIT.minChatPx,
        }
      : {
          flex: '0.9 1 0',
          minWidth: LAYOUT_SPLIT.minChatPx,
        };

  const canvasPanelStyle = isNarrow
    ? undefined
    : chatPanelWidthPx != null
      ? { flex: '1 1 0', minWidth: LAYOUT_SPLIT.minCanvasPx, minHeight: 0 }
      : { flex: '1.4 1 0', minWidth: LAYOUT_SPLIT.minCanvasPx, minHeight: 0 };

  return (
    <>
    <main className={`layout${isNarrow ? ' layout-narrow' : ''}${mobileCanvasOpen ? ' is-mobile-canvas-open' : ''}`}>
      <div className="layout-dual" ref={layoutDualRef}>
        <section ref={chatPanelRef} className="chat-panel" style={chatPanelStyle}>
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
                </Link>
              </div>
            </div>
            <div className="chat-header-actions">
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
              <button
                type="button"
                className="button-new-thread"
                onClick={() => navigate({ pathname: `/strategy/${randomUUID()}`, hash: '' })}
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
                  <ProfileMenu user={user} signOut={signOut} />
                </div>
              )}
            </div>
          </div>
        </header>

        <div className="chat-stream">
          {loading ? (
            <div className="chat-spinner-row" role="status" aria-live="polite" aria-label="Loading thread">
              <span className="chat-spinner" aria-hidden />
              <span className="chat-processing-label">Loading thread…</span>
            </div>
          ) : null}
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
                        setTimeout(() => void handleSubmit({ preventDefault() {} }, p), 0);
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
            <button
              type="button"
              className="chat-compose-expand"
              onClick={() => setComposerExpanded(true)}
              disabled={showProcessing}
              aria-label="Expand message editor"
              aria-expanded={composerExpanded}
              title="Expand editor"
            >
              <svg
                xmlns="http://www.w3.org/2000/svg"
                viewBox="0 0 24 24"
                fill="none"
                stroke="currentColor"
                strokeWidth="2"
                strokeLinecap="round"
                strokeLinejoin="round"
                aria-hidden
              >
                <path d="M15 3h6v6M9 21H3v-6M21 3l-7 7M3 21l7-7" />
              </svg>
            </button>
            <textarea
              ref={messageTextareaRef}
              id="message"
              placeholder="Describe your strategy in your own words..."
              title="Ctrl+Enter or ⌘+Enter to send"
              value={draft}
              onChange={(event) => setDraft(event.target.value)}
              onKeyDown={(event) => {
                if (event.nativeEvent.isComposing) return;
                if (event.key !== 'Enter' && event.key !== 'NumpadEnter') return;
                if (!event.metaKey && !event.ctrlKey) return;
                event.preventDefault();
                void handleSubmit(event, event.currentTarget.value);
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

      {!isNarrow ? (
        <div
          role="separator"
          aria-orientation="vertical"
          aria-label="Resize chat and strategy panels"
          className="layout-splitter"
          onPointerDown={handleLayoutSplitterPointerDown}
        />
      ) : null}

      <section className="canvas-panel canvas-panel-charts" style={canvasPanelStyle}>
        <header className="canvas-hero">
          <div className="canvas-hero-actions">
            {viewingRunId ? (
              <button
                type="button"
                className="button-back-to-current"
                onClick={() => {
                  setViewingRunId(null);
                  setHistoricalCanvas(null);
                  navigate(
                    { pathname: location.pathname, search: location.search, hash: '' },
                    { replace: true },
                  );
                }}
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
        {!loading && displayStrategyRunId && hasAnyCanvasData ? (
          <details
            key={displayStrategyRunId}
            className="canvas-text-block canvas-text-block-pseudocode canvas-pseudocode-details"
            onToggle={(e) => {
              if (!e.currentTarget.open) return;
              const rid = displayStrategyRunId;
              if (!String(displayAlgorithmText || '').trim()) {
                void fetchStrategyAlgorithm(rid);
              }
            }}
          >
            <summary className="canvas-pseudocode-summary">Strategy Algorithm</summary>
            <CanvasPanelCopyButton
              text={String(displayAlgorithmText || '').trim()}
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
                  {displayAlgorithmText?.trim() || ''}
                </ReactMarkdown>
              </div>
            )}
          </details>
        ) : null}
        {chartError ? <p className="canvas-chart-error">{chartError}</p> : null}
        {!hasRenderableChartOutput(output) && !chartError ? (
          <p className="canvas-charts-placeholder muted">
            No charts yet. Send a message to refresh the strategy run.
          </p>
        ) : null}
        <div
          ref={chartsMountRef}
          className="canvas-charts-mount"
          aria-label="Strategy backtest charts"
        />
      </section>
      </div>
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
                              navigate({ pathname: `/strategy/${tid}`, hash: '' });
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
    </main>
    {composerExpanded
      ? createPortal(
          <div
            className="chat-compose-fullscreen"
            role="dialog"
            aria-modal="true"
            aria-label="Message editor"
          >
            <button
              type="button"
              className="chat-compose-fullscreen-scrim"
              aria-label="Close expanded editor"
              onClick={() => setComposerExpanded(false)}
            />
            <div className="chat-compose-fullscreen-panel">
              <div className="chat-compose-fullscreen-toolbar">
                <button
                  type="button"
                  className="chat-compose-fullscreen-close"
                  onClick={() => setComposerExpanded(false)}
                  aria-label="Close"
                >
                  ×
                </button>
              </div>
              <textarea
                ref={composerExpandedTextareaRef}
                className="chat-compose-fullscreen-textarea"
                placeholder="Describe your strategy in your own words..."
                title="Ctrl+Enter or ⌘+Enter to send"
                value={draft}
                onChange={(event) => setDraft(event.target.value)}
                onKeyDown={(event) => {
                  if (event.nativeEvent.isComposing) return;
                  if (event.key !== 'Enter' && event.key !== 'NumpadEnter') return;
                  if (!event.metaKey && !event.ctrlKey) return;
                  event.preventDefault();
                  const v = event.currentTarget.value;
                  if (!v.trim() || showProcessing) return;
                  void handleSubmit(event, v);
                  setComposerExpanded(false);
                }}
              />
              <div className="chat-compose-fullscreen-footer">
                <button
                  type="button"
                  className="chat-compose-fullscreen-done"
                  onClick={() => setComposerExpanded(false)}
                >
                  Done
                </button>
                <button
                  type="button"
                  className="chat-compose-fullscreen-send"
                  disabled={showProcessing}
                  onClick={() => {
                    const v = composerExpandedTextareaRef.current?.value ?? draft;
                    if (!v.trim() || showProcessing) return;
                    void handleSubmit({ preventDefault() {} }, v);
                    setComposerExpanded(false);
                  }}
                >
                  Send
                </button>
              </div>
            </div>
          </div>,
          document.body,
        )
      : null}
    </>
  );
}
