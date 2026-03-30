import { useEffect, useRef, useState } from 'react';
import { useNavigate, useParams } from 'react-router-dom';
import ReactMarkdown from 'react-markdown';
import remarkGfm from 'remark-gfm';
import { loadStrategyChartsModule } from '../strategyChartsModule.js';

function DiffView({ diff }) {
  const lines = (diff || '').split('\n');
  return (
    <pre className="canvas-pseudocode-diff">
      {lines.map((line, i) => {
        let cls = 'diff-ctx';
        if (line.startsWith('+')) cls = 'diff-add';
        else if (line.startsWith('-')) cls = 'diff-del';
        else if (line.startsWith('@@')) cls = 'diff-hunk';
        return (
          <span key={i} className={cls}>
            {line}
            {'\n'}
          </span>
        );
      })}
    </pre>
  );
}

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
      <span className="message-role">Agent</span>
      <div className="chat-spinner-row">
        <span className="chat-spinner" aria-hidden />
        <span className="chat-processing-label">{text}</span>
      </div>
    </div>
  );
}

const API_BASE_URL = import.meta.env.VITE_API_BASE_URL || 'http://localhost:5000';

function MessageBubble({ message, activeRunId, onViewRun }) {
  const isAssistant = message.role === 'assistant';
  const hasRunId = isAssistant && message.run_id;
  const isActive = hasRunId && message.run_id === activeRunId;
  return (
    <div
      className={`message message-${message.role}${hasRunId ? ' message-clickable' : ''}${isActive ? ' message-active-run' : ''}`}
      title={hasRunId ? 'Click to view strategy output' : undefined}
      onClick={hasRunId ? () => onViewRun(message.run_id) : undefined}
      role={hasRunId ? 'button' : undefined}
      tabIndex={hasRunId ? 0 : undefined}
      onKeyDown={hasRunId ? (e) => { if (e.key === 'Enter' || e.key === ' ') onViewRun(message.run_id); } : undefined}
    >
      <span className="message-role">{isAssistant ? 'Agent' : 'You'}</span>
      {isAssistant ? (
        <div className="message-markdown">
          <ReactMarkdown remarkPlugins={[remarkGfm]}>{message.content || ''}</ReactMarkdown>
        </div>
      ) : (
        <p>{message.content}</p>
      )}
    </div>
  );
}

function hasRenderableChartOutput(output) {
  if (!output || typeof output !== 'object') {
    return false;
  }
  const chartsSource = output['charts.js'];
  const chartData = output['data.json'];
  return (
    typeof chartsSource === 'string' &&
    chartsSource.trim().length > 0 &&
    chartData != null
  );
}

function hasNonEmptyOutputText(value) {
  return typeof value === 'string' && value.trim().length > 0;
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
  const name = data.strategy_name;
  return typeof name === 'string' && name.trim() ? name.trim() : '';
}

function normalizeLightweightChartTimes(data) {
  if (!data || typeof data !== 'object' || !data.chart_data || typeof data.chart_data !== 'object') {
    return data;
  }
  const chartData = data.chart_data;
  const keys = [
    'candles',
    'mean',
    'upper_band',
    'lower_band',
    'equity_curve',
    'zscore',
    'trade_markers',
  ];
  const nextCd = { ...chartData };
  for (const key of keys) {
    const arr = chartData[key];
    if (!Array.isArray(arr)) {
      continue;
    }
    nextCd[key] = arr.map((row) => {
      if (!row || typeof row !== 'object') {
        return row;
      }
      const t = row.time;
      if (typeof t === 'string' && t.includes('T')) {
        return { ...row, time: t.slice(0, 10) };
      }
      return row;
    });
  }
  return { ...data, chart_data: nextCd };
}

function attachSyncedTimeScales(charts) {
  if (charts.length < 2) {
    return undefined;
  }
  let syncing = false;
  const subscriptions = charts.map((chart) => {
    const handler = (range) => {
      if (syncing || range === null) {
        return;
      }
      syncing = true;
      try {
        for (const other of charts) {
          if (other !== chart) {
            other.timeScale().setVisibleRange({
              from: range.from,
              to: range.to,
            });
          }
        }
      } finally {
        syncing = false;
      }
    };
    chart.timeScale().subscribeVisibleTimeRangeChange(handler);
    return { chart, handler };
  });
  return () => {
    for (const { chart, handler } of subscriptions) {
      chart.timeScale().unsubscribeVisibleTimeRangeChange(handler);
    }
  };
}

export function StrategyPage() {
  const { threadId } = useParams();
  const navigate = useNavigate();
  const [messages, setMessages] = useState([]);
  const [canvas, setCanvas] = useState({});
  const [draft, setDraft] = useState('');
  const [loading, setLoading] = useState(true);
  const [submitting, setSubmitting] = useState(false);
  const [serverJob, setServerJob] = useState({ status: null, statusText: '' });
  const [error, setError] = useState('');
  const chatEndRef = useRef(null);
  const chatFormRef = useRef(null);
  const optimisticUserContentRef = useRef(null);
  const chartsMountRef = useRef(null);
  const [chartError, setChartError] = useState('');
  const [viewingRunId, setViewingRunId] = useState(null);
  const [historicalCanvas, setHistoricalCanvas] = useState(null);

  async function handleViewRun(runId) {
    if (runId === viewingRunId) {
      setViewingRunId(null);
      setHistoricalCanvas(null);
      return;
    }
    try {
      const response = await fetch(
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

  useEffect(() => {
    const controller = new AbortController();

    async function loadThread() {
      try {
        setLoading(true);
        setError('');
        const response = await fetch(
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

    loadThread();
    return () => controller.abort();
  }, [threadId]);

  useEffect(() => {
    if (serverJob.status !== 'running') {
      return undefined;
    }

    const evtSource = new EventSource(
      `${API_BASE_URL}/strategy/stream?thread_id=${encodeURIComponent(threadId)}`,
    );

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

    return () => {
      evtSource.close();
    };
  }, [threadId, serverJob.status]);

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
    const chartsSource = output['charts.js'];
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
    if (typeof chartsSource !== 'string' || !chartsSource.trim()) {
      mount.innerHTML = '';
      return undefined;
    }
    if (chartData === null || typeof chartData !== 'object') {
      mount.innerHTML = '';
      return undefined;
    }
    chartData = normalizeLightweightChartTimes(chartData);
    mount.innerHTML = '';
    const root = document.createElement('div');
    root.className = 'strategy-charts-root';
    mount.appendChild(root);

    let cancelled = false;
    const stateRef = { detachTimeScaleSync: undefined, revokeModuleUrl: undefined };

    (async () => {
      try {
        const mod = await loadStrategyChartsModule(chartsSource);
        if (cancelled) {
          mod.revokeModuleUrl();
          return;
        }
        stateRef.revokeModuleUrl = mod.revokeModuleUrl;
        mod.render_charts(root, chartData);
        stateRef.detachTimeScaleSync = attachSyncedTimeScales(mod.getCollectedCharts());
      } catch (err) {
        if (!cancelled) {
          setChartError(err instanceof Error ? err.message : String(err));
        }
      }
    })();

    return () => {
      cancelled = true;
      stateRef.detachTimeScaleSync?.();
      stateRef.revokeModuleUrl?.();
      stateRef.detachTimeScaleSync = undefined;
      stateRef.revokeModuleUrl = undefined;
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
      const response = await fetch(`${API_BASE_URL}/strategy`, {
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

  const output = displayOutput;
  const strategyName = strategyNameFromOutput(output);
  const summaryText = output && typeof output === 'object' ? output['summary.txt'] : undefined;
  const pseudocodeText = output && typeof output === 'object' ? output['pseudocode.txt'] : undefined;
  const pseudocodeDiff = output && typeof output === 'object' ? output['pseudocode.diff'] : undefined;
  const showSummary = hasNonEmptyOutputText(summaryText);
  const showPseudocode = hasNonEmptyOutputText(pseudocodeText);
  const showPseudocodeDiff = hasNonEmptyOutputText(pseudocodeDiff);

  return (
    <main className="layout">
      <section className="chat-panel">
        <header className="chat-header">
          <div className="chat-header-top">
            <div>
              <span className="app-logo">VibeTrader</span>
            </div>
            <button
              type="button"
              className="button-new-thread"
              onClick={() => navigate(`/strategy/${crypto.randomUUID()}`)}
            >
              New strategy
            </button>
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
            />
          ))}
          {showProcessing ? <ChatProcessingSpinner label={processingLabel} /> : null}
          <div ref={chatEndRef} />
        </div>

        <form ref={chatFormRef} className="chat-input" onSubmit={handleSubmit}>
          <label htmlFor="message" className="sr-only">
            Message
          </label>
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
          <div className="chat-actions">
            <span className="status chat-actions-status">
 
            </span>
            <button type="submit" disabled={showProcessing}>
              Send
            </button>
          </div>
        </form>
      </section>

      <section className="canvas-panel canvas-panel-charts">
        <header className="canvas-hero">
          <h2>{strategyName || 'Strategy'}</h2>
          {viewingRunId ? (
            <button
              type="button"
              className="button-back-to-current"
              onClick={() => { setViewingRunId(null); setHistoricalCanvas(null); }}
            >
              Back to current
            </button>
          ) : null}
        </header>
        {showSummary ? (
          <article className="canvas-text-block" aria-label="Strategy summary">
            <h3 className="canvas-text-block-title">Summary</h3>
            <div className="canvas-text-block-body">{summaryText}</div>
          </article>
        ) : null}
        {showPseudocode ? (
          <details className="canvas-text-block canvas-text-block-pseudocode canvas-pseudocode-details">
            <summary className="canvas-pseudocode-summary">Pseudocode</summary>
            {showPseudocodeDiff ? (
              <details className="canvas-pseudocode-diff-details" open>
                <summary className="canvas-pseudocode-diff-summary">Changes</summary>
                <DiffView diff={pseudocodeDiff} />
              </details>
            ) : null}
            <pre className="canvas-pseudocode">{pseudocodeText}</pre>
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
