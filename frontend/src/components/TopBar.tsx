import { NavLink } from "react-router-dom";
import { useAuth } from "../auth/AuthContext";

export function TopBar() {
  const { user, logout } = useAuth();
  return (
    <header className="topbar">
      <NavLink to="/upload" className="brand" aria-label="Liquid+ home">
        <svg width="28" height="28" viewBox="0 0 32 32" aria-hidden="true">
          <rect width="32" height="32" rx="7" fill="var(--primary)" />
          <path d="M16 6c4 5 7 8.5 7 12a7 7 0 0 1-14 0c0-3.5 3-7 7-12z" fill="#fff" />
        </svg>
        Liquid+
      </NavLink>
      <nav className="topnav" aria-label="Main">
        <NavLink to="/upload">Data ingestion</NavLink>
        <NavLink to="/ladder">Gap ladder · deterministic</NavLink>
        <NavLink to="/ai" className="ai-nav">AI gap ladder <span className="ai-badge">AI</span></NavLink>
        <NavLink to="/whatif">What-if</NavLink>
      </nav>
      {user && (
        <div className="userbox">
          <span className="who" title={user.email ?? undefined}>
            <span className="role-chip" data-role={user.role}>{user.role === "support" ? "Support" : "Standard"}</span>
            <span className="scope">{user.scope}</span>
          </span>
          <button type="button" className="btn secondary small" onClick={() => logout()}>Sign out</button>
        </div>
      )}
    </header>
  );
}
