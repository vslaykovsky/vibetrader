import { useCallback, useEffect, useMemo, useState } from 'react';
import { LogoMark } from './LogoMark.jsx';
import { useAuth } from './AuthContext.jsx';

const API_BASE_URL =
  import.meta.env.VITE_API_BASE_URL ||
  (import.meta.env.PROD ? '/api' : 'http://localhost:8080');

function normalizeEulaPayload(payload) {
  const agreement = payload?.agreement;
  if (!agreement || typeof agreement !== 'object') return null;
  const version = typeof agreement.version === 'string' ? agreement.version.trim() : '';
  const title = typeof agreement.title === 'string' ? agreement.title.trim() : '';
  const sections = Array.isArray(agreement.sections) ? agreement.sections : [];
  if (!version || !title || sections.length === 0) return null;
  return {
    ...agreement,
    version,
    title,
    sections,
  };
}

export function EulaGate({ children }) {
  const {
    actorUser,
    loading: authLoading,
    getAccessToken,
    refreshAdminUsers,
    signOut,
  } = useAuth();
  const [agreement, setAgreement] = useState(null);
  const [accepted, setAccepted] = useState(false);
  const [loading, setLoading] = useState(false);
  const [submitting, setSubmitting] = useState(false);
  const [error, setError] = useState('');
  const [ageConfirmed, setAgeConfirmed] = useState(false);
  const [riskAcknowledged, setRiskAcknowledged] = useState(false);

  const loadEula = useCallback(async (signal) => {
    if (!actorUser?.id) {
      setAgreement(null);
      setAccepted(false);
      setError('');
      return;
    }
    setLoading(true);
    setError('');
    try {
      const token = await getAccessToken();
      if (!token) throw new Error('Your session has expired. Sign in again.');
      const response = await fetch(`${API_BASE_URL}/eula`, {
        headers: { Authorization: `Bearer ${token}` },
        cache: 'no-store',
        signal,
      });
      const payload = await response.json().catch(() => ({}));
      if (!response.ok) throw new Error(payload.error || 'Unable to load the agreement.');
      const nextAgreement = normalizeEulaPayload(payload);
      if (!nextAgreement) throw new Error('The agreement returned by the server is invalid.');
      setAgreement(nextAgreement);
      setAccepted(payload?.acceptance?.accepted === true);
      setAgeConfirmed(false);
      setRiskAcknowledged(false);
    } catch (loadError) {
      if (loadError?.name !== 'AbortError') {
        setError(loadError instanceof Error ? loadError.message : String(loadError));
      }
    } finally {
      if (!signal?.aborted) setLoading(false);
    }
  }, [actorUser?.id, getAccessToken]);

  useEffect(() => {
    const controller = new AbortController();
    setAccepted(false);
    setAgreement(null);
    void loadEula(controller.signal);
    return () => controller.abort();
  }, [loadEula]);

  const canAccept = Boolean(agreement && ageConfirmed && riskAcknowledged && !submitting);
  const accountLabel = useMemo(
    () => actorUser?.email || actorUser?.user_metadata?.full_name || '',
    [actorUser],
  );

  const acceptEula = useCallback(async () => {
    if (!canAccept || !agreement) return;
    setSubmitting(true);
    setError('');
    try {
      const token = await getAccessToken();
      if (!token) throw new Error('Your session has expired. Sign in again.');
      const response = await fetch(`${API_BASE_URL}/eula/accept`, {
        method: 'POST',
        headers: {
          Authorization: `Bearer ${token}`,
          'Content-Type': 'application/json',
        },
        cache: 'no-store',
        body: JSON.stringify({
          version: agreement.version,
          accepted: true,
          age_confirmed: ageConfirmed,
          risk_acknowledged: riskAcknowledged,
        }),
      });
      const payload = await response.json().catch(() => ({}));
      if (!response.ok) {
        if (response.status === 409) void loadEula();
        throw new Error(payload.error || 'Unable to record your acceptance.');
      }
      setAccepted(payload?.acceptance?.accepted === true);
      void refreshAdminUsers();
    } catch (acceptError) {
      setError(acceptError instanceof Error ? acceptError.message : String(acceptError));
    } finally {
      setSubmitting(false);
    }
  }, [ageConfirmed, agreement, canAccept, getAccessToken, loadEula, refreshAdminUsers, riskAcknowledged]);

  if (authLoading) return null;
  if (!actorUser || accepted) return children;

  return (
    <main className="eula-gate">
      <header className="eula-gate-header">
        <div className="eula-gate-brand">
          <LogoMark className="logo-mark eula-gate-logo" />
          <span>TraderChat</span>
        </div>
        <div className="eula-gate-account">
          {accountLabel ? <span title={accountLabel}>{accountLabel}</span> : null}
          <button type="button" className="eula-text-button" onClick={() => void signOut()}>
            Sign out
          </button>
        </div>
      </header>

      <div className="eula-gate-layout">
        <div className="eula-gate-intro">
          <span className="eula-gate-kicker">Required agreement</span>
          <h1>{agreement?.title || 'End User License Agreement'}</h1>
          <p>
            Review and accept the current agreement before accessing TraderChat research, simulation, or live-trading tools.
          </p>
          {agreement ? (
            <div className="eula-gate-meta">
              <span>Effective {agreement.effective_date}</span>
              <span>Version {agreement.version}</span>
            </div>
          ) : null}
        </div>

        {loading && !agreement ? (
          <section className="eula-status" aria-live="polite">
            <span className="home-ms eula-status-icon" aria-hidden>hourglass_top</span>
            <h2>Loading agreement</h2>
          </section>
        ) : null}

        {error && !agreement ? (
          <section className="eula-status" role="alert">
            <span className="home-ms eula-status-icon is-error" aria-hidden>error</span>
            <h2>Agreement unavailable</h2>
            <p>{error}</p>
            <button type="button" className="eula-secondary-button" onClick={() => void loadEula()} disabled={loading}>
              <span className="home-ms" aria-hidden>refresh</span>
              Retry
            </button>
          </section>
        ) : null}

        {agreement ? (
          <>
            <article className="eula-document" aria-label={agreement.title}>
              <div className="eula-document-toolbar">
                <strong>{agreement.title}</strong>
                <button type="button" className="eula-print-button" onClick={() => window.print()}>
                  <span className="home-ms" aria-hidden>print</span>
                  Print
                </button>
              </div>
              <div className="eula-document-scroll" tabIndex="0">
                <p className="eula-document-summary">{agreement.summary}</p>
                {agreement.sections.map((section) => (
                  <section key={section.title} className="eula-document-section">
                    <h2>{section.title}</h2>
                    {(Array.isArray(section.paragraphs) ? section.paragraphs : []).map((paragraph) => (
                      <p key={paragraph}>{paragraph}</p>
                    ))}
                    {Array.isArray(section.bullets) && section.bullets.length > 0 ? (
                      <ul>
                        {section.bullets.map((bullet) => <li key={bullet}>{bullet}</li>)}
                      </ul>
                    ) : null}
                  </section>
                ))}
              </div>
            </article>

            <section className="eula-consent" aria-labelledby="eula-consent-title">
              <div className="eula-consent-heading">
                <span className="home-ms" aria-hidden>verified_user</span>
                <div>
                  <h2 id="eula-consent-title">Your acknowledgment</h2>
                  <p>Both confirmations are required. They are not preselected.</p>
                </div>
              </div>
              <label className="eula-checkbox-row">
                <input
                  type="checkbox"
                  checked={ageConfirmed}
                  onChange={(event) => setAgeConfirmed(event.target.checked)}
                />
                <span>I am at least 18 years old, have legal capacity, and agree to the End User License Agreement.</span>
              </label>
              <label className="eula-checkbox-row">
                <input
                  type="checkbox"
                  checked={riskAcknowledged}
                  onChange={(event) => setRiskAcknowledged(event.target.checked)}
                />
                <span>
                  I understand that TraderChat is a software tool, not financial advice, and that enabled strategies may
                  submit live orders automatically and cause substantial or total loss.
                </span>
              </label>
              {error ? <p className="eula-consent-error" role="alert">{error}</p> : null}
              <div className="eula-consent-actions">
                <button
                  type="button"
                  className="eula-accept-button"
                  disabled={!canAccept}
                  onClick={() => void acceptEula()}
                >
                  {submitting ? 'Recording acceptance…' : 'Accept and continue'}
                  {!submitting ? <span className="home-ms" aria-hidden>arrow_forward</span> : null}
                </button>
              </div>
            </section>
          </>
        ) : null}
      </div>
    </main>
  );
}

export { normalizeEulaPayload };
