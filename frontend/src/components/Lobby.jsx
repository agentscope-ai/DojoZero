import { useState, useEffect, useCallback } from "react";
import { useNavigate } from "react-router-dom";
import { motion } from "framer-motion";
import { useTheme } from "../App";
import { API_BASE_URL, nbaTeams, findTeamByName, getTeamLogo } from "../constants";
import ThemeToggle from "./ThemeToggle";

// Team Logo component with fallback
function TeamLogo({ team, size = 50 }) {
  const [imageError, setImageError] = useState(false);
  
  const logoSize = size;
  const imgSize = size * 0.625; // 50/80 ratio from original
  
  if (imageError || !team.logo) {
    // Fallback: show first letter of team name
    return (
      <div
        style={{
          width: logoSize,
          height: logoSize,
          borderRadius: "50%",
          background: `linear-gradient(135deg, ${team.color}88 0%, ${team.color}44 100%)`,
          display: "flex",
          alignItems: "center",
          justifyContent: "center",
          boxShadow: "0 4px 20px rgba(0, 0, 0, 0.3)",
        }}
      >
        <span
          style={{
            fontSize: size * 0.4,
            color: "#fff",
            fontWeight: "bold",
            textShadow: "0 2px 4px rgba(0,0,0,0.3)",
          }}
        >
          {team.name?.charAt(0) || "?"}
        </span>
      </div>
    );
  }
  
  return (
    <div
      style={{
        width: logoSize,
        height: logoSize,
        borderRadius: "50%",
        background: `linear-gradient(135deg, ${team.color}88 0%, ${team.color}44 100%)`,
        display: "flex",
        alignItems: "center",
        justifyContent: "center",
        boxShadow: "0 4px 20px rgba(0, 0, 0, 0.3)",
      }}
    >
      <img
        src={team.logo}
        alt={team.name}
        style={{
          width: imgSize,
          height: imgSize,
          objectFit: "contain",
        }}
        onError={() => setImageError(true)}
      />
    </div>
  );
}

export default function Lobby() {
  const [trials, setTrials] = useState([]);
  const [loading, setLoading] = useState(true);
  const [hoveredTrial, setHoveredTrial] = useState(null);
  const navigate = useNavigate();
  const { theme } = useTheme();

  const fetchTrials = useCallback(async () => {
    try {
      const response = await fetch(`${API_BASE_URL}/trials`);
      const data = await response.json();
      setTrials(data);
    } catch (error) {
      console.error("Failed to fetch trials:", error);
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => {
    fetchTrials();
    // Poll for trial updates every 5 seconds
    const interval = setInterval(fetchTrials, 5000);
    return () => clearInterval(interval);
  }, [fetchTrials]);

  const getTeamInfo = (tricode) => {
    // First try direct tricode lookup
    if (nbaTeams[tricode]) {
      const team = nbaTeams[tricode];
      return { ...team, tricode, logo: getTeamLogo(tricode) };
    }
    // Try finding by name (handles full names like "Brooklyn Nets")
    const found = findTeamByName(tricode);
    if (found) {
      return found; // findTeamByName already includes logo
    }
    // Fallback with the provided name
    return { name: tricode || "Team", city: "", color: "#64748B", logo: null };
  };

  const isTrialLive = (trial) => {
    return trial.phase !== "stopped" && trial.phase !== "completed";
  };

  return (
    <div className="lobby-container" style={styles.container}>
      {/* Background with NBA 2K style lighting */}
      <div style={styles.backgroundOverlay}>
        <div style={styles.spotlightLeft} />
        <div style={styles.spotlightRight} />
        <div style={styles.gridPattern} />
      </div>

      {/* Header */}
      <header style={styles.header}>
        <motion.div
          initial={{ opacity: 0, y: -20 }}
          animate={{ opacity: 1, y: 0 }}
          transition={{ duration: 0.6 }}
        >
          <h1 className="font-display" style={styles.title}>
            <span style={styles.titleAccent}>DOJO</span> ZERO
          </h1>
          <p style={styles.subtitle}>AI BETTING SHOWDOWN</p>
        </motion.div>
        <ThemeToggle />
      </header>

      {/* Main content */}
      <main style={styles.main}>
        <motion.h2
          className="font-tech"
          style={styles.sectionTitle}
          initial={{ opacity: 0 }}
          animate={{ opacity: 1 }}
          transition={{ delay: 0.3 }}
        >
          SELECT BETTING ROOM
        </motion.h2>

        {loading ? (
          <div style={styles.loadingContainer}>
            {[1, 2, 3].map((i) => (
              <div key={i} className="shimmer" style={styles.loadingCard} />
            ))}
          </div>
        ) : (
          <div style={styles.trialsGrid}>
            {trials.map((trial, index) => {
              const homeTeam = getTeamInfo(
                trial.metadata?.home_team_tricode || "MIA"
              );
              const awayTeam = getTeamInfo(
                trial.metadata?.away_team_tricode || "TOR"
              );
              const isHovered = hoveredTrial === trial.id;

              return (
                <motion.div
                  key={trial.id}
                  initial={{ opacity: 0, y: 30 }}
                  animate={{ opacity: 1, y: 0 }}
                  transition={{ delay: 0.1 * index, duration: 0.5 }}
                  onMouseEnter={() => setHoveredTrial(trial.id)}
                  onMouseLeave={() => setHoveredTrial(null)}
                  onClick={() => navigate(`/room/${trial.id}`, { state: { isLive: isTrialLive(trial) } })}
                  style={{
                    ...styles.trialCard,
                    transform: isHovered ? "scale(1.02)" : "scale(1)",
                    boxShadow: isHovered
                      ? `0 20px 60px rgba(59, 130, 246, 0.3), 
                         inset 0 1px 0 rgba(255, 255, 255, 0.1)`
                      : `0 10px 40px rgba(0, 0, 0, 0.4), 
                         inset 0 1px 0 rgba(255, 255, 255, 0.05)`,
                  }}
                  className="metal-card"
                >
                  {/* Status indicator */}
                  <div
                    style={{
                      ...styles.statusBadge,
                      background: isTrialLive(trial) ? "#F59E0B" : "#10B981",
                    }}
                  >
                    {isTrialLive(trial) ? "LIVE" : "COMPLETED"}
                  </div>

                  {/* Teams matchup display */}
                  <div style={styles.matchupContainer}>
                    {/* Home team */}
                    <div style={styles.teamSection}>
                      <TeamLogo team={homeTeam} size={80} />
                      <span className="font-display" style={styles.teamName}>
                        {homeTeam.city}
                      </span>
                      <span className="font-display" style={styles.teamNameBold}>
                        {homeTeam.name}
                      </span>
                    </div>

                    {/* VS */}
                    <div style={styles.vsContainer}>
                      <span className="font-display" style={styles.vsText}>
                        VS
                      </span>
                      <div style={styles.vsLine} />
                    </div>

                    {/* Away team */}
                    <div style={styles.teamSection}>
                      <TeamLogo team={awayTeam} size={80} />
                      <span className="font-display" style={styles.teamName}>
                        {awayTeam.city}
                      </span>
                      <span className="font-display" style={styles.teamNameBold}>
                        {awayTeam.name}
                      </span>
                    </div>
                  </div>

                  {/* Game info */}
                  <div style={styles.gameInfo}>
                    <div style={styles.infoRow}>
                      <span style={styles.infoLabel}>GAME ID</span>
                      <span className="font-tech" style={styles.infoValue}>
                        {trial.metadata?.game_id || trial.id}
                      </span>
                    </div>
                    <div style={styles.infoRow}>
                      <span style={styles.infoLabel}>DATE</span>
                      <span className="font-tech" style={styles.infoValue}>
                        {trial.metadata?.game_date || "TBD"}
                      </span>
                    </div>
                  </div>

                  {/* Enter button */}
                  <motion.button
                    style={styles.enterButton}
                    whileHover={{ scale: 1.05 }}
                    whileTap={{ scale: 0.98 }}
                  >
                    <span className="font-display">ENTER ARENA</span>
                    <svg
                      width="20"
                      height="20"
                      viewBox="0 0 24 24"
                      fill="none"
                      stroke="currentColor"
                      strokeWidth="2"
                    >
                      <path d="M5 12h14M12 5l7 7-7 7" />
                    </svg>
                  </motion.button>
                </motion.div>
              );
            })}
          </div>
        )}
      </main>

      {/* Footer */}
      <footer style={styles.footer}>
        <span className="font-tech" style={styles.footerText}>
          POWERED BY AI AGENTS
        </span>
      </footer>
    </div>
  );
}

const styles = {
  container: {
    minHeight: "100vh",
    position: "relative",
    display: "flex",
    flexDirection: "column",
    overflow: "hidden",
  },
  backgroundOverlay: {
    position: "fixed",
    inset: 0,
    zIndex: 0,
    pointerEvents: "none",
  },
  spotlightLeft: {
    position: "absolute",
    top: "-50%",
    left: "-20%",
    width: "60%",
    height: "100%",
    background:
      "radial-gradient(ellipse at center, rgba(59, 130, 246, 0.15) 0%, transparent 70%)",
  },
  spotlightRight: {
    position: "absolute",
    bottom: "-30%",
    right: "-10%",
    width: "50%",
    height: "80%",
    background:
      "radial-gradient(ellipse at center, rgba(139, 92, 246, 0.1) 0%, transparent 70%)",
  },
  gridPattern: {
    position: "absolute",
    inset: 0,
    backgroundImage: `
      linear-gradient(rgba(59, 130, 246, 0.03) 1px, transparent 1px),
      linear-gradient(90deg, rgba(59, 130, 246, 0.03) 1px, transparent 1px)
    `,
    backgroundSize: "60px 60px",
  },
  header: {
    position: "relative",
    zIndex: 10,
    padding: "40px 60px",
    display: "flex",
    justifyContent: "space-between",
    alignItems: "center",
  },
  title: {
    fontSize: "48px",
    color: "var(--text-primary)",
    margin: 0,
    letterSpacing: "0.1em",
  },
  titleAccent: {
    color: "var(--accent)",
    textShadow: "0 0 30px rgba(59, 130, 246, 0.5)",
  },
  subtitle: {
    fontSize: "14px",
    color: "var(--text-muted)",
    letterSpacing: "0.3em",
    marginTop: "4px",
  },
  main: {
    flex: 1,
    position: "relative",
    zIndex: 10,
    padding: "20px 60px 60px",
  },
  sectionTitle: {
    fontSize: "16px",
    color: "var(--text-secondary)",
    letterSpacing: "0.2em",
    marginBottom: "30px",
  },
  loadingContainer: {
    display: "grid",
    gridTemplateColumns: "repeat(auto-fill, minmax(380px, 1fr))",
    gap: "30px",
  },
  loadingCard: {
    height: "300px",
    borderRadius: "16px",
  },
  trialsGrid: {
    display: "grid",
    gridTemplateColumns: "repeat(auto-fill, minmax(380px, 1fr))",
    gap: "30px",
  },
  trialCard: {
    padding: "24px",
    cursor: "pointer",
    transition: "all 0.3s ease",
    position: "relative",
  },
  statusBadge: {
    position: "absolute",
    top: "16px",
    right: "16px",
    padding: "4px 12px",
    borderRadius: "4px",
    fontSize: "11px",
    fontWeight: "700",
    letterSpacing: "0.1em",
    color: "#fff",
  },
  matchupContainer: {
    display: "flex",
    alignItems: "center",
    justifyContent: "space-between",
    marginBottom: "24px",
    padding: "16px 0",
  },
  teamSection: {
    display: "flex",
    flexDirection: "column",
    alignItems: "center",
    flex: 1,
  },
  teamName: {
    fontSize: "12px",
    color: "var(--text-muted)",
    textTransform: "uppercase",
    letterSpacing: "0.1em",
  },
  teamNameBold: {
    fontSize: "22px",
    color: "var(--text-primary)",
    textTransform: "uppercase",
  },
  vsContainer: {
    display: "flex",
    flexDirection: "column",
    alignItems: "center",
    padding: "0 20px",
  },
  vsText: {
    fontSize: "24px",
    color: "var(--accent)",
    textShadow: "0 0 20px rgba(59, 130, 246, 0.5)",
  },
  vsLine: {
    width: "40px",
    height: "2px",
    background: "linear-gradient(90deg, transparent, var(--accent), transparent)",
    marginTop: "8px",
  },
  gameInfo: {
    borderTop: "1px solid var(--glass-border)",
    paddingTop: "16px",
    marginBottom: "20px",
  },
  infoRow: {
    display: "flex",
    justifyContent: "space-between",
    alignItems: "center",
    marginBottom: "8px",
  },
  infoLabel: {
    fontSize: "11px",
    color: "var(--text-muted)",
    letterSpacing: "0.1em",
  },
  infoValue: {
    fontSize: "14px",
    color: "var(--text-secondary)",
  },
  enterButton: {
    width: "100%",
    padding: "14px 24px",
    background: "linear-gradient(135deg, var(--accent) 0%, #1D4ED8 100%)",
    border: "none",
    borderRadius: "8px",
    color: "#fff",
    fontSize: "16px",
    fontWeight: "700",
    letterSpacing: "0.1em",
    cursor: "pointer",
    display: "flex",
    alignItems: "center",
    justifyContent: "center",
    gap: "10px",
    boxShadow: "0 4px 20px rgba(59, 130, 246, 0.4)",
  },
  footer: {
    position: "relative",
    zIndex: 10,
    padding: "30px 60px",
    textAlign: "center",
    borderTop: "1px solid var(--glass-border)",
  },
  footerText: {
    fontSize: "12px",
    color: "var(--text-muted)",
    letterSpacing: "0.3em",
  },
};


