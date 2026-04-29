import { useCallback, useEffect, useState } from 'react';
import { Link } from 'react-router-dom';
import { useAuth } from '../AuthContext';
import { useTheme } from '../ThemeContext';
import { ProfileMenu } from '../ProfileMenu';

const API_BASE_URL =
  import.meta.env.VITE_API_BASE_URL ||
  (import.meta.env.PROD ? '/api' : 'http://localhost:8080');

export function TradingSettingsPage() {
  const { user, signOut, getAccessToken } = useAuth();
  const { theme, toggleTheme } = useTheme();
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
      setAccounts(Array.isArray(payload.alpaca_accounts) ? payload.alpaca_accounts : []);
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e));
    } finally {
      setLoading(false);
    }
  }, [authFetch]);

  useEffect(() => {
    load();
  }, [load]);

  const saveProfile = async () => {
    setSaving(true);
    setError('');
    setOkMsg('');
    try {
      const body = {};
      if (apiKeyInput.trim()) body.alpaca_api_key = apiKeyInput.trim();
      if (secretInput.trim()) body.alpaca_secret_key = secretInput.trim();
      if (!Object.keys(body).length) {
        setError('Enter at least one of API key or secret to update.');
        return;
      }
      const res = await authFetch(`${API_BASE_URL}/settings/trading/profile`, {
        method: 'PUT',
        body: JSON.stringify(body),
      });
      const payload = await res.json().catch(() => ({}));
      if (!res.ok) throw new Error(payload.error || `Save failed (${res.status})`);
      setApiKeyInput('');
      setSecretInput('');
      setOkMsg('Saved Alpaca credentials.');
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
      const res = await authFetch(`${API_BASE_URL}/settings/trading/alpaca-accounts`, {
        method: 'POST',
        body: JSON.stringify({
          account: newAccount.trim(),
          label: newLabel.trim(),
          is_live: newIsLive,
        }),
      });
      const payload = await res.json().catch(() => ({}));
      if (!res.ok) throw new Error(payload.error || `Create failed (${res.status})`);
      setNewAccount('');
      setNewLabel('');
      setNewIsLive(false);
      setOkMsg('Account added.');
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
        <section className="dashboard-hero">
          <div className="dashboard-hero-copy">
            <h1 className="dashboard-hero-title">Alpaca trading</h1>
            <p className="dashboard-hero-lead">
              Store your Alpaca API keys and optional account labels for live runs. Keys are kept on the server and
              never shown in full after saving.
            </p>
          </div>
          <div className="dashboard-hero-actions">
            <Link to="/dashboard" className="dashboard-link-btn">
              ← Dashboard
            </Link>
          </div>
        </section>

        {error ? <p className="dashboard-banner-error">{error}</p> : null}
        {okMsg ? <p className="dashboard-banner-ok">{okMsg}</p> : null}
        {loading ? <p className="dashboard-loading muted">Loading…</p> : null}

        {!loading && profile ? (
          <div className="dashboard-grid dashboard-grid--single">
            <section className="dashboard-panel">
              <div className="dashboard-panel-head">
                <h2 className="dashboard-panel-title">API credentials</h2>
              </div>
              <p className="dashboard-card-meta muted">
                {profile.has_alpaca_api_key ? `API key: ${profile.alpaca_api_key_hint || 'set'}` : 'API key: not set'}
                <br />
                {profile.has_alpaca_secret_key
                  ? `Secret: ${profile.alpaca_secret_key_hint || 'set'}`
                  : 'Secret: not set'}
              </p>
              <div className="settings-form">
                <label className="settings-label">
                  Alpaca API key
                  <input
                    className="settings-input"
                    type="password"
                    autoComplete="off"
                    value={apiKeyInput}
                    onChange={(e) => setApiKeyInput(e.target.value)}
                    placeholder="PK…"
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
                    placeholder="••••"
                  />
                </label>
                <button type="button" className="dashboard-btn-primary" disabled={saving} onClick={saveProfile}>
                  Save credentials
                </button>
              </div>
            </section>

            <section className="dashboard-panel">
              <div className="dashboard-panel-head">
                <h2 className="dashboard-panel-title">Alpaca accounts</h2>
                <span className="dashboard-panel-count">{accounts.length}</span>
              </div>
              <p className="dashboard-card-meta muted">
                Optional labels for accounts (paper vs live). Live runner still uses the keys above; use labels to
                remember which Alpaca account you use.
              </p>
              <div className="settings-form settings-form--inline">
                <input
                  className="settings-input"
                  value={newAccount}
                  onChange={(e) => setNewAccount(e.target.value)}
                  placeholder="Account id"
                />
                <input
                  className="settings-input"
                  value={newLabel}
                  onChange={(e) => setNewLabel(e.target.value)}
                  placeholder="Label"
                />
                <label className="settings-checkbox">
                  <input type="checkbox" checked={newIsLive} onChange={(e) => setNewIsLive(e.target.checked)} />
                  Live
                </label>
                <button type="button" className="dashboard-btn-primary" disabled={saving} onClick={addAccount}>
                  Add account
                </button>
              </div>
              {accounts.length ? (
                <ul className="settings-account-list">
                  {accounts.map((a) => (
                    <li key={a.id} className="settings-account-row">
                      <div>
                        <strong>{a.label || '—'}</strong>
                        <span className="muted"> · {a.account || '—'}</span>
                        {a.is_live ? <span className="dashboard-pill dashboard-pill--warn">live</span> : null}
                        {!a.is_live ? <span className="dashboard-pill dashboard-pill--muted">paper</span> : null}
                      </div>
                      <button type="button" className="dashboard-link-btn" disabled={saving} onClick={() => deleteAccount(a.id)}>
                        Remove
                      </button>
                    </li>
                  ))}
                </ul>
              ) : (
                <p className="dashboard-empty-text muted">No accounts yet.</p>
              )}
            </section>
          </div>
        ) : null}
      </div>
    </div>
  );
}
