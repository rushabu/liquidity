import { createContext, useCallback, useContext, useEffect, useMemo, useState, type ReactNode } from "react";
import { Navigate, useLocation } from "react-router-dom";
import { clearAiCache, setUnauthorizedHandler } from "../api/client";
import { clearSticky } from "../lib/sticky";
import { getCurrentSession, refreshSession, setCurrentSession, signIn, type Profile, type Session } from "./session";

interface Auth {
  user: Profile | null;
  login: (email: string, password: string) => Promise<Profile>;
  logout: (reason?: string) => void;
  notice: string | null;           // why the user was signed out (expired session), shown on the login page
}

const AuthContext = createContext<Auth | null>(null);

/** Everything remembered in memory belongs to the user who saw it: wipe it whenever the user changes. */
function forgetData() {
  clearAiCache();
  clearSticky();
}

export function AuthProvider({ children }: { children: ReactNode }) {
  const [session, setSession] = useState<Session | null>(getCurrentSession);
  const [notice, setNotice] = useState<string | null>(null);

  const apply = useCallback((s: Session | null) => { setCurrentSession(s); setSession(s); }, []);

  const logout = useCallback((reason?: string) => {
    forgetData();
    apply(null);
    setNotice(reason ?? null);
  }, [apply]);

  const login = useCallback(async (email: string, password: string) => {
    const s = await signIn(email, password);
    forgetData();
    setNotice(null);
    apply(s);
    return s.user;
  }, [apply]);

  // any 401 from the API (expired or revoked token) signs out
  useEffect(() => {
    setUnauthorizedHandler(() => logout("Your session has expired. Please sign in again."));
    return () => setUnauthorizedHandler(null);
  }, [logout]);

  // renew the access token a minute before it expires
  useEffect(() => {
    if (!session?.refresh_token) return;
    const wait = Math.max(5_000, session.expires_at * 1000 - Date.now() - 60_000);
    const t = setTimeout(() => {
      refreshSession(session.refresh_token!).then(apply, () => logout("Your session has expired. Please sign in again."));
    }, wait);
    return () => clearTimeout(t);
  }, [session, apply, logout]);

  const value = useMemo<Auth>(() => ({ user: session?.user ?? null, login, logout, notice }), [session, login, logout, notice]);
  return <AuthContext.Provider value={value}>{children}</AuthContext.Provider>;
}

export function useAuth(): Auth {
  const a = useContext(AuthContext);
  if (!a) throw new Error("useAuth must be used inside <AuthProvider>");
  return a;
}

/** The signed-in user, or null outside the provider (pages rendered on their own in unit tests). */
export function useUser(): Profile | null {
  return useContext(AuthContext)?.user ?? null;
}

/** Route guard: no session -> /login, remembering where the user wanted to go. */
export function RequireAuth({ children }: { children: ReactNode }) {
  const { user } = useAuth();
  const loc = useLocation();
  if (!user) return <Navigate to="/login" replace state={{ from: loc.pathname + loc.search }} />;
  return <>{children}</>;
}
