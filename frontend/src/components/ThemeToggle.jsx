import { motion } from "framer-motion";
import { useTheme } from "../App";

export default function ThemeToggle() {
  const { theme, toggleTheme } = useTheme();

  return (
    <motion.button
      onClick={toggleTheme}
      style={styles.button}
      whileHover={{ scale: 1.05 }}
      whileTap={{ scale: 0.95 }}
      aria-label={`Switch to ${theme === "dark" ? "light" : "dark"} mode`}
    >
      <div style={styles.iconContainer}>
        {theme === "dark" ? (
          <svg
            width="20"
            height="20"
            viewBox="0 0 24 24"
            fill="none"
            stroke="currentColor"
            strokeWidth="2"
            strokeLinecap="round"
            strokeLinejoin="round"
          >
            <circle cx="12" cy="12" r="5" />
            <line x1="12" y1="1" x2="12" y2="3" />
            <line x1="12" y1="21" x2="12" y2="23" />
            <line x1="4.22" y1="4.22" x2="5.64" y2="5.64" />
            <line x1="18.36" y1="18.36" x2="19.78" y2="19.78" />
            <line x1="1" y1="12" x2="3" y2="12" />
            <line x1="21" y1="12" x2="23" y2="12" />
            <line x1="4.22" y1="19.78" x2="5.64" y2="18.36" />
            <line x1="18.36" y1="5.64" x2="19.78" y2="4.22" />
          </svg>
        ) : (
          <svg
            width="20"
            height="20"
            viewBox="0 0 24 24"
            fill="none"
            stroke="currentColor"
            strokeWidth="2"
            strokeLinecap="round"
            strokeLinejoin="round"
          >
            <path d="M21 12.79A9 9 0 1 1 11.21 3 7 7 0 0 0 21 12.79z" />
          </svg>
        )}
      </div>
      <span className="font-tech" style={styles.label}>
        {theme === "dark" ? "LIGHT" : "DARK"}
      </span>
    </motion.button>
  );
}

const styles = {
  button: {
    display: "flex",
    alignItems: "center",
    gap: "8px",
    padding: "10px 16px",
    background: "var(--glass)",
    border: "1px solid var(--glass-border)",
    borderRadius: "8px",
    color: "var(--text-primary)",
    cursor: "pointer",
    backdropFilter: "blur(10px)",
    WebkitBackdropFilter: "blur(10px)",
  },
  iconContainer: {
    display: "flex",
    alignItems: "center",
    justifyContent: "center",
  },
  label: {
    fontSize: "12px",
    letterSpacing: "0.1em",
  },
};







