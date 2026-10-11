import { useState, type FormEvent } from "react";
import { Navigate, useLocation, useNavigate } from "react-router-dom";
import type { ApiError } from "../api/client";
import { useAuth } from "../auth/AuthContext";

/** Predefined accounts (problem statement: "log in with predefined credentials"). Passwords are never shown here;
 *  they are set by scripts/setup_supabase.py (SEED_USER_PASSWORD) and handed out by the team. */
const DEMO_ACCOUNTS = [
  { email: "support@liquidplus.dev", label: "Support · all locations" },
  { email: "singapore@liquidplus.dev", label: "Singapore" },
  { email: "london@liquidplus.dev", label: "London" },
  { email: "hongkong@liquidplus.dev", label: "Hong Kong" },
  { email: "newyork@liquidplus.dev", label: "New York" },
  { email: "paris@liquidplus.dev", label: "Paris" },
];

export function LoginPage() {
  const { user, login, notice } = useAuth();
  const navigate = useNavigate();
  const from = (useLocation().state as { from?: string } | null)?.from ?? "/upload";
  const [email, setEmail] = useState("");
  const [password, setPassword] = useState("");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);

  if (user) return <Navigate to={from === "/login" ? "/upload" : from} replace />;

  async function submit(e: FormEvent) {
    e.preventDefault();
    setBusy(true); setError(null);
    try {
      const u = await login(email, password);
      navigate(from === "/login" ? (u.can_upload ? "/upload" : "/ladder") : from, { replace: true });
    } catch (err) {
      setError((err as ApiError).message);
      setPassword("");
    } finally {
      setBusy(false);
    }
  }

  return (
    <main className="login-page">
      <section className="card login-card" aria-labelledby="login-h">
        <div className="login-brand">
          <svg width="36" height="36" viewBox="0 0 32 32" aria-hidden="true">
            <rect width="32" height="32" rx="7" fill="var(--primary)" />
            <path d="M16 6c4 5 7 8.5 7 12a7 7 0 0 1-14 0c0-3.5 3-7 7-12z" fill="#fff" />
          </svg>
          <div>
            <h1 id="login-h">Sign in to Liquid+</h1>
            <p className="meta">Liquidity gap forecasting · access is scoped to your location</p>
          </div>
        </div>
        {notice && <p className="banner" role="status">{notice}</p>}
        <form onSubmit={submit} className="login-form" noValidate>
          <label>Email
            <input type="email" autoComplete="username" required value={email} onChange={(e) => setEmail(e.target.value)} autoFocus />
          </label>
          <label>Password
            <input type="password" autoComplete="current-password" required value={password} onChange={(e) => setPassword(e.target.value)} />
          </label>
          {error && <p className="tbl-error" role="alert">{error}</p>}
          <button type="submit" className="btn" disabled={busy || !email.trim() || !password}>{busy ? "Signing in…" : "Sign in"}</button>
        </form>
        <details className="login-accounts">
          <summary>Predefined accounts</summary>
          <ul>
            {DEMO_ACCOUNTS.map((a) => (
              <li key={a.email}>
                <button type="button" className="link-btn" onClick={() => { setEmail(a.email); setError(null); }}>{a.email}</button>
                <span className="meta">{a.label}</span>
              </li>
            ))}
          </ul>
          <p className="meta">Standard users see only their location. Support users see every location and can upload data.</p>
        </details>
      </section>
    </main>
  );
}
