import { useEffect, useRef, useState } from 'react';
import { useNavigate, useParams } from 'react-router-dom';
import { loadStrategyChartsModule } from '../strategyChartsModule.js';

function ChatProcessingSpinner() {
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
        <span className="chat-processing-label">Working…</span>
      </div>
    </div>
  );
}

const API_BASE_URL = import.meta.env.VITE_API_BASE_URL || 'http://localhost:5000';

function MessageBubble({ message }) {
  return (
    <div className={`message message-${message.role}`}>
      <span className="message-role">{message.role === 'assistant' ? 'Agent' : 'You'}</span>
      <p>{message.content}</p>
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
  const name = data.strategy && data.strategy.name;
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
  const [error, setError] = useState('');
  const chatEndRef = useRef(null);
  const chatFormRef = useRef(null);
  const optimisticUserContentRef = useRef(null);
  const chartsMountRef = useRef(null);
  const [chartError, setChartError] = useState('');

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
    chatEndRef.current?.scrollIntoView({ behavior: 'smooth' });
  }, [messages, submitting]);

  useEffect(() => {
    const mount = chartsMountRef.current;
    const output = canvas.output;
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
  }, [canvas.output]);

  async function handleSubmit(event) {
    event.preventDefault();
    const message = draft.trim();
    if (!message || submitting) {
      return;
    }

    setSubmitting(true);
    setError('');
    optimisticUserContentRef.current = message;
    setMessages((prev) => [...prev, { role: 'user', content: message }]);
    setDraft('');

    try {
      const response = await fetch(`${API_BASE_URL}/strategy`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ thread_id: threadId, message }),
      });

      const payload = await response.json().catch(() => ({}));
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
        throw new Error(payload.error || 'Failed to send message');
      }

      optimisticUserContentRef.current = null;
      setMessages(payload.messages || []);
      setCanvas(payload.canvas || {});
    } catch (submitError) {
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
    } finally {
      setSubmitting(false);
    }
  }

  const output = canvas.output;
  const strategyName = strategyNameFromOutput(output);
  const summaryText = output && typeof output === 'object' ? output['summary.txt'] : undefined;
  const pseudocodeText = output && typeof output === 'object' ? output['pseudocode.txt'] : undefined;
  const showSummary = hasNonEmptyOutputText(summaryText);
  const showPseudocode = hasNonEmptyOutputText(pseudocodeText);

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
          <p>Shape a strategy in chat. Backtest charts appear on the right.</p>
        </header>

        <div className="chat-stream">
          {loading ? <p className="status">Loading thread…</p> : null}
          {messages.map((message, index) => (
            <MessageBubble key={`${message.role}-${index}`} message={message} />
          ))}
          {submitting ? <ChatProcessingSpinner /> : null}
          <div ref={chatEndRef} />
        </div>

        <form ref={chatFormRef} className="chat-input" onSubmit={handleSubmit}>
          <label htmlFor="message" className="sr-only">
            Message
          </label>
          <textarea
            id="message"
            placeholder="Describe the edge, constraints, or risk logic…"
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
              {submitting ? (
                <>
                  <span className="chat-spinner chat-spinner-inline" aria-hidden />
                  <span>Processing…</span>
                </>
              ) : (
                error || 'Ready'
              )}
            </span>
            <button type="submit" disabled={submitting}>
              Send
            </button>
          </div>
        </form>
      </section>

      <section className="canvas-panel canvas-panel-charts">
        <header className="canvas-hero">
          <h2>{strategyName || 'Strategy'}</h2>
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
