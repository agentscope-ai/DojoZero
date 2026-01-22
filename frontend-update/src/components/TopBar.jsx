import { NavLink } from "react-router-dom";
import { Sun, Moon } from "lucide-react";
import { useTheme } from "../App";

const navItems = [
  { path: "/games", label: "Games" },
  { path: "/leaderboard", label: "Leaderboard" },
  { path: "/blog", label: "Blog" },
  { path: "/about", label: "About Us" },
];

export default function TopBar() {
  const { theme, toggleTheme } = useTheme();

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
