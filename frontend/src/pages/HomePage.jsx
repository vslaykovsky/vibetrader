import { useMemo } from 'react';
import { useNavigate } from 'react-router-dom';
import { randomUUID } from '../randomUUID.js';

function createThreadId() {
  return randomUUID();
}

export function HomePage() {
  const navigate = useNavigate();
  const examplePrompts = useMemo(
    () => [
      'Turn my manual strategy into rules I can backtest',
      'Build a profitable strategy for BTC or SPY from scratch',
      'Improve my entries/exits and risk management',
      'Help me avoid overfitting and validate on past data',
    ],
    [],
  );

  return (
    <div className="home">
      <div className="home-shell">
        <main className="home-main">
          <section className="home-hero">
            <h1 className="home-title">An AI chat that builds a profitable trading strategy for you.</h1>
            <p className="home-subtitle">
              Discuss a new strategy from scratch or automate your existing manual process. Backtest on past data, iterate
              quickly, and get ready for live trading.
            </p>

            <div className="home-actions">
              <button className="home-cta" type="button" onClick={() => navigate(`/strategy/${createThreadId()}`)}>
                Start a new strategy thread
              </button>
              <div className="home-badges" aria-label="Supported markets and features">
                <span className="home-badge">Stocks</span>
                <span className="home-badge">Crypto</span>
                <span className="home-badge">Backtesting</span>
                <span className="home-badge">Live trading (coming soon)</span>
              </div>
            </div>
          </section>

          <section className="home-grid" aria-label="Product highlights">
            <div className="home-card">
              <div className="home-card-title">Automate your manual strategy</div>
              <div className="home-card-text">
                Explain how you trade today. Vibetrader helps translate it into clear, testable rules.
              </div>
            </div>
            <div className="home-card">
              <div className="home-card-title">Build from scratch</div>
              <div className="home-card-text">
                Brainstorm signals, entries/exits, position sizing, and risk controls—then iterate until it holds up.
              </div>
            </div>
            <div className="home-card">
              <div className="home-card-title">Backtest on past data</div>
              <div className="home-card-text">
                Validate ideas, spot weaknesses, and compare variants with charts and metrics.
              </div>
            </div>
            <div className="home-card">
              <div className="home-card-title">Live trading (coming soon)</div>
              <div className="home-card-text">
                When ready, deploy your strategy to trade live with guardrails and monitoring.
              </div>
            </div>
          </section>

          <section className="home-screenshots" aria-label="Screenshots">
            <div className="home-section-title">Screenshots</div>
            <div className="home-shot-grid">
              <div className="home-shot">
                <div className="home-shot-label">Chat + strategy runs</div>
                <div className="home-shot-placeholder" />
              </div>
              <div className="home-shot">
                <div className="home-shot-label">Backtest charts</div>
                <div className="home-shot-placeholder" />
              </div>
              <div className="home-shot">
                <div className="home-shot-label">Metrics + trade log</div>
                <div className="home-shot-placeholder" />
              </div>
            </div>
            <div className="home-shot-note">
              Add real images later (these placeholders won’t break builds if you haven’t added assets yet).
            </div>
          </section>

          <section className="home-prompts" aria-label="Example prompts">
            <div className="home-section-title">Try asking</div>
            <ul className="home-prompt-list">
              {examplePrompts.map((p) => (
                <li key={p} className="home-prompt-item">
                  <button
                    type="button"
                    className="home-prompt"
                    onClick={() => navigate(`/strategy/${createThreadId()}`, { state: { draft: p } })}
                  >
                    {p}
                  </button>
                </li>
              ))}
            </ul>
          </section>
        </main>

        <footer className="home-footer">
          <div className="home-footer-text">Vibetrader • Strategy research and backtesting in a single chat thread.</div>
        </footer>
      </div>
    </div>
  );
}

