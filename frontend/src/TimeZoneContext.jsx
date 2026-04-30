import { createContext, useCallback, useContext, useEffect, useMemo, useState } from 'react';
import { useAuth } from './AuthContext';
import { browserTimeZone, normalizeTimeZone } from './lib/dateTime.js';

const API_BASE_URL =
  import.meta.env.VITE_API_BASE_URL ||
  (import.meta.env.PROD ? '/api' : 'http://localhost:8080');

const STORAGE_KEY = 'vibetrader.timezone';
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

export function TimeZoneProvider({ children }) {
  const { user, getAccessToken } = useAuth();
  const [timeZone, setTimeZoneState] = useState(storedTimeZone);
  const [loading, setLoading] = useState(false);

  const setTimeZone = useCallback((value) => {
    const next = normalizeTimeZone(value);
    rememberTimeZone(next);
    setTimeZoneState(next);
  }, []);

  const refreshTimeZone = useCallback(async () => {
    if (!user) {
      setTimeZone(browserTimeZone());
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
      }
    } finally {
      setLoading(false);
    }
  }, [getAccessToken, setTimeZone, user]);

  useEffect(() => {
    refreshTimeZone();
  }, [refreshTimeZone]);

  const value = useMemo(
    () => ({
      timeZone,
      loading,
      setTimeZone,
      refreshTimeZone,
    }),
    [loading, refreshTimeZone, setTimeZone, timeZone],
  );

  return <TimeZoneContext.Provider value={value}>{children}</TimeZoneContext.Provider>;
}

export function useTimeZone() {
  const ctx = useContext(TimeZoneContext);
  if (!ctx) throw new Error('useTimeZone must be used within TimeZoneProvider');
  return ctx;
}
