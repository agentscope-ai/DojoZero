import { NavLink } from "react-router-dom";
import { Sun, Moon, Cloud } from "lucide-react";
import { useTheme } from "../App";
import { useDataSource } from "../hooks/useDataSource.jsx";

const navItems = [
  { path: "/games", label: "Games" },
  { path: "/leaderboard", label: "Leaderboard" },
  { path: "/blog", label: "Blog" },
  { path: "/about", label: "About Us" },
];

export default function TopBar() {
  const { theme, toggleTheme } = useTheme();
  const { isLoading, error } = useDataSource();

  return (
    <header style={styles.header}>
      <div style={styles.container}>
        {/* Logo */}
        <NavLink to="/" style={styles.logo}>
          <span style={styles.logoAccent}>DOJO</span>
          <span style={styles.logoText}>ZERO</span>
        </NavLink>

        {/* Navigation */}
        <nav style={styles.nav}>
          {navItems.map((item) => (
            <NavLink
              key={item.path}
              to={item.path}
              style={({ isActive }) => ({
                ...styles.navLink,
                color: isActive ? "var(--accent-primary)" : "var(--text-secondary)",
                background: isActive ? "rgba(59, 130, 246, 0.1)" : "transparent",
              })}
            >
              {item.label}
            </NavLink>
          ))}
        </nav>

        {/* Right side controls */}
        <div style={styles.controls}>
          {/* API status indicator */}
          <div
            style={{
              ...styles.dataIndicator,
              borderColor: error ? "var(--accent-error)" : "var(--accent-success)",
            }}
            title={error ? `API Error: ${error}` : "Connected to Arena API"}
          >
            <Cloud size={14} style={{ color: error ? "var(--accent-error)" : "var(--accent-success)" }} />
            <span style={{
              ...styles.dataIndicatorLabel,
              color: error ? "var(--accent-error)" : "var(--accent-success)",
            }}>
              {error ? "ERROR" : "LIVE"}
            </span>
            {isLoading && (
              <span style={styles.loadingDot} />
            )}
          </div>

          {/* Theme toggle */}
          <button
            onClick={toggleTheme}
            style={styles.themeToggle}
            aria-label="Toggle theme"
          >
            {theme === "dark" ? (
              <Sun size={18} />
            ) : (
              <Moon size={18} />
            )}
          </button>
        </div>
      </div>
    </header>
  );
}

const styles = {
  header: {
    position: "sticky",
    top: 0,
    zIndex: 100,
    background: "var(--bg-secondary)",
    borderBottom: "1px solid var(--border-subtle)",
    backdropFilter: "blur(12px)",
  },
  container: {
    maxWidth: 1400,
    margin: "0 auto",
    padding: "0 24px",
    height: 64,
    display: "flex",
    alignItems: "center",
    justifyContent: "space-between",
  },
  logo: {
    display: "flex",
    alignItems: "baseline",
    gap: 4,
    textDecoration: "none",
    fontSize: 28,
    fontFamily: "'Bebas Neue', sans-serif",
    letterSpacing: "0.02em",
  },
  logoAccent: {
    color: "var(--accent-primary)",
  },
  logoText: {
    color: "var(--text-primary)",
  },
  nav: {
    display: "flex",
    alignItems: "center",
    gap: 4,
  },
  navLink: {
    padding: "8px 16px",
    borderRadius: 8,
    fontSize: 14,
    fontWeight: 500,
    textDecoration: "none",
    transition: "all 0.2s ease",
  },
  controls: {
    display: "flex",
    alignItems: "center",
    gap: 8,
  },
  dataIndicator: {
    display: "flex",
    alignItems: "center",
    gap: 5,
    padding: "4px 10px",
    background: "var(--bg-tertiary)",
    border: "1px solid",
    borderRadius: 6,
    fontSize: 11,
  },
  dataIndicatorLabel: {
    fontWeight: 700,
    letterSpacing: "0.05em",
  },
  loadingDot: {
    width: 6,
    height: 6,
    borderRadius: "50%",
    background: "var(--accent-primary)",
    animation: "pulse 1s infinite",
  },
  errorDot: {
    width: 6,
    height: 6,
    borderRadius: "50%",
    background: "var(--accent-error, #ef4444)",
  },
  themeToggle: {
    width: 40,
    height: 40,
    display: "flex",
    alignItems: "center",
    justifyContent: "center",
    background: "var(--bg-tertiary)",
    border: "1px solid var(--border-default)",
    borderRadius: 8,
    color: "var(--text-secondary)",
    cursor: "pointer",
    transition: "all 0.2s ease",
  },
};
