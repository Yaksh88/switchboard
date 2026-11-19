import { useCallback, useEffect, useState } from "react";
import { NavLink, Outlet } from "react-router-dom";

const THEME_KEY = "switchboard.theme";
type Theme = "light" | "dark";

const NAV = [
  { to: "/", label: "Traffic", end: true },
  { to: "/requests", label: "Requests", end: false },
  { to: "/evals", label: "Evals", end: false },
  { to: "/providers", label: "Providers", end: false },
  { to: "/settings", label: "Settings", end: false },
];

function systemTheme(): Theme {
  return window.matchMedia("(prefers-color-scheme: dark)").matches ? "dark" : "light";
}

function storedTheme(): Theme | null {
  try {
    const value = localStorage.getItem(THEME_KEY);
    return value === "light" || value === "dark" ? value : null;
  } catch {
    return null;
  }
}

function useTheme(): [Theme, () => void] {
  const [chosen, setChosen] = useState<Theme | null>(storedTheme);
  const [system, setSystem] = useState<Theme>(systemTheme);

  useEffect(() => {
    const query = window.matchMedia("(prefers-color-scheme: dark)");
    const onChange = () => setSystem(query.matches ? "dark" : "light");
    query.addEventListener("change", onChange);
    return () => query.removeEventListener("change", onChange);
  }, []);

  useEffect(() => {
    if (chosen) document.documentElement.dataset.theme = chosen;
    else delete document.documentElement.dataset.theme;
  }, [chosen]);

  const current = chosen ?? system;
  const toggle = useCallback(() => {
    const next: Theme = current === "dark" ? "light" : "dark";
    setChosen(next);
    try {
      localStorage.setItem(THEME_KEY, next);
    } catch {
      // Not persisted; the choice still applies for this session.
    }
  }, [current]);

  return [current, toggle];
}

function PlugMark() {
  return (
    <svg className="wordmark__plug" width="28" height="28" viewBox="0 0 32 32" aria-hidden="true">
      <circle cx="9" cy="16" r="5" fill="none" stroke="currentColor" strokeWidth="2.5" />
      <circle cx="23" cy="16" r="5" fill="currentColor" />
      <path d="M14 16h4" stroke="currentColor" strokeWidth="2.5" />
    </svg>
  );
}

export function Shell() {
  const [theme, toggleTheme] = useTheme();

  return (
    <div className="shell">
      <aside className="rail">
        <NavLink to="/" className="wordmark" aria-label="Switchboard home">
          <PlugMark />
          <span className="expanded">Switchboard</span>
        </NavLink>
        <nav className="rail__nav" aria-label="Main">
          {NAV.map(({ to, label, end }) => (
            <NavLink
              key={to}
              to={to}
              end={end}
              className={({ isActive }) => `rail__link${isActive ? " rail__link--active" : ""}`}
            >
              <span className="rail__lamp" aria-hidden="true" />
              {label}
            </NavLink>
          ))}
        </nav>
        <div className="rail__foot">
          <button type="button" className="button button--quiet" onClick={toggleTheme}>
            {theme === "dark" ? "Light theme" : "Dark theme"}
          </button>
        </div>
      </aside>
      <main className="main">
        <Outlet />
      </main>
    </div>
  );
}
