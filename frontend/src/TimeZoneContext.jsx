import { createContext, useCallback, useContext, useEffect, useMemo, useState } from 'react';
import { useAuth } from './AuthContext';
import { browserTimeZone, normalizeHourFormat, normalizeTimeZone } from './lib/dateTime.js';
import { setStoredLang } from './lib/i18n.js';

const API_BASE_URL =
  import.meta.env.VITE_API_BASE_URL ||
  (import.meta.env.PROD ? '/api' : 'http://localhost:8080');

const STORAGE_KEY = 'vibetrader.timezone';
const HOUR_FORMAT_STORAGE_KEY = 'vibetrader.hourFormat';
const LANG_STORAGE_KEY = 'vibetrader.lang';
const SUPPORTED_LANGS = ['en', 'ru'];
const TimeZoneContext = createContext(null);

function storedTimeZone() {
  if (typeof localStorage === 'undefined') return browserTimeZone();
  try {
    return normalizeTimeZone(localStorage.getItem(STORAGE_KEY), browserTimeZone());
  } catch {
    return browserTimeZone();
  }
}

function rememberTimeZone(value) {
  if (typeof localStorage === 'undefined') return;
  try {
    localStorage.setItem(STORAGE_KEY, normalizeTimeZone(value));
  } catch {
    void 0;
  }
}

function storedHourFormat() {
  if (typeof localStorage === 'undefined') return 'auto';
  try {
    return normalizeHourFormat(localStorage.getItem(HOUR_FORMAT_STORAGE_KEY), 'auto');
  } catch {
    return 'auto';
  }
}

function rememberHourFormat(value) {
  if (typeof localStorage === 'undefined') return;
  try {
    localStorage.setItem(HOUR_FORMAT_STORAGE_KEY, normalizeHourFormat(value, 'auto'));
  } catch {
    void 0;
  }
}

function storedLang() {
  if (typeof localStorage === 'undefined') return '';
  try {
    const v = localStorage.getItem(LANG_STORAGE_KEY);
    return SUPPORTED_LANGS.includes(v) ? v : '';
  } catch {
    return '';
  }
}

export function TimeZoneProvider({ children }) {
  const { user, getAccessToken } = useAuth();
  const [timeZone, setTimeZoneState] = useState(storedTimeZone);
  const [hourFormat, setHourFormatState] = useState(storedHourFormat);
  const [interfaceLang, setInterfaceLangState] = useState(storedLang);
  const [loading, setLoading] = useState(false);

  const setTimeZone = useCallback((value) => {
    const next = normalizeTimeZone(value);
    rememberTimeZone(next);
    setTimeZoneState(next);
  }, []);

  const setHourFormat = useCallback((value) => {
    const next = normalizeHourFormat(value, 'auto');
    rememberHourFormat(next);
    setHourFormatState(next);
  }, []);

  const setInterfaceLang = useCallback((value) => {
    const next = SUPPORTED_LANGS.includes(value) ? value : '';
    setStoredLang(next || 'en');
    setInterfaceLangState(next);
  }, []);

  const refreshTimeZone = useCallback(async () => {
    if (!user) {
      setTimeZone(browserTimeZone());
      setHourFormat('auto');
      return;
    }
    setLoading(true);
    try {
      const token = await getAccessToken();
      const headers = {};
      if (token) headers.Authorization = `Bearer ${token}`;
      const res = await fetch(`${API_BASE_URL}/settings/trading`, { headers });
      const payload = await res.json().catch(() => ({}));
      if (res.ok) {
        setTimeZone(payload?.profile?.timezone || browserTimeZone());
        if (payload?.profile && Object.prototype.hasOwnProperty.call(payload.profile, 'hour_format')) {
          setHourFormat(payload.profile.hour_format || 'auto');
        }
        if (payload?.profile && Object.prototype.hasOwnProperty.call(payload.profile, 'interface_language')) {
          const serverLang = payload.profile.interface_language || '';
          if (SUPPORTED_LANGS.includes(serverLang)) {
            setStoredLang(serverLang);
            setInterfaceLangState(serverLang);
          }
        }
      }
    } finally {
      setLoading(false);
    }
  }, [getAccessToken, setHourFormat, setTimeZone, user]);

  useEffect(() => {
    refreshTimeZone();
  }, [refreshTimeZone]);

  useEffect(() => {
    if (typeof window === 'undefined') return undefined;
    const onStorage = (event) => {
      if (event.key === STORAGE_KEY) {
        setTimeZoneState(normalizeTimeZone(event.newValue, browserTimeZone()));
      }
      if (event.key === HOUR_FORMAT_STORAGE_KEY) {
        setHourFormatState(normalizeHourFormat(event.newValue, 'auto'));
      }
      if (event.key === LANG_STORAGE_KEY) {
        const v = event.newValue;
        if (SUPPORTED_LANGS.includes(v)) setInterfaceLangState(v);
      }
    };
    window.addEventListener('storage', onStorage);
    return () => window.removeEventListener('storage', onStorage);
  }, []);

  const value = useMemo(
    () => ({
      timeZone,
      hourFormat,
      interfaceLang,
      loading,
      setTimeZone,
      setHourFormat,
      setInterfaceLang,
      refreshTimeZone,
    }),
    [hourFormat, interfaceLang, loading, refreshTimeZone, setHourFormat, setInterfaceLang, setTimeZone, timeZone],
  );

  return <TimeZoneContext.Provider value={value}>{children}</TimeZoneContext.Provider>;
}

export function useTimeZone() {
  const ctx = useContext(TimeZoneContext);
  if (!ctx) throw new Error('useTimeZone must be used within TimeZoneProvider');
  return ctx;
}
