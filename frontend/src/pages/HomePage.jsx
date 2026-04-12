import { useMemo } from 'react';
import { useNavigate } from 'react-router-dom';
import { randomUUID } from '../randomUUID.js';
import { useAuth } from '../AuthContext';
import { useTheme } from '../ThemeContext';
import { ProfileMenu } from '../ProfileMenu';

function createThreadId() {
  return randomUUID();
}

const HERO_CHART_IMG =
  'https://lh3.googleusercontent.com/aida-public/AB6AXuBurKkY1bKcBFWjtm2ceXH8JkWhy_VRSP_cBsqfk-MRG2eQ-8i4Y6kAOkeAkw_4PJTXqZFMyR-gnNnX2z1QcKwx3yWYejRWXl8nS_ArqZlixRJkE6oi2nb_9BGSCfKdejaNax2mzaWqYohE-i6n7u-jmNWIvZafoOf1XK-DOcXC30o3CDCU2SYy3q_nWlNjzqIZGJzef8I2oN-BBp-oJnZxmd8KnXxzZJ1lGZhf6CfxO1Gq9nYHqsaqVqP18uxExidJRtz6m3DLmZfm';

const SPLIT_IMG_A =
  'https://lh3.googleusercontent.com/aida-public/AB6AXuCverN5--WcyyAhT-iYArlaKb66uqdrOcJl1xVig5f42vcydRuV3OTGeGDMl5YKkETbMLh7I2WAOAwYzWn-KHaRqs2CqXp4nsmCjtaKn7Whu4lyWzAlqdbYMpLRDTS6fk3kYckrxAyl3WkBYevsFwYXjF6ZzO6LU1nrRB0g6or3y8LQYA5naFge0tHaA5RsQjAfxlbIpOcm_StF-2MSeZJ6zdn1oFjdREtxfXkY62Q4AmzdcPTa2O1_be9Dqjirm8qGOg4jrR2UnUIa';

const SPLIT_IMG_B =
  'https://lh3.googleusercontent.com/aida-public/AB6AXuDKPz36lzM20MaS9v9v9z50gdgCcOJmIYpyURZ-_ZWEBdV6fM43iQRXZbgpq3tLIOwsYkphc4m3XWz_wc9ahiaI9QfqJ9FzFxE499nnZQoaxYhQf9kcwpisjs5T19l-E0dsqf445A_9yWzSFG8XFSW1_2gBHrPIuNjJvY4r-U8dVoMyLETRtNKVvCxTpRC-PJllWJAjvoHwO0jhFUYhzkS9niUq5NozwVCbXZktBKrQHVu-HUN-BuTVIPQohciRVN0KLFzGFLfGNl-9';

function scrollToId(id) {
  document.getElementById(id)?.scrollIntoView({ behavior: 'smooth' });
}

export function HomePage() {
  const navigate = useNavigate();
  const { user, loading, signInWithGoogle, signOut } = useAuth();
  const { theme, toggleTheme } = useTheme();
  const examplePrompts = useMemo(
    () => [
      'Turn my manual strategy into rules I can backtest',
      'Build a profitable strategy for BTC or SPY from scratch',
      'Improve my entries/exits and risk management',
      'Help me avoid overfitting and validate on past data',
    ],
    [],
  );

  const goStrategy = () => navigate(`/strategy/${createThreadId()}`);
  const primaryCta = () => (user ? goStrategy() : signInWithGoogle());

  return (
    <div className="home">
      <nav className="home-nav" aria-label="Primary">
        <div className="home-nav-inner">
          <div className="home-nav-brand">
            <span className="home-nav-logo">TraderChat.AI</span>
            <span className="app-beta-badge" aria-label="Beta">
              Beta
            </span>
          </div>
          <div className="home-nav-links">
            <button type="button" className="home-nav-link home-nav-link-active" onClick={() => scrollToId('home-features')}>
              Features
            </button>
            <button type="button" className="home-nav-link" onClick={() => scrollToId('home-cta')}>
              Pricing
            </button>
            <button type="button" className="home-nav-link" onClick={() => scrollToId('home-asymmetric')}>
              How it Works
            </button>
            <button type="button" className="home-nav-link" onClick={() => scrollToId('home-footer')}>
              About
            </button>
          </div>
          <div className="home-nav-auth">
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
            {!loading &&
              (user ? (
                <div className="home-nav-user">
                  <ProfileMenu user={user} signOut={signOut} surface="home" />
                  <span className="auth-name">{user.user_metadata?.full_name || user.email}</span>
                  <button type="button" className="home-btn home-btn-primary" onClick={goStrategy}>
                    Open app
                  </button>
                </div>
              ) : (
                <>
                  <button type="button" className="home-btn home-btn-ghost" onClick={signInWithGoogle}>
                    Sign in
                  </button>
                  <button type="button" className="home-btn home-btn-primary" onClick={signInWithGoogle}>
                    Get Started
                  </button>
                </>
              ))}
          </div>
        </div>
      </nav>

      <main className="home-main-outer">
        <section className="home-hero-section">
          <div className="home-hero-grid">
            <div className="home-hero-copy">
              <div className="home-hero-pill">Multi-Asset Intelligence</div>
              <h1 className="home-hero-title">
                Your AI Co-Pilot for <em className="home-hero-em">Precision Trading</em>
              </h1>
              <p className="home-hero-lead">
                Chat with global markets to discover trends in <strong>stocks and crypto</strong>, backtest strategies, and
                iterate toward live trading.
              </p>
              <div className="home-hero-actions">
                <button type="button" className="home-btn home-btn-hero" onClick={primaryCta}>
                  Get Started
                </button>
                <button type="button" className="home-btn home-btn-secondary-solid" onClick={() => scrollToId('home-features')}>
                  How it Works
                </button>
              </div>
              <div className="home-hero-exchanges">
                <span className="home-hero-exchanges-label">Trade On</span>
                <div className="home-hero-exchanges-list">
                  <span>NYSE</span>
                  <span>NASDAQ</span>
                  <span>BINANCE</span>
                  <span>COINBASE</span>
                </div>
              </div>
            </div>
            <div className="home-hero-visual">
              <div className="home-hero-glow" aria-hidden />
              <div className="home-mock-chat">
                <div className="home-mock-chat-top">
                  <div className="home-mock-chat-dots" aria-hidden>
                    <span className="home-mock-dot home-mock-dot-error" />
                    <span className="home-mock-dot home-mock-dot-primary" />
                  </div>
                  <span className="home-mock-chat-tickers">NVDA · 1D | BTC/USDT · 1H</span>
                  <div className="home-mock-scan">
                    <span className="home-ms" aria-hidden>
                      query_stats
                    </span>
                    <span className="home-mock-scan-label">Active Scanning</span>
                  </div>
                </div>
                <div className="home-mock-chat-body">
                  <div className="home-mock-row home-mock-row-user">
                    <div className="home-bubble home-bubble-user">
                      &quot;Analyze NVDA for post-earnings gap support and compare relative strength against BTC.&quot;
                    </div>
                  </div>
                  <div className="home-mock-row home-mock-row-ai">
                    <div className="home-bubble home-bubble-ai">
                      <div className="home-bubble-ai-head">
                        <span className="home-ms" aria-hidden>
                          auto_awesome
                        </span>
                        <span>AI Multi-Asset Insight</span>
                      </div>
                      <p className="home-bubble-ai-text">
                        NVDA is showing strong support at $820. Relative strength vs SPY is elevated. BTC is consolidating.
                        Example paired idea: bullish NVDA exposure with a BTC-aware risk frame.
                      </p>
                      <div className="home-bubble-ai-chart">
                        <img src={HERO_CHART_IMG} alt="" className="home-bubble-ai-chart-img" />
                      </div>
                    </div>
                  </div>
                </div>
                <div className="home-mock-chat-input">
                  <div className="home-mock-input-fake">Compare stocks, crypto, or forex…</div>
                  <div className="home-mock-send" aria-hidden>
                    <span className="home-ms">send</span>
                  </div>
                </div>
              </div>
            </div>
          </div>
        </section>

        <section className="home-trust" aria-label="Trust signals">
          <div className="home-trust-inner">
            <div className="home-trust-item">
              <span className="home-ms home-ms-fill" aria-hidden>
                verified_user
              </span>
              <span>Secure &amp; Encrypted</span>
            </div>
            <div className="home-trust-item">
              <span className="home-ms home-ms-fill" aria-hidden>
                monitoring
              </span>
              <span>Real-time Multi-Asset Feeds</span>
            </div>
            <div className="home-trust-item">
              <span className="home-ms home-ms-fill" aria-hidden>
                account_balance
              </span>
              <span>Stock &amp; Crypto Support</span>
            </div>
            <div className="home-trust-item">
              <span className="home-ms home-ms-fill" aria-hidden>
                bolt
              </span>
              <span>Backtesting Engine</span>
            </div>
          </div>
        </section>

        <section className="home-features" id="home-features" aria-labelledby="home-features-heading">
          <div className="home-features-head">
            <h2 id="home-features-heading" className="home-features-title">
              Precision Tools for Modern Markets
            </h2>
            <div className="home-features-rule" aria-hidden />
          </div>
          <div className="home-features-grid">
            <article className="home-feature-card">
              <div className="home-feature-icon-wrap">
                <span className="home-ms home-feature-icon" aria-hidden>
                  search_insights
                </span>
              </div>
              <h3 className="home-feature-card-title">Explore &amp; Analyze</h3>
              <p className="home-feature-card-text">
                Scan markets for stocks and crypto, shape hypotheses with the assistant, and pressure-test ideas before you
                commit capital.
              </p>
              <ul className="home-feature-list">
                <li>
                  <span className="home-ms" aria-hidden>
                    check_circle
                  </span>
                  Market-wide context
                </li>
                <li>
                  <span className="home-ms" aria-hidden>
                    check_circle
                  </span>
                  Cross-asset framing
                </li>
              </ul>
            </article>
            <article className="home-feature-card">
              <div className="home-feature-icon-wrap">
                <span className="home-ms home-feature-icon" aria-hidden>
                  show_chart
                </span>
              </div>
              <h3 className="home-feature-card-title">Build &amp; Backtest</h3>
              <p className="home-feature-card-text">
                Turn chat into testable rules, run historical backtests, and iterate with charts and metrics in one thread.
              </p>
              <div className="home-feature-code">
                <span>// Cross-asset strategy sketch</span>
                <span>IF (STOCK.NVDA &gt; EMA200) AND (CRYPTO.BTC &lt; RSI30)</span>
                <span>THEN DEFINE_RULESET(&quot;paired_long_nvda&quot;);</span>
              </div>
            </article>
            <article className="home-feature-card">
              <div className="home-feature-icon-wrap">
                <span className="home-ms home-feature-icon" aria-hidden>
                  account_tree
                </span>
              </div>
              <h3 className="home-feature-card-title">From research to execution</h3>
              <p className="home-feature-card-text">
                Stay in one workspace as you refine risk, sizing, and guardrails. Live trading is on the roadmap with the
                same thread-centric workflow.
              </p>
              <div className="home-feature-meter">
                <div className="home-feature-meter-top">
                  <span>Research loop</span>
                  <span className="home-feature-meter-value">Chat → backtest</span>
                </div>
                <div className="home-feature-meter-track">
                  <div className="home-feature-meter-fill" />
                </div>
              </div>
            </article>
          </div>
        </section>

        <section className="home-asymmetric" id="home-asymmetric">
          <div className="home-asymmetric-copy">
            <span className="home-asymmetric-kicker">Engineered for Alpha</span>
            <h2 className="home-asymmetric-title">Built for the next generation of multi-asset seekers.</h2>
            <p className="home-asymmetric-lead">
              Whether you are trading equities or crypto, keep research, code, and results in one calm, high-contrast
              surface.
            </p>
            <div className="home-asymmetric-list">
              <div className="home-asymmetric-item">
                <span className="home-ms" aria-hidden>
                  chat_bubble
                </span>
                <div>
                  <h4 className="home-asymmetric-item-title">Natural language interface</h4>
                  <p className="home-asymmetric-item-text">Describe edge cases and constraints in plain English.</p>
                </div>
              </div>
              <div className="home-asymmetric-item">
                <span className="home-ms" aria-hidden>
                  hub
                </span>
                <div>
                  <h4 className="home-asymmetric-item-title">One thread, many assets</h4>
                  <p className="home-asymmetric-item-text">Stocks, crypto, and workflow context stay connected.</p>
                </div>
              </div>
            </div>
          </div>
          <div className="home-asymmetric-visuals">
            <div className="home-asymmetric-img-wrap">
              <img src={SPLIT_IMG_A} alt="" className="home-asymmetric-img" />
            </div>
            <div className="home-asymmetric-img-wrap home-asymmetric-img-offset">
              <img src={SPLIT_IMG_B} alt="" className="home-asymmetric-img" />
            </div>
          </div>
        </section>

        <section className="home-prompts" id="home-prompts" aria-labelledby="home-prompts-heading">
          <h2 id="home-prompts-heading" className="home-section-title">
            Try asking
          </h2>
          <ul className="home-prompt-list">
            {examplePrompts.map((p) => (
              <li key={p} className="home-prompt-item">
                <button
                  type="button"
                  className="home-prompt"
                  onClick={() => {
                    if (user) {
                      navigate(`/strategy/${createThreadId()}`, { state: { draft: p } });
                    } else {
                      signInWithGoogle();
                    }
                  }}
                >
                  {p}
                </button>
              </li>
            ))}
          </ul>
        </section>

        <section className="home-cta-wrap" id="home-cta">
          <div className="home-cta-panel">
            <div className="home-cta-glow" aria-hidden />
            <div className="home-cta-inner">
              <h2 className="home-cta-title">
                Ready to evolve your <span className="home-cta-accent">trading edge?</span>
              </h2>
              <p className="home-cta-lead">
                Start a strategy thread, backtest on historical data, and iterate with the assistant at your side.
              </p>
              <button type="button" className="home-btn home-btn-hero home-btn-cta" onClick={primaryCta}>
                {user ? 'Open your workspace' : 'Create free account'}
              </button>
              <p className="home-cta-note">No credit card required · Stocks &amp; crypto workflows · Built for serious research</p>
            </div>
          </div>
        </section>
      </main>

      <footer className="home-footer" id="home-footer">
        <div className="home-footer-grid">
          <div className="home-footer-brand-block">
            <span className="home-footer-logo">TraderChat.AI</span>
            <p className="home-footer-tag">
              Vibetrader · Strategy research and backtesting in a single chat thread.
            </p>
          </div>
          <div>
            <h5 className="home-footer-col-title">Platform</h5>
            <ul className="home-footer-links">
              <li>
                <button type="button" className="home-footer-link" onClick={primaryCta}>
                  Strategy chat
                </button>
              </li>
              <li>
                <button type="button" className="home-footer-link" onClick={() => scrollToId('home-features')}>
                  Backtesting
                </button>
              </li>
            </ul>
          </div>
          <div>
            <h5 className="home-footer-col-title">Product</h5>
            <ul className="home-footer-links">
              <li>
                <button type="button" className="home-footer-link" onClick={() => scrollToId('home-prompts')}>
                  Example prompts
                </button>
              </li>
              <li>
                <button type="button" className="home-footer-link" onClick={() => scrollToId('home-asymmetric')}>
                  How it works
                </button>
              </li>
            </ul>
          </div>
          <div>
            <h5 className="home-footer-col-title">Stay updated</h5>
            <p className="home-footer-small">We&apos;ll announce live trading and new markets here first.</p>
          </div>
        </div>
        <div className="home-footer-bottom">
          <p className="home-footer-copy">© {new Date().getFullYear()} TraderChat.AI · Trading involves significant risk.</p>
        </div>
      </footer>
    </div>
  );
}
