import { memo, useCallback, useEffect, useLayoutEffect, useMemo, useRef, useState } from 'react';
import { createPortal } from 'react-dom';
import { Link, useLocation, useNavigate, useParams } from 'react-router-dom';
import hljs from 'highlight.js/lib/core';
import python from 'highlight.js/lib/languages/python';
import ReactMarkdown from 'react-markdown';
import remarkGfm from 'remark-gfm';
import { randomUUID } from '../randomUUID.js';
import { attachSyncedCrosshair, attachSyncedTimeScales } from '../lib/lwcSync.js';
import { renderCharts } from '../strategyChartRenderer.js';
import { useAuth } from '../AuthContext';
import { useTheme } from '../ThemeContext';
import { useTimeZone } from '../TimeZoneContext.jsx';
import { dateKeyFromIso as zonedDateKeyFromIso, parseIsoInstant, todayDateKey } from '../lib/dateTime.js';
import { ProfileMenu } from '../ProfileMenu';
import { ConfirmDialog } from '../components/ConfirmDialog.jsx';
import { SimulationPanel } from '../components/SimulationPanel.jsx';
import {
  appendAssistantDeltaMessage,
  mergeStrategySnapshotMessages,
} from '../lib/strategyStreamMessages.js';

hljs.registerLanguage('python', python);

const MARKDOWN_REMARK_PLUGINS = [remarkGfm];

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

const ChatComposer = memo(function ChatComposer({
  panelRef,
  loading,
  prompts,
  showProcessing,
  showSuggestedPrompts,
  onSubmit,
}) {
  const [draft, setDraft] = useState('');
  const [composerExpanded, setComposerExpanded] = useState(false);
  const [localSubmitting, setLocalSubmitting] = useState(false);
  const messageTextareaRef = useRef(null);
  const composerExpandedTextareaRef = useRef(null);
  const disabled = showProcessing || localSubmitting;

  const fitMessageTextarea = useCallback(() => {
    const ta = messageTextareaRef.current;
    const panel = panelRef.current;
    if (!ta || !panel) return;
    const maxH = panel.clientHeight * 0.5;
    if (!Number.isFinite(maxH) || maxH <= 0) return;
    ta.style.maxHeight = `${maxH}px`;
    ta.style.height = 'auto';
    ta.style.height = `${Math.min(ta.scrollHeight, maxH)}px`;
  }, [panelRef]);

  useLayoutEffect(() => {
    fitMessageTextarea();
  }, [draft, loading, fitMessageTextarea]);

  useLayoutEffect(() => {
    if (typeof ResizeObserver === 'undefined') {
      return undefined;
    }
    const panel = panelRef.current;
    if (!panel) {
      return undefined;
    }
    const ro = new ResizeObserver(() => {
      fitMessageTextarea();
    });
    ro.observe(panel);
    return () => ro.disconnect();
  }, [fitMessageTextarea, panelRef]);

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

  const submitDraft = useCallback(
    async (value) => {
      const message = String(value ?? '').trim();
      if (!message || disabled) {
        return { accepted: false };
      }
      setLocalSubmitting(true);
      setDraft('');
      let result;
      try {
        result = await onSubmit(message);
      } catch {
        result = { accepted: true, ok: false };
      }
      if (!result?.accepted || result?.ok === false || result?.restoreDraft) {
        setDraft((current) => (current.trim() ? current : message));
      }
      setLocalSubmitting(false);
      return result;
    },
    [disabled, onSubmit],
  );

  const handleSubmit = useCallback(
    (event) => {
      event.preventDefault();
      void submitDraft(draft);
    },
    [draft, submitDraft],
  );

  const handleComposerKeyDown = useCallback(
    (event) => {
      if (event.nativeEvent.isComposing) return;
      if (event.key !== 'Enter' && event.key !== 'NumpadEnter') return;
      if (!event.metaKey && !event.ctrlKey) return;
      event.preventDefault();
      void submitDraft(event.currentTarget.value);
    },
    [submitDraft],
  );

  return (
    <>
      <form className="chat-input" onSubmit={handleSubmit}>
        {showSuggestedPrompts ? (
          <section className="home-prompts chat-suggested-prompts" aria-label="Suggested prompts">
            <ul className="home-prompt-list">
              {prompts.map((p) => (
                <li key={p} className="home-prompt-item">
                  <button
                    type="button"
                    className="home-prompt"
                    disabled={disabled}
                    onClick={() => {
                      void submitDraft(p);
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
            disabled={disabled}
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
            onKeyDown={handleComposerKeyDown}
            rows={4}
          />
          <button
            type="submit"
            className="chat-send-button"
            disabled={disabled}
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
                    void submitDraft(event.currentTarget.value).then((result) => {
                      if (result?.accepted) {
                        setComposerExpanded(false);
                      }
                    });
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
                    disabled={disabled}
                    onClick={() => {
                      const value = composerExpandedTextareaRef.current?.value ?? draft;
                      void submitDraft(value).then((result) => {
                        if (result?.accepted) {
                          setComposerExpanded(false);
                        }
                      });
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
});

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
  return parseIsoInstant(value);
}

function dateKeyFromIso(value, timeZone) {
  return zonedDateKeyFromIso(value, timeZone);
}

function isoDateTodayKey(timeZone) {
  return todayDateKey(timeZone);
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

const MessageBubble = memo(function MessageBubble({
  message,
  isActive,
  onViewRun,
  onRevertRun,
  revertDisabled,
  showViewStrategy,
  onViewStrategy,
}) {
  const isAssistant = message.role === 'assistant';
  const hasRunId = isAssistant && message.run_id && !message.streaming;
  const answerDomId = hasRunId ? agentAnswerElementId(message.run_id) : undefined;
  const langsmithTrace =
    isAssistant && typeof message.langsmith_trace === 'string'
      ? message.langsmith_trace.trim()
      : '';
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
        {replyMs != null || hasRunId || langsmithTrace ? (
          <div className="message-header-end">
            {langsmithTrace ? (
              <a
                className="message-langsmith-link"
                href={langsmithTrace}
                target="_blank"
                rel="noreferrer"
                onClick={(e) => {
                  e.stopPropagation();
                }}
                title="Open LangSmith trace"
                aria-label="Open LangSmith trace"
              >
                LangSmith
              </a>
            ) : null}
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
        <ReactMarkdown remarkPlugins={MARKDOWN_REMARK_PLUGINS}>{message.content || ''}</ReactMarkdown>
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
});

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

function parsedChartOutput(output) {
  if (!output || typeof output !== 'object') {
    return null;
  }
  const raw = output['backtest.json'] ?? output['data.json'];
  if (raw == null) {
    return null;
  }
  if (typeof raw === 'string') {
    try {
      const parsed = JSON.parse(raw);
      return parsed && typeof parsed === 'object' ? parsed : null;
    } catch {
      return null;
    }
  }
  return raw && typeof raw === 'object' ? raw : null;
}

function hasStrategyTrades(output) {
  const chartData = parsedChartOutput(output);
  const embeddedMetrics = chartData && typeof chartData.metrics === 'object' ? chartData.metrics : null;
  const metrics = embeddedMetrics || metricsJsonFromOutput(output);
  const numTrades = Number(metrics?.num_trades);
  if (Number.isFinite(numTrades)) {
    return numTrades > 0;
  }
  const tradesChart = Array.isArray(chartData?.charts)
    ? chartData.charts.find(
        (chart) =>
          chart?.type === 'table' &&
          typeof chart.title === 'string' &&
          chart.title.trim().toLowerCase() === 'trades',
      )
    : null;
  return Array.isArray(tradesChart?.rows) && tradesChart.rows.length > 0;
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

function canvasPayloadsEqual(a, b) {
  if (a === b) {
    return true;
  }
  try {
    return JSON.stringify(a) === JSON.stringify(b);
  } catch {
    return false;
  }
}

const CHAT_BOTTOM_STICKY_PX = 96;

function isChatScrollNearBottom(scroller) {
  if (!scroller) {
    return true;
  }
  return scroller.scrollHeight - scroller.scrollTop - scroller.clientHeight <= CHAT_BOTTOM_STICKY_PX;
}


export function StrategyPage() {
  const { threadId } = useParams();
  const navigate = useNavigate();
  const location = useLocation();
  const { user, signOut, getAccessToken } = useAuth();
  const { theme, toggleTheme } = useTheme();
  const { timeZone, hourFormat } = useTimeZone();
  const signedInUserId = user?.id ?? null;
  const [messages, setMessages] = useState([]);
  const [canvas, setCanvas] = useState({});
  const [loading, setLoading] = useState(true);
  const [canvasLoading, setCanvasLoading] = useState(false);
  const [submitting, setSubmitting] = useState(false);
  const [serverJob, setServerJob] = useState({ status: null, statusText: '' });
  const [streamingAssistantRunId, setStreamingAssistantRunId] = useState('');
  const [error, setError] = useState('');
  const [threads, setThreads] = useState([]);
  const [threadsError, setThreadsError] = useState('');
  const [sidebarOpen, setSidebarOpen] = useState(false);
  const [deletingThread, setDeletingThread] = useState(false);
  const [deleteThreadDialogOpen, setDeleteThreadDialogOpen] = useState(false);
  const chatEndRef = useRef(null);
  const chatStreamRef = useRef(null);
  const chatPanelRef = useRef(null);
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
  const submittingRef = useRef(false);
  const serverJobStatusRef = useRef(null);
  const viewingRunIdRef = useRef(null);
  const liveStrategyRunIdRef = useRef('');
  const lastStreamEventSeqByRunIdRef = useRef({});
  const ignoredStreamRunIdsRef = useRef(new Set());
  const algorithmFetchAbortRef = useRef(null);
  const canvasLoadSeqRef = useRef(0);
  const chartsMountRef = useRef(null);
  const [chartError, setChartError] = useState('');
  const [viewingRunId, setViewingRunId] = useState(null);
  const [historicalCanvas, setHistoricalCanvas] = useState(null);
  const [liveStrategyRunId, setLiveStrategyRunId] = useState('');
  const [liveStrategyAlgorithm, setLiveStrategyAlgorithm] = useState('');
  const [historicalStrategyAlgorithm, setHistoricalStrategyAlgorithm] = useState('');
  const [liveStrategyPythonCode, setLiveStrategyPythonCode] = useState('');
  const [historicalStrategyPythonCode, setHistoricalStrategyPythonCode] = useState('');
  const [algorithmLoading, setAlgorithmLoading] = useState(false);
  const [reverting, setReverting] = useState(false);
  const [revertRunRequest, setRevertRunRequest] = useState('');
  const [isNarrow, setIsNarrow] = useState(false);
  const [mobileCanvasOpen, setMobileCanvasOpen] = useState(false);
  const [chatPanelWidthPx, setChatPanelWidthPx] = useState(null);
  const [canvasTab, setCanvasTab] = useState('strategy');
  const [deployModalOpen, setDeployModalOpen] = useState(false);
  const [deployModalPhase, setDeployModalPhase] = useState('loading');
  const [deployModalError, setDeployModalError] = useState('');
  const [deployTradingConfigured, setDeployTradingConfigured] = useState(false);
  const [deployAccounts, setDeployAccounts] = useState([]);
  const [deploySelectedAccountId, setDeploySelectedAccountId] = useState('');
  const [deploySubmitting, setDeploySubmitting] = useState(false);
  const deploySelectedAccountReady = deployAccounts.some(
    (a) =>
      String(a?.id || '').trim() === String(deploySelectedAccountId || '').trim() &&
      a?.has_alpaca_api_key &&
      a?.has_alpaca_secret_key,
  );
  const [strategyNameByRunId, setStrategyNameByRunId] = useState(() => ({}));
  const [editingCanvasTitle, setEditingCanvasTitle] = useState(false);
  const [canvasTitleDraft, setCanvasTitleDraft] = useState('');
  const [savingCanvasTitle, setSavingCanvasTitle] = useState(false);
  const canvasTitleInputRef = useRef(null);

  const mergeStrategyNameFromPayload = useCallback((payload) => {
    if (!payload || typeof payload !== 'object' || !('strategy_name' in payload)) {
      return;
    }
    const rid = typeof payload.id === 'string' ? payload.id.trim() : '';
    if (!rid) {
      return;
    }
    const sn = typeof payload.strategy_name === 'string' ? payload.strategy_name : '';
    setStrategyNameByRunId((prev) => {
      if (prev[rid] === sn) {
        return prev;
      }
      return { ...prev, [rid]: sn };
    });
  }, []);

  const skipNextCanvasTitleBlurCommitRef = useRef(false);
  const layoutDualRef = useRef(null);
  const hashHydratedRef = useRef(false);
  const appliedHashKeyRef = useRef('');
  const hydratingForRef = useRef('');
  const skipNextChatEndScrollRef = useRef(false);
  const shouldStickToChatBottomRef = useRef(true);
  const chatScrollFrameRef = useRef(0);
  const pendingChatScrollBehaviorRef = useRef('auto');
  submittingRef.current = submitting;
  serverJobStatusRef.current = serverJob.status;

  useLayoutEffect(() => {
    if (!editingCanvasTitle) {
      return undefined;
    }
    const el = canvasTitleInputRef.current;
    if (el) {
      el.focus();
      if (typeof el.select === 'function') {
        el.select();
      }
    }
    return undefined;
  }, [editingCanvasTitle]);

  useLayoutEffect(() => {
    if (!threadId) {
      return;
    }
    try {
      const v = localStorage.getItem(`vibetrader:lastTab:${threadId}`);
      if (v === 'simulation' || v === 'strategy') {
        setCanvasTab(v);
      }
    } catch {
      /* ignore */
    }
  }, [threadId]);

  useLayoutEffect(() => {
    if (!threadId) {
      return;
    }
    setServerJob({ status: null, statusText: '' });
    setSubmitting(false);
    setStreamingAssistantRunId('');
    ignoredStreamRunIdsRef.current = new Set();
    shouldStickToChatBottomRef.current = true;
  }, [threadId]);

  useEffect(() => {
    return () => {
      if (chatScrollFrameRef.current && typeof window !== 'undefined') {
        window.cancelAnimationFrame(chatScrollFrameRef.current);
      }
    };
  }, []);

  useEffect(() => {
    const scroller = chatStreamRef.current;
    if (!scroller) {
      return undefined;
    }
    const updateStickiness = () => {
      shouldStickToChatBottomRef.current = isChatScrollNearBottom(scroller);
    };
    updateStickiness();
    scroller.addEventListener('scroll', updateStickiness, { passive: true });
    return () => scroller.removeEventListener('scroll', updateStickiness);
  }, [threadId]);

  useEffect(() => {
    if (!threadId) {
      return;
    }
    try {
      localStorage.setItem(`vibetrader:lastTab:${threadId}`, canvasTab);
    } catch {
      /* ignore */
    }
  }, [threadId, canvasTab]);

  const scrollChatToBottom = useCallback((behavior = 'auto') => {
    const scroller = chatStreamRef.current;
    if (!scroller) {
      chatEndRef.current?.scrollIntoView({ behavior });
      return;
    }
    const top = Math.max(0, scroller.scrollHeight - scroller.clientHeight);
    if (typeof scroller.scrollTo === 'function') {
      scroller.scrollTo({ top, behavior });
    } else {
      scroller.scrollTop = top;
    }
  }, []);

  const scheduleChatScrollToBottom = useCallback(
    (behavior = 'auto') => {
      pendingChatScrollBehaviorRef.current = behavior;
      if (typeof window === 'undefined') {
        scrollChatToBottom(behavior);
        return;
      }
      if (chatScrollFrameRef.current) {
        return;
      }
      chatScrollFrameRef.current = window.requestAnimationFrame(() => {
        chatScrollFrameRef.current = 0;
        scrollChatToBottom(pendingChatScrollBehaviorRef.current);
      });
    },
    [scrollChatToBottom],
  );

  const authFetch = useCallback(async (url, options = {}) => {
    const token = await getAccessToken();
    const headers = { ...options.headers };
    if (token) headers['Authorization'] = `Bearer ${token}`;
    return fetch(url, { ...options, headers });
  }, [getAccessToken]);

  const setCanvasIfChanged = useCallback((nextCanvas) => {
    const normalized = nextCanvas && typeof nextCanvas === 'object' ? nextCanvas : {};
    setCanvas((prev) => (canvasPayloadsEqual(prev, normalized) ? prev : normalized));
  }, []);

  const applyLiveCanvasPayload = useCallback(
    (payload) => {
      setCanvasIfChanged(payload?.canvas);
      if (typeof payload?.id === 'string') {
        setLiveStrategyRunId(payload.id);
      }
      if (typeof payload?.algorithm === 'string') {
        setLiveStrategyAlgorithm(payload.algorithm);
      }
      setLiveStrategyPythonCode(typeof payload?.python_code === 'string' ? payload.python_code : '');
      if (payload?.status != null || payload?.status_text != null) {
        setServerJob({
          status: payload.status ?? null,
          statusText: payload.status_text || '',
        });
      }
      mergeStrategyNameFromPayload(payload);
    },
    [mergeStrategyNameFromPayload, setCanvasIfChanged],
  );

  const setMessagesFromStrategyPayload = useCallback((payload) => {
    const rid = typeof payload?.id === 'string' ? payload.id.trim() : '';
    if (rid && payload?.status !== 'running') {
      setStreamingAssistantRunId((prev) => (prev === rid ? '' : prev));
    }
    setMessages((prev) =>
      mergeStrategySnapshotMessages(
        payload?.messages || [],
        prev,
        payload?.id,
        payload?.status,
      ),
    );
  }, []);

  const fetchLiveCanvas = useCallback(
    async (signal) => {
      const tid = String(threadId || '').trim();
      if (!tid || !signedInUserId) {
        setCanvasLoading(false);
        return;
      }
      const seq = canvasLoadSeqRef.current + 1;
      canvasLoadSeqRef.current = seq;
      setCanvasLoading(true);
      try {
        const response = await authFetch(
          `${API_BASE_URL}/strategy/canvas?thread_id=${encodeURIComponent(tid)}`,
          signal ? { signal } : undefined,
        );
        if (!response.ok) {
          return;
        }
        const payload = await response.json().catch(() => ({}));
        if (signal?.aborted || viewingRunIdRef.current) {
          return;
        }
        applyLiveCanvasPayload(payload);
      } finally {
        if (!signal?.aborted && canvasLoadSeqRef.current === seq) {
          setCanvasLoading(false);
        }
      }
    },
    [applyLiveCanvasPayload, authFetch, signedInUserId, threadId],
  );

  const shouldApplyStreamEvent = useCallback((payload) => {
    const rid = typeof payload?.run_id === 'string' ? payload.run_id.trim() : '';
    const seq = Number(payload?.seq);
    if (!rid || !Number.isFinite(seq) || seq <= 0) {
      return false;
    }
    if (ignoredStreamRunIdsRef.current.has(rid)) {
      return false;
    }
    const prev = Number(lastStreamEventSeqByRunIdRef.current[rid] || 0);
    if (seq <= prev) {
      return false;
    }
    lastStreamEventSeqByRunIdRef.current = {
      ...lastStreamEventSeqByRunIdRef.current,
      [rid]: seq,
    };
    return true;
  }, []);

  const applyAssistantDelta = useCallback(
    (payload) => {
      if (!shouldApplyStreamEvent(payload)) {
        return;
      }
      setStreamingAssistantRunId(String(payload.run_id || '').trim());
      setMessages((prev) =>
        appendAssistantDeltaMessage(prev, payload.run_id, payload.delta),
      );
    },
    [shouldApplyStreamEvent],
  );

  const openTradingSettingsWindow = useCallback(() => {
    const base = window.location.origin || '';
    window.open(`${base}/dashboard/settings`, '_blank', 'noopener,noreferrer');
  }, []);

  useEffect(() => {
    if (!deployModalOpen) {
      return undefined;
    }
    let cancelled = false;
    setDeployModalPhase('loading');
    setDeployModalError('');
    (async () => {
      try {
        const res = await authFetch(`${API_BASE_URL}/settings/trading`);
        const payload = await res.json().catch(() => ({}));
        if (cancelled) return;
        if (res.status === 503) {
          setDeployTradingConfigured(false);
          setDeployAccounts([]);
          setDeploySelectedAccountId('');
          setDeployModalPhase('ready');
          return;
        }
        if (!res.ok) {
          setDeployTradingConfigured(false);
          setDeployAccounts([]);
          setDeploySelectedAccountId('');
          setDeployModalError(payload.error || `Could not load accounts (${res.status})`);
          setDeployModalPhase('ready');
          return;
        }
        setDeployTradingConfigured(true);
        const acc = Array.isArray(payload.alpaca_accounts) ? payload.alpaca_accounts : [];
        setDeployAccounts(acc);
        const firstReady = acc.find(
          (a) => a?.has_alpaca_api_key && a?.has_alpaca_secret_key && typeof a.id === 'string',
        );
        const firstId = firstReady ? firstReady.id : '';
        setDeploySelectedAccountId(firstId);
        setDeployModalPhase('ready');
      } catch (e) {
        if (cancelled) return;
        setDeployTradingConfigured(false);
        setDeployAccounts([]);
        setDeploySelectedAccountId('');
        setDeployModalError(e instanceof Error ? e.message : String(e));
        setDeployModalPhase('ready');
      }
    })();
    return () => {
      cancelled = true;
    };
  }, [deployModalOpen, authFetch]);

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
        `${API_BASE_URL}/strategy?thread_id=${encodeURIComponent(tid)}&include_canvas=0`,
      );
      if (!response.ok) {
        return;
      }
      const payload = await response.json().catch(() => ({}));
      if (viewingRunIdRef.current) {
        return;
      }
      setMessagesFromStrategyPayload(payload);
      if (typeof payload.id === 'string') {
        setLiveStrategyRunId(payload.id);
      }
      if (typeof payload.algorithm === 'string') {
        setLiveStrategyAlgorithm(payload.algorithm);
      }
      setLiveStrategyPythonCode(typeof payload.python_code === 'string' ? payload.python_code : '');
      setServerJob({
        status: payload.status ?? null,
        statusText: payload.status_text || '',
      });
      mergeStrategyNameFromPayload(payload);
      void fetchLiveCanvas();
    } catch {
    }
  }, [
    threadId,
    signedInUserId,
    authFetch,
    fetchLiveCanvas,
    mergeStrategyNameFromPayload,
    setMessagesFromStrategyPayload,
  ]);

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
  const displayPythonCodeText = useMemo(
    () => (viewingRunId ? historicalStrategyPythonCode : liveStrategyPythonCode) || '',
    [viewingRunId, historicalStrategyPythonCode, liveStrategyPythonCode],
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
        setHistoricalStrategyPythonCode('');
        setCanvasLoading(false);
        appliedHashKeyRef.current = '';
        navigate(
          { pathname: location.pathname, search: location.search, hash: '' },
          { replace: true },
        );
        return;
      }
      setViewingRunId(runId);
      setHistoricalCanvas(null);
      setHistoricalStrategyAlgorithm('');
      setHistoricalStrategyPythonCode('');
      const seq = canvasLoadSeqRef.current + 1;
      canvasLoadSeqRef.current = seq;
      setCanvasLoading(true);
      try {
        const response = await authFetch(
          `${API_BASE_URL}/strategy/canvas?id=${encodeURIComponent(runId)}`,
        );
        if (!response.ok) throw new Error('Failed to load strategy run');
        const payload = await response.json();
        setHistoricalCanvas(payload.canvas || {});
        setHistoricalStrategyAlgorithm(
          typeof payload.algorithm === 'string' ? payload.algorithm : '',
        );
        setHistoricalStrategyPythonCode(
          typeof payload.python_code === 'string' ? payload.python_code : '',
        );
        mergeStrategyNameFromPayload(payload);
      } catch (err) {
        setViewingRunId(null);
        setHistoricalCanvas(null);
        setHistoricalStrategyAlgorithm('');
        setHistoricalStrategyPythonCode('');
        navigate(
          { pathname: location.pathname, search: location.search, hash: '' },
          { replace: true },
        );
        setError(err.message);
      } finally {
        if (canvasLoadSeqRef.current === seq) {
          setCanvasLoading(false);
        }
      }
    },
    [authFetch, navigate, location.pathname, location.search, mergeStrategyNameFromPayload],
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

  const handleSubmit = useCallback(async (messageText) => {
    const message = String(messageText ?? '').trim();
    if (!message || submittingRef.current || serverJobStatusRef.current === 'running') {
      return { accepted: false };
    }

    setSubmitting(true);
    setStreamingAssistantRunId('');
    setError('');
    setViewingRunId(null);
    setHistoricalCanvas(null);
    setHistoricalStrategyPythonCode('');
    shouldStickToChatBottomRef.current = true;
    navigate(
      { pathname: location.pathname, search: location.search, hash: '' },
      { replace: true },
    );
    optimisticUserContentRef.current = message;
    setMessages((prev) => [...prev, { role: 'user', content: message }]);

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
          setMessagesFromStrategyPayload(payload);
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
          }
        }
        if (payload.canvas) {
          setCanvasIfChanged(payload.canvas);
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
      setMessagesFromStrategyPayload(payload);
      setCanvasIfChanged(payload.canvas);
      if (typeof payload.id === 'string') {
        setLiveStrategyRunId(payload.id);
      }
      if (typeof payload.algorithm === 'string') {
        setLiveStrategyAlgorithm(payload.algorithm);
      }
      setLiveStrategyPythonCode(typeof payload.python_code === 'string' ? payload.python_code : '');
      setServerJob({
        status: payload.status ?? null,
        statusText: payload.status_text || '',
      });
      mergeStrategyNameFromPayload(payload);
      if (payload.status !== 'running') {
        setSubmitting(false);
      }
      return { accepted: true, ok: true };
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
      }
      setError(submitError instanceof Error ? submitError.message : String(submitError));
      return { accepted: true, ok: false };
    }
  }, [
    authFetch,
    location.pathname,
    location.search,
    mergeStrategyNameFromPayload,
    navigate,
    setCanvasIfChanged,
    setMessagesFromStrategyPayload,
    threadId,
  ]);

  const handleViewStrategy = useCallback(async (runId) => {
    await handleViewRun(runId);
    setMobileCanvasOpen(true);
  }, [handleViewRun]);

  useEffect(() => {
    setViewingRunId(null);
    setHistoricalCanvas(null);
    setCanvas({});
    setCanvasLoading(false);
    setChartError('');
    setLiveStrategyRunId('');
    setLiveStrategyAlgorithm('');
    setHistoricalStrategyAlgorithm('');
    setLiveStrategyPythonCode('');
    setHistoricalStrategyPythonCode('');
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
    lastStreamEventSeqByRunIdRef.current = {};
    setStreamingAssistantRunId('');
    setStrategyNameByRunId({});
    setEditingCanvasTitle(false);
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
    setHistoricalStrategyPythonCode('');
    const ignoredRunId =
      serverJobStatusRef.current === 'running'
        ? String(liveStrategyRunIdRef.current || '').trim()
        : '';
    if (ignoredRunId) {
      ignoredStreamRunIdsRef.current.add(ignoredRunId);
    }
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
        `${API_BASE_URL}/strategy?thread_id=${encodeURIComponent(threadId)}&include_canvas=0`,
      );
      if (!refreshed.ok) {
        throw new Error('Reverted, but failed to reload thread');
      }
      const next = await refreshed.json();
      setMessagesFromStrategyPayload(next);
      setLiveStrategyRunId(typeof next.id === 'string' ? next.id : '');
      setLiveStrategyAlgorithm(typeof next.algorithm === 'string' ? next.algorithm : '');
      setLiveStrategyPythonCode(typeof next.python_code === 'string' ? next.python_code : '');
      setServerJob({
        status: next.status ?? null,
        statusText: next.status_text || '',
      });
      setSubmitting(false);
      setStreamingAssistantRunId('');
      mergeStrategyNameFromPayload(next);
      void fetchLiveCanvas();
      setRevertRunRequest('');
    } catch (err) {
      if (ignoredRunId) {
        ignoredStreamRunIdsRef.current.delete(ignoredRunId);
      }
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
      setDeleteThreadDialogOpen(false);
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
        setCanvasLoading(false);
        setError('');
        const response = await authFetch(
          `${API_BASE_URL}/strategy?thread_id=${encodeURIComponent(threadId)}&include_canvas=0`,
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
        setMessagesFromStrategyPayload(payload);
        setLiveStrategyRunId(typeof payload.id === 'string' ? payload.id : '');
        setLiveStrategyAlgorithm(typeof payload.algorithm === 'string' ? payload.algorithm : '');
        setLiveStrategyPythonCode(typeof payload.python_code === 'string' ? payload.python_code : '');
        setServerJob({
          status: payload.status ?? null,
          statusText: payload.status_text || '',
        });
        mergeStrategyNameFromPayload(payload);
        const loc = locationRef.current;
        const rawDraft = loc?.state?.draft;
        const draftText = typeof rawDraft === 'string' ? rawDraft.trim() : '';
        if (msgs.length === 0 && draftText && !homePromptAutoSubmitRef.current) {
          homePromptAutoSubmitRef.current = true;
          navigate('.', { replace: true, state: {} });
          setTimeout(() => {
            void handleSubmit(draftText);
          }, 0);
        } else if (msgs.length > 0) {
          void fetchLiveCanvas(controller.signal);
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
  }, [
    threadId,
    signedInUserId,
    mergeStrategyNameFromPayload,
    handleSubmit,
    fetchLiveCanvas,
    setMessagesFromStrategyPayload,
  ]);

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
          setMessagesFromStrategyPayload(payload);
          setCanvasIfChanged(payload.canvas);
          if (typeof payload.id === 'string') {
            setLiveStrategyRunId(payload.id);
          }
          if (typeof payload.algorithm === 'string') {
            setLiveStrategyAlgorithm(payload.algorithm);
          }
          setLiveStrategyPythonCode(typeof payload.python_code === 'string' ? payload.python_code : '');
          setServerJob({
            status: payload.status ?? null,
            statusText: payload.status_text || '',
          });
          mergeStrategyNameFromPayload(payload);
          if (payload.status !== 'running') {
            setSubmitting(false);
            evtSource.close();
          }
        } catch {
          /* ignore malformed events */
        }
      };

      evtSource.addEventListener('assistant_delta', (event) => {
        try {
          applyAssistantDelta(JSON.parse(event.data));
        } catch {
        }
      });

      evtSource.addEventListener('agent_status', (event) => {
        try {
          const payload = JSON.parse(event.data);
          if (!shouldApplyStreamEvent(payload)) {
            return;
          }
          setServerJob((prev) => ({
            ...prev,
            status: prev.status || 'running',
            statusText: payload.status_text || prev.statusText || '',
          }));
        } catch {
        }
      });

      evtSource.addEventListener('agent_error', (event) => {
        try {
          const payload = JSON.parse(event.data);
          if (shouldApplyStreamEvent(payload) && payload.message) {
            setError(payload.message);
          }
        } catch {
        }
      });

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
  }, [
    threadId,
    serverJob.status,
    getAccessToken,
    syncLiveStrategyFromServer,
    mergeStrategyNameFromPayload,
    setCanvasIfChanged,
    setMessagesFromStrategyPayload,
    applyAssistantDelta,
    shouldApplyStreamEvent,
  ]);

  useLayoutEffect(() => {
    if (skipNextChatEndScrollRef.current) {
      skipNextChatEndScrollRef.current = false;
      return undefined;
    }
    if (!shouldStickToChatBottomRef.current) {
      return undefined;
    }
    const behavior = serverJob.status === 'running' || streamingAssistantRunId ? 'auto' : 'smooth';
    scheduleChatScrollToBottom(behavior);
    return undefined;
  }, [
    messages,
    submitting,
    serverJob.status,
    streamingAssistantRunId,
    scheduleChatScrollToBottom,
  ]);

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
    let detachChartDnD;
    try {
      const rendered = renderCharts(root, chartData, { chartOrderStorageBase: threadId, timeZone, hourFormat });
      detachChartDnD = rendered.detachChartDnD;
      detachSync = attachSyncedTimeScales(rendered.lwCharts);
      detachCrosshair = attachSyncedCrosshair(rendered.lwCrosshairBindings);
    } catch (err) {
      setChartError(err instanceof Error ? err.message : String(err));
    }

    return () => {
      detachChartDnD?.();
      detachSync?.();
      detachCrosshair?.();
      mount.innerHTML = '';
    };
  }, [displayOutput, hourFormat, threadId, timeZone]);

  const showProcessing =
    (submitting || serverJob.status === 'running') &&
    (!streamingAssistantRunId || streamingAssistantRunId !== liveStrategyRunId);
  const processingLabel =
    serverJob.status === 'running'
      ? serverJob.statusText?.trim() || 'Working…'
      : submitting
        ? 'Sending…'
        : 'Working…';

  const sortedThreads = useMemo(
    () =>
      [...threads].sort((a, b) => {
        const at = parseIsoTime(a?.latest_created_at) ?? -1;
        const bt = parseIsoTime(b?.latest_created_at) ?? -1;
        return bt - at;
      }),
    [threads],
  );

  const todayKey = isoDateTodayKey(timeZone);
  const groupedThreads = useMemo(
    () =>
      sortedThreads.reduce((acc, t) => {
        const key = dateKeyFromIso(t?.latest_created_at, timeZone) || 'Unknown date';
        if (!acc[key]) {
          acc[key] = [];
        }
        acc[key].push(t);
        return acc;
      }, {}),
    [sortedThreads, timeZone],
  );
  const groupKeys = useMemo(
    () =>
      Object.keys(groupedThreads).sort((a, b) => {
        if (a === 'Unknown date') return 1;
        if (b === 'Unknown date') return -1;
        return b.localeCompare(a);
      }),
    [groupedThreads],
  );

  const output = displayOutput;
  const outputDerived = useMemo(
    () => ({
      nameFromRunOutput: strategyNameFromOutput(output),
      cliDescriptionText: strategyCliDescriptionFromOutput(output),
      paramsJsonText: paramsJsonFromOutput(output),
      paramsHyperoptJsonText: paramsHyperoptJsonFromOutput(output),
      hasMetrics: metricsJsonFromOutput(output) != null,
      hasRenderableCharts: hasRenderableChartOutput(output),
      hasTrades: hasStrategyTrades(output),
    }),
    [output],
  );
  const { nameFromRunOutput } = outputDerived;
  const strategyName = useMemo(() => {
    const r = String(displayStrategyRunId || '').trim();
    if (!r) {
      return nameFromRunOutput || 'Strategy';
    }
    if (Object.prototype.hasOwnProperty.call(strategyNameByRunId, r)) {
      const db = String(strategyNameByRunId[r] ?? '').trim();
      if (db) {
        return db;
      }
      return nameFromRunOutput || 'Strategy';
    }
    return nameFromRunOutput || 'Strategy';
  }, [displayStrategyRunId, strategyNameByRunId, nameFromRunOutput]);

  async function commitCanvasTitle() {
    if (skipNextCanvasTitleBlurCommitRef.current) {
      skipNextCanvasTitleBlurCommitRef.current = false;
      return;
    }
    const rid = String(displayStrategyRunId || '').trim();
    if (!rid) {
      setEditingCanvasTitle(false);
      return;
    }
    const next = canvasTitleDraft.trim();
    if (next === String(strategyName || '').trim()) {
      setEditingCanvasTitle(false);
      return;
    }
    setSavingCanvasTitle(true);
    setError('');
    try {
      const res = await authFetch(`${API_BASE_URL}/strategy`, {
        method: 'PATCH',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ id: rid, strategy_name: next }),
      });
      const data = await res.json().catch(() => ({}));
      if (!res.ok) {
        setError(typeof data.error === 'string' ? data.error : 'Failed to save name');
        return;
      }
      mergeStrategyNameFromPayload(data);
      setEditingCanvasTitle(false);
      await refreshThreads();
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e));
    } finally {
      setSavingCanvasTitle(false);
    }
  }

  function startEditCanvasTitle() {
    if (!String(displayStrategyRunId || '').trim() || savingCanvasTitle) {
      return;
    }
    setCanvasTitleDraft(strategyName);
    setEditingCanvasTitle(true);
  }

  function cancelEditCanvasTitle() {
    skipNextCanvasTitleBlurCommitRef.current = true;
    setEditingCanvasTitle(false);
  }

  const cliDescriptionText = outputDerived.cliDescriptionText;
  const showCliDescription = cliDescriptionText != null;
  const paramsJsonText = outputDerived.paramsJsonText;
  const showParamsPanel = paramsJsonText != null;
  const paramsHyperoptJsonText = outputDerived.paramsHyperoptJsonText;
  const showHyperoptParamsPanel = paramsHyperoptJsonText != null;
  const showMetricsPanel = outputDerived.hasMetrics;
  const showPythonCodePanel = String(displayPythonCodeText || '').trim().length > 0;
  const hasAnyCanvasData =
    showCliDescription ||
    showParamsPanel ||
    showHyperoptParamsPanel ||
    showMetricsPanel ||
    outputDerived.hasRenderableCharts;
  const showSimulationTab = outputDerived.hasRenderableCharts;
  const showCanvasLoading = canvasLoading;

  useEffect(() => {
    if (!showSimulationTab && canvasTab === 'simulation') {
      setCanvasTab('strategy');
    }
  }, [showSimulationTab, canvasTab]);

  const currentThreadMeta = useMemo(
    () => threads.find((t) => t?.thread_id && t.thread_id === threadId) || null,
    [threads, threadId],
  );
  const strategyAvailable =
    (!loading && Array.isArray(messages) && messages.length > 0) ||
    (Number.isFinite(Number(currentThreadMeta?.message_count)) &&
      Number(currentThreadMeta?.message_count) > 0);
  const deployableStrategyHasTrades = outputDerived.hasTrades;
  const deployDisabled =
    loading ||
    showCanvasLoading ||
    showProcessing ||
    !strategyAvailable ||
    !displayStrategyRunId ||
    !deployableStrategyHasTrades;
  const deployTitle = loading
    ? 'Strategy is loading'
    : showCanvasLoading
      ? 'Strategy canvas is loading'
    : showProcessing
      ? 'Wait for the strategy run to finish'
      : !strategyAvailable
        ? 'Strategy not available yet'
        : !deployableStrategyHasTrades
          ? 'Live deployment requires a strategy run with at least one trade'
          : 'Deploy live';

  const handleRequestRevertRun = useCallback((runId) => {
    setRevertRunRequest(String(runId || '').trim());
  }, []);

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
    <div className="dashboard-page strategy-shell">
      <header className="dashboard-topbar">
        <div className="dashboard-topbar-left">
          <Link to="/" className="app-home-link" aria-label="Go to home page">
            <span className="app-logo">TraderChat</span>
          </Link>
          <span className="dashboard-topbar-sep" aria-hidden>
            /
          </span>
          <span className="dashboard-topbar-crumb">Strategy</span>
        </div>
        <div className="dashboard-topbar-right">
          <Link className="dashboard-topbar-crumb dashboard-topbar-link" to="/dashboard">
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
    <main className={`layout${isNarrow ? ' layout-narrow' : ''}${mobileCanvasOpen ? ' is-mobile-canvas-open' : ''}`}>
      <div className="layout-dual" ref={layoutDualRef}>
        <section ref={chatPanelRef} className="chat-panel" style={chatPanelStyle}>
        <header className="chat-header">
          <div className="chat-header-top">
            <button
              type="button"
              className="sidebar-toggle"
              onClick={() => setSidebarOpen(true)}
              aria-label="Open sidebar"
            >
              ☰
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
          </div>
        </header>

        <div className="chat-stream" ref={chatStreamRef}>
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
              isActive={
                message.role === 'assistant' &&
                Boolean(message.run_id) &&
                !message.streaming &&
                message.run_id === viewingRunId
              }
              onViewRun={handleViewRun}
              onViewStrategy={handleViewStrategy}
              onRevertRun={handleRequestRevertRun}
              revertDisabled={reverting}
              showViewStrategy={isNarrow && !mobileCanvasOpen && hasAnyCanvasData}
            />
          ))}
          {showProcessing ? <ChatProcessingSpinner label={processingLabel} /> : null}
          <div ref={chatEndRef} />
        </div>

        <ChatComposer
          panelRef={chatPanelRef}
          loading={loading}
          prompts={emptyThreadPrompts}
          showProcessing={showProcessing}
          showSuggestedPrompts={!loading && messages.length === 0}
          onSubmit={handleSubmit}
        />
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
          <div className="canvas-hero-main">
          <div className="canvas-hero-title-row">
            {editingCanvasTitle && String(displayStrategyRunId || '').trim() ? (
              <input
                ref={canvasTitleInputRef}
                className="canvas-hero-title-input"
                value={canvasTitleDraft}
                onChange={(e) => setCanvasTitleDraft(e.target.value)}
                onKeyDown={(e) => {
                  if (e.key === 'Enter') {
                    e.preventDefault();
                    void commitCanvasTitle();
                  } else if (e.key === 'Escape') {
                    e.preventDefault();
                    cancelEditCanvasTitle();
                  }
                }}
                onBlur={() => {
                  void commitCanvasTitle();
                }}
                disabled={savingCanvasTitle}
                maxLength={512}
                aria-label="Strategy name"
              />
            ) : (
              <h2 className="canvas-hero-title">{strategyName || 'Strategy'}</h2>
            )}
            {String(displayStrategyRunId || '').trim() && !editingCanvasTitle && !savingCanvasTitle ? (
              <button
                type="button"
                className="button-canvas-edit-title"
                onClick={startEditCanvasTitle}
                aria-label="Edit strategy name"
                title="Edit name"
              >
                <svg viewBox="0 0 24 24" fill="none" aria-hidden>
                  <path
                    d="M4 20.5L4 16.5L15.2 5.3C15.7 4.8 16.3 4.5 17 4.5C18.1 4.5 19 5.4 19 6.5C19 7.1 18.7 7.7 18.2 8.2L7 19.4L4 20.5Z"
                    stroke="currentColor"
                    strokeWidth="1.75"
                    strokeLinejoin="round"
                  />
                  <path
                    d="M12.5 6.5L16.5 10.5"
                    stroke="currentColor"
                    strokeWidth="1.75"
                    strokeLinecap="round"
                  />
                </svg>
              </button>
            ) : null}
          </div>
            <div className="canvas-hero-actions">
              <button
                type="button"
                className={`canvas-header-action${canvasTab === 'strategy' || !showSimulationTab ? ' is-active' : ''}`}
                onClick={() => setCanvasTab('strategy')}
                aria-pressed={canvasTab === 'strategy' || !showSimulationTab}
              >
                Backtest
              </button>
              <span
                className="canvas-header-action-wrap"
                title={showSimulationTab ? 'Simulation' : 'Run a backtest first'}
              >
                <button
                  type="button"
                  className={`canvas-header-action${canvasTab === 'simulation' && showSimulationTab ? ' is-active' : ''}`}
                  onClick={() => {
                    if (showSimulationTab) {
                      setCanvasTab('simulation');
                    }
                  }}
                  disabled={!showSimulationTab}
                  aria-pressed={canvasTab === 'simulation' && showSimulationTab}
                  title={showSimulationTab ? 'Simulation' : 'Run a backtest first'}
                >
                  Simulation
                </button>
              </span>
              <span className="canvas-header-action-wrap" title={deployTitle}>
                <button
                  type="button"
                  className="canvas-header-action"
                  disabled={deployDisabled}
                  onClick={() => {
                    if (deployDisabled) return;
                    setDeployModalOpen(true);
                  }}
                  aria-label="Deploy live"
                  aria-disabled={deployDisabled}
                  title={deployTitle}
                >
                  🚀 Live 🚀
                </button>
              </span>
              <button
                type="button"
                className="canvas-header-action canvas-header-action-icon canvas-header-action-danger"
                onClick={() => setDeleteThreadDialogOpen(true)}
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
          </div>
        </header>
        {canvasTab === 'simulation' && showSimulationTab ? (
          <SimulationPanel
            threadId={threadId}
            apiBaseUrl={API_BASE_URL}
            authFetch={authFetch}
            getAccessToken={getAccessToken}
          />
        ) : null}
        {canvasTab === 'strategy' || !showSimulationTab ? (
        <>
        {showCanvasLoading ? (
          <div
            className="chat-spinner-row canvas-loading-row"
            role="status"
            aria-live="polite"
            aria-label="Loading strategy canvas"
          >
            <span className="chat-spinner" aria-hidden />
            <span className="chat-processing-label">Loading strategy canvas…</span>
          </div>
        ) : null}
        {showCliDescription ? (
          <article className="canvas-text-block" aria-label="Strategy description">
            <h3 className="canvas-text-block-title">Description</h3>
            <div className="canvas-text-block-body">{cliDescriptionText}</div>
          </article>
        ) : null}
        {chartError ? <p className="canvas-chart-error">{chartError}</p> : null}
        {!showCanvasLoading && !outputDerived.hasRenderableCharts && !chartError ? (
          <p className="canvas-charts-placeholder muted">
            No charts yet. Send a message to refresh the strategy run.
          </p>
        ) : null}
        <div
          ref={chartsMountRef}
          className="canvas-charts-mount canvas-charts-mount-inline"
          aria-label="Strategy backtest charts"
        />
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
                <ReactMarkdown remarkPlugins={MARKDOWN_REMARK_PLUGINS}>
                  {displayAlgorithmText?.trim() || ''}
                </ReactMarkdown>
              </div>
            )}
          </details>
        ) : null}
        {showPythonCodePanel ? (
          <details
            key={`source:${displayStrategyRunId}`}
            className="canvas-text-block canvas-text-block-pseudocode canvas-pseudocode-details"
          >
            <summary className="canvas-pseudocode-summary">Python Source Code</summary>
            <CanvasPanelCopyButton
              text={displayPythonCodeText}
              ariaLabel="Copy Python source code"
            />
            <PythonSourceCode code={displayPythonCodeText} />
          </details>
        ) : null}
        </>
        ) : null}
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
    </div>
    <ConfirmDialog
      open={Boolean(revertRunRequest)}
      title="Revert thread?"
      message="Revert this thread to this agent message? This will delete all later strategy runs for this thread."
      confirmLabel={reverting ? 'Reverting…' : 'Revert'}
      icon="history"
      busy={reverting}
      danger
      onCancel={() => {
        if (!reverting) setRevertRunRequest('');
      }}
      onConfirm={() => void handleRevertRun(revertRunRequest)}
    />
    <ConfirmDialog
      open={deleteThreadDialogOpen}
      title="Delete strategy thread?"
      message={`Delete "${threadDisplayName(currentThreadMeta)}"? This cannot be undone.`}
      confirmLabel={deletingThread ? 'Deleting…' : 'Delete'}
      busy={deletingThread}
      danger
      onCancel={() => {
        if (!deletingThread) setDeleteThreadDialogOpen(false);
      }}
      onConfirm={() => void handleDeleteThread()}
    />
    {deployModalOpen
      ? createPortal(
          <div className="deploy-live-modal" role="dialog" aria-modal="true" aria-label="Deploy to Alpaca">
            <button
              type="button"
              className="deploy-live-modal-scrim"
              aria-label="Close"
              onClick={() => {
                if (!deploySubmitting) {
                  setDeployModalOpen(false);
                  setDeployModalPhase('loading');
                }
              }}
            />
            <div className="deploy-live-modal-panel">
              <div className="deploy-live-modal-head">
                <h2 className="deploy-live-modal-title">Deploy to Alpaca</h2>
                <button
                  type="button"
                  className="deploy-live-modal-close"
                  disabled={deploySubmitting}
                  aria-label="Close"
                  onClick={() => {
                    setDeployModalOpen(false);
                    setDeployModalPhase('loading');
                  }}
                >
                  ×
                </button>
              </div>
              {deployModalPhase === 'loading' ? <p className="deploy-live-modal-muted">Loading accounts…</p> : null}
              {deployModalPhase === 'ready' && deployModalError ? (
                <p className="deploy-live-modal-error">{deployModalError}</p>
              ) : null}
              {deployModalPhase === 'ready' && deployTradingConfigured && deployAccounts.length === 0 ? (
                <p className="deploy-live-modal-muted">
                  No Alpaca accounts yet. Add at least one in Settings, then open this dialog again.
                </p>
              ) : null}
              {deployModalPhase === 'ready' && deployTradingConfigured && deployAccounts.length > 0 && !deploySelectedAccountReady ? (
                <p className="deploy-live-modal-muted">
                  Select an Alpaca account with a saved API key and secret.
                </p>
              ) : null}
              {deployModalPhase === 'ready' && deployTradingConfigured && deployAccounts.length > 0 ? (
                <div className="deploy-live-modal-accounts" role="group" aria-label="Alpaca account">
                  {deployAccounts.map((a) => {
                    const id = String(a.id || '').trim();
                    if (!id) return null;
                    const lab = typeof a.label === 'string' && a.label.trim() ? a.label.trim() : id.slice(0, 8);
                    const mode = a.is_live ? 'Live' : 'Paper';
                    const accountReady = Boolean(a.has_alpaca_api_key && a.has_alpaca_secret_key);
                    return (
                      <label key={id} className="deploy-live-account-option">
                        <input
                          type="radio"
                          name="deploy-alpaca-account"
                          value={id}
                          disabled={!accountReady}
                          checked={deploySelectedAccountId === id}
                          onChange={() => setDeploySelectedAccountId(id)}
                        />
                        <span>
                          <span className="deploy-live-account-label">{lab}</span>
                          <span className="deploy-live-modal-muted">
                            {' '}
                            · {mode}
                            {!accountReady ? ' · missing credentials' : ''}
                          </span>
                        </span>
                      </label>
                    );
                  })}
                </div>
              ) : null}
              {deployModalPhase === 'ready' && !deployTradingConfigured ? (
                <p className="deploy-live-modal-muted">
                  Trading settings are not available on the server. Deploy will use paper mode and server default
                  Alpaca environment variables.
                </p>
              ) : null}
              <div className="deploy-live-modal-actions">
                <button type="button" className="dashboard-btn-ghost" onClick={openTradingSettingsWindow}>
                  Settings
                </button>               
                <button
                  type="button"
                  className="dashboard-btn-primary"
                  disabled={
                    deploySubmitting ||
                    deployModalPhase !== 'ready' ||
                    (deployTradingConfigured &&
                      (deployAccounts.length === 0 ||
                        !String(deploySelectedAccountId || '').trim() ||
                        !deploySelectedAccountReady))
                  }
                  onClick={async () => {
                    setDeployModalError('');
                    const tid = String(threadId || '').trim();
                    const rid = String(displayStrategyRunId || '').trim();
                    if (!tid || !rid) {
                      setDeployModalError('Missing thread_id or strategy run id');
                      return;
                    }
                    if (deployTradingConfigured) {
                      if (!deployAccounts.length) {
                        setDeployModalError('Add an Alpaca account in Settings first.');
                        return;
                      }
                      const sid = String(deploySelectedAccountId || '').trim();
                      if (!sid) {
                        setDeployModalError('Select an Alpaca account.');
                        return;
                      }
                      if (!deploySelectedAccountReady) {
                        setDeployModalError('Select an Alpaca account with saved API credentials.');
                        return;
                      }
                    }
                    setDeploySubmitting(true);
                    setError('');
                    try {
                      const body = {
                        thread_id: tid,
                        enable_trading: true,
                        strategy_id: rid,
                      };
                      if (deployTradingConfigured && String(deploySelectedAccountId || '').trim()) {
                        body.alpaca_account_id = String(deploySelectedAccountId || '').trim();
                      } else {
                        body.paper = true;
                      }
                      const res = await authFetch(`${API_BASE_URL}/live/start`, {
                        method: 'POST',
                        headers: { 'Content-Type': 'application/json' },
                        body: JSON.stringify(body),
                      });
                      const payload = await res.json().catch(() => ({}));
                      if (!res.ok) {
                        throw new Error(payload.error || `Deploy failed (${res.status})`);
                      }
                      const liveRunId = String(payload.run_id || '').trim();
                      setDeployModalOpen(false);
                      setDeployModalPhase('loading');
                      if (liveRunId) {
                        window.open(`/live/${encodeURIComponent(liveRunId)}`, '_blank', 'noopener,noreferrer');
                      }
                    } catch (e) {
                      setError(e instanceof Error ? e.message : String(e));
                    } finally {
                      setDeploySubmitting(false);
                    }
                  }}
                >
                  Deploy
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
