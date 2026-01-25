/**
 * RoomPage - Game Room with live streaming
 * 
 * Layout:
 * - Header: Back button + game info + theme toggle
 * - Main: Game stage (left) + Agent sidebar (right)
 *   - Stage contains: Scoreboard (top), Court animation, Playback controls (bottom)
 */

import { useState, useEffect, useMemo, useCallback, useRef } from "react";
import { useParams, useNavigate } from "react-router-dom";
import { motion, AnimatePresence } from "framer-motion";
import { useRoomStream } from "../hooks/useRoomStream";
import { usePlayback } from "../hooks/usePlayback";
import { useTheme } from "../App";
import { getTeamInfo } from "../data/nba/teams";
import { DOJOZERO_CDN } from "../data/constants";
import { Scoreboard, CourtAnimator, ActionOverlay, PlaybackBar } from "../components/room";

// =============================================================================
// ROOM PAGE COMPONENT
// =============================================================================

export default function RoomPage() {
  const { gameId } = useParams();
  const navigate = useNavigate();
  const { theme, toggleTheme } = useTheme();

  // WebSocket data stream
  const {
    connected,
    loading,
    error,
    trialEnded,
    isLive,
    metadata,
    phase,
    events,
    agents,
    agentStates,
    retry,
  } = useRoomStream(gameId);

  // Playback controls via dedicated hook
  const playback = usePlayback({
    totalEvents: events.length,
    isLive,
    playbackSpeed: 1000,
  });

  // Danmaku messages
  const [danmakuMessages, setDanmakuMessages] = useState([]);
  const [danmakuInput, setDanmakuInput] = useState("");

  // Team info derived from metadata
  const homeTeam = useMemo(() => {
    return getTeamInfo(metadata.home_team_tricode) || {
      name: metadata.home_team_name || "HOME",
      tricode: metadata.home_team_tricode || "HOM",
      color: "#3B82F6",
      secondaryColor: "#60A5FA",
    };
  }, [metadata]);

  const awayTeam = useMemo(() => {
    return getTeamInfo(metadata.away_team_tricode) || {
      name: metadata.away_team_name || "AWAY",
      tricode: metadata.away_team_tricode || "AWY",
      color: "#EF4444",
      secondaryColor: "#F87171",
    };
  }, [metadata]);

  // Current event for display
  const currentEvent = events[playback.currentIndex] || null;

  // Animation state for action overlay
  const [isAnimating, setIsAnimating] = useState(false);
  const lastAnimatedIndex = useRef(-1);

  // Trigger animation on event change (only for play_by_play events)
  useEffect(() => {
    if (currentEvent && playback.currentIndex !== lastAnimatedIndex.current) {
      const shouldAnimate = currentEvent.event_type === "play_by_play" && 
        ["2pt", "3pt", "dunk", "freethrow", "block", "steal", "rebound", "turnover", "foul"]
          .includes(currentEvent.action_type?.toLowerCase());
      
      if (shouldAnimate) {
        setIsAnimating(true);
        lastAnimatedIndex.current = playback.currentIndex;
        
        // Clear animation after duration
        const timer = setTimeout(() => {
          setIsAnimating(false);
        }, 2500);
        
        return () => clearTimeout(timer);
      }
    }
  }, [currentEvent, playback.currentIndex]);

  // Handle danmaku send
  const handleSendDanmaku = useCallback((e) => {
    e.preventDefault();
    if (!danmakuInput.trim()) return;

    const newMessage = {
      id: Date.now(),
      text: danmakuInput,
      timestamp: new Date().toISOString(),
    };

    setDanmakuMessages((prev) => [...prev, newMessage]);
    setDanmakuInput("");

    // Auto-remove after animation
    setTimeout(() => {
      setDanmakuMessages((prev) => prev.filter((m) => m.id !== newMessage.id));
    }, 12000);
  }, [danmakuInput]);

  // Loading state
  if (loading) {
    return (
      <div style={styles.loadingScreen}>
        <div style={styles.loadingContent}>
          <div style={styles.loadingSpinner} />
          <span style={styles.loadingText}>CONNECTING TO ARENA...</span>
        </div>
      </div>
    );
  }

  // Error state
  if (error) {
    return (
      <div style={styles.loadingScreen}>
        <div style={styles.errorContent}>
          <span style={styles.errorIcon}>⚠️</span>
          <span style={styles.errorText}>CONNECTION ERROR</span>
          <span style={styles.errorDetail}>{error}</span>
          <div style={styles.errorActions}>
            <button onClick={retry} style={styles.retryButton}>
              RETRY CONNECTION
            </button>
            <button onClick={() => navigate("/games")} style={styles.backButton}>
              BACK TO GAMES
            </button>
          </div>
        </div>
      </div>
    );
  }

  return (
    <div style={styles.container}>
      {/* Header */}
      <header style={styles.header}>
        <div style={styles.headerLeft}>
          <motion.button
            onClick={() => navigate("/games")}
            style={styles.navButton}
            whileHover={{ scale: 1.05 }}
            whileTap={{ scale: 0.95 }}
          >
            <svg width="20" height="20" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2">
              <path d="M19 12H5M12 19l-7-7 7-7" />
            </svg>
            <span>GAMES</span>
          </motion.button>
          
          {/* Game info badge */}
          <div style={styles.gameInfoBadge}>
            <span style={{ color: homeTeam.color }}>{homeTeam.tricode}</span>
            <span style={styles.vsText}>vs</span>
            <span style={{ color: awayTeam.color }}>{awayTeam.tricode}</span>
          </div>
        </div>

        <div style={styles.headerRight}>
          {/* Live indicator */}
          {isLive && (
            <div style={styles.liveIndicator}>
              <span style={styles.liveDot} />
              <span style={styles.liveText}>LIVE</span>
            </div>
          )}
          
          {/* Theme toggle */}
          <motion.button
            onClick={toggleTheme}
            style={styles.iconButton}
            whileHover={{ scale: 1.1 }}
            whileTap={{ scale: 0.9 }}
          >
            {theme === "dark" ? "☀️" : "🌙"}
          </motion.button>
        </div>
      </header>

      {/* Main Content */}
      <main style={styles.main}>
        {/* Game Stage (Left) */}
        <section style={styles.stageSection}>
          <div style={styles.stageContainer}>
            {/* Scoreboard - Floating on top */}
            <div style={styles.scoreboardContainer}>
              <Scoreboard
                homeTeam={homeTeam}
                awayTeam={awayTeam}
                events={events}
                currentIndex={playback.currentIndex}
              />
            </div>

            {/* Game Court Area */}
            <div style={styles.courtArea}>
              {/* Background */}
              <div style={styles.courtBackground}>
                <img
                  src={DOJOZERO_CDN.room_background}
                  alt="Arena"
                  style={styles.courtBackgroundImage}
                />
                <div style={styles.courtOverlay} />
              </div>

              {/* Court Animation */}
              <CourtAnimator
                events={events}
                currentEventIndex={playback.currentIndex}
                homeTeam={homeTeam}
                awayTeam={awayTeam}
                isPlaying={playback.isPlaying}
              />

              {/* Danmaku layer */}
              <DanmakuLayer messages={danmakuMessages} />

              {/* Action effects overlay */}
              <ActionOverlay
                currentEvent={currentEvent}
                isAnimating={isAnimating}
                homeTeam={homeTeam}
                awayTeam={awayTeam}
              />
            </div>

            {/* Playback Controls - Bottom of stage */}
            <div style={styles.playbackContainer}>
              <PlaybackBar
                events={events}
                currentIndex={playback.currentIndex}
                isPlaying={playback.isPlaying}
                isLive={isLive}
                followLive={playback.followLive}
                onPlayPause={playback.togglePlay}
                onSeek={playback.seek}
                onSkipPrev={playback.skipPrev}
                onSkipNext={playback.skipNext}
                onGoLive={playback.goToLive}
                danmakuInput={danmakuInput}
                onDanmakuInputChange={setDanmakuInput}
                onDanmakuSend={handleSendDanmaku}
                homeTeam={homeTeam}
                awayTeam={awayTeam}
              />
            </div>
          </div>
        </section>

        {/* Agent Sidebar (Right) */}
        <aside style={styles.sidebar}>
          <AgentSidebarPlaceholder
            agents={agents}
            agentStates={agentStates}
            currentIndex={playback.currentIndex}
          />
        </aside>
      </main>
    </div>
  );
}

// =============================================================================
// SUPPORTING COMPONENTS
// =============================================================================

function DanmakuLayer({ messages }) {
  return (
    <div style={danmakuStyles.container}>
      <AnimatePresence>
        {messages.map((msg, index) => (
          <motion.div
            key={msg.id}
            style={{
              ...danmakuStyles.message,
              top: `${20 + (index % 5) * 15}%`,
            }}
            initial={{ x: "100vw", opacity: 1 }}
            animate={{ x: "-100%" }}
            exit={{ opacity: 0 }}
            transition={{ duration: 10, ease: "linear" }}
          >
            {msg.text}
          </motion.div>
        ))}
      </AnimatePresence>
    </div>
  );
}

function AgentSidebarPlaceholder({ agents, agentStates, currentIndex }) {
  return (
    <div style={sidebarStyles.container}>
      <div style={sidebarStyles.header}>
        <h3 style={sidebarStyles.title}>AI AGENTS</h3>
        <span style={sidebarStyles.count}>{agents.length} agents</span>
      </div>

      {/* Agent cards */}
      <div style={sidebarStyles.agentList}>
        {agents.length === 0 ? (
          <div style={sidebarStyles.emptyState}>
            <span style={sidebarStyles.emptyIcon}>🤖</span>
            <span style={sidebarStyles.emptyText}>Waiting for agents...</span>
          </div>
        ) : (
          agents.map((agent) => (
            <div key={agent.id} style={sidebarStyles.agentCard}>
              <div style={{
                ...sidebarStyles.agentAvatar,
                background: getAgentColor(agent),
              }}>
                {getAgentInitials(agent)}
              </div>
              <div style={sidebarStyles.agentInfo}>
                <span style={sidebarStyles.agentName}>{agent.name}</span>
                <span style={sidebarStyles.agentModel}>{agent.model || "Unknown"}</span>
              </div>
            </div>
          ))
        )}
      </div>

      {/* Activity stream */}
      <div style={sidebarStyles.streamSection}>
        <div style={sidebarStyles.streamHeader}>
          <span>ACTIVITY STREAM</span>
        </div>
        <div style={sidebarStyles.streamContent}>
          <div style={sidebarStyles.streamEmpty}>
            <span>Play to see agent activity</span>
          </div>
        </div>
      </div>
    </div>
  );
}

// Helper functions
function getAgentColor(agent) {
  const colors = ["#3B82F6", "#8B5CF6", "#10B981", "#F59E0B", "#EF4444", "#EC4899"];
  const hash = agent.id.split("").reduce((acc, char) => acc + char.charCodeAt(0), 0);
  return colors[hash % colors.length];
}

function getAgentInitials(agent) {
  return (agent.name || "AI")
    .split(/[-_\s]/)
    .map((w) => w[0]?.toUpperCase())
    .join("")
    .slice(0, 2);
}

// =============================================================================
// STYLES
// =============================================================================

const styles = {
  container: {
    height: "100vh",
    display: "flex",
    flexDirection: "column",
    background: "var(--bg-primary)",
    overflow: "hidden",
  },
  loadingScreen: {
    height: "100vh",
    display: "flex",
    alignItems: "center",
    justifyContent: "center",
    background: "var(--bg-primary)",
  },
  loadingContent: {
    display: "flex",
    flexDirection: "column",
    alignItems: "center",
    gap: 20,
  },
  loadingSpinner: {
    width: 60,
    height: 60,
    border: "3px solid var(--border-default)",
    borderTopColor: "var(--accent-primary)",
    borderRadius: "50%",
    animation: "spin 1s linear infinite",
  },
  loadingText: {
    fontSize: 14,
    color: "var(--text-secondary)",
    letterSpacing: "0.2em",
  },
  errorContent: {
    display: "flex",
    flexDirection: "column",
    alignItems: "center",
    gap: 16,
  },
  errorIcon: {
    fontSize: 48,
  },
  errorText: {
    fontSize: 18,
    fontWeight: 600,
    color: "var(--text-primary)",
  },
  errorDetail: {
    fontSize: 14,
    color: "var(--text-secondary)",
  },
  errorActions: {
    display: "flex",
    gap: 12,
    marginTop: 16,
  },
  retryButton: {
    padding: "10px 20px",
    background: "var(--accent-primary)",
    color: "white",
    border: "none",
    borderRadius: 8,
    fontSize: 14,
    fontWeight: 600,
    cursor: "pointer",
  },
  backButton: {
    padding: "10px 20px",
    background: "var(--bg-tertiary)",
    color: "var(--text-primary)",
    border: "1px solid var(--border-default)",
    borderRadius: 8,
    fontSize: 14,
    fontWeight: 600,
    cursor: "pointer",
  },
  header: {
    display: "flex",
    justifyContent: "space-between",
    alignItems: "center",
    padding: "12px 24px",
    background: "var(--bg-secondary)",
    borderBottom: "1px solid var(--border-default)",
    flexShrink: 0,
  },
  headerLeft: {
    display: "flex",
    alignItems: "center",
    gap: 20,
  },
  headerRight: {
    display: "flex",
    alignItems: "center",
    gap: 16,
  },
  navButton: {
    display: "flex",
    alignItems: "center",
    gap: 8,
    padding: "8px 16px",
    background: "transparent",
    border: "1px solid var(--border-default)",
    borderRadius: 8,
    color: "var(--text-secondary)",
    fontSize: 14,
    fontWeight: 600,
    cursor: "pointer",
  },
  gameInfoBadge: {
    display: "flex",
    alignItems: "center",
    gap: 8,
    padding: "6px 14px",
    background: "var(--bg-tertiary)",
    borderRadius: 6,
    fontSize: 14,
    fontWeight: 700,
  },
  vsText: {
    color: "var(--text-muted)",
    fontSize: 12,
  },
  liveIndicator: {
    display: "flex",
    alignItems: "center",
    gap: 8,
    padding: "6px 12px",
    background: "rgba(239, 68, 68, 0.15)",
    borderRadius: 6,
  },
  liveDot: {
    width: 8,
    height: 8,
    borderRadius: "50%",
    background: "#EF4444",
    boxShadow: "0 0 8px #EF4444",
    animation: "pulse 1.5s ease-in-out infinite",
  },
  liveText: {
    fontSize: 12,
    fontWeight: 700,
    color: "#EF4444",
    letterSpacing: "0.1em",
  },
  iconButton: {
    width: 40,
    height: 40,
    display: "flex",
    alignItems: "center",
    justifyContent: "center",
    background: "var(--bg-tertiary)",
    border: "1px solid var(--border-default)",
    borderRadius: 8,
    fontSize: 18,
    cursor: "pointer",
  },
  main: {
    flex: 1,
    display: "flex",
    gap: 16,
    padding: 16,
    overflow: "hidden",
    minHeight: 0,
  },
  stageSection: {
    flex: 1,
    display: "flex",
    flexDirection: "column",
    minWidth: 0,
  },
  stageContainer: {
    flex: 1,
    position: "relative",
    borderRadius: 16,
    overflow: "hidden",
    background: "var(--bg-secondary)",
    border: "1px solid var(--border-default)",
  },
  scoreboardContainer: {
    position: "absolute",
    top: 16,
    left: "50%",
    transform: "translateX(-50%)",
    zIndex: 1000, // Top layer - above everything
  },
  courtArea: {
    position: "absolute",
    top: 0,
    left: 0,
    right: 0,
    bottom: 95, // Leave space for two-row playback bar
    pointerEvents: "auto",
    zIndex: 10, // Below scoreboard and playback bar
  },
  courtBackground: {
    position: "absolute",
    inset: 0,
  },
  courtBackgroundImage: {
    width: "100%",
    height: "100%",
    objectFit: "cover",
  },
  courtOverlay: {
    position: "absolute",
    inset: 0,
    background: "rgba(0, 0, 0, 0.3)",
  },
  sidebar: {
    width: 340,
    flexShrink: 0,
  },
  playbackContainer: {
    position: "absolute",
    bottom: 0,
    left: 0,
    right: 0,
    zIndex: 1000, // Top layer - same as scoreboard
    pointerEvents: "auto",
  },
};

const danmakuStyles = {
  container: {
    position: "absolute",
    inset: 0,
    overflow: "hidden",
    pointerEvents: "none",
    zIndex: 900, // Above court but below scoreboard/playback
  },
  message: {
    position: "absolute",
    whiteSpace: "nowrap",
    fontSize: 16,
    fontWeight: 600,
    color: "white",
    textShadow: "0 2px 4px rgba(0, 0, 0, 0.8)",
    padding: "4px 12px",
    background: "rgba(0, 0, 0, 0.5)",
    borderRadius: 4,
  },
};

const sidebarStyles = {
  container: {
    height: "100%",
    display: "flex",
    flexDirection: "column",
    background: "var(--bg-secondary)",
    border: "1px solid var(--border-default)",
    borderRadius: 16,
    overflow: "hidden",
  },
  header: {
    display: "flex",
    justifyContent: "space-between",
    alignItems: "center",
    padding: "16px 20px",
    borderBottom: "1px solid var(--border-default)",
  },
  title: {
    fontSize: 14,
    fontWeight: 700,
    color: "var(--text-primary)",
    letterSpacing: "0.1em",
    margin: 0,
  },
  count: {
    fontSize: 12,
    color: "var(--text-muted)",
  },
  agentList: {
    padding: 12,
    display: "flex",
    flexDirection: "column",
    gap: 8,
  },
  agentCard: {
    display: "flex",
    alignItems: "center",
    gap: 12,
    padding: 12,
    background: "var(--bg-tertiary)",
    borderRadius: 10,
    border: "1px solid var(--border-subtle)",
  },
  agentAvatar: {
    width: 36,
    height: 36,
    borderRadius: 8,
    display: "flex",
    alignItems: "center",
    justifyContent: "center",
    color: "white",
    fontSize: 14,
    fontWeight: 700,
  },
  agentInfo: {
    display: "flex",
    flexDirection: "column",
    gap: 2,
  },
  agentName: {
    fontSize: 14,
    fontWeight: 600,
    color: "var(--text-primary)",
  },
  agentModel: {
    fontSize: 11,
    color: "var(--text-muted)",
  },
  emptyState: {
    display: "flex",
    flexDirection: "column",
    alignItems: "center",
    gap: 8,
    padding: 24,
    color: "var(--text-muted)",
  },
  emptyIcon: {
    fontSize: 32,
    opacity: 0.5,
  },
  emptyText: {
    fontSize: 13,
  },
  streamSection: {
    flex: 1,
    display: "flex",
    flexDirection: "column",
    borderTop: "1px solid var(--border-default)",
    marginTop: 8,
    overflow: "hidden",
  },
  streamHeader: {
    padding: "12px 20px",
    fontSize: 11,
    fontWeight: 600,
    color: "var(--text-muted)",
    letterSpacing: "0.1em",
    borderBottom: "1px solid var(--border-subtle)",
  },
  streamContent: {
    flex: 1,
    overflow: "auto",
    padding: 12,
  },
  streamEmpty: {
    display: "flex",
    alignItems: "center",
    justifyContent: "center",
    height: "100%",
    fontSize: 13,
    color: "var(--text-muted)",
  },
};

