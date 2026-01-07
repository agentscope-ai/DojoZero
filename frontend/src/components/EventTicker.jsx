import { useState, useEffect, useMemo } from "react";
import { motion, AnimatePresence } from "framer-motion";
import { eventTypes } from "../constants";

/**
 * Advanced Event Ticker with vertical flip animation and detailed message rendering
 */
export default function EventTicker({ events, homeTeam, awayTeam, currentEventIndex }) {
  const [currentDisplayIndex, setCurrentDisplayIndex] = useState(0);
  const [direction, setDirection] = useState(0); // 1 for next, -1 for previous

  // Get the most recent events (last 20)
  // Events are already normalized by useTrialStream
  const recentEvents = useMemo(() => {
    if (!events || events.length === 0) return [];
    const count = Math.min(20, events.length);
    return events.slice(-count);
  }, [events]);

  // Auto-rotate through events
  useEffect(() => {
    if (recentEvents.length === 0) return;
    
    const interval = setInterval(() => {
      setDirection(1);
      setCurrentDisplayIndex((prev) => (prev + 1) % recentEvents.length);
    }, 5000); // Change every 5 seconds

    return () => clearInterval(interval);
  }, [recentEvents.length]);

  // When new events come in, jump to the latest
  useEffect(() => {
    if (recentEvents.length > 0) {
      setDirection(1);
      setCurrentDisplayIndex(recentEvents.length - 1);
    }
  }, [currentEventIndex]);

  if (recentEvents.length === 0) {
    return (
      <div style={styles.ticker}>
        <div style={styles.label}>
          <span style={styles.liveDot} />
          <span className="font-tech">LIVE</span>
        </div>
        <div style={styles.content}>
          <span className="font-tech" style={styles.noData}>Waiting for events...</span>
        </div>
      </div>
    );
  }

  const currentEvent = recentEvents[currentDisplayIndex];

  return (
    <div style={styles.ticker}>
      {/* Live Label */}
      <div style={styles.label}>
        <span style={styles.liveDot} />
        <span className="font-tech">LIVE</span>
      </div>

      {/* Event Display with Vertical Flip Animation */}
      <div style={styles.content}>
        <AnimatePresence mode="wait" custom={direction}>
          <motion.div
            key={currentDisplayIndex}
            custom={direction}
            initial={(direction) => ({
              y: direction > 0 ? 40 : -40,
              opacity: 0,
            })}
            animate={{
              y: 0,
              opacity: 1,
            }}
            exit={(direction) => ({
              y: direction > 0 ? -40 : 40,
              opacity: 0,
            })}
            transition={{
              duration: 0.4,
              ease: "easeInOut",
            }}
            style={styles.eventContainer}
          >
            <EventMessage 
              event={currentEvent} 
              homeTeam={homeTeam} 
              awayTeam={awayTeam}
            />
          </motion.div>
        </AnimatePresence>
      </div>

      {/* Event Counter */}
      <div style={styles.counter}>
        <span className="font-tech" style={styles.counterText}>
          {currentDisplayIndex + 1}/{recentEvents.length}
        </span>
      </div>
    </div>
  );
}

/**
 * Render individual event message with rich details
 */
function EventMessage({ event, homeTeam, awayTeam }) {
  const eventType = event.event_type || "game_update";
  const config = eventTypes[eventType] || eventTypes.game_update;

  let content = null;

  switch (eventType) {
    case "game_update": {
      const home = event.home_team || {};
      const away = event.away_team || {};
      const leaders = event.game_leaders || { home: {}, away: {} };
      
      content = (
        <div style={styles.messageContent}>
          <span style={{ ...styles.eventBadge, background: config.color }}>
            {config.icon} SCORE
          </span>
          <div style={styles.scoreDisplay}>
            <span style={{ color: homeTeam?.color || '#3B82F6' }}>
              {home.teamTricode || 'HOME'} {home.score || 0}
            </span>
            <span style={styles.scoreSeparator}>-</span>
            <span style={{ color: awayTeam?.color || '#EF4444' }}>
              {away.score || 0} {away.teamTricode || 'AWAY'}
            </span>
          </div>
          {event.game_status_text && (
            <span style={styles.statusText}>• {event.game_status_text}</span>
          )}
          {leaders.home?.points?.playerName && (
            <span style={styles.leaderText}>
              • {leaders.home.points.playerName}: {leaders.home.points.value}pts
            </span>
          )}
        </div>
      );
      break;
    }

    case "odds_update": {
      const homeProb = ((event.home_probability || 0) * 100).toFixed(1);
      const awayProb = ((event.away_probability || 0) * 100).toFixed(1);
      const homeOdds = event.home_odds || 0;
      const awayOdds = event.away_odds || 0;

      content = (
        <div style={styles.messageContent}>
          <span style={{ ...styles.eventBadge, background: config.color }}>
            {config.icon} ODDS
          </span>
          <div style={styles.oddsDisplay}>
            <span style={{ color: homeTeam?.color || '#3B82F6' }}>
              {homeTeam?.name || 'Home'}: {homeProb}% ({homeOdds > 0 ? '+' : ''}{homeOdds})
            </span>
            <span style={styles.oddsSeparator}>|</span>
            <span style={{ color: awayTeam?.color || '#EF4444' }}>
              {awayTeam?.name || 'Away'}: {awayProb}% ({awayOdds > 0 ? '+' : ''}{awayOdds})
            </span>
          </div>
        </div>
      );
      break;
    }

    case "in_game_critical": {
      const player = event.player_name || 'Player';
      const team = event.team_tricode || '';
      const desc = event.description || '';
      const actionType = event.action_type || '';
      const clock = event.clock || '';
      const period = event.period || 1;

      content = (
        <div style={styles.messageContent}>
          <span style={{ ...styles.eventBadge, background: config.color }}>
            {config.icon} {event.critical_type?.toUpperCase() || 'ACTION'}
          </span>
          <div style={styles.criticalDisplay}>
            <span style={styles.playerName}>{player}</span>
            {team && <span style={styles.teamBadge}>{team}</span>}
            <span style={styles.criticalDesc}>{desc}</span>
            <span style={styles.timeInfo}>Q{period} {clock}</span>
          </div>
        </div>
      );
      break;
    }

    case "expert_prediction": {
      const predictions = event.predictions || [];
      const predCount = predictions.length;
      const firstPred = predictions[0];

      content = (
        <div style={styles.messageContent}>
          <span style={{ ...styles.eventBadge, background: config.color }}>
            {config.icon} EXPERT
          </span>
          <div style={styles.expertDisplay}>
            <span style={styles.expertText}>
              {predCount} New Prediction{predCount > 1 ? 's' : ''}
            </span>
            {firstPred && (
              <>
                <span style={styles.expertName}>• {firstPred.expert}</span>
                <span style={styles.expertPrediction}>
                  {firstPred.prediction?.substring(0, 60)}
                  {firstPred.prediction?.length > 60 ? '...' : ''}
                </span>
              </>
            )}
          </div>
        </div>
      );
      break;
    }

    case "injury_summary": {
      const injured = event.injured_players || [];
      const summary = event.summary || '';

      content = (
        <div style={styles.messageContent}>
          <span style={{ ...styles.eventBadge, background: config.color }}>
            {config.icon} INJURY
          </span>
          <div style={styles.injuryDisplay}>
            <span style={styles.injuryText}>
              {injured.length} Player{injured.length > 1 ? 's' : ''} Injured
            </span>
            {summary && (
              <span style={styles.injurySummary}>
                • {summary.substring(0, 80)}
                {summary.length > 80 ? '...' : ''}
              </span>
            )}
          </div>
        </div>
      );
      break;
    }

    case "power_ranking": {
      const rankings = event.rankings || [];
      const query = event.query || '';

      content = (
        <div style={styles.messageContent}>
          <span style={{ ...styles.eventBadge, background: config.color }}>
            {config.icon} RANKINGS
          </span>
          <div style={styles.rankingDisplay}>
            <span style={styles.rankingText}>Power Rankings Updated</span>
            {query && (
              <span style={styles.rankingQuery}>• {query.substring(0, 60)}</span>
            )}
          </div>
        </div>
      );
      break;
    }

    case "game_start": {
      content = (
        <div style={styles.messageContent}>
          <span style={{ ...styles.eventBadge, background: config.color }}>
            {config.icon} TIP-OFF
          </span>
          <div style={styles.gameStartDisplay}>
            <span style={styles.gameStartText}>
              🏀 {homeTeam?.name || 'Home'} vs {awayTeam?.name || 'Away'}
            </span>
            <span style={styles.gameStartSubtext}>Game is underway!</span>
          </div>
        </div>
      );
      break;
    }

    case "game_result": {
      const finalScore = event.final_score || { home: 0, away: 0 };
      const winner = event.winner;
      const winnerName = winner === 'home' ? homeTeam?.name : awayTeam?.name;

      content = (
        <div style={styles.messageContent}>
          <span style={{ ...styles.eventBadge, background: config.color }}>
            {config.icon} FINAL
          </span>
          <div style={styles.resultDisplay}>
            <span style={styles.finalScore}>
              {finalScore.home} - {finalScore.away}
            </span>
            {winnerName && (
              <span style={styles.winnerText}>🏆 {winnerName} wins!</span>
            )}
          </div>
        </div>
      );
      break;
    }

    case "raw_web_search": {
      const query = event.query || '';
      const results = event.results || [];

      content = (
        <div style={styles.messageContent}>
          <span style={{ ...styles.eventBadge, background: config.color }}>
            {config.icon} SEARCH
          </span>
          <div style={styles.searchDisplay}>
            <span style={styles.searchQuery}>"{query}"</span>
            <span style={styles.searchResults}>
              • {results.length} result{results.length > 1 ? 's' : ''} found
            </span>
          </div>
        </div>
      );
      break;
    }

    case "play_by_play": {
      const player = event.player_name || '';
      const team = event.team_tricode || '';
      const desc = event.description || '';
      const actionType = event.action_type || '';
      const clock = event.clock || '';
      const period = event.period || 1;
      const homeScore = event.home_score || 0;
      const awayScore = event.away_score || 0;

      // Determine badge color based on action type
      let badgeColor = "#6B7280"; // default gray
      if (["2pt", "3pt", "freethrow"].includes(actionType)) {
        badgeColor = "#10B981"; // green for scoring
      } else if (actionType === "foul") {
        badgeColor = "#F59E0B"; // yellow for fouls
      } else if (actionType === "turnover") {
        badgeColor = "#EF4444"; // red for turnovers
      } else if (actionType === "block" || actionType === "steal") {
        badgeColor = "#3B82F6"; // blue for defensive plays
      }

      content = (
        <div style={styles.messageContent}>
          <span style={{ ...styles.eventBadge, background: badgeColor }}>
            🏀 {actionType.toUpperCase() || 'PLAY'}
          </span>
          <div style={styles.playByPlayDisplay}>
            {player && <span style={styles.playerName}>{player}</span>}
            {team && <span style={styles.teamBadge}>{team}</span>}
            <span style={styles.playDesc}>{desc}</span>
          </div>
          <span style={styles.gameInfo}>
            {homeScore}-{awayScore} • Q{period} {clock}
          </span>
        </div>
      );
      break;
    }

    case "game_initialize": {
      const home = event.home_team || 'Home';
      const away = event.away_team || 'Away';

      content = (
        <div style={styles.messageContent}>
          <span style={{ ...styles.eventBadge, background: "#6B7280" }}>
            📋 SETUP
          </span>
          <div style={styles.gameStartDisplay}>
            <span style={styles.gameStartText}>
              Game: {home} vs {away}
            </span>
            <span style={styles.gameStartSubtext}>Initializing...</span>
          </div>
        </div>
      );
      break;
    }

    default: {
      content = (
        <div style={styles.messageContent}>
          <span style={{ ...styles.eventBadge, background: config.color || "#6B7280" }}>
            {config.icon || "📌"} {config.label || eventType}
          </span>
          <span style={styles.defaultText}>Event occurred</span>
        </div>
      );
    }
  }

  return content;
}

const styles = {
  ticker: {
    position: "absolute",
    bottom: 0,
    left: 0,
    right: 0,
    height: "50px",
    display: "flex",
    alignItems: "center",
    background: "linear-gradient(90deg, rgba(0, 0, 0, 0.95) 0%, rgba(0, 0, 0, 0.85) 100%)",
    borderTop: "1px solid rgba(59, 130, 246, 0.3)",
    borderBottomLeftRadius: "16px",
    borderBottomRightRadius: "16px",
    zIndex: 30,
    overflow: "hidden",
  },
  label: {
    display: "flex",
    alignItems: "center",
    gap: "6px",
    padding: "0 16px",
    height: "100%",
    background: "linear-gradient(90deg, #DC2626 0%, #B91C1C 100%)",
    color: "#fff",
    fontSize: "11px",
    fontWeight: "600",
    letterSpacing: "0.1em",
    flexShrink: 0,
  },
  liveDot: {
    width: "6px",
    height: "6px",
    borderRadius: "50%",
    background: "#fff",
    animation: "pulse-glow 1.5s ease-in-out infinite",
  },
  content: {
    flex: 1,
    height: "100%",
    position: "relative",
    overflow: "hidden",
    display: "flex",
    alignItems: "center",
    padding: "0 16px",
  },
  eventContainer: {
    width: "100%",
    display: "flex",
    alignItems: "center",
  },
  messageContent: {
    display: "flex",
    alignItems: "center",
    gap: "12px",
    width: "100%",
    overflow: "hidden",
  },
  eventBadge: {
    display: "inline-flex",
    alignItems: "center",
    gap: "4px",
    padding: "4px 10px",
    borderRadius: "4px",
    fontSize: "10px",
    fontWeight: "700",
    letterSpacing: "0.05em",
    color: "#fff",
    flexShrink: 0,
    textTransform: "uppercase",
  },
  scoreDisplay: {
    display: "flex",
    alignItems: "center",
    gap: "8px",
    fontSize: "14px",
    fontWeight: "600",
    letterSpacing: "0.02em",
  },
  scoreSeparator: {
    color: "rgba(255, 255, 255, 0.3)",
  },
  statusText: {
    color: "rgba(255, 255, 255, 0.6)",
    fontSize: "12px",
  },
  leaderText: {
    color: "rgba(255, 255, 255, 0.7)",
    fontSize: "12px",
    fontStyle: "italic",
  },
  oddsDisplay: {
    display: "flex",
    alignItems: "center",
    gap: "12px",
    fontSize: "13px",
    fontWeight: "500",
  },
  oddsSeparator: {
    color: "rgba(255, 255, 255, 0.3)",
  },
  criticalDisplay: {
    display: "flex",
    alignItems: "center",
    gap: "8px",
    fontSize: "13px",
    overflow: "hidden",
  },
  playerName: {
    color: "#fff",
    fontWeight: "600",
    flexShrink: 0,
  },
  teamBadge: {
    padding: "2px 6px",
    background: "rgba(59, 130, 246, 0.2)",
    borderRadius: "3px",
    fontSize: "10px",
    color: "#3B82F6",
    fontWeight: "600",
    flexShrink: 0,
  },
  criticalDesc: {
    color: "rgba(255, 255, 255, 0.8)",
    overflow: "hidden",
    textOverflow: "ellipsis",
    whiteSpace: "nowrap",
  },
  timeInfo: {
    color: "rgba(255, 255, 255, 0.5)",
    fontSize: "11px",
    flexShrink: 0,
  },
  expertDisplay: {
    display: "flex",
    alignItems: "center",
    gap: "8px",
    fontSize: "13px",
    overflow: "hidden",
  },
  expertText: {
    color: "#fff",
    fontWeight: "600",
    flexShrink: 0,
  },
  expertName: {
    color: "rgba(255, 255, 255, 0.7)",
    flexShrink: 0,
  },
  expertPrediction: {
    color: "rgba(255, 255, 255, 0.6)",
    overflow: "hidden",
    textOverflow: "ellipsis",
    whiteSpace: "nowrap",
  },
  injuryDisplay: {
    display: "flex",
    alignItems: "center",
    gap: "8px",
    fontSize: "13px",
    overflow: "hidden",
  },
  injuryText: {
    color: "#fff",
    fontWeight: "600",
    flexShrink: 0,
  },
  injurySummary: {
    color: "rgba(255, 255, 255, 0.7)",
    overflow: "hidden",
    textOverflow: "ellipsis",
    whiteSpace: "nowrap",
  },
  rankingDisplay: {
    display: "flex",
    alignItems: "center",
    gap: "8px",
    fontSize: "13px",
  },
  rankingText: {
    color: "#fff",
    fontWeight: "600",
  },
  rankingQuery: {
    color: "rgba(255, 255, 255, 0.7)",
  },
  gameStartDisplay: {
    display: "flex",
    alignItems: "center",
    gap: "8px",
    fontSize: "13px",
  },
  gameStartText: {
    color: "#fff",
    fontWeight: "600",
  },
  gameStartSubtext: {
    color: "rgba(255, 255, 255, 0.7)",
  },
  resultDisplay: {
    display: "flex",
    alignItems: "center",
    gap: "12px",
    fontSize: "13px",
  },
  finalScore: {
    color: "#fff",
    fontWeight: "700",
    fontSize: "16px",
  },
  winnerText: {
    color: "#10B981",
    fontWeight: "600",
  },
  searchDisplay: {
    display: "flex",
    alignItems: "center",
    gap: "8px",
    fontSize: "13px",
    overflow: "hidden",
  },
  searchQuery: {
    color: "#fff",
    fontWeight: "500",
    fontStyle: "italic",
    overflow: "hidden",
    textOverflow: "ellipsis",
    whiteSpace: "nowrap",
  },
  searchResults: {
    color: "rgba(255, 255, 255, 0.7)",
    flexShrink: 0,
  },
  defaultText: {
    color: "rgba(255, 255, 255, 0.7)",
    fontSize: "13px",
  },
  noData: {
    color: "rgba(255, 255, 255, 0.5)",
    fontSize: "12px",
  },
  playByPlayDisplay: {
    display: "flex",
    alignItems: "center",
    gap: "8px",
    fontSize: "13px",
    overflow: "hidden",
    flex: 1,
  },
  playDesc: {
    color: "rgba(255, 255, 255, 0.8)",
    overflow: "hidden",
    textOverflow: "ellipsis",
    whiteSpace: "nowrap",
  },
  gameInfo: {
    color: "rgba(255, 255, 255, 0.5)",
    fontSize: "11px",
    flexShrink: 0,
    marginLeft: "auto",
  },
  counter: {
    padding: "0 16px",
    height: "100%",
    display: "flex",
    alignItems: "center",
    borderLeft: "1px solid rgba(255, 255, 255, 0.1)",
    flexShrink: 0,
  },
  counterText: {
    fontSize: "11px",
    color: "rgba(255, 255, 255, 0.6)",
    letterSpacing: "0.05em",
  },
};

