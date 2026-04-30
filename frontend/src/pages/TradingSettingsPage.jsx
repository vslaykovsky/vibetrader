import { useCallback, useEffect, useState } from 'react';
import { Link } from 'react-router-dom';
import { useAuth } from '../AuthContext';
import { useTheme } from '../ThemeContext';
import { useTimeZone } from '../TimeZoneContext.jsx';
import { browserTimeZone, supportedTimeZones } from '../lib/dateTime.js';
import { ProfileMenu } from '../ProfileMenu';

const API_BASE_URL =
  import.meta.env.VITE_API_BASE_URL ||
  (import.meta.env.PROD ? '/api' : 'http://localhost:8080');

export function TradingSettingsPage() {
  const { user, signOut, getAccessToken } = useAuth();
  const { theme, toggleTheme } = useTheme();
  const { timeZone, setTimeZone, refreshTimeZone } = useTimeZone();
  const [loading, setLoading] = useState(true);
  const [saving, setSaving] = useState(false);
  const [error, setError] = useState('');
  const [okMsg, setOkMsg] = useState('');
  const [profile, setProfile] = useState(null);
  const [accounts, setAccounts] = useState([]);
  const [apiKeyInput, setApiKeyInput] = useState('');
  const [secretInput, setSecretInput] = useState('');
  const [newAccount, setNewAccount] = useState('');
  const [newLabel, setNewLabel] = useState('');
  const [newIsLive, setNewIsLive] = useState(false);
  const [timezoneInput, setTimezoneInput] = useState(timeZone || browserTimeZone());
  const timezoneOptions = supportedTimeZones();
  const credentialsReady = Boolean(profile?.has_alpaca_api_key && profile?.has_alpaca_secret_key);

  const authFetch = useCallback(
    async (url, options = {}) => {
      const token = await getAccessToken();
      const headers = { ...options.headers };
      if (token) headers['Authorization'] = `Bearer ${token}`;
      if (!headers['Content-Type'] && options.body) headers['Content-Type'] = 'application/json';
      return fetch(url, { ...options, headers });
    },
    [getAccessToken],
  );

  const load = useCallback(async () => {
    setLoading(true);
    setError('');
    setOkMsg('');
    try {
      const res = await authFetch(`${API_BASE_URL}/settings/trading`);
      const payload = await res.json().catch(() => ({}));
      if (res.status === 503) {
        setError(
          payload.error ||
            'Trading settings are not available. Set SUPABASE_URL and SUPABASE_SERVICE_ROLE_KEY on the API server.',
        );
        setProfile(null);
        setAccounts([]);
        return;
      }
      if (!res.ok) throw new Error(payload.error || `Load failed (${res.status})`);
      const p = payload.profile;
      setProfile(typeof p === 'object' && p !== null ? p : {});
      const nextTimezone = typeof p?.timezone === 'string' && p.timezone ? p.timezone : browserTimeZone();
      setTimezoneInput(nextTimezone);
      setTimeZone(nextTimezone);
      setAccounts(Array.isArray(payload.alpaca_accounts) ? payload.alpaca_accounts : []);
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e));
    } finally {
      setLoading(false);
    }
  }, [authFetch, setTimeZone]);

  useEffect(() => {
    load();
  }, [load]);

  const saveSettings = async () => {
    setSaving(true);
    setError('');
    setOkMsg('');
    try {
      const profileBody = { timezone: timezoneInput };
      if (apiKeyInput.trim()) profileBody.alpaca_api_key = apiKeyInput.trim();
      if (secretInput.trim()) profileBody.alpaca_secret_key = secretInput.trim();

      const profileRes = await authFetch(`${API_BASE_URL}/settings/trading/profile`, {
        method: 'PUT',
        body: JSON.stringify(profileBody),
      });
      const profilePayload = await profileRes.json().catch(() => ({}));
      if (!profileRes.ok) throw new Error(profilePayload.error || `Save failed (${profileRes.status})`);

      setTimeZone(timezoneInput);
      setApiKeyInput('');
      setSecretInput('');
      setOkMsg('Saved settings.');
      await refreshTimeZone();
      await load();
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e));
    } finally {
      setSaving(false);
    }
  };

  const addAccount = async () => {
    setSaving(true);
    setError('');
    setOkMsg('');
    try {
      const accountValue = newAccount.trim();
      if (!accountValue) {
        setError('Enter an account id to add a label.');
        return;
      }
      const res = await authFetch(`${API_BASE_URL}/settings/trading/alpaca-accounts`, {
        method: 'POST',
        body: JSON.stringify({
          account: accountValue,
          label: newLabel.trim(),
          is_live: newIsLive,
        }),
      });
      const payload = await res.json().catch(() => ({}));
      if (!res.ok) throw new Error(payload.error || `Create failed (${res.status})`);
      setNewAccount('');
      setNewLabel('');
      setNewIsLive(false);
      setOkMsg('Account label added.');
      await load();
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e));
    } finally {
      setSaving(false);
    }
  };

  const deleteAccount = async (id) => {
    if (!id) return;
    setSaving(true);
    setError('');
    setOkMsg('');
    try {
      const res = await authFetch(`${API_BASE_URL}/settings/trading/alpaca-accounts/${encodeURIComponent(id)}`, {
        method: 'DELETE',
      });
      const payload = await res.json().catch(() => ({}));
      if (!res.ok) throw new Error(payload.error || `Delete failed (${res.status})`);
      setOkMsg('Account removed.');
      await load();
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e));
    } finally {
      setSaving(false);
    }
  };

  return (
    <div className="dashboard-page">
      <header className="dashboard-topbar">
        <div className="dashboard-topbar-left">
          <Link to="/dashboard" className="app-home-link" aria-label="Go to dashboard">
            <span className="app-logo">TraderChat</span>
          </Link>
          <span className="dashboard-topbar-sep" aria-hidden>
            /
          </span>
          <span className="dashboard-topbar-crumb">Trading settings</span>
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
        <section className="dashboard-hero settings-hero">
          <div className="dashboard-hero-copy">
            <span className="settings-kicker">Trading controls</span>
            <h1 className="dashboard-hero-title">Settings that travel with your runs</h1>
            <p className="dashboard-hero-lead">
              Keep broker access, account notes, and chart timestamps in one place. Credentials stay server-side and
              are never shown in full after saving.
            </p>
          </div>
          <div className="dashboard-hero-actions">
            <Link to="/dashboard" className="dashboard-link-btn">
              Dashboard
            </Link>
          </div>
        </section>

        {error ? <p className="dashboard-banner-error">{error}</p> : null}
        {okMsg ? <p className="dashboard-banner-ok">{okMsg}</p> : null}
        {loading ? <p className="dashboard-loading muted">Loading…</p> : null}

        {!loading && profile ? (
          <div className="settings-layout">
            <section className="settings-editor" aria-label="Trading settings editor">
              <div className="settings-editor-head">
                <div>
                  <p className="settings-kicker">Editor</p>
                  <h2>Broker and display preferences</h2>
                </div>
                <div className="settings-editor-actions">
                  <span className={credentialsReady ? 'dashboard-pill dashboard-pill--ok' : 'dashboard-pill dashboard-pill--warn'}>
                    {credentialsReady ? 'Ready' : 'Setup needed'}
                  </span>
                  <button type="button" className="dashboard-btn-primary" disabled={saving} onClick={saveSettings}>
                    Save settings
                  </button>
                </div>
              </div>

              <div className="settings-editor-section">
                <div className="settings-section-copy">
                  <span className="home-ms settings-section-icon" aria-hidden>
                    key
                  </span>
                  <div>
                    <h3>API credentials</h3>
                    <p>Update either field when a key rotates. Saved values stay hidden after this form clears.</p>
                  </div>
                </div>
                <div className="settings-form settings-form--credentials">
                  <label className="settings-label">
                    Alpaca API key
                    <input
                      className="settings-input"
                      type="password"
                      autoComplete="off"
                      value={apiKeyInput}
                      onChange={(e) => setApiKeyInput(e.target.value)}
                      placeholder="PK..."
                    />
                  </label>
                  <label className="settings-label">
                    Alpaca secret key
                    <input
                      className="settings-input"
                      type="password"
                      autoComplete="off"
                      value={secretInput}
                      onChange={(e) => setSecretInput(e.target.value)}
                      placeholder="Hidden after saving"
                    />
                  </label>
                </div>
              </div>

              <div className="settings-editor-section">
                <div className="settings-section-copy">
                  <span className="home-ms settings-section-icon" aria-hidden>
                    schedule
                  </span>
                  <div>
                    <h3>Display timezone</h3>
                    <p>Charts, tables, and timestamps use this timezone. Market data remains stored in UTC.</p>
                  </div>
                </div>
                <div className="settings-form settings-form--inline">
                  {timezoneOptions.length ? (
                    <select
                      className="settings-input settings-input--wide"
                      value={timezoneInput}
                      onChange={(e) => setTimezoneInput(e.target.value)}
                    >
                      {timezoneOptions.map((tz) => (
                        <option key={tz} value={tz}>
                          {tz}
                        </option>
                      ))}
                    </select>
                  ) : (
                    <input
                      className="settings-input settings-input--wide"
                      value={timezoneInput}
                      onChange={(e) => setTimezoneInput(e.target.value)}
                      placeholder="America/New_York"
                    />
                  )}
                </div>
              </div>

              <div className="settings-editor-section settings-editor-section--accounts">
                <div className="settings-section-copy">
                  <span className="home-ms settings-section-icon" aria-hidden>
                    account_balance
                  </span>
                  <div>
                    <h3>Account labels</h3>
                    <p>Use labels to remember which Alpaca account is paper or live. The runner still uses the keys above.</p>
                  </div>
                </div>
                <div className="settings-account-area">
                  <div className="settings-account-summary">
                    <h4>Existing labels</h4>
                    <span className="dashboard-panel-count">{accounts.length}</span>
                  </div>
                  {accounts.length ? (
                    <ul className="settings-account-list">
                      {accounts.map((a) => (
                        <li key={a.id} className="settings-account-row">
                          <div className="settings-account-main">
                            <strong>{a.label || 'Unlabeled account'}</strong>
                            <span>{a.account || 'No account id'}</span>
                          </div>
                          <div className="settings-account-actions">
                            {a.is_live ? <span className="dashboard-pill dashboard-pill--warn">live</span> : null}
                            {!a.is_live ? <span className="dashboard-pill dashboard-pill--muted">paper</span> : null}
                            <button
                              type="button"
                              className="dashboard-link-btn"
                              disabled={saving}
                              onClick={() => deleteAccount(a.id)}
                            >
                              Remove
                            </button>
                          </div>
                        </li>
                      ))}
                    </ul>
                  ) : (
                    <div className="settings-empty">
                      <span className="home-ms" aria-hidden>
                        add_card
                      </span>
                      <p>No account labels yet.</p>
                    </div>
                  )}

                  <div className="settings-account-add">
                    <div className="settings-form settings-form--account">
                      <label className="settings-label">
                        Account id
                        <input
                          className="settings-input"
                          value={newAccount}
                          onChange={(e) => setNewAccount(e.target.value)}
                          placeholder="Account id"
                        />
                      </label>
                      <label className="settings-label">
                        Label
                        <input
                          className="settings-input"
                          value={newLabel}
                          onChange={(e) => setNewLabel(e.target.value)}
                          placeholder="Label"
                        />
                      </label>
                      <div className="settings-account-type" role="group" aria-label="Account type">
                        <button
                          type="button"
                          className={!newIsLive ? 'settings-type-btn settings-type-btn--active' : 'settings-type-btn'}
                          onClick={() => setNewIsLive(false)}
                        >
                          Paper
                        </button>
                        <button
                          type="button"
                          className={newIsLive ? 'settings-type-btn settings-type-btn--active' : 'settings-type-btn'}
                          onClick={() => setNewIsLive(true)}
                        >
                          Live
                        </button>
                      </div>
                      <button type="button" className="dashboard-btn-ghost settings-add-btn" disabled={saving} onClick={addAccount}>
                        Add
                      </button>
                    </div>
                  </div>
                </div>
              </div>
            </section>
          </div>
        ) : null}
      </div>
    </div>
  );
}
