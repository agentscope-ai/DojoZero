import { useRef, useEffect } from "react";
import { motion, AnimatePresence } from "framer-motion";
import { eventTypes } from "../constants";

export default function EventReplay({
  events,
  currentIndex,
  homeTeam,
  awayTeam,
}) {
  const scrollRef = useRef(null);

  // Auto-scroll to latest event
  useEffect(() => {
    if (scrollRef.current) {
      scrollRef.current.scrollTop = scrollRef.current.scrollHeight;
    }
  }, [currentIndex]);

  const getEventIcon = (type) => {
    const config = eventTypes[type] || eventTypes.game_update;
    const icons = {
      activity: (
        <svg
          width="18"
          height="18"
          viewBox="0 0 24 24"
          fill="none"
          stroke="currentColor"
          strokeWidth="2"
        >
          <polyline points="22 12 18 12 15 21 9 3 6 12 2 12" />
        </svg>
      ),
      "trending-up": (
        <svg
          width="18"
          height="18"
          viewBox="0 0 24 24"
          fill="none"
          stroke="currentColor"
          strokeWidth="2"
        >
          <polyline points="23 6 13.5 15.5 8.5 10.5 1 18" />
          <polyline points="17 6 23 6 23 12" />
        </svg>
      ),
      play: (
        <svg
          width="18"
          height="18"
          viewBox="0 0 24 24"
          fill="none"
          stroke="currentColor"
          strokeWidth="2"
        >
          <polygon points="5 3 19 12 5 21 5 3" />
        </svg>
      ),
      trophy: (
        <svg
          width="18"
          height="18"
          viewBox="0 0 24 24"
          fill="none"
          stroke="currentColor"
          strokeWidth="2"
        >
          <path d="M6 9H4.5a2.5 2.5 0 0 1 0-5H6" />
          <path d="M18 9h1.5a2.5 2.5 0 0 0 0-5H18" />
          <path d="M4 22h16" />
          <path d="M10 14.66V17c0 .55-.47.98-.97 1.21C7.85 18.75 7 20.24 7 22" />
          <path d="M14 14.66V17c0 .55.47.98.97 1.21C16.15 18.75 17 20.24 17 22" />
          <path d="M18 2H6v7a6 6 0 0 0 12 0V2Z" />
        </svg>
      ),
      brain: (
        <svg
          width="18"
          height="18"
          viewBox="0 0 24 24"
          fill="none"
          stroke="currentColor"
          strokeWidth="2"
        >
          <path d="M9.5 2A2.5 2.5 0 0 1 12 4.5v15a2.5 2.5 0 0 1-4.96.44 2.5 2.5 0 0 1-2.96-3.08 3 3 0 0 1-.34-5.58 2.5 2.5 0 0 1 1.32-4.24 2.5 2.5 0 0 1 4.44-1.54" />
          <path d="M14.5 2A2.5 2.5 0 0 0 12 4.5v15a2.5 2.5 0 0 0 4.96.44 2.5 2.5 0 0 0 2.96-3.08 3 3 0 0 0 .34-5.58 2.5 2.5 0 0 0-1.32-4.24 2.5 2.5 0 0 0-4.44-1.54" />
        </svg>
      ),
      "alert-triangle": (
        <svg
          width="18"
          height="18"
          viewBox="0 0 24 24"
          fill="none"
          stroke="currentColor"
          strokeWidth="2"
        >
          <path d="M10.29 3.86L1.82 18a2 2 0 0 0 1.71 3h16.94a2 2 0 0 0 1.71-3L13.71 3.86a2 2 0 0 0-3.42 0z" />
          <line x1="12" y1="9" x2="12" y2="13" />
          <line x1="12" y1="17" x2="12.01" y2="17" />
        </svg>
      ),
      "bar-chart-2": (
        <svg
          width="18"
          height="18"
          viewBox="0 0 24 24"
          fill="none"
          stroke="currentColor"
          strokeWidth="2"
        >
          <line x1="18" y1="20" x2="18" y2="10" />
          <line x1="12" y1="20" x2="12" y2="4" />
          <line x1="6" y1="20" x2="6" y2="14" />
        </svg>
      ),
      zap: (
        <svg
          width="18"
          height="18"
          viewBox="0 0 24 24"
          fill="none"
          stroke="currentColor"
          strokeWidth="2"
        >
          <polygon points="13 2 3 14 12 14 11 22 21 10 12 10 13 2" />
        </svg>
      ),
    };
    return icons[config.icon] || icons.activity;
  };

  const formatEvent = (event) => {
    const type = event.event_type;
    const config = eventTypes[type] || { label: type, color: "#64748B" };

    switch (type) {
      case "game_update": {
        const home = event.home_team || {};
        const away = event.away_team || {};
        return {
          title: "Score Update",
          subtitle: `${home.teamTricode || "HOME"} ${home.score || 0} - ${
            away.score || 0
          } ${away.teamTricode || "AWAY"}`,
          details: event.game_status_text || event.game_clock || `Period ${event.period}`,
          color: config.color,
          visual: "scoreboard",
        };
      }
      case "odds_update":
        return {
          title: "Odds Changed",
          subtitle: `Home: ${((event.home_probability || 0.5) * 100).toFixed(
            1
          )}% | Away: ${((event.away_probability || 0.5) * 100).toFixed(1)}%`,
          details: `Odds: ${event.home_odds?.toFixed(
            2
          ) || "N/A"} / ${event.away_odds?.toFixed(2) || "N/A"}`,
          color: config.color,
          visual: "chart",
        };
      case "expert_prediction":
        return {
          title: "Expert Predictions",
          subtitle: `${event.predictions?.length || 0} predictions received`,
          details:
            event.predictions?.[0]?.prediction?.substring(0, 50) + "..." || "",
          color: config.color,
          visual: "brain",
        };
      case "injury_summary":
        return {
          title: "Injury Report",
          subtitle: event.query || "Injury updates",
          details: event.summary?.substring(0, 80) || "Player status changes",
          color: config.color,
          visual: "injury",
        };
      case "power_ranking":
        return {
          title: "Power Rankings",
          subtitle: "Team rankings updated",
          details: event.query || "",
          color: config.color,
          visual: "ranking",
        };
      case "game_start":
        return {
          title: "Game Started",
          subtitle: `${homeTeam.name} vs ${awayTeam.name}`,
          details: "Tip-off!",
          color: config.color,
          visual: "basketball",
        };
      case "game_initialize":
        return {
          title: "Game Setup",
          subtitle: `${event.home_team || "Home"} vs ${event.away_team || "Away"}`,
          details: `Game ID: ${event.game_id || "N/A"}`,
          color: "#6B7280",
          visual: "default",
        };
      case "game_result":
        return {
          title: "Final Result",
          subtitle: `Winner: ${event.winner === "home" ? homeTeam.name : awayTeam.name}`,
          details: `Final: ${event.final_score?.home || 0} - ${event.final_score?.away || 0}`,
          color: config.color,
          visual: "trophy",
        };
      case "play_by_play": {
        const actionType = event.action_type || "";
        const player = event.player_name || "";
        return {
          title: actionType.toUpperCase() || "Play",
          subtitle: event.description || "Game action",
          details: player ? `${player} (${event.team_tricode || ""})` : `Q${event.period || 1} ${event.clock || ""}`,
          color: getPlayColor(actionType),
          visual: "basketball",
        };
      }
      default:
        return {
          title: config.label || type || "Event",
          subtitle: "Event occurred",
          details: event.description || "",
          color: config.color || "#64748B",
          visual: "default",
        };
    }
  };

  // Get color based on play-by-play action type
  const getPlayColor = (actionType) => {
    if (["2pt", "3pt", "freethrow"].includes(actionType)) return "#10B981";
    if (actionType === "foul") return "#F59E0B";
    if (actionType === "turnover") return "#EF4444";
    if (["block", "steal"].includes(actionType)) return "#3B82F6";
    if (actionType === "rebound") return "#8B5CF6";
    return "#64748B";
  };

  const getVisualComponent = (visual, color) => {
    const visuals = {
      scoreboard: (
        <div style={{ ...styles.visual, borderColor: color }}>
          <div style={styles.scoreboardVisual}>
            <div
              style={{
                ...styles.scoreTeam,
                background: homeTeam.color + "33",
              }}
            >
              <span className="font-display" style={styles.scoreTeamCode}>
                {homeTeam.name?.substring(0, 3).toUpperCase()}
              </span>
            </div>
            <div style={styles.scoreVs}>
              <svg
                width="16"
                height="16"
                viewBox="0 0 24 24"
                fill="none"
                stroke={color}
                strokeWidth="2"
              >
                <circle cx="12" cy="12" r="10" />
                <path d="M12 6v6l4 2" />
              </svg>
            </div>
            <div
              style={{
                ...styles.scoreTeam,
                background: awayTeam.color + "33",
              }}
            >
              <span className="font-display" style={styles.scoreTeamCode}>
                {awayTeam.name?.substring(0, 3).toUpperCase()}
              </span>
            </div>
          </div>
        </div>
      ),
      chart: (
        <div style={{ ...styles.visual, borderColor: color }}>
          <svg width="60" height="40" viewBox="0 0 60 40">
            <polyline
              points="5,35 15,25 25,30 35,15 45,20 55,10"
              fill="none"
              stroke={color}
              strokeWidth="2"
            />
            <circle cx="55" cy="10" r="3" fill={color} />
          </svg>
        </div>
      ),
      brain: (
        <div style={{ ...styles.visual, borderColor: color }}>
          <div style={styles.brainVisual}>
            <svg
              width="32"
              height="32"
              viewBox="0 0 24 24"
              fill="none"
              stroke={color}
              strokeWidth="1.5"
            >
              <path d="M9.5 2A2.5 2.5 0 0 1 12 4.5v15a2.5 2.5 0 0 1-4.96.44 2.5 2.5 0 0 1-2.96-3.08 3 3 0 0 1-.34-5.58 2.5 2.5 0 0 1 1.32-4.24 2.5 2.5 0 0 1 4.44-1.54" />
              <path d="M14.5 2A2.5 2.5 0 0 0 12 4.5v15a2.5 2.5 0 0 0 4.96.44 2.5 2.5 0 0 0 2.96-3.08 3 3 0 0 0 .34-5.58 2.5 2.5 0 0 0-1.32-4.24 2.5 2.5 0 0 0-4.44-1.54" />
            </svg>
          </div>
        </div>
      ),
      basketball: (
        <div style={{ ...styles.visual, borderColor: color }}>
          <div style={styles.basketballVisual}>
            <svg
              width="32"
              height="32"
              viewBox="0 0 24 24"
              fill="none"
              stroke={color}
              strokeWidth="1.5"
            >
              <circle cx="12" cy="12" r="10" />
              <path d="M12 2v20" />
              <path d="M2 12h20" />
              <path d="M4.93 4.93c4.08 4.08 4.08 10.06 0 14.14" />
              <path d="M19.07 4.93c-4.08 4.08-4.08 10.06 0 14.14" />
            </svg>
          </div>
        </div>
      ),
      default: (
        <div style={{ ...styles.visual, borderColor: color }}>
          <svg
            width="32"
            height="32"
            viewBox="0 0 24 24"
            fill="none"
            stroke={color}
            strokeWidth="1.5"
          >
            <circle cx="12" cy="12" r="10" />
            <line x1="12" y1="16" x2="12" y2="12" />
            <line x1="12" y1="8" x2="12.01" y2="8" />
          </svg>
        </div>
      ),
    };
    return visuals[visual] || visuals.default;
  };

  const formatTime = (timestamp) => {
    if (!timestamp) return "";
    try {
      const date = new Date(timestamp);
      return date.toLocaleTimeString("en-US", {
        hour: "2-digit",
        minute: "2-digit",
        second: "2-digit",
      });
    } catch {
      return "";
    }
  };

  return (
    <div style={styles.container}>
      <div ref={scrollRef} style={styles.eventsList}>
        <AnimatePresence mode="popLayout">
          {events.map((event, index) => {
            const formatted = formatEvent(event);
            const isLatest = index === events.length - 1;
            const config = eventTypes[event.event_type] || {};

            return (
              <motion.div
                key={`${event.timestamp}-${index}`}
                initial={{ opacity: 0, x: -30, scale: 0.95 }}
                animate={{ opacity: 1, x: 0, scale: 1 }}
                exit={{ opacity: 0, x: 30 }}
                transition={{ duration: 0.3 }}
                style={{
                  ...styles.eventCard,
                  borderLeft: `3px solid ${formatted.color}`,
                  background: isLatest
                    ? `linear-gradient(90deg, ${formatted.color}11 0%, transparent 100%)`
                    : "var(--frosted-glass)",
                }}
              >
                {/* Visual section */}
                <div style={styles.eventVisual}>
                  {getVisualComponent(formatted.visual, formatted.color)}
                </div>

                {/* Content section */}
                <div style={styles.eventContent}>
                  <div style={styles.eventHeader}>
                    <div
                      style={{
                        ...styles.eventIcon,
                        background: formatted.color + "22",
                        color: formatted.color,
                      }}
                    >
                      {getEventIcon(event.event_type)}
                    </div>
                    <div style={styles.eventTitleSection}>
                      <span
                        className="font-display"
                        style={styles.eventTitle}
                      >
                        {formatted.title}
                      </span>
                      <span className="font-tech" style={styles.eventTime}>
                        {formatTime(event.timestamp)}
                      </span>
                    </div>
                  </div>

                  <p style={styles.eventSubtitle}>{formatted.subtitle}</p>

                  {formatted.details && (
                    <p style={styles.eventDetails}>{formatted.details}</p>
                  )}
                </div>

                {/* Pulse indicator for latest */}
                {isLatest && (
                  <div style={styles.latestIndicator}>
                    <span style={styles.latestDot} />
                    <span className="font-tech" style={styles.latestText}>
                      LATEST
                    </span>
                  </div>
                )}
              </motion.div>
            );
          })}
        </AnimatePresence>

        {events.length === 0 && (
          <div style={styles.emptyState}>
            <svg
              width="48"
              height="48"
              viewBox="0 0 24 24"
              fill="none"
              stroke="var(--text-muted)"
              strokeWidth="1"
            >
              <circle cx="12" cy="12" r="10" />
              <polyline points="12 6 12 12 16 14" />
            </svg>
            <span className="font-tech" style={styles.emptyText}>
              AWAITING EVENTS...
            </span>
          </div>
        )}
      </div>
    </div>
  );
}

const styles = {
  container: {
    flex: 1,
    display: "flex",
    flexDirection: "column",
    overflow: "hidden",
    marginTop: "12px",
  },
  eventsList: {
    flex: 1,
    overflowY: "auto",
    display: "flex",
    flexDirection: "column",
    gap: "12px",
    paddingRight: "8px",
  },
  eventCard: {
    display: "flex",
    gap: "16px",
    padding: "16px",
    borderRadius: "12px",
    border: "1px solid var(--frosted-border)",
    position: "relative",
    transition: "all 0.2s ease",
  },
  eventVisual: {
    flexShrink: 0,
  },
  visual: {
    width: "80px",
    height: "60px",
    display: "flex",
    alignItems: "center",
    justifyContent: "center",
    borderRadius: "8px",
    background: "var(--bg-tertiary)",
    border: "1px solid",
  },
  scoreboardVisual: {
    display: "flex",
    alignItems: "center",
    gap: "6px",
  },
  scoreTeam: {
    padding: "4px 8px",
    borderRadius: "4px",
  },
  scoreTeamCode: {
    fontSize: "11px",
    color: "var(--text-primary)",
    letterSpacing: "0.05em",
  },
  scoreVs: {
    display: "flex",
    alignItems: "center",
  },
  brainVisual: {
    animation: "pulse-glow 2s ease-in-out infinite",
  },
  basketballVisual: {
    animation: "float 3s ease-in-out infinite",
  },
  eventContent: {
    flex: 1,
    minWidth: 0,
  },
  eventHeader: {
    display: "flex",
    alignItems: "center",
    gap: "10px",
    marginBottom: "8px",
  },
  eventIcon: {
    width: "32px",
    height: "32px",
    display: "flex",
    alignItems: "center",
    justifyContent: "center",
    borderRadius: "8px",
  },
  eventTitleSection: {
    flex: 1,
    display: "flex",
    justifyContent: "space-between",
    alignItems: "center",
  },
  eventTitle: {
    fontSize: "16px",
    color: "var(--text-primary)",
    letterSpacing: "0.05em",
  },
  eventTime: {
    fontSize: "11px",
    color: "var(--text-muted)",
    letterSpacing: "0.05em",
  },
  eventSubtitle: {
    fontSize: "14px",
    color: "var(--text-secondary)",
    margin: "0 0 4px 0",
  },
  eventDetails: {
    fontSize: "12px",
    color: "var(--text-muted)",
    margin: 0,
    overflow: "hidden",
    textOverflow: "ellipsis",
    whiteSpace: "nowrap",
  },
  latestIndicator: {
    position: "absolute",
    top: "8px",
    right: "12px",
    display: "flex",
    alignItems: "center",
    gap: "6px",
  },
  latestDot: {
    width: "6px",
    height: "6px",
    borderRadius: "50%",
    background: "var(--success)",
    animation: "pulse-glow 1.5s ease-in-out infinite",
  },
  latestText: {
    fontSize: "10px",
    color: "var(--success)",
    letterSpacing: "0.1em",
  },
  emptyState: {
    flex: 1,
    display: "flex",
    flexDirection: "column",
    alignItems: "center",
    justifyContent: "center",
    gap: "16px",
    padding: "40px",
    opacity: 0.6,
  },
  emptyText: {
    fontSize: "12px",
    color: "var(--text-muted)",
    letterSpacing: "0.2em",
  },
};







