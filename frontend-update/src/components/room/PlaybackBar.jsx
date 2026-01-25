/**
 * PlaybackBar - Bottom control bar with playback controls, slider, and event ticker
 * 
 * Two-row layout:
 * - Row 1: Controls + Slider + Danmaku input
 * - Row 2: Event ticker (full width)
 */

import { useState, useEffect, useMemo, useCallback, useRef } from "react";
import { motion, AnimatePresence } from "framer-motion";
import { getActionConfig } from "../../data/nba/eventTypes";

// Inject CSS to remove focus outlines on interactive elements
const styleSheet = document.createElement("style");
styleSheet.textContent = `
  /* Remove focus outlines */
  input[type="text"],
  input[type="text"]:focus,
  input[type="text"]:active,
  input[type="text"]:focus-visible {
    outline: none !important;
  }
  
  button,
  button:focus,
  button:active,
  button:focus-visible,
  button:focus-within {
    outline: none !important;
  }
  
  /* Remove tap highlight on mobile */
  * {
    -webkit-tap-highlight-color: transparent !important;
  }
`;
if (!document.head.querySelector('[data-playback-bar-styles]')) {
  styleSheet.setAttribute('data-playback-bar-styles', '');
  document.head.appendChild(styleSheet);
}

/**
 * Parse ISO 8601 duration format (e.g., "PT04M27.00S") to readable format (e.g., "4:27")
 */
function parseISODuration(duration) {
  if (!duration || typeof duration !== "string") return null;
  
  // Match PT{minutes}M{seconds}S format
  const match = duration.match(/PT(\d+)M(\d+(?:\.\d+)?)S/i);
  if (!match) {
    // Try alternative formats: PT{seconds}S or just number
    const secMatch = duration.match(/PT(\d+(?:\.\d+)?)S/i);
    if (secMatch) {
      const totalSeconds = Math.floor(parseFloat(secMatch[1]));
      const mins = Math.floor(totalSeconds / 60);
      const secs = totalSeconds % 60;
      return `${mins}:${secs.toString().padStart(2, "0")}`;
    }
    return null;
  }
  
  const minutes = parseInt(match[1], 10);
  const seconds = Math.floor(parseFloat(match[2]));
  return `${minutes}:${seconds.toString().padStart(2, "0")}`;
}

/**
 * Format clock time - handles both ISO duration and regular time formats
 */
function formatClockTime(clock) {
  if (!clock) return "";
  
  // If already in mm:ss format, return as-is
  if (/^\d{1,2}:\d{2}$/.test(clock)) return clock;
  
  // Try to parse ISO duration
  const parsed = parseISODuration(clock);
  if (parsed) return parsed;
  
  // Return original if can't parse
  return clock;
}

export default function PlaybackBar({
  events = [],
  currentIndex = 0,
  isPlaying = false,
  onPlayPause,
  onSeek,
  onSkipPrev,
  onSkipNext,
  danmakuInput = "",
  onDanmakuInputChange,
  onDanmakuSend,
  homeTeam,
  awayTeam,
}) {
  // Slider state
  const [previewIndex, setPreviewIndex] = useState(null);
  const [previewPosition, setPreviewPosition] = useState(0);
  const [isDragging, setIsDragging] = useState(false);
  const sliderRef = useRef(null);

  // Calculate slider percentage (0-100)
  const sliderPercentage = useMemo(() => {
    if (events.length <= 1) return 0;
    return (currentIndex / (events.length - 1)) * 100;
  }, [currentIndex, events.length]);

  // Custom slider: calculate index from mouse position
  const getIndexFromEvent = useCallback((e) => {
    if (!sliderRef.current || events.length === 0) return 0;
    const rect = sliderRef.current.getBoundingClientRect();
    const x = e.clientX - rect.left;
    const percentage = Math.max(0, Math.min(1, x / rect.width));
    return Math.round(percentage * (events.length - 1));
  }, [events.length]);

  // Custom slider: handle click to seek
  const handleSliderClick = useCallback((e) => {
    const index = getIndexFromEvent(e);
    onSeek?.(index);
  }, [getIndexFromEvent, onSeek]);

  // Custom slider: handle drag start
  const handleSliderMouseDown = useCallback((e) => {
    e.preventDefault();
    setIsDragging(true);
    const index = getIndexFromEvent(e);
    onSeek?.(index);
  }, [getIndexFromEvent, onSeek]);

  // Custom slider: handle drag move (attached to document when dragging)
  useEffect(() => {
    if (!isDragging) return;

    const handleMouseMove = (e) => {
      const index = getIndexFromEvent(e);
      onSeek?.(index);
    };

    const handleMouseUp = () => {
      setIsDragging(false);
    };

    document.addEventListener('mousemove', handleMouseMove);
    document.addEventListener('mouseup', handleMouseUp);

    return () => {
      document.removeEventListener('mousemove', handleMouseMove);
      document.removeEventListener('mouseup', handleMouseUp);
    };
  }, [isDragging, getIndexFromEvent, onSeek]);

  // Current ticker event - synced with playback position
  const currentTickerEvent = events[currentIndex];

  // Button click handlers - allow interaction even in live mode
  const handleSkipPrevClick = useCallback((e) => {
    e.preventDefault();
    e.stopPropagation();
    onSkipPrev?.();
  }, [onSkipPrev]);

  const handlePlayPauseClick = useCallback((e) => {
    e.preventDefault();
    e.stopPropagation();
    onPlayPause?.();
  }, [onPlayPause]);

  const handleSkipNextClick = useCallback((e) => {
    e.preventDefault();
    e.stopPropagation();
    onSkipNext?.();
  }, [onSkipNext]);

  // Slider preview handlers
  const handleSliderMouseMove = useCallback((e) => {
    if (!sliderRef.current || events.length === 0) return;
    
    const rect = sliderRef.current.getBoundingClientRect();
    const x = e.clientX - rect.left;
    const percentage = Math.max(0, Math.min(1, x / rect.width));
    const index = Math.round(percentage * (events.length - 1));
    
    setPreviewIndex(index);
    setPreviewPosition(x);
  }, [events.length]);

  const handleSliderMouseLeave = useCallback(() => {
    setPreviewIndex(null);
  }, []);

  // Get preview event
  const previewEvent = previewIndex !== null ? events[previewIndex] : null;

  return (
    <div style={styles.container}>
      {/* Row 1: Controls + Slider + Danmaku */}
      <div style={styles.controlsRow}>
        {/* Left: Playback Controls */}
        <div style={styles.controlsSection}>
          <button
            type="button"
            onClick={handleSkipPrevClick}
            style={styles.controlButton}
          >
            <SkipBackIcon />
          </button>

          <button
            type="button"
            onClick={handlePlayPauseClick}
            style={styles.playButton}
          >
            {isPlaying ? <PauseIcon /> : <PlayIcon />}
          </button>

          <button
            type="button"
            onClick={handleSkipNextClick}
            style={styles.controlButton}
          >
            <SkipForwardIcon />
          </button>
        </div>

        {/* Center: Custom Timeline Slider */}
        <div style={styles.sliderSection}>
          <div 
            ref={sliderRef}
            style={{
              ...styles.sliderWrapper,
              cursor: isDragging ? 'grabbing' : 'pointer',
            }}
            onMouseDown={handleSliderMouseDown}
            onMouseMove={handleSliderMouseMove}
            onMouseLeave={handleSliderMouseLeave}
          >
            {/* Track background */}
            <div style={styles.sliderTrack}>
              {/* Fill - width uses same percentage as thumb position */}
              <div 
                style={{
                  ...styles.sliderFill,
                  width: `${sliderPercentage}%`,
                }} 
              />
            </div>
            
            {/* Custom thumb - positioned with same percentage as fill */}
            <div 
              style={{
                ...styles.sliderThumb,
                left: `${sliderPercentage}%`,
              }}
            />
            
            {/* Preview tooltip */}
            <AnimatePresence>
              {previewEvent && previewIndex !== currentIndex && !isDragging && (
                <motion.div
                  initial={{ opacity: 0, x: "-50%", y: 5 }}
                  animate={{ opacity: 1, x: "-50%", y: 0 }}
                  exit={{ opacity: 0, x: "-50%", y: 5 }}
                  transition={{ duration: 0.15 }}
                  style={{
                    ...styles.previewTooltip,
                    left: `${previewPosition}px`,
                  }}
                >
                  <EventPreview event={previewEvent} homeTeam={homeTeam} awayTeam={awayTeam} />
                </motion.div>
              )}
            </AnimatePresence>
          </div>
        </div>

        {/* Right: Danmaku Input */}
        <form onSubmit={onDanmakuSend} style={styles.danmakuForm}>
          <input
            type="text"
            value={danmakuInput}
            onChange={(e) => onDanmakuInputChange?.(e.target.value)}
            placeholder="Send a comment..."
            style={styles.danmakuInput}
            maxLength={100}
          />
          <button type="submit" style={styles.sendButton}>
            <SendIcon />
          </button>
        </form>
      </div>

      {/* Row 2: Event Ticker (full width) - synced with playback */}
      <div style={styles.tickerRow}>
        <div style={styles.tickerLabel}>
          <span style={styles.tickerLabelDot} />
          <span>EVENTS</span>
          {events.length > 0 && (
            <span style={styles.tickerLabelCounter}>[{currentIndex + 1}/{events.length}]</span>
          )}
        </div>
        
        <div style={styles.tickerContainer}>
          <AnimatePresence mode="wait">
            {currentTickerEvent ? (
              <motion.div
                key={currentIndex}
                initial={{ y: 16, opacity: 0 }}
                animate={{ y: 0, opacity: 1 }}
                exit={{ y: -16, opacity: 0 }}
                transition={{ duration: 0.25, ease: "easeOut" }}
                style={styles.tickerContent}
              >
                <EventMessage 
                  event={currentTickerEvent} 
                  homeTeam={homeTeam}
                  awayTeam={awayTeam}
                />
              </motion.div>
            ) : (
              <div style={styles.tickerEmpty}>
                Waiting for events...
              </div>
            )}
          </AnimatePresence>
        </div>
      </div>
    </div>
  );
}

/**
 * Event Preview Component - Shown when hovering over slider
 */
function EventPreview({ event, homeTeam, awayTeam }) {
  if (!event) return null;
  
  const eventType = event.event_type;
  const actionType = event.action_type?.toLowerCase() || "";
  const player = event.player_name || "";
  const team = event.team_tricode || "";
  const clock = formatClockTime(event.clock);
  const period = event.period || 1;
  
  // Determine badge info
  let badgeColor = "#6B7280";
  let badgeText = eventType;
  
  if (eventType === "play_by_play") {
    if (["2pt", "3pt", "dunk", "layup", "freethrow"].includes(actionType)) {
      badgeColor = "#22C55E";
      badgeText = actionType.toUpperCase();
    } else if (["block", "steal"].includes(actionType)) {
      badgeColor = "#3B82F6";
      badgeText = actionType.toUpperCase();
    } else if (["turnover", "foul"].includes(actionType)) {
      badgeColor = "#EF4444";
      badgeText = actionType.toUpperCase();
    } else {
      badgeText = actionType?.toUpperCase() || "PLAY";
    }
  } else if (eventType === "game_update") {
    badgeColor = "#3B82F6";
    badgeText = "SCORE";
  } else if (eventType === "in_game_critical") {
    badgeColor = "#F59E0B";
    badgeText = event.critical_type?.toUpperCase() || "CRITICAL";
  }
  
  return (
    <div style={previewStyles.container}>
      <div style={previewStyles.header}>
        <span style={{ ...previewStyles.badge, background: badgeColor }}>
          {badgeText}
        </span>
        {clock && <span style={previewStyles.time}>Q{period} {clock}</span>}
      </div>
      <div style={previewStyles.content}>
        {player && <span style={previewStyles.player}>{player}</span>}
        {team && (
          <span style={{
            ...previewStyles.team,
            color: team === homeTeam?.tricode ? homeTeam?.color : 
                   team === awayTeam?.tricode ? awayTeam?.color : "#3B82F6",
          }}>
            {team}
          </span>
        )}
      </div>
    </div>
  );
}

const previewStyles = {
  container: {
    display: "flex",
    flexDirection: "column",
    gap: 4,
  },
  header: {
    display: "flex",
    alignItems: "center",
    gap: 8,
  },
  badge: {
    padding: "2px 6px",
    borderRadius: 3,
    fontSize: 9,
    fontWeight: 700,
    color: "white",
    letterSpacing: "0.03em",
  },
  time: {
    fontSize: 10,
    color: "rgba(255, 255, 255, 0.6)",
    fontFamily: "'JetBrains Mono', monospace",
  },
  content: {
    display: "flex",
    alignItems: "center",
    gap: 6,
  },
  player: {
    fontSize: 11,
    fontWeight: 600,
    color: "white",
  },
  team: {
    fontSize: 10,
    fontWeight: 700,
  },
};

/**
 * Event Message Component
 */
function EventMessage({ event, homeTeam, awayTeam }) {
  if (!event) return null;

  const eventType = event.event_type;
  const actionType = event.action_type?.toLowerCase() || "";

  // Determine badge color and icon
  let badgeColor = "#6B7280";
  let badgeIcon = "📌";
  let badgeText = eventType;

  switch (eventType) {
    case "play_by_play":
      if (["2pt", "3pt", "dunk", "layup", "freethrow"].includes(actionType)) {
        badgeColor = "#22C55E";
        badgeIcon = "🏀";
        badgeText = actionType.toUpperCase();
      } else if (["block", "steal"].includes(actionType)) {
        badgeColor = "#3B82F6";
        badgeIcon = "🛡️";
        badgeText = actionType.toUpperCase();
      } else if (["turnover", "foul"].includes(actionType)) {
        badgeColor = "#EF4444";
        badgeIcon = "⚠️";
        badgeText = actionType.toUpperCase();
      } else if (actionType === "rebound") {
        badgeColor = "#8B5CF6";
        badgeIcon = "📥";
        badgeText = "REB";
      } else if (actionType === "substitution") {
        badgeColor = "#F59E0B";
        badgeIcon = "🔄";
        badgeText = "SUB";
      } else {
        badgeIcon = "🏀";
        badgeText = actionType?.toUpperCase() || "PLAY";
      }
      break;

    case "game_update":
      badgeColor = "#3B82F6";
      badgeIcon = "📊";
      badgeText = "SCORE";
      break;

    case "in_game_critical":
      badgeColor = "#F59E0B";
      badgeIcon = "⚡";
      badgeText = event.critical_type?.toUpperCase() || "CRITICAL";
      break;

    case "game_start":
      badgeColor = "#22C55E";
      badgeIcon = "🏁";
      badgeText = "TIP-OFF";
      break;

    case "game_result":
      badgeColor = "#8B5CF6";
      badgeIcon = "🏆";
      badgeText = "FINAL";
      break;
  }

  // Build message content
  let messageContent = null;

  if (eventType === "play_by_play") {
    const player = event.player_name || "";
    const team = event.team_tricode || "";
    const desc = event.description || "";
    const clock = formatClockTime(event.clock) || "";
    const period = event.period || 1;
    const homeScore = event.home_score ?? "";
    const awayScore = event.away_score ?? "";

    messageContent = (
      <div style={styles.messageRow}>
        {player && <span style={styles.playerName}>{player}</span>}
        {team && (
          <span style={{
            ...styles.teamBadge,
            background: team === homeTeam?.tricode 
              ? `${homeTeam?.color}20` 
              : team === awayTeam?.tricode 
                ? `${awayTeam?.color}20` 
                : "rgba(59, 130, 246, 0.2)",
            color: team === homeTeam?.tricode 
              ? homeTeam?.color 
              : team === awayTeam?.tricode 
                ? awayTeam?.color 
                : "#3B82F6",
          }}>
            {team}
          </span>
        )}
        <span style={styles.messageDesc}>{desc}</span>
        {(homeScore !== "" && awayScore !== "") && (
          <span style={styles.scoreInfo}>{homeScore}-{awayScore}</span>
        )}
        {clock && <span style={styles.timeInfo}>Q{period} {clock}</span>}
      </div>
    );
  } else if (eventType === "game_update") {
    const home = event.home_team || {};
    const away = event.away_team || {};
    
    messageContent = (
      <div style={styles.messageRow}>
        <span style={{ color: homeTeam?.color || "#3B82F6", fontWeight: 600 }}>
          {home.teamTricode || "HOME"} {home.score ?? 0}
        </span>
        <span style={styles.scoreSeparator}>-</span>
        <span style={{ color: awayTeam?.color || "#EF4444", fontWeight: 600 }}>
          {away.score ?? 0} {away.teamTricode || "AWAY"}
        </span>
        {event.game_status_text && (
          <span style={styles.statusText}>• {event.game_status_text}</span>
        )}
      </div>
    );
  } else if (eventType === "in_game_critical") {
    const player = event.player_name || "";
    const desc = event.description || "";
    
    messageContent = (
      <div style={styles.messageRow}>
        {player && <span style={styles.playerName}>{player}</span>}
        <span style={styles.messageDesc}>{desc}</span>
      </div>
    );
  } else if (eventType === "game_start") {
    messageContent = (
      <div style={styles.messageRow}>
        <span>{homeTeam?.name || "Home"} vs {awayTeam?.name || "Away"}</span>
        <span style={styles.subText}>Game is underway!</span>
      </div>
    );
  } else if (eventType === "game_result") {
    const finalScore = event.final_score || { home: 0, away: 0 };
    
    messageContent = (
      <div style={styles.messageRow}>
        <span style={styles.finalScore}>{finalScore.home} - {finalScore.away}</span>
        {event.winner && (
          <span style={styles.winnerText}>
            🏆 {event.winner === "home" ? homeTeam?.name : awayTeam?.name} wins!
          </span>
        )}
      </div>
    );
  } else {
    messageContent = (
      <div style={styles.messageRow}>
        <span style={styles.messageDesc}>Event occurred</span>
      </div>
    );
  }

  return (
    <div style={styles.eventMessage}>
      <span style={{ ...styles.eventBadge, background: badgeColor }}>
        {badgeIcon} {badgeText}
      </span>
      {messageContent}
    </div>
  );
}

// SVG Icons
const PlayIcon = () => (
  <svg width="20" height="20" viewBox="0 0 24 24" fill="currentColor">
    <path d="M8 5v14l11-7z" />
  </svg>
);

const PauseIcon = () => (
  <svg width="20" height="20" viewBox="0 0 24 24" fill="currentColor">
    <path d="M6 4h4v16H6zM14 4h4v16h-4z" />
  </svg>
);

const SkipBackIcon = () => (
  <svg width="16" height="16" viewBox="0 0 24 24" fill="currentColor">
    <path d="M6 6h2v12H6zm3.5 6l8.5 6V6z" />
  </svg>
);

const SkipForwardIcon = () => (
  <svg width="16" height="16" viewBox="0 0 24 24" fill="currentColor">
    <path d="M6 18l8.5-6L6 6v12zM16 6v12h2V6h-2z" />
  </svg>
);

const SendIcon = () => (
  <svg width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2">
    <path d="M22 2L11 13M22 2l-7 20-4-9-9-4 20-7z" />
  </svg>
);

// =============================================================================
// STYLES
// =============================================================================

const styles = {
  container: {
    display: "flex",
    flexDirection: "column",
    background: "linear-gradient(180deg, rgba(0, 0, 0, 0.85) 0%, rgba(0, 0, 0, 0.98) 100%)",
    backdropFilter: "blur(12px)",
    borderTop: "1px solid rgba(255, 255, 255, 0.1)",
  },

  // Row 1: Controls
  controlsRow: {
    display: "flex",
    alignItems: "center",
    gap: 16,
    padding: "10px 16px",
    borderBottom: "1px solid rgba(255, 255, 255, 0.08)",
  },

  controlsSection: {
    display: "flex",
    alignItems: "center",
    gap: 6,
    flexShrink: 0,
  },

  controlButton: {
    width: 34,
    height: 34,
    display: "flex",
    alignItems: "center",
    justifyContent: "center",
    background: "rgba(255, 255, 255, 0.1)",
    border: "1px solid rgba(255, 255, 255, 0.15)",
    borderRadius: 8,
    color: "white",
    cursor: "pointer",
    transition: "all 0.15s ease",
    pointerEvents: "auto",
    outline: "none",
    WebkitTapHighlightColor: "transparent",
    userSelect: "none",
  },

  playButton: {
    width: 44,
    height: 44,
    display: "flex",
    alignItems: "center",
    justifyContent: "center",
    background: "linear-gradient(135deg, #3B82F6 0%, #2563EB 100%)",
    border: "none",
    borderRadius: "50%",
    color: "white",
    cursor: "pointer",
    boxShadow: "0 4px 12px rgba(59, 130, 246, 0.4)",
    transition: "all 0.15s ease",
    pointerEvents: "auto",
    outline: "none",
    WebkitTapHighlightColor: "transparent",
    userSelect: "none",
  },

  // Slider Section
  sliderSection: {
    flex: 1,
    display: "flex",
    alignItems: "center",
    gap: 12,
    minWidth: 0,
  },

  sliderWrapper: {
    flex: 1,
    position: "relative",
    height: 24,
    display: "flex",
    alignItems: "center",
    userSelect: "none",
    WebkitTapHighlightColor: "transparent",
  },

  sliderTrack: {
    position: "absolute",
    left: 0,
    right: 0,
    height: 6,
    background: "rgba(255, 255, 255, 0.15)",
    borderRadius: 3,
    overflow: "hidden",
  },

  sliderFill: {
    height: "100%",
    background: "linear-gradient(90deg, #3B82F6 0%, #8B5CF6 100%)",
    borderRadius: 3,
    transition: "width 0.08s ease-out",
  },

  sliderThumb: {
    position: "absolute",
    top: "50%",
    width: 16,
    height: 16,
    borderRadius: "50%",
    background: "linear-gradient(135deg, #fff 0%, #e5e7eb 100%)",
    boxShadow: "0 2px 8px rgba(0, 0, 0, 0.3), 0 0 0 2px rgba(255, 255, 255, 0.1)",
    transform: "translate(-50%, -50%)",
    transition: "transform 0.1s ease, box-shadow 0.1s ease, left 0.08s ease-out",
    zIndex: 10,
    pointerEvents: "none",
  },

  previewTooltip: {
    position: "absolute",
    bottom: 30,
    padding: "8px 12px",
    background: "rgba(0, 0, 0, 0.95)",
    border: "1px solid rgba(255, 255, 255, 0.2)",
    borderRadius: 8,
    boxShadow: "0 4px 16px rgba(0, 0, 0, 0.4)",
    zIndex: 100,
    minWidth: 120,
    pointerEvents: "none",
  },

  // Danmaku Form
  danmakuForm: {
    display: "flex",
    alignItems: "center",
    gap: 6,
    flexShrink: 0,
  },

  danmakuInput: {
    width: 160,
    padding: "7px 12px",
    background: "rgba(255, 255, 255, 0.1)",
    border: "1px solid rgba(255, 255, 255, 0.15)",
    borderRadius: 8,
    color: "white",
    fontSize: 13,
    outline: "none",
    transition: "all 0.2s ease",
    WebkitTapHighlightColor: "transparent",
  },

  sendButton: {
    width: 34,
    height: 34,
    display: "flex",
    alignItems: "center",
    justifyContent: "center",
    background: "linear-gradient(135deg, #8B5CF6 0%, #7C3AED 100%)",
    border: "none",
    borderRadius: 8,
    color: "white",
    cursor: "pointer",
    transition: "all 0.15s ease",
    pointerEvents: "auto",
    outline: "none",
    WebkitTapHighlightColor: "transparent",
    userSelect: "none",
  },

  // Row 2: Event Ticker
  tickerRow: {
    display: "flex",
    alignItems: "center",
    gap: 12,
    padding: "8px 16px",
    minHeight: 40,
  },

  tickerLabel: {
    display: "flex",
    alignItems: "center",
    gap: 6,
    padding: "4px 10px",
    background: "linear-gradient(90deg, #DC2626 0%, #B91C1C 100%)",
    borderRadius: 4,
    fontSize: 10,
    fontWeight: 700,
    color: "white",
    letterSpacing: "0.08em",
    flexShrink: 0,
  },

  tickerLabelDot: {
    width: 5,
    height: 5,
    borderRadius: "50%",
    background: "white",
    animation: "pulse 1.5s ease-in-out infinite",
  },

  tickerLabelCounter: {
    fontSize: 9,
    fontWeight: 500,
    opacity: 0.9,
    marginLeft: 2,
    fontFamily: "'JetBrains Mono', monospace",
  },

  tickerContainer: {
    flex: 1,
    height: 32,
    display: "flex",
    alignItems: "center",
    overflow: "hidden",
    background: "rgba(0, 0, 0, 0.3)",
    borderRadius: 6,
    padding: "0 12px",
    border: "1px solid rgba(255, 255, 255, 0.08)",
  },

  tickerContent: {
    width: "100%",
    display: "flex",
    alignItems: "center",
  },

  tickerEmpty: {
    fontSize: 13,
    color: "rgba(255, 255, 255, 0.4)",
    fontStyle: "italic",
  },

  // Event Message
  eventMessage: {
    display: "flex",
    alignItems: "center",
    gap: 10,
    width: "100%",
    overflow: "hidden",
  },

  eventBadge: {
    display: "inline-flex",
    alignItems: "center",
    gap: 4,
    padding: "2px 8px",
    borderRadius: 4,
    fontSize: 10,
    fontWeight: 700,
    letterSpacing: "0.03em",
    color: "white",
    flexShrink: 0,
    textTransform: "uppercase",
  },

  messageRow: {
    display: "flex",
    alignItems: "center",
    gap: 8,
    overflow: "hidden",
    flex: 1,
  },

  playerName: {
    color: "white",
    fontWeight: 600,
    fontSize: 13,
    flexShrink: 0,
  },

  teamBadge: {
    padding: "2px 6px",
    borderRadius: 4,
    fontSize: 10,
    fontWeight: 700,
    flexShrink: 0,
  },

  messageDesc: {
    color: "rgba(255, 255, 255, 0.8)",
    fontSize: 13,
    overflow: "hidden",
    textOverflow: "ellipsis",
    whiteSpace: "nowrap",
  },

  scoreInfo: {
    color: "rgba(255, 255, 255, 0.6)",
    fontSize: 12,
    fontFamily: "'JetBrains Mono', monospace",
    flexShrink: 0,
  },

  timeInfo: {
    color: "rgba(255, 255, 255, 0.5)",
    fontSize: 11,
    fontFamily: "'JetBrains Mono', monospace",
    flexShrink: 0,
  },

  scoreSeparator: {
    color: "rgba(255, 255, 255, 0.3)",
    margin: "0 2px",
  },

  statusText: {
    color: "rgba(255, 255, 255, 0.6)",
    fontSize: 12,
  },

  subText: {
    color: "rgba(255, 255, 255, 0.6)",
    fontSize: 12,
    fontStyle: "italic",
  },

  finalScore: {
    color: "white",
    fontWeight: 700,
    fontSize: 15,
  },

  winnerText: {
    color: "#22C55E",
    fontWeight: 600,
    fontSize: 13,
  },
};
