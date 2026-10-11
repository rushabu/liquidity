import { API_URL, ApiError, setTokenProvider } from "../api/client";

/** The signed-in user as the API sees it (role and location come from Supabase app_metadata). */
export interface Profile {
  id: string;
  email: string | null;
  role: "support" | "standard";
  location: string | null;
  scope: string | null;          // "All locations" for support, else the user's location
  can_upload: boolean;
}

export interface Session {
  access_token: string;
  refresh_token: string | null;
  expires_at: number;            // unix seconds
  user: Profile;
}

const KEY = "liquidplus.session";

/** Per browser tab (sessionStorage): closing the tab signs out; nothing is kept on disk between visits. */
export function loadSession(): Session | null {
  try {
    const raw = sessionStorage.getItem(KEY);
    if (!raw) return null;
    const s = JSON.parse(raw) as Session;
    return s.expires_at * 1000 > Date.now() ? s : null;
  } catch {
    return null;
  }
}

export function saveSession(s: Session | null): void {
  try {
    if (s) sessionStorage.setItem(KEY, JSON.stringify(s));
    else sessionStorage.removeItem(KEY);
    sessionStorage.removeItem("liquidplus.token");            // the old manual-token key
  } catch {
    /* storage blocked: the session lives in memory only */
  }
}

let current: Session | null = loadSession();
setTokenProvider(() => current?.access_token ?? null);
export const setCurrentSession = (s: Session | null) => { current = s; saveSession(s); };
export const getCurrentSession = () => current;

async function post<T>(path: string, body: unknown): Promise<T> {
  let res: Response;
  try {
    res = await fetch(`${API_URL}${path}`, { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(body) });
  } catch {
    throw new ApiError("NETWORK", "Cannot reach the server. Is the backend running?");
  }
  const data = await res.json().catch(() => null);
  if (!res.ok) {
    const e = (data as { error?: { code?: string; message?: string } } | null)?.error;
    throw new ApiError(e?.code ?? "HTTP_ERROR", e?.message ?? `Request failed (${res.status})`, res.status);
  }
  return data as T;
}

export const signIn = (email: string, password: string) => post<Session>("/api/auth/login", { email, password });
export const refreshSession = (refresh_token: string) => post<Session>("/api/auth/refresh", { refresh_token });
