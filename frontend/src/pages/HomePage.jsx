import { useEffect, useMemo, useState } from 'react';
import { Link, useNavigate } from 'react-router-dom';
import { randomUUID } from '../randomUUID.js';
import { useAuth } from '../AuthContext';
import { ProfileMenu } from '../ProfileMenu';
import { LogoMark } from '../LogoMark';

function createThreadId() {
  return randomUUID();
}

function scrollToId(id) {
  document.getElementById(id)?.scrollIntoView({ behavior: 'smooth' });
}

export function HomePage() {
  const navigate = useNavigate();
  const { user, loading, signInWithGoogle, signOut } = useAuth();
  const [pricingOpen, setPricingOpen] = useState(false);
  const examplePrompts = useMemo(
    () => [
      'Analyze the top 100 S&P 500 stocks and list the ones that have crossed above their 50-day moving average',
      'Create a simple mean reversion strategy and backtest it on SPY',
      'Come up with a profitable pair trading strategy. Suggest a pair and backtest it',
      'What are ways to incorporate volatility into a strategy?',
    ],
    [],
  );

  useEffect(() => {
    if (!pricingOpen) return;
    const prevOverflow = document.body.style.overflow;
    document.body.style.overflow = 'hidden';
    const onKeyDown = (e) => {
      if (e.key === 'Escape') setPricingOpen(false);
    };
    window.addEventListener('keydown', onKeyDown);
    return () => {
      document.body.style.overflow = prevOverflow;
      window.removeEventListener('keydown', onKeyDown);
    };
  }, [pricingOpen]);

  const goStrategy = () => navigate(`/strategy/${createThreadId()}`);
  const goDashboard = () => navigate('/dashboard');
  const primaryCta = () => (user ? goDashboard() : signInWithGoogle());

  return (
    <div className="home">
      <nav className="home-nav" aria-label="Primary">
        <div className="home-nav-inner">
          <div className="home-nav-brand">
            <LogoMark className="logo-mark logo-mark--nav" />
            <span className="home-nav-logo">TraderChat</span>
          </div>
          <div className="home-nav-links">
            {user ? (
              <Link to="/dashboard" className="home-nav-link home-nav-link--anchor">
                Dashboard
              </Link>
            ) : null}
            <button type="button" className="home-nav-link" onClick={() => setPricingOpen(true)}>
              Pricing
            </button>
            <button type="button" className="home-nav-link" onClick={() => scrollToId('home-features')}>
              How it works
            </button>
          </div>
          <div className="home-nav-auth">
            {!loading &&
              (user ? (
                <div className="home-nav-user">
                  <ProfileMenu user={user} signOut={signOut} surface="home" />
                  <span className="auth-name">{user.user_metadata?.full_name || user.email}</span>
                </div>
              ) : (
                <button type="button" className="home-btn home-btn-secondary" onClick={signInWithGoogle}>
                  Sign in
                </button>
              ))}
          </div>
        </div>
      </nav>

      <main className="home-main-outer">
        <section className="home-hero-section">
          <div className="home-hero-grid">
            <div className="home-hero-copy">
              <h1 className="home-hero-title">
                Your AI Co-Pilot for <em className="home-hero-em">Precision Trading</em>
              </h1>
              <p className="home-hero-lead">
                Chat with global markets to discover trends in <strong>stocks and crypto</strong>, backtest strategies, and
                iterate toward live trading.
              </p>
              <div className="home-hero-actions">
                <button type="button" className="home-btn home-btn-hero" onClick={primaryCta}>
                  {user ? 'Open workspace' : 'Get Started'}
                </button>
                <button type="button" className="home-btn home-btn-secondary-solid" onClick={() => scrollToId('home-features')}>
                  How it Works
                </button>
              </div>
            </div>
            <div className="home-hero-visual">
              <div className="home-hero-glow" aria-hidden />
              <div className="home-hero-screenshot-wrap">
                <img
                  src="/tc.png"
                  alt="Strategy workspace with chat and SPY mean reversion backtest charts"
                  className="home-hero-screenshot"
                  width={1904}
                  height={1264}
                />
              </div>
            </div>
          </div>
        </section>

        <section className="home-trust" aria-label="Trust signals">
          <div className="home-trust-inner">
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
            <div className="home-trust-item">
              <span className="home-ms home-ms-fill" aria-hidden>
                monitoring
              </span>
              <span>Real-time Multi-Asset Feeds</span>
            </div>
            <div className="home-trust-item">
              <span className="home-ms home-ms-fill" aria-hidden>
                verified_user
              </span>
              <span>Secure &amp; Encrypted</span>
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

      <footer className="home-footer home-footer--simple" id="home-footer">
        <p className="home-footer-copy">© 2026 TraderChat · Trading involves significant risk</p>
      </footer>

      {pricingOpen ? (
        <div
          className="home-pricing-overlay"
          role="presentation"
          onClick={() => setPricingOpen(false)}
        >
          <div
            className="home-pricing-sheet"
            role="dialog"
            aria-modal="true"
            aria-labelledby="home-pricing-title"
            onClick={(e) => e.stopPropagation()}
          >
            <button
              type="button"
              className="home-pricing-close"
              onClick={() => setPricingOpen(false)}
              aria-label="Close pricing"
            >
              <span className="home-ms" aria-hidden>
                close
              </span>
            </button>
            <h2 id="home-pricing-title" className="home-pricing-title">
              Pricing
            </h2>
            <p className="home-pricing-body">
              TraderChat is <strong>free while we are in beta</strong>. We are gathering feedback and hardening the product
              before we introduce paid plans.
            </p>
            <button type="button" className="home-btn home-btn-secondary-solid" onClick={() => setPricingOpen(false)}>
              Got it
            </button>
          </div>
        </div>
      ) : null}
    </div>
  );
}
