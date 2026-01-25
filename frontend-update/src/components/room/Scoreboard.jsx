/**
 * Scoreboard - Game scoreboard with collapsible player stats
 * 
 * Features:
 * - Team logos and scores with animation
 * - Game clock and period
 * - Expandable player statistics table
 * - Team totals bar
 */

import { useState, useMemo } from "react";
import { motion, AnimatePresence } from "framer-motion";
import { NBA_CDN } from "../../data/nba/teams";

// =============================================================================
// TEAM LOGO COMPONENT
// =============================================================================

function TeamLogo({ team, size = 48 }) {
  const [imageError, setImageError] = useState(false);

  if (imageError || !team?.logo) {
    return (
      <div
        style={{
          width: size,
          height: size,
          display: "flex",
          alignItems: "center",
          justifyContent: "center",
          borderRadius: 10,
          background: `linear-gradient(135deg, ${team?.color || "#666"}90 0%, ${team?.color || "#666"}50 100%)`,
          boxShadow: "0 4px 16px rgba(0, 0, 0, 0.3)",
          flexShrink: 0,
        }}
      >
        <span style={{
          fontSize: size * 0.35,
          color: "#fff",
          fontWeight: 700,
          textShadow: "0 2px 4px rgba(0,0,0,0.3)",
        }}>
          {team?.tricode || "?"}
        </span>
      </div>
    );
  }

  return (
    <div
      style={{
        width: size,
        height: size,
        display: "flex",
        alignItems: "center",
        justifyContent: "center",
        borderRadius: 10,
        background: `linear-gradient(135deg, ${team.color}40 0%, ${team.color}20 100%)`,
        boxShadow: "0 4px 16px rgba(0, 0, 0, 0.3)",
        padding: 6,
        flexShrink: 0,
      }}
    >
      <img
        src={team.logo}
        alt={team.name}
        style={{
          width: "100%",
          height: "100%",
          objectFit: "contain",
        }}
        onError={() => setImageError(true)}
      />
    </div>
  );
}

// =============================================================================
// PLAYER HEADSHOT COMPONENT
// =============================================================================

function PlayerHeadshot({ playerId, name, size = 36 }) {
  const [imageError, setImageError] = useState(false);

  if (imageError || !playerId) {
    return (
      <div
        style={{
          width: size,
          height: size,
          display: "flex",
          alignItems: "center",
          justifyContent: "center",
          borderRadius: "50%",
          background: "linear-gradient(135deg, #374151 0%, #1F2937 100%)",
          border: "2px solid rgba(255,255,255,0.1)",
          flexShrink: 0,
        }}
      >
        <span style={{
          fontSize: size * 0.4,
          color: "#9CA3AF",
          fontWeight: 600,
        }}>
          {name?.charAt(0) || "?"}
        </span>
      </div>
    );
  }

  return (
    <div
      style={{
        width: size,
        height: size,
        borderRadius: "50%",
        overflow: "hidden",
        background: "linear-gradient(135deg, #374151 0%, #1F2937 100%)",
        border: "2px solid rgba(255,255,255,0.15)",
        flexShrink: 0,
      }}
    >
      <img
        src={NBA_CDN.playerHeadshot(playerId)}
        alt={name}
        style={{
          width: "100%",
          height: "100%",
          objectFit: "cover",
          objectPosition: "top center",
        }}
        onError={() => setImageError(true)}
      />
    </div>
  );
}

// =============================================================================
// MAIN SCOREBOARD COMPONENT
// =============================================================================

export default function Scoreboard({
  homeTeam,
  awayTeam,
  events = [],
  currentIndex = 0,
}) {
  const [isExpanded, setIsExpanded] = useState(false);
  const [activeTab, setActiveTab] = useState("home");

  // Extract latest game_update data
  const gameData = useMemo(() => {
    const gameUpdates = events
      .slice(0, currentIndex + 1)
      .filter((e) => e.event_type === "game_update");
    const latest = gameUpdates[gameUpdates.length - 1];
    if (!latest) return null;

    return {
      homeTeam: latest.home_team,
      awayTeam: latest.away_team,
      playerStats: latest.player_stats,
      period: latest.period,
      gameClock: latest.game_clock,
    };
  }, [events, currentIndex]);

  // Process player stats
  const processedPlayers = useMemo(() => {
    if (!gameData?.playerStats) return { home: [], away: [] };

    const processTeam = (players) => {
      if (!players) return [];
      return players
        .filter((p) => p.statistics?.minutes && p.statistics.minutes !== "")
        .sort((a, b) => (b.statistics?.points || 0) - (a.statistics?.points || 0));
    };

    return {
      home: processTeam(gameData.playerStats.home),
      away: processTeam(gameData.playerStats.away),
    };
  }, [gameData]);

  // Calculate team totals
  const teamTotals = useMemo(() => {
    if (!gameData?.playerStats) return { home: null, away: null };

    const calculateTotals = (players) => {
      if (!players) return null;
      const activePlayers = players.filter((p) => p.statistics?.minutes);
      const fgMade = activePlayers.reduce((sum, p) => sum + (p.statistics?.fieldGoalsMade || 0), 0);
      const fgAttempted = activePlayers.reduce((sum, p) => sum + (p.statistics?.fieldGoalsAttempted || 0), 0);
      const threeMade = activePlayers.reduce((sum, p) => sum + (p.statistics?.threePointersMade || 0), 0);
      const threeAttempted = activePlayers.reduce((sum, p) => sum + (p.statistics?.threePointersAttempted || 0), 0);

      return {
        rebounds: activePlayers.reduce((sum, p) => sum + (p.statistics?.reboundsTotal || 0), 0),
        assists: activePlayers.reduce((sum, p) => sum + (p.statistics?.assists || 0), 0),
        steals: activePlayers.reduce((sum, p) => sum + (p.statistics?.steals || 0), 0),
        blocks: activePlayers.reduce((sum, p) => sum + (p.statistics?.blocks || 0), 0),
        fgPct: fgAttempted > 0 ? ((fgMade / fgAttempted) * 100).toFixed(1) : "0.0",
        threePct: threeAttempted > 0 ? ((threeMade / threeAttempted) * 100).toFixed(1) : "0.0",
      };
    };

    return {
      home: calculateTotals(gameData.playerStats.home),
      away: calculateTotals(gameData.playerStats.away),
    };
  }, [gameData]);

  // Current tab data
  const currentPlayers = activeTab === "home" ? processedPlayers.home : processedPlayers.away;
  const currentTeam = activeTab === "home" ? homeTeam : awayTeam;
  const currentTotals = activeTab === "home" ? teamTotals.home : teamTotals.away;

  // Scoreboard data
  const homeScore = gameData?.homeTeam?.score ?? 0;
  const awayScore = gameData?.awayTeam?.score ?? 0;
  const period = gameData?.period ?? 1;
  const clock = gameData?.gameClock ?? "12:00";

  return (
    <>
      {/* Hide scrollbar CSS */}
      <style>
        {`
          .scoreboard-table-container::-webkit-scrollbar {
            display: none;
          }
        `}
      </style>
      
      <div style={styles.container}>
        {/* Main Scoreboard */}
        <div style={styles.scoreboard}>
        {/* Home Team */}
        <div style={styles.teamSection}>
          <TeamLogo team={homeTeam} size={44} />
          <div style={styles.teamInfo}>
            <span style={styles.teamTricode}>{homeTeam?.tricode}</span>
            <span style={styles.teamName}>{homeTeam?.name}</span>
          </div>
          <motion.span
            key={`home-${homeScore}`}
            style={styles.score}
            initial={{ scale: 1.3, opacity: 0 }}
            animate={{ scale: 1, opacity: 1 }}
            transition={{ duration: 0.3 }}
          >
            {homeScore}
          </motion.span>
        </div>

        {/* Center Clock */}
        <div style={styles.clockSection}>
          <span style={styles.period}>Q{period}</span>
          <span style={styles.clock}>{clock}</span>
        </div>

        {/* Away Team */}
        <div style={{ ...styles.teamSection, flexDirection: "row-reverse" }}>
          <TeamLogo team={awayTeam} size={44} />
          <div style={{ ...styles.teamInfo, alignItems: "flex-end" }}>
            <span style={styles.teamTricode}>{awayTeam?.tricode}</span>
            <span style={styles.teamName}>{awayTeam?.name}</span>
          </div>
          <motion.span
            key={`away-${awayScore}`}
            style={styles.score}
            initial={{ scale: 1.3, opacity: 0 }}
            animate={{ scale: 1, opacity: 1 }}
            transition={{ duration: 0.3 }}
          >
            {awayScore}
          </motion.span>
        </div>
      </div>

      {/* Expand Toggle */}
      <motion.button
        onClick={() => setIsExpanded(!isExpanded)}
        style={styles.expandToggle}
        whileHover={{ background: "rgba(255,255,255,0.08)" }}
        whileTap={{ scale: 0.98 }}
      >
        <motion.svg
          width="12"
          height="12"
          viewBox="0 0 24 24"
          fill="none"
          stroke="currentColor"
          strokeWidth="2"
          animate={{ rotate: isExpanded ? 180 : 0 }}
          transition={{ duration: 0.2 }}
        >
          <path d="M6 9l6 6 6-6" />
        </motion.svg>
        <span style={styles.expandText}>PLAYER STATS</span>
      </motion.button>

      {/* Expanded Stats Panel */}
      <AnimatePresence>
        {isExpanded && gameData && (
          <motion.div
            style={styles.statsPanel}
            initial={{ height: 0, opacity: 0 }}
            animate={{ height: "auto", opacity: 1 }}
            exit={{ height: 0, opacity: 0 }}
            transition={{ duration: 0.25 }}
          >
            {/* Team Tabs */}
            <div style={styles.tabContainer}>
              <button
                onClick={() => setActiveTab("home")}
                style={{
                  ...styles.tab,
                  borderBottom: activeTab === "home" ? `2px solid ${homeTeam?.color}` : "2px solid transparent",
                  background: activeTab === "home" ? `${homeTeam?.color}15` : "transparent",
                }}
              >
                <span style={{ ...styles.tabIndicator, background: homeTeam?.color }} />
                <span style={styles.tabName}>{homeTeam?.name}</span>
              </button>

              <button
                onClick={() => setActiveTab("away")}
                style={{
                  ...styles.tab,
                  borderBottom: activeTab === "away" ? `2px solid ${awayTeam?.color}` : "2px solid transparent",
                  background: activeTab === "away" ? `${awayTeam?.color}15` : "transparent",
                }}
              >
                <span style={{ ...styles.tabIndicator, background: awayTeam?.color }} />
                <span style={styles.tabName}>{awayTeam?.name}</span>
              </button>
            </div>

            {/* Team Totals Bar */}
            {currentTotals && (
              <div style={styles.totalsBar}>
                {[
                  { label: "FG%", value: `${currentTotals.fgPct}%` },
                  { label: "3P%", value: `${currentTotals.threePct}%` },
                  { label: "REB", value: currentTotals.rebounds },
                  { label: "AST", value: currentTotals.assists },
                  { label: "STL", value: currentTotals.steals },
                  { label: "BLK", value: currentTotals.blocks },
                ].map((stat) => (
                  <div key={stat.label} style={styles.totalItem}>
                    <span style={styles.totalLabel}>{stat.label}</span>
                    <span style={styles.totalValue}>{stat.value}</span>
                  </div>
                ))}
              </div>
            )}

            {/* Player Stats Table */}
            <div className="scoreboard-table-container" style={styles.tableContainer}>
              {/* Header */}
              <div style={styles.tableHeader}>
                <span style={{ ...styles.headerCell, flex: 2, textAlign: "left" }}>PLAYER</span>
                <span style={styles.headerCell}>MIN</span>
                <span style={styles.headerCell}>PTS</span>
                <span style={styles.headerCell}>REB</span>
                <span style={styles.headerCell}>AST</span>
                <span style={styles.headerCell}>FG%</span>
                <span style={styles.headerCell}>+/-</span>
              </div>

              {/* Body */}
              <div style={styles.tableBody}>
                {currentPlayers.slice(0, 8).map((player, index) => {
                  const stats = player.statistics;
                  const plusMinus = stats?.plusMinusPoints || 0;
                  const isTopScorer = index === 0;

                  return (
                    <motion.div
                      key={player.personId}
                      style={{
                        ...styles.playerRow,
                        background: isTopScorer
                          ? `linear-gradient(90deg, ${currentTeam?.color}15 0%, transparent 100%)`
                          : "transparent",
                      }}
                      initial={{ opacity: 0, x: -10 }}
                      animate={{ opacity: 1, x: 0 }}
                      transition={{ delay: index * 0.03 }}
                    >
                      <div style={{ ...styles.playerCell, flex: 2 }}>
                        <PlayerHeadshot
                          playerId={player.personId}
                          name={player.familyName}
                          size={32}
                        />
                        <div style={styles.playerInfo}>
                          <span style={styles.playerName}>
                            {player.nameI || `${player.firstName?.charAt(0)}. ${player.familyName}`}
                            {isTopScorer && <span style={styles.topBadge}>TOP</span>}
                          </span>
                          <span style={styles.playerPosition}>{player.position || "-"}</span>
                        </div>
                      </div>
                      <span style={styles.statCell}>{stats?.minutes || "-"}</span>
                      <span style={{ ...styles.statCell, fontWeight: 600, color: "#fff" }}>
                        {stats?.points ?? 0}
                      </span>
                      <span style={styles.statCell}>{stats?.reboundsTotal ?? 0}</span>
                      <span style={styles.statCell}>{stats?.assists ?? 0}</span>
                      <span style={styles.statCell}>
                        {stats?.fieldGoalsPercentage
                          ? (stats.fieldGoalsPercentage * 100).toFixed(0)
                          : 0}%
                      </span>
                      <span
                        style={{
                          ...styles.statCell,
                          color: plusMinus > 0 ? "#10B981" : plusMinus < 0 ? "#EF4444" : "#9CA3AF",
                        }}
                      >
                        {plusMinus > 0 ? "+" : ""}{plusMinus}
                      </span>
                    </motion.div>
                  );
                })}
              </div>
            </div>
          </motion.div>
        )}
      </AnimatePresence>
      </div>
    </>
  );
}

// =============================================================================
// STYLES
// =============================================================================

const styles = {
  container: {
    display: "flex",
    flexDirection: "column",
    background: "rgba(0, 0, 0, 0.85)",
    backdropFilter: "blur(20px)",
    borderRadius: 14,
    border: "1px solid rgba(255, 255, 255, 0.1)",
    overflow: "hidden",
    minWidth: 520,
  },
  scoreboard: {
    display: "flex",
    alignItems: "center",
    justifyContent: "space-between",
    padding: "16px 20px",
    gap: 16,
  },
  teamSection: {
    display: "flex",
    alignItems: "center",
    gap: 12,
    flex: 1,
  },
  teamInfo: {
    display: "flex",
    flexDirection: "column",
    gap: 1,
  },
  teamTricode: {
    fontSize: 11,
    color: "var(--text-muted)",
    letterSpacing: "0.08em",
    fontWeight: 600,
  },
  teamName: {
    fontSize: 15,
    color: "var(--text-primary)",
    fontWeight: 600,
  },
  score: {
    fontSize: 38,
    fontWeight: 700,
    color: "#fff",
    fontFamily: "'JetBrains Mono', monospace",
    minWidth: 50,
    textAlign: "center",
    textShadow: "0 2px 8px rgba(0, 0, 0, 0.5)",
  },
  clockSection: {
    display: "flex",
    flexDirection: "column",
    alignItems: "center",
    padding: "0 20px",
    borderLeft: "1px solid rgba(255, 255, 255, 0.1)",
    borderRight: "1px solid rgba(255, 255, 255, 0.1)",
  },
  period: {
    fontSize: 11,
    color: "var(--text-muted)",
    letterSpacing: "0.1em",
    fontWeight: 600,
  },
  clock: {
    fontSize: 20,
    fontWeight: 700,
    color: "#fff",
    fontFamily: "'JetBrains Mono', monospace",
    letterSpacing: "0.05em",
  },
  expandToggle: {
    display: "flex",
    alignItems: "center",
    justifyContent: "center",
    gap: 8,
    padding: "8px 16px",
    background: "rgba(255, 255, 255, 0.03)",
    border: "none",
    borderTop: "1px solid rgba(255, 255, 255, 0.05)",
    color: "#9CA3AF",
    cursor: "pointer",
    transition: "background 0.2s ease",
  },
  expandText: {
    fontSize: 10,
    letterSpacing: "0.15em",
    color: "#CBD5E1",
    fontWeight: 600,
  },
  statsPanel: {
    overflow: "hidden",
    background: "rgba(0, 0, 0, 0.2)", // 20% opacity for transparency
    backdropFilter: "blur(8px)", // Add blur for better readability
  },
  tabContainer: {
    display: "flex",
    borderBottom: "1px solid rgba(255, 255, 255, 0.05)",
  },
  tab: {
    flex: 1,
    display: "flex",
    alignItems: "center",
    justifyContent: "center",
    gap: 8,
    padding: "10px 16px",
    border: "none",
    cursor: "pointer",
    transition: "all 0.2s ease",
  },
  tabIndicator: {
    width: 3,
    height: 14,
    borderRadius: 2,
  },
  tabName: {
    fontSize: 12,
    color: "#E2E8F0",
    letterSpacing: "0.04em",
    fontWeight: 500,
  },
  totalsBar: {
    display: "flex",
    justifyContent: "space-around",
    padding: "10px 16px",
    background: "rgba(0, 0, 0, 0.2)",
    borderBottom: "1px solid rgba(255, 255, 255, 0.05)",
  },
  totalItem: {
    display: "flex",
    flexDirection: "column",
    alignItems: "center",
    gap: 2,
  },
  totalLabel: {
    fontSize: 9,
    color: "#64748B",
    letterSpacing: "0.1em",
    fontWeight: 500,
  },
  totalValue: {
    fontSize: 14,
    color: "#E2E8F0",
    fontWeight: 600,
    fontFamily: "'JetBrains Mono', monospace",
  },
  tableContainer: {
    maxHeight: 240,
    overflow: "auto",
    // Hide scrollbar while keeping scrollable
    scrollbarWidth: "none", // Firefox
    msOverflowStyle: "none", // IE/Edge
    WebkitOverflowScrolling: "touch", // Smooth scrolling on iOS
  },
  tableHeader: {
    display: "flex",
    alignItems: "center",
    padding: "8px 16px",
    background: "rgba(0, 0, 0, 0.85)", // More opaque to prevent overlap visibility
    backdropFilter: "blur(10px)", // Add blur for better separation
    borderBottom: "1px solid rgba(255, 255, 255, 0.08)",
    position: "sticky",
    top: 0,
    zIndex: 10, // Higher z-index to stay above player rows
  },
  headerCell: {
    flex: 1,
    fontSize: 9,
    color: "#64748B",
    letterSpacing: "0.1em",
    textAlign: "center",
    fontWeight: 600,
  },
  tableBody: {
    padding: "4px 0",
  },
  playerRow: {
    display: "flex",
    alignItems: "center",
    padding: "6px 16px",
    borderBottom: "1px solid rgba(255, 255, 255, 0.03)",
  },
  playerCell: {
    display: "flex",
    alignItems: "center",
    gap: 10,
  },
  playerInfo: {
    display: "flex",
    flexDirection: "column",
    gap: 1,
  },
  playerName: {
    fontSize: 12,
    color: "#E2E8F0",
    display: "flex",
    alignItems: "center",
    gap: 6,
    fontWeight: 500,
  },
  playerPosition: {
    fontSize: 9,
    color: "#64748B",
    letterSpacing: "0.05em",
  },
  topBadge: {
    fontSize: 8,
    padding: "1px 5px",
    background: "linear-gradient(135deg, #F59E0B 0%, #D97706 100%)",
    color: "#fff",
    borderRadius: 3,
    letterSpacing: "0.05em",
    fontWeight: 700,
  },
  statCell: {
    flex: 1,
    fontSize: 12,
    color: "#9CA3AF",
    textAlign: "center",
    fontFamily: "'JetBrains Mono', monospace",
  },
};
