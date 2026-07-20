import { createContext, useCallback, useContext, useEffect, useId, useMemo, useRef, useState } from 'react';
import { createPortal } from 'react-dom';
import { useAuth } from './AuthContext';
import { useTimeZone } from './TimeZoneContext.jsx';
import { formatIsoDateTime } from './lib/dateTime.js';
import { t } from './lib/i18n.js';
import {
  formatMessageQuotaCountdown,
  messageQuotaCountdownSeconds,
  normalizeMessageQuota,
} from './lib/messageQuota.js';

const API_BASE_URL =
  import.meta.env.VITE_API_BASE_URL ||
  (import.meta.env.PROD ? '/api' : 'http://localhost:8080');
const MessageQuotaContext = createContext(null);

export function MessageQuotaProvider({ children }) {
  const { user, authFetch } = useAuth();
  const { timeZone, hourFormat } = useTimeZone();
  const [quota, setQuota] = useState(null);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState('');
  const [dialogMode, setDialogMode] = useState('');
  const [nowMs, setNowMs] = useState(Date.now());
  const closeRef = useRef(null);
  const titleId = useId();

  const applyMessageQuota = useCallback((value) => {
    const normalized = normalizeMessageQuota(value);
    if (normalized) {
      setQuota(normalized);
      setError('');
    }
    return normalized;
  }, []);

  const refreshMessageQuota = useCallback(async () => {
    if (!user?.id) {
      setQuota(null);
      setError('');
      setLoading(false);
      return null;
    }
    setLoading(true);
    try {
      const response = await authFetch(`${API_BASE_URL}/message-quota`, { cache: 'no-store' });
      const payload = await response.json().catch(() => ({}));
      if (!response.ok) {
        setError(payload.error || t('quota.unavailable'));
        return null;
      }
      return applyMessageQuota(payload.quota);
    } catch (fetchError) {
      setError(fetchError instanceof Error ? fetchError.message : t('quota.unavailable'));
      return null;
    } finally {
      setLoading(false);
    }
  }, [applyMessageQuota, authFetch, user?.id]);

  useEffect(() => {
    setQuota(null);
    setError('');
    setDialogMode('');
    void refreshMessageQuota();
  }, [refreshMessageQuota, user?.id]);

  useEffect(() => {
    if (!user?.id) return undefined;
    const interval = window.setInterval(() => void refreshMessageQuota(), 60_000);
    const onVisibility = () => {
      if (document.visibilityState === 'visible') void refreshMessageQuota();
    };
    document.addEventListener('visibilitychange', onVisibility);
    return () => {
      window.clearInterval(interval);
      document.removeEventListener('visibilitychange', onVisibility);
    };
  }, [refreshMessageQuota, user?.id]);

  useEffect(() => {
    if (!quota?.retry_at) return undefined;
    const retryAtMs = Date.parse(quota.retry_at);
    if (!Number.isFinite(retryAtMs)) return undefined;
    const delay = Math.max(250, retryAtMs - Date.now() + 250);
    const timeout = window.setTimeout(() => void refreshMessageQuota(), delay);
    return () => window.clearTimeout(timeout);
  }, [quota?.retry_at, refreshMessageQuota]);

  useEffect(() => {
    if (!dialogMode) return undefined;
    const previousOverflow = document.body.style.overflow;
    document.body.style.overflow = 'hidden';
    setNowMs(Date.now());
    const timer = window.setInterval(() => setNowMs(Date.now()), 1000);
    const focusTimer = window.setTimeout(() => closeRef.current?.focus(), 0);
    const onKeyDown = (event) => {
      if (event.key === 'Escape') setDialogMode('');
    };
    window.addEventListener('keydown', onKeyDown);
    return () => {
      window.clearInterval(timer);
      window.clearTimeout(focusTimer);
      window.removeEventListener('keydown', onKeyDown);
      document.body.style.overflow = previousOverflow;
    };
  }, [dialogMode]);

  const openMessageQuotaInfo = useCallback(() => setDialogMode('info'), []);
  const openMessageQuotaExhausted = useCallback(() => setDialogMode('exhausted'), []);
  const ensureMessageQuotaAvailable = useCallback(() => {
    if (quota && quota.remaining <= 0) {
      setDialogMode('exhausted');
      return false;
    }
    return true;
  }, [quota]);

  const countdown = formatMessageQuotaCountdown(messageQuotaCountdownSeconds(quota, nowMs));
  const retryAtLabel = quota?.retry_at ? formatIsoDateTime(quota.retry_at, timeZone, hourFormat) : '';
  const value = useMemo(
    () => ({
      quota,
      loading,
      error,
      applyMessageQuota,
      refreshMessageQuota,
      openMessageQuotaInfo,
      openMessageQuotaExhausted,
      ensureMessageQuotaAvailable,
    }),
    [
      applyMessageQuota,
      ensureMessageQuotaAvailable,
      error,
      loading,
      openMessageQuotaExhausted,
      openMessageQuotaInfo,
      quota,
      refreshMessageQuota,
    ],
  );

  return (
    <MessageQuotaContext.Provider value={value}>
      {children}
      {dialogMode && typeof document !== 'undefined'
        ? createPortal(
            <div className="message-quota-dialog" role="dialog" aria-modal="true" aria-labelledby={titleId}>
              <button
                type="button"
                className="message-quota-dialog-scrim"
                aria-label={t('quota.close')}
                onClick={() => setDialogMode('')}
              />
              <div className="message-quota-dialog-panel">
                <div className="message-quota-dialog-icon" aria-hidden>
                  <span className="home-ms">schedule</span>
                </div>
                <h2 id={titleId} className="message-quota-dialog-title">
                  {dialogMode === 'exhausted' ? t('quota.exhausted_title') : t('quota.info_title')}
                </h2>
                {quota ? (
                  <p className="message-quota-dialog-usage">
                    {t('quota.remaining', { remaining: quota.remaining, limit: quota.limit })}
                  </p>
                ) : null}
                <p className="message-quota-dialog-copy">{t('quota.explanation')}</p>
                {quota?.remaining === 0 && quota.retry_at ? (
                  <div className="message-quota-reset" aria-live="polite">
                    <span>{t('quota.try_again_in', { duration: countdown })}</span>
                    <strong>{t('quota.available_at', { time: retryAtLabel })}</strong>
                  </div>
                ) : null}
                {!quota && error ? <p className="message-quota-dialog-error">{error}</p> : null}
                <div className="message-quota-dialog-actions">
                  <button
                    ref={closeRef}
                    type="button"
                    className="dashboard-btn-primary"
                    onClick={() => setDialogMode('')}
                  >
                    {t('quota.close')}
                  </button>
                </div>
              </div>
            </div>,
            document.body,
          )
        : null}
    </MessageQuotaContext.Provider>
  );
}

export function MessageQuotaButton() {
  const { quota, loading, error, openMessageQuotaInfo } = useMessageQuota();
  const remaining = quota?.remaining;
  const limit = quota?.limit;
  const label = quota
    ? t('quota.header_value', { remaining, limit })
    : t('quota.header_loading');
  const title = error || t('quota.header_title');
  return (
    <button
      type="button"
      className={`message-quota-button${quota?.remaining === 0 ? ' is-exhausted' : ''}`}
      onClick={openMessageQuotaInfo}
      aria-label={label}
      title={title}
      aria-busy={loading}
    >
      <span className="home-ms" aria-hidden>chat_bubble</span>
      <span className="message-quota-button-count">
        {quota ? `${remaining} / ${limit}` : '— / —'}
      </span>
      <span className="message-quota-button-label">{t('quota.messages')}</span>
    </button>
  );
}

export function useMessageQuota() {
  const context = useContext(MessageQuotaContext);
  if (!context) throw new Error('useMessageQuota must be used within MessageQuotaProvider');
  return context;
}
