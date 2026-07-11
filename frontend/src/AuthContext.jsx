import { createContext, useCallback, useContext, useEffect, useMemo, useRef, useState } from 'react';
import { supabase } from './supabaseClient';

const AuthContext = createContext(null);
const API_BASE_URL =
  import.meta.env.VITE_API_BASE_URL ||
  (import.meta.env.PROD ? '/api' : 'http://localhost:8080');
const IMPERSONATION_STORAGE_KEY = 'vibetrader.admin.impersonatedUser';

function storedImpersonatedUser() {
  if (typeof sessionStorage === 'undefined') return null;
  try {
    const parsed = JSON.parse(sessionStorage.getItem(IMPERSONATION_STORAGE_KEY) || 'null');
    const id = typeof parsed?.id === 'string' ? parsed.id.trim() : '';
    if (!id) return null;
    return {
      id,
      email: typeof parsed?.email === 'string' ? parsed.email.trim() : '',
    };
  } catch {
    return null;
  }
}

function rememberImpersonatedUser(user) {
  if (typeof sessionStorage === 'undefined') return;
  try {
    if (user?.id) {
      sessionStorage.setItem(IMPERSONATION_STORAGE_KEY, JSON.stringify(user));
    } else {
      sessionStorage.removeItem(IMPERSONATION_STORAGE_KEY);
    }
  } catch {
    void 0;
  }
}

export function AuthProvider({ children }) {
  const [session, setSession] = useState(null);
  const [loading, setLoading] = useState(true);
  const [isAdmin, setIsAdmin] = useState(false);
  const [adminUsers, setAdminUsers] = useState([]);
  const [adminUsersLoading, setAdminUsersLoading] = useState(false);
  const [impersonatedUser, setImpersonatedUserState] = useState(null);
  const restoredImpersonatedUserRef = useRef(storedImpersonatedUser());
  const restoredImpersonationAppliedRef = useRef(false);

  useEffect(() => {
    supabase.auth.getSession().then(({ data: { session: s } }) => {
      setSession(s);
      setLoading(false);
    });

    const { data: { subscription } } = supabase.auth.onAuthStateChange((_event, s) => {
      setSession(s);
    });

    return () => subscription.unsubscribe();
  }, []);

  const signInWithGoogle = useCallback(async () => {
    const { error } = await supabase.auth.signInWithOAuth({
      provider: 'google',
      options: { redirectTo: `${window.location.origin}/auth/callback` },
    });
    if (error) throw error;
  }, []);

  const signOut = useCallback(async () => {
    rememberImpersonatedUser(null);
    restoredImpersonatedUserRef.current = null;
    restoredImpersonationAppliedRef.current = true;
    setImpersonatedUserState(null);
    const { error } = await supabase.auth.signOut();
    if (error) throw error;
  }, []);

  const getAccessToken = useCallback(async () => {
    const { data: { session }, error } = await supabase.auth.getSession();
    if (error || !session) return null;
    const nowSec = Math.floor(Date.now() / 1000);
    const refreshIfBefore = nowSec + 120;
    if (session.expires_at != null && session.expires_at < refreshIfBefore) {
      const { data, error: refErr } = await supabase.auth.refreshSession();
      if (!refErr && data.session?.access_token) {
        return data.session.access_token;
      }
      if (session.expires_at < nowSec) {
        return null;
      }
    }
    return session.access_token ?? null;
  }, []);

  const refreshAdminUsers = useCallback(async () => {
    const token = await getAccessToken();
    if (!token) {
      setIsAdmin(false);
      setAdminUsers([]);
      return;
    }
    setAdminUsersLoading(true);
    try {
      const response = await fetch(`${API_BASE_URL}/admin/users/recent?limit=50`, {
        headers: { Authorization: `Bearer ${token}` },
      });
      if (response.status === 403) {
        setIsAdmin(false);
        setAdminUsers([]);
        rememberImpersonatedUser(null);
        restoredImpersonatedUserRef.current = null;
        restoredImpersonationAppliedRef.current = true;
        setImpersonatedUserState(null);
        return;
      }
      if (!response.ok) return;
      const payload = await response.json().catch(() => ({}));
      setIsAdmin(true);
      setAdminUsers(Array.isArray(payload.users) ? payload.users : []);
      if (!restoredImpersonationAppliedRef.current) {
        restoredImpersonationAppliedRef.current = true;
        setImpersonatedUserState(restoredImpersonatedUserRef.current);
      }
    } finally {
      setAdminUsersLoading(false);
    }
  }, [getAccessToken]);

  useEffect(() => {
    if (!session?.user) {
      setIsAdmin(false);
      setAdminUsers([]);
      return;
    }
    void refreshAdminUsers();
  }, [refreshAdminUsers, session?.user?.id]);

  const setImpersonatedUser = useCallback((nextUser) => {
    const id = typeof nextUser?.id === 'string' ? nextUser.id.trim() : '';
    const normalized = id
      ? {
          id,
          email: typeof nextUser?.email === 'string' ? nextUser.email.trim() : '',
        }
      : null;
    rememberImpersonatedUser(normalized);
    setImpersonatedUserState(normalized);
  }, []);

  const authFetch = useCallback(async (url, options = {}) => {
    const token = await getAccessToken();
    const headers = new Headers(options.headers || {});
    if (token) headers.set('Authorization', `Bearer ${token}`);
    if (impersonatedUser?.id) headers.set('X-Act-As-User', impersonatedUser.id);
    return fetch(url, { ...options, headers });
  }, [getAccessToken, impersonatedUser?.id]);

  const getAuthenticatedUrl = useCallback(async (url) => {
    const token = await getAccessToken();
    const authenticatedUrl = new URL(String(url), window.location.origin);
    if (token) authenticatedUrl.searchParams.set('access_token', token);
    if (impersonatedUser?.id) {
      authenticatedUrl.searchParams.set('act_as_user_id', impersonatedUser.id);
    }
    return authenticatedUrl.toString();
  }, [getAccessToken, impersonatedUser?.id]);

  const actorUser = session?.user ?? null;
  const user = useMemo(() => {
    if (!actorUser || !impersonatedUser?.id) return actorUser;
    const label = impersonatedUser.email || impersonatedUser.id;
    return {
      ...actorUser,
      id: impersonatedUser.id,
      email: impersonatedUser.email || null,
      user_metadata: {
        full_name: label,
      },
    };
  }, [actorUser, impersonatedUser]);

  const value = useMemo(
    () => ({
      session,
      user,
      actorUser,
      loading,
      isAdmin,
      adminUsers,
      adminUsersLoading,
      impersonatedUser,
      setImpersonatedUser,
      refreshAdminUsers,
      signInWithGoogle,
      signOut,
      getAccessToken,
      authFetch,
      getAuthenticatedUrl,
    }),
    [
      actorUser,
      adminUsers,
      adminUsersLoading,
      authFetch,
      getAccessToken,
      getAuthenticatedUrl,
      impersonatedUser,
      isAdmin,
      loading,
      refreshAdminUsers,
      session,
      setImpersonatedUser,
      signInWithGoogle,
      signOut,
      user,
    ],
  );

  return <AuthContext.Provider value={value}>{children}</AuthContext.Provider>;
}

export function useAuth() {
  const ctx = useContext(AuthContext);
  if (!ctx) throw new Error('useAuth must be used within AuthProvider');
  return ctx;
}
