import { useState, useEffect, useCallback, useMemo, useRef } from "react";
import { useParams, useNavigate, useLocation } from "react-router-dom";
import { motion } from "framer-motion";
import { nbaTeams, findTeamByName, getTeamLogo, DOJOZERO_CDN } from "../constants";
import { useTrialStream } from "../hooks/useTrialStream";
import ThemeToggle from "./ThemeToggle";
import GameStatsPanel from "./GameStatsPanel";
import AgentPanel from "./AgentPanel";
import DanmakuOverlay from "./DanmakuOverlay";
import ArenaBackground from "./ArenaBackground";
import CourtAnimator from "./CourtAnimator";
import EventTicker from "./EventTicker";

// Helper to get team info with fallback
// Dynamically adds logo URL from team ID
const getTeamInfo = (tricode) => {
  if (nbaTeams[tricode]) {
    const team = nbaTeams[tricode];
    return { ...team, tricode, logo: getTeamLogo(tricode) };
  }
  const found = findTeamByName(tricode);
  if (found) {
    return found; // findTeamByName already includes logo
  }
  return { name: tricode || "Team", city: "", color: "#64748B", logo: null };
};

export default function GameRoom() {
  const { trialId } = useParams();
  const navigate = useNavigate();
  const location = useLocation();

  // Determine if this is a live trial from navigation state, default to trying live first
  const isLiveFromNav = location.state?.isLive ?? true;

  // Use the WebSocket streaming hook
  const {
    connected,
    loading,
    error,
    trialEnded,
    metadata,
    phase,
    agents,
    events: streamEvents,
    agentStates,
  } = useTrialStream(trialId, isLiveFromNav);

  // Replay mode state - when trial ends or for completed trials, user can replay
  const [isReplayMode, setIsReplayMode] = useState(!isLiveFromNav);
  const [isPlaying, setIsPlaying] = useState(false);
  const [currentEventIndex, setCurrentEventIndex] = useState(0);
  const [playbackSpeed, setPlaybackSpeed] = useState(1);
  const lastLiveEventCount = useRef(0);

  // User danmaku state
  const [userDanmaku, setUserDanmaku] = useState([]);
  const [danmakuInput, setDanmakuInput] = useState("");

  // Switch to replay mode when trial ends
  useEffect(() => {
    if (trialEnded && !isReplayMode) {
      setIsReplayMode(true);
      setCurrentEventIndex(streamEvents.length - 1);
    }
  }, [trialEnded, isReplayMode, streamEvents.length]);

  // In live mode (not replay), always show latest events
  useEffect(() => {
    if (!isReplayMode && streamEvents.length > lastLiveEventCount.current) {
      setCurrentEventIndex(streamEvents.length - 1);
      lastLiveEventCount.current = streamEvents.length;
    }
  }, [isReplayMode, streamEvents.length]);

  // Replay control - only active in replay mode
  useEffect(() => {
    let interval;
    if (isReplayMode && isPlaying && currentEventIndex < streamEvents.length - 1) {
      interval = setInterval(() => {
        setCurrentEventIndex((prev) => {
          if (prev >= streamEvents.length - 1) {
            setIsPlaying(false);
            return prev;
          }
          return prev + 1;
        });
      }, 1000 / playbackSpeed);
    }
    return () => clearInterval(interval);
  }, [isReplayMode, isPlaying, currentEventIndex, streamEvents.length, playbackSpeed]);

  // Toggle between live and replay mode
  const handleToggleMode = useCallback(() => {
    if (isReplayMode) {
      // Switch to live mode - show latest events
      setIsReplayMode(false);
      setIsPlaying(false);
      setCurrentEventIndex(streamEvents.length - 1);
    } else {
      // Switch to replay mode
      setIsReplayMode(true);
      setIsPlaying(false);
    }
  }, [isReplayMode, streamEvents.length]);

  const handlePlayPause = useCallback(() => {
    if (!isReplayMode) {
      // In live mode, clicking play enters replay mode and starts from beginning
      setIsReplayMode(true);
      setCurrentEventIndex(0);
      setIsPlaying(true);
      return;
    }

    if (currentEventIndex >= streamEvents.length - 1) {
      setCurrentEventIndex(0);
    }
    setIsPlaying(!isPlaying);
  }, [isReplayMode, currentEventIndex, streamEvents.length, isPlaying]);

  const handleRestart = useCallback(() => {
    setCurrentEventIndex(0);
    setIsPlaying(false);
    if (!isReplayMode) {
      setIsReplayMode(true);
    }
  }, [isReplayMode]);

  const handleSendDanmaku = useCallback(
    (e) => {
      e.preventDefault();
      if (!danmakuInput.trim()) return;

      const newMessage = {
        id: Date.now(),
        text: danmakuInput,
        timestamp: new Date().toISOString(),
      };

      setUserDanmaku((prev) => [...prev, newMessage]);
      setDanmakuInput("");
    },
    [danmakuInput]
  );

  // Remove old user danmaku after animation
  useEffect(() => {
    const cleanup = setInterval(() => {
      setUserDanmaku((prev) => prev.filter((msg) => Date.now() - msg.id < 12000));
    }, 1000);
    return () => clearInterval(cleanup);
  }, []);

  // Team info from metadata
  const homeTeam = getTeamInfo(metadata.home_team_tricode) || {
    name: metadata.home_team_tricode || "HOME",
    color: "#3B82F6",
  };
  const awayTeam = getTeamInfo(metadata.away_team_tricode) || {
    name: metadata.away_team_tricode || "AWAY",
    color: "#EF4444",
  };

  // Events to display based on mode
  const currentEvents = useMemo(() => {
    if (!isReplayMode) {
      // Live mode - show all events
      return streamEvents;
    }
    // Replay mode - show events up to current index
    return streamEvents.slice(0, currentEventIndex + 1);
  }, [isReplayMode, streamEvents, currentEventIndex]);

  if (loading) {
    return (
      <div style={styles.loadingScreen}>
        <div className="shimmer" style={styles.loadingSpinner} />
        <p className="font-tech" style={styles.loadingText}>LOADING ARENA...</p>
      </div>
    );
  }

  if (error) {
    return (
      <div style={styles.loadingScreen}>
        <p className="font-tech" style={styles.loadingText}>CONNECTION ERROR: {error}</p>
        <motion.button
          onClick={() => navigate("/")}
          style={styles.backButton}
          whileHover={{ scale: 1.05 }}
        >
          BACK TO LOBBY
        </motion.button>
      </div>
    );
  }

  // Determine if we should show live indicator
  const isCurrentlyLive = connected && !trialEnded && !isReplayMode;

  return (
    <div style={styles.container}>
      {/* NBA 2K-style Arena Background */}
      <ArenaBackground homeTeam={homeTeam} awayTeam={awayTeam} />

      {/* Header */}
      <header style={styles.header}>
        <div style={styles.headerLeft}>
          <motion.button
            onClick={() => navigate("/")}
            style={styles.backButton}
            whileHover={{ scale: 1.05 }}
            whileTap={{ scale: 0.95 }}
          >
            <svg width="20" height="20" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2">
              <path d="M19 12H5M12 19l-7-7 7-7" />
            </svg>
            <span className="font-display">LOBBY</span>
          </motion.button>
        </div>
        <ThemeToggle />
      </header>

      {/* Main content */}
      <main style={styles.main}>
        <div style={styles.mainGrid}>
          {/* Left: Game Arena with Danmaku */}
          <section style={styles.arenaSection}>
            <div style={styles.arenaCard}>
              {/* Scoreboard + Game Stats Panel */}
              <GameStatsPanel
                events={currentEvents}
                homeTeam={homeTeam}
                awayTeam={awayTeam}
              />

              {/* Animated Court with Players */}
              <div style={styles.danmakuArea}>
                {/* Court with animated basketball players */}
                <div style={styles.courtLayer}>
                  <CourtAnimator
                    events={currentEvents}
                    currentEventIndex={currentEventIndex}
                    homeTeam={homeTeam}
                    awayTeam={awayTeam}
                  />
                </div>

                {/* User Danmaku Overlay - only for user messages */}
                <div style={styles.danmakuLayer}>
                  <DanmakuOverlay
                    userMessages={userDanmaku}
                  />
                </div>

                {/* Advanced Event Ticker at Bottom */}
                <div style={styles.tickerLayer}>
                  <EventTicker
                    events={currentEvents}
                    homeTeam={homeTeam}
                    awayTeam={awayTeam}
                    currentEventIndex={currentEventIndex}
                  />
                </div>
              </div>
            </div>
          </section>

          {/* Right: AI Agents only */}
          <div style={styles.rightColumn}>
            {/* Agent Panel */}
            <section style={styles.agentSection}>
              <div className="metal-card" style={styles.agentCard}>
                <h3 className="font-display" style={styles.sectionTitle}>AI AGENTS</h3>
                <AgentPanel 
                  agents={agents} 
                  events={currentEvents}
                  currentEventIndex={currentEventIndex}
                  agentStates={agentStates}
                />
              </div>
            </section>
          </div>
        </div>
      </main>

      {/* Footer: Controls + Danmaku input */}
      <footer style={styles.footer}>
        {/* Replay controls */}
        <div style={styles.replayControls}>
          {/* Live/Replay Mode Toggle */}
          {connected && !trialEnded && (
            <motion.button
              onClick={handleToggleMode}
              style={{
                ...styles.controlButton,
                background: isReplayMode ? "var(--bg-tertiary)" : "rgba(16, 185, 129, 0.2)",
                borderColor: isReplayMode ? "var(--glass-border)" : "#10B981",
              }}
              whileHover={{ scale: 1.1 }}
              whileTap={{ scale: 0.9 }}
              title={isReplayMode ? "Switch to Live" : "Switch to Replay"}
            >
              {isReplayMode ? (
                <svg width="20" height="20" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2">
                  <circle cx="12" cy="12" r="10" />
                  <circle cx="12" cy="12" r="3" fill="#10B981" />
                </svg>
              ) : (
                <svg width="20" height="20" viewBox="0 0 24 24" fill="none" stroke="#10B981" strokeWidth="2">
                  <circle cx="12" cy="12" r="10" />
                  <circle cx="12" cy="12" r="3" fill="#10B981" />
                </svg>
              )}
            </motion.button>
          )}

          <motion.button
            onClick={handleRestart}
            style={styles.controlButton}
            whileHover={{ scale: 1.1 }}
            whileTap={{ scale: 0.9 }}
            title="Restart"
          >
            <svg width="20" height="20" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2">
              <path d="M1 4v6h6M23 20v-6h-6" />
              <path d="M20.49 9A9 9 0 0 0 5.64 5.64L1 10m22 4l-4.64 4.36A9 9 0 0 1 3.51 15" />
            </svg>
          </motion.button>

          <motion.button
            onClick={handlePlayPause}
            style={styles.playButton}
            whileHover={{ scale: 1.1 }}
            whileTap={{ scale: 0.9 }}
          >
            {isPlaying ? (
              <svg width="24" height="24" viewBox="0 0 24 24" fill="currentColor">
                <rect x="6" y="4" width="4" height="16" />
                <rect x="14" y="4" width="4" height="16" />
              </svg>
            ) : (
              <svg width="24" height="24" viewBox="0 0 24 24" fill="currentColor">
                <polygon points="5 3 19 12 5 21 5 3" />
              </svg>
            )}
          </motion.button>

          <div style={styles.speedControl}>
            <span className="font-tech" style={styles.speedLabel}>SPEED</span>
            <select
              value={playbackSpeed}
              onChange={(e) => setPlaybackSpeed(Number(e.target.value))}
              style={styles.speedSelect}
            >
              <option value={0.5}>0.5x</option>
              <option value={1}>1x</option>
              <option value={2}>2x</option>
              <option value={4}>4x</option>
            </select>
          </div>

          <div style={styles.progressInfo}>
            <span className="font-tech" style={styles.progressText}>
              {isReplayMode ? `${currentEventIndex + 1} / ${streamEvents.length}` : `LIVE: ${streamEvents.length} events`}
            </span>
            <input
              type="range"
              min={0}
              max={Math.max(0, streamEvents.length - 1)}
              value={currentEventIndex}
              onChange={(e) => {
                if (!isReplayMode) setIsReplayMode(true);
                setCurrentEventIndex(Number(e.target.value));
              }}
              style={styles.slider}
              disabled={!isReplayMode && streamEvents.length === 0}
            />
          </div>

          {/* Live indicator */}
          {isCurrentlyLive && (
            <div style={styles.liveTag}>
              <span style={styles.liveDotFooter} />
              <span className="font-tech" style={styles.liveTextFooter}>LIVE</span>
            </div>
          )}
        </div>

        {/* Danmaku input */}
        <form onSubmit={handleSendDanmaku} style={styles.danmakuForm}>
          <input
            type="text"
            value={danmakuInput}
            onChange={(e) => setDanmakuInput(e.target.value)}
            placeholder="Send a comment..."
            style={styles.danmakuInput}
            maxLength={100}
          />
          <motion.button
            type="submit"
            style={styles.danmakuButton}
            whileHover={{ scale: 1.05 }}
            whileTap={{ scale: 0.95 }}
          >
            <svg width="20" height="20" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2">
              <line x1="22" y1="2" x2="11" y2="13" />
              <polygon points="22 2 15 22 11 13 2 9 22 2" />
            </svg>
          </motion.button>
        </form>
      </footer>
    </div>
  );
}

const styles = {
  container: {
    height: "100vh",
    maxHeight: "100vh",
    display: "flex",
    flexDirection: "column",
    position: "relative",
    overflow: "hidden",
  },
  loadingScreen: {
    height: "100vh",
    display: "flex",
    flexDirection: "column",
    alignItems: "center",
    justifyContent: "center",
    gap: "20px",
  },
  loadingSpinner: {
    width: "60px",
    height: "60px",
    borderRadius: "50%",
  },
  loadingText: {
    fontSize: "14px",
    letterSpacing: "0.2em",
    color: "var(--text-secondary)",
  },
  header: {
    position: "relative",
    zIndex: 100,
    padding: "12px 32px",
    display: "flex",
    justifyContent: "space-between",
    alignItems: "center",
    borderBottom: "1px solid var(--glass-border)",
    background: "var(--glass)",
    backdropFilter: "blur(20px)",
    WebkitBackdropFilter: "blur(20px)",
    flexShrink: 0,
  },
  headerLeft: {
    display: "flex",
    alignItems: "center",
    gap: "30px",
  },
  backButton: {
    display: "flex",
    alignItems: "center",
    gap: "8px",
    padding: "8px 16px",
    background: "transparent",
    border: "1px solid var(--glass-border)",
    borderRadius: "8px",
    color: "var(--text-secondary)",
    cursor: "pointer",
    fontSize: "14px",
    letterSpacing: "0.1em",
  },
  main: {
    flex: 1,
    position: "relative",
    zIndex: 10,
    padding: "16px 32px",
    display: "flex",
    flexDirection: "column",
    overflow: "hidden",
    minHeight: 0,
  },
  mainGrid: {
    flex: 1,
    display: "grid",
    gridTemplateColumns: "1fr 320px",
    gap: "16px",
    minHeight: 0,
    overflow: "hidden",
  },
  arenaSection: {
    overflow: "hidden",
    minHeight: 0,
    display: "flex",
    flexDirection: "column",
  },
  arenaCard: {
    flex: 1,
    display: "flex",
    flexDirection: "column",
    background: `url(${DOJOZERO_CDN.room_background}) center center / cover no-repeat`,
    borderRadius: "16px",
    border: "1px solid var(--glass-border)",
    borderBottom: "none",
    overflow: "hidden",
    position: "relative",
  },
  danmakuArea: {
    flex: 1,
    position: "relative",
    overflow: "hidden",
    minHeight: "300px",
  },
  courtLayer: {
    position: "absolute",
    top: 0,
    left: 0,
    right: 0,
    bottom: 0,
    zIndex: 1,
  },
  danmakuLayer: {
    position: "absolute",
    top: 0,
    left: 0,
    right: 0,
    bottom: 0,
    zIndex: 10,
    pointerEvents: "none",
  },
  tickerLayer: {
    position: "absolute",
    top: 0,
    left: 0,
    right: 0,
    bottom: 0,
    zIndex: 10,
    pointerEvents: "none",
  },
  rightColumn: {
    display: "flex",
    flexDirection: "column",
    minHeight: 0,
    overflow: "hidden",
  },
  sectionHeader: {
    display: "flex",
    justifyContent: "space-between",
    alignItems: "center",
    marginBottom: "10px",
  },
  sectionTitle: {
    fontSize: "14px",
    color: "var(--text-primary)",
    letterSpacing: "0.1em",
    margin: 0,
  },
  liveIndicator: {
    display: "flex",
    alignItems: "center",
    gap: "6px",
    fontSize: "10px",
    color: "var(--success)",
    letterSpacing: "0.1em",
  },
  liveDotSmall: {
    width: "6px",
    height: "6px",
    borderRadius: "50%",
    background: "var(--success)",
    animation: "pulse-glow 2s ease-in-out infinite",
  },
  agentSection: {
    flex: 1,
    overflow: "hidden",
    minHeight: 0,
    display: "flex",
    flexDirection: "column",
  },
  agentCard: {
    flex: 1,
    padding: "14px 18px",
    display: "flex",
    flexDirection: "column",
    overflow: "hidden",
  },
  footer: {
    position: "relative",
    zIndex: 100,
    padding: "12px 32px",
    display: "flex",
    justifyContent: "space-between",
    alignItems: "center",
    gap: "24px",
    borderTop: "1px solid var(--glass-border)",
    background: "var(--glass)",
    backdropFilter: "blur(20px)",
    WebkitBackdropFilter: "blur(20px)",
    flexShrink: 0,
  },
  replayControls: {
    display: "flex",
    alignItems: "center",
    gap: "16px",
  },
  controlButton: {
    width: "40px",
    height: "40px",
    display: "flex",
    alignItems: "center",
    justifyContent: "center",
    background: "var(--bg-tertiary)",
    border: "1px solid var(--glass-border)",
    borderRadius: "8px",
    color: "var(--text-secondary)",
    cursor: "pointer",
  },
  playButton: {
    width: "50px",
    height: "50px",
    display: "flex",
    alignItems: "center",
    justifyContent: "center",
    background: "linear-gradient(135deg, var(--accent) 0%, #1D4ED8 100%)",
    border: "none",
    borderRadius: "50%",
    color: "#fff",
    cursor: "pointer",
    boxShadow: "0 4px 20px rgba(59, 130, 246, 0.4)",
  },
  speedControl: {
    display: "flex",
    alignItems: "center",
    gap: "8px",
  },
  speedLabel: {
    fontSize: "11px",
    color: "var(--text-muted)",
    letterSpacing: "0.1em",
  },
  speedSelect: {
    padding: "6px 12px",
    background: "var(--bg-tertiary)",
    border: "1px solid var(--glass-border)",
    borderRadius: "6px",
    color: "var(--text-primary)",
    fontSize: "13px",
    cursor: "pointer",
  },
  progressInfo: {
    display: "flex",
    alignItems: "center",
    gap: "12px",
    marginLeft: "16px",
  },
  progressText: {
    fontSize: "12px",
    color: "var(--text-secondary)",
    letterSpacing: "0.05em",
    minWidth: "70px",
  },
  slider: {
    width: "200px",
    height: "6px",
    appearance: "none",
    background: "var(--bg-tertiary)",
    borderRadius: "3px",
    cursor: "pointer",
  },
  danmakuForm: {
    display: "flex",
    alignItems: "center",
    gap: "12px",
  },
  danmakuInput: {
    width: "300px",
    padding: "10px 16px",
    background: "var(--bg-tertiary)",
    border: "1px solid var(--glass-border)",
    borderRadius: "8px",
    color: "var(--text-primary)",
    fontSize: "14px",
    outline: "none",
    transition: "border-color 0.2s",
  },
  danmakuButton: {
    width: "40px",
    height: "40px",
    display: "flex",
    alignItems: "center",
    justifyContent: "center",
    background: "linear-gradient(135deg, #8B5CF6 0%, #7C3AED 100%)",
    border: "none",
    borderRadius: "8px",
    color: "#fff",
    cursor: "pointer",
  },
  liveTag: {
    display: "flex",
    alignItems: "center",
    gap: "6px",
    padding: "6px 12px",
    background: "rgba(16, 185, 129, 0.15)",
    border: "1px solid rgba(16, 185, 129, 0.4)",
    borderRadius: "6px",
    marginLeft: "8px",
  },
  liveDotFooter: {
    width: "8px",
    height: "8px",
    borderRadius: "50%",
    background: "#10B981",
    animation: "pulse-glow 1.5s ease-in-out infinite",
  },
  liveTextFooter: {
    fontSize: "11px",
    color: "#10B981",
    letterSpacing: "0.1em",
  },
};
