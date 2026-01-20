import { useMemo, useState } from "react";
import { motion, AnimatePresence } from "framer-motion";
import { NBA_CDN } from "../constants";

// Team Logo component with fallback
function TeamLogo({ team, size = 54 }) {
  const [imageError, setImageError] = useState(false);
  const imgSize = size * 0.74;
  
  if (imageError || !team.logo) {
    return (
      <div
        style={{
          width: size,
          height: size,
          display: "flex",
          alignItems: "center",
          justifyContent: "center",
          borderRadius: "50%",
          background: `linear-gradient(135deg, ${team.color}88 0%, ${team.color}44 100%)`,
          boxShadow: "0 4px 20px rgba(0, 0, 0, 0.3)",
          flexShrink: 0,
        }}
      >
        <span style={{ fontSize: size * 0.44, color: "#fff", fontWeight: "bold", textShadow: "0 2px 4px rgba(0,0,0,0.3)" }}>
          {team.name?.charAt(0) || "?"}
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
        borderRadius: "50%",
        background: `linear-gradient(135deg, ${team.color}88 0%, ${team.color}44 100%)`,
        boxShadow: "0 4px 20px rgba(0, 0, 0, 0.3)",
        overflow: "hidden",
        flexShrink: 0,
      }}
    >
      <img
        src={team.logo}
        alt={team.name}
        style={{ width: imgSize, height: imgSize, objectFit: "contain" }}
        onError={() => setImageError(true)}
      />
    </div>
  );
}

// Player headshot component with fallback
function PlayerHeadshot({ playerId, name, size = 40 }) {
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
        <span style={{ fontSize: size * 0.4, color: "#9CA3AF", fontWeight: "bold" }}>
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
        style={{ width: "100%", height: "100%", objectFit: "cover", objectPosition: "top center" }}
        onError={() => setImageError(true)}
      />
    </div>
  );
}

export default function GameStatsPanel({ events, homeTeam, awayTeam }) {
  const [isExpanded, setIsExpanded] = useState(false);
  const [activeTab, setActiveTab] = useState("home");

  // Extract latest game_update data
  const gameData = useMemo(() => {
    const gameUpdates = events.filter((e) => e.event_type === "game_update");
    const latest = gameUpdates[gameUpdates.length - 1];
    if (!latest) return null;
    
    return {
      homeTeam: latest.home_team,
      awayTeam: latest.away_team,
      playerStats: latest.player_stats,
      period: latest.period,
      gameClock: latest.game_clock,
    };
  }, [events]);

  // Process player stats - filter out DNP players and sort by points
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

  const currentPlayers = activeTab === "home" ? processedPlayers.home : processedPlayers.away;
  const currentTeam = activeTab === "home" ? homeTeam : awayTeam;
  const currentTotals = activeTab === "home" ? teamTotals.home : teamTotals.away;

  // Scoreboard data
  const homeScore = gameData?.homeTeam?.score;
  const awayScore = gameData?.awayTeam?.score;
  const period = gameData?.period;
  const clock = gameData?.gameClock;

  return (
    <div style={styles.container}>
      {/* Scoreboard */}
      <div style={styles.scoreboard}>
        {/* VS Label */}
        <div style={styles.vsLabel}>
          <span className="font-tech" style={styles.vsText}>VS</span>
        </div>
        
        {/* Home Team Panel */}
        <div style={styles.teamPanelWrapper}>
          <div style={{ ...styles.panelOuterFrame, clipPath: "polygon(0 0, 94% 0, 100% 100%, 0 100%)" }}>
            <div style={{ ...styles.teamPanelInner, clipPath: "polygon(0 0, 94% 0, 100% 100%, 0 100%)" }}>
              <div style={styles.teamPanelContent}>
                <TeamLogo team={homeTeam} size={54} />
                <div style={styles.teamInfo}>
                  <span className="font-display" style={styles.teamName}>{homeTeam.name}</span>
                </div>
                <motion.span
                  className="font-display"
                  style={styles.broadcastScore}
                  key={homeScore}
                  initial={{ scale: 1.3, opacity: 0 }}
                  animate={{ scale: 1, opacity: 1 }}
                >
                  {homeScore ?? "-"}
                </motion.span>
              </div>
              <div style={{ ...styles.panelBottomGlow, background: `linear-gradient(90deg, ${homeTeam.color}00 0%, ${homeTeam.color} 50%, ${homeTeam.color}80 100%)` }} />
            </div>
          </div>
        </div>

        {/* Center Clock Box */}
        <div style={styles.centerClockContainer}>
          <div style={styles.clockTrapezoidOuter}>
            <div style={styles.clockTrapezoidInner}>
              <div style={styles.clockInnerDisplay}>
                <span className="font-tech" style={styles.periodBroadcast}>{period ? `Q${period}` : "-"}</span>
                <span className="font-tech" style={styles.clockBroadcast}>{clock || "--:--"}</span>
              </div>
            </div>
          </div>
        </div>

        {/* Away Team Panel */}
        <div style={{ ...styles.teamPanelWrapper, flexDirection: "row-reverse" }}>
          <div style={{ ...styles.panelOuterFrame, clipPath: "polygon(6% 0, 100% 0, 100% 100%, 0 100%)" }}>
            <div style={{ ...styles.teamPanelInner, background: "linear-gradient(225deg, rgba(25, 30, 40, 0.98) 0%, rgba(15, 20, 30, 0.99) 100%)", clipPath: "polygon(6% 0, 100% 0, 100% 100%, 0 100%)" }}>
              <div style={{ ...styles.teamPanelContent, flexDirection: "row-reverse" }}>
                <TeamLogo team={awayTeam} size={54} />
                <div style={{ ...styles.teamInfo, alignItems: "flex-end" }}>
                  <span className="font-display" style={styles.teamName}>{awayTeam.name}</span>
                </div>
                <motion.span
                  className="font-display"
                  style={styles.broadcastScore}
                  key={awayScore}
                  initial={{ scale: 1.3, opacity: 0 }}
                  animate={{ scale: 1, opacity: 1 }}
                >
                  {awayScore ?? "-"}
                </motion.span>
              </div>
              <div style={{ ...styles.panelBottomGlow, background: `linear-gradient(270deg, ${awayTeam.color}00 0%, ${awayTeam.color} 50%, ${awayTeam.color}80 100%)` }} />
            </div>
          </div>
        </div>
      </div>

      {/* Expand Toggle - integrated into scoreboard */}
      <motion.button
        onClick={() => setIsExpanded(!isExpanded)}
        style={styles.expandToggle}
        whileHover={{ background: "rgba(255,255,255,0.08)" }}
      >
        <motion.svg
          width="14"
          height="14"
          viewBox="0 0 24 24"
          fill="none"
          stroke="currentColor"
          strokeWidth="2"
          animate={{ rotate: isExpanded ? 180 : 0 }}
          transition={{ duration: 0.25 }}
        >
          <path d="M6 9l6 6 6-6" />
        </motion.svg>
        <span className="font-tech" style={styles.expandText}>PLAYER STATS</span>
      </motion.button>

      {/* Expanded Stats Panel */}
      <AnimatePresence>
        {isExpanded && gameData && (
          <motion.div
            style={styles.statsPanel}
            initial={{ height: 0, opacity: 0 }}
            animate={{ height: "auto", opacity: 1 }}
            exit={{ height: 0, opacity: 0 }}
            transition={{ duration: 0.25, ease: "easeOut" }}
          >
            {/* Team Tabs - simplified, no score */}
            <div style={styles.tabContainer}>
              <button
                onClick={() => setActiveTab("home")}
                style={{
                  ...styles.tab,
                  borderBottom: activeTab === "home" ? `2px solid ${homeTeam.color}` : "2px solid transparent",
                  background: activeTab === "home" ? `${homeTeam.color}15` : "transparent",
                }}
              >
                <span style={{ ...styles.tabIndicator, background: homeTeam.color }} />
                <span className="font-tech" style={styles.tabName}>{homeTeam.name}</span>
              </button>
              
              <button
                onClick={() => setActiveTab("away")}
                style={{
                  ...styles.tab,
                  borderBottom: activeTab === "away" ? `2px solid ${awayTeam.color}` : "2px solid transparent",
                  background: activeTab === "away" ? `${awayTeam.color}15` : "transparent",
                }}
              >
                <span style={{ ...styles.tabIndicator, background: awayTeam.color }} />
                <span className="font-tech" style={styles.tabName}>{awayTeam.name}</span>
              </button>
            </div>

            {/* Team Totals Bar */}
            {currentTotals && (
              <div style={styles.totalsBar}>
                <div style={styles.totalItem}>
                  <span className="font-tech" style={styles.totalLabel}>FG%</span>
                  <span className="font-tech" style={styles.totalValue}>{currentTotals.fgPct}%</span>
                </div>
                <div style={styles.totalItem}>
                  <span className="font-tech" style={styles.totalLabel}>3P%</span>
                  <span className="font-tech" style={styles.totalValue}>{currentTotals.threePct}%</span>
                </div>
                <div style={styles.totalItem}>
                  <span className="font-tech" style={styles.totalLabel}>REB</span>
                  <span className="font-tech" style={styles.totalValue}>{currentTotals.rebounds}</span>
                </div>
                <div style={styles.totalItem}>
                  <span className="font-tech" style={styles.totalLabel}>AST</span>
                  <span className="font-tech" style={styles.totalValue}>{currentTotals.assists}</span>
                </div>
                <div style={styles.totalItem}>
                  <span className="font-tech" style={styles.totalLabel}>STL</span>
                  <span className="font-tech" style={styles.totalValue}>{currentTotals.steals}</span>
                </div>
                <div style={styles.totalItem}>
                  <span className="font-tech" style={styles.totalLabel}>BLK</span>
                  <span className="font-tech" style={styles.totalValue}>{currentTotals.blocks}</span>
                </div>
              </div>
            )}

            {/* Player Stats Table */}
            <div style={styles.tableContainer}>
              <div style={styles.tableHeader}>
                <span className="font-tech" style={{ ...styles.headerCell, flex: 2 }}>PLAYER</span>
                <span className="font-tech" style={styles.headerCell}>MIN</span>
                <span className="font-tech" style={styles.headerCell}>PTS</span>
                <span className="font-tech" style={styles.headerCell}>REB</span>
                <span className="font-tech" style={styles.headerCell}>AST</span>
                <span className="font-tech" style={styles.headerCell}>FG%</span>
                <span className="font-tech" style={styles.headerCell}>+/-</span>
              </div>

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
                        background: isTopScorer ? `linear-gradient(90deg, ${currentTeam.color}15 0%, transparent 100%)` : "transparent",
                      }}
                      initial={{ opacity: 0, x: -10 }}
                      animate={{ opacity: 1, x: 0 }}
                      transition={{ delay: index * 0.03 }}
                    >
                      <div style={{ ...styles.playerCell, flex: 2 }}>
                        <PlayerHeadshot playerId={player.personId} name={player.familyName} size={32} />
                        <div style={styles.playerInfo}>
                          <span className="font-tech" style={styles.playerName}>
                            {player.nameI || `${player.firstName?.charAt(0)}. ${player.familyName}`}
                            {isTopScorer && <span style={styles.topBadge}>TOP</span>}
                          </span>
                          <span className="font-tech" style={styles.playerPosition}>{player.position || "-"}</span>
                        </div>
                      </div>
                      <span className="font-tech" style={styles.statCell}>{stats?.minutes || "-"}</span>
                      <span className="font-tech" style={{ ...styles.statCell, fontWeight: "bold", color: "#fff" }}>
                        {stats?.points ?? 0}
                      </span>
                      <span className="font-tech" style={styles.statCell}>{stats?.reboundsTotal ?? 0}</span>
                      <span className="font-tech" style={styles.statCell}>{stats?.assists ?? 0}</span>
                      <span className="font-tech" style={styles.statCell}>
                        {stats?.fieldGoalsPercentage ? (stats.fieldGoalsPercentage * 100).toFixed(0) : 0}%
                      </span>
                      <span
                        className="font-tech"
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
  );
}

const styles = {
  container: {
    position: "relative",
    display: "flex",
    flexDirection: "column",
    zIndex: 10,
  },
  // Scoreboard styles
  scoreboard: {
    display: "flex",
    alignItems: "center",
    justifyContent: "center",
    padding: "40px 24px 20px",
    background: "linear-gradient(180deg, rgba(0,0,0,0.75) 0%, rgba(0,0,0,0.3) 70%, transparent 100%)",
    position: "relative",
    gap: "10px",
  },
  vsLabel: {
    position: "absolute",
    top: "70px",
    left: "50%",
    transform: "translate(-50%, -50%)",
    zIndex: 10,
  },
  vsText: {
    fontSize: "28px",
    color: "#6B8ABD",
    letterSpacing: "0.4em",
    fontWeight: "bold",
    textShadow: "0 2px 15px rgba(107, 138, 189, 0.6)",
  },
  teamPanelWrapper: {
    display: "flex",
    alignItems: "stretch",
    position: "relative",
    height: "85px",
  },
  panelOuterFrame: {
    padding: "4px",
    background: "linear-gradient(180deg, #D0D8E0 0%, #C0C8D0 8%, #A8B0B8 20%, #8A949C 35%, #6A7A8B 55%, #5A646C 75%, #4A545C 90%, #3A444C 100%)",
    boxShadow: "0 6px 25px rgba(0,0,0,0.6), inset 0 2px 0 rgba(255,255,255,0.5), inset 0 -1px 0 rgba(0,0,0,0.3)",
  },
  teamPanelInner: {
    display: "flex",
    flexDirection: "column",
    justifyContent: "center",
    minWidth: "300px",
    height: "100%",
    position: "relative",
    background: "linear-gradient(135deg, rgba(25, 30, 40, 0.98) 0%, rgba(15, 20, 30, 0.99) 100%)",
  },
  teamPanelContent: {
    display: "flex",
    alignItems: "center",
    gap: "14px",
    padding: "0 24px",
    width: "100%",
    zIndex: 3,
  },
  panelBottomGlow: {
    position: "absolute",
    bottom: 0,
    left: 0,
    right: 0,
    height: "5px",
    zIndex: 4,
  },
  teamInfo: {
    display: "flex",
    flexDirection: "column",
    gap: "2px",
    flex: 1,
  },
  teamName: {
    fontSize: "18px",
    color: "var(--text-primary)",
    letterSpacing: "0.08em",
    fontWeight: "bold",
    textTransform: "uppercase",
  },
  broadcastScore: {
    fontSize: "48px",
    fontWeight: "bold",
    color: "#FF3B3B",
    textShadow: "0 0 25px rgba(255, 59, 59, 0.7), 0 2px 4px rgba(0,0,0,0.9)",
    fontFamily: "'Oswald', 'Impact', sans-serif",
    lineHeight: 1,
    minWidth: "55px",
    textAlign: "center",
  },
  centerClockContainer: {
    display: "flex",
    flexDirection: "column",
    alignItems: "center",
    gap: "8px",
    zIndex: 5,
    marginLeft: "-4px",
    marginRight: "-4px",
    marginTop: "50px",
  },
  clockTrapezoidOuter: {
    height: "85px",
    padding: "4px",
    minWidth: "200px",
    clipPath: "polygon(0 0, 100% 0, 93% 100%, 7% 100%)",
    background: "linear-gradient(180deg, #D0D8E0 0%, #C0C8D0 8%, #A8B0B8 20%, #8A949C 35%, #6A7A8B 55%, #5A646C 75%, #4A545C 90%, #3A444C 100%)",
    boxShadow: "0 6px 25px rgba(0,0,0,0.6), inset 0 2px 0 rgba(255,255,255,0.5), inset 0 -1px 0 rgba(0,0,0,0.3)",
    display: "flex",
    alignItems: "center",
    justifyContent: "center",
  },
  clockTrapezoidInner: {
    height: "100%",
    width: "100%",
    padding: "4px 28px",
    clipPath: "polygon(0 0, 100% 0, 92% 100%, 8% 100%)",
    background: "linear-gradient(180deg, #8A949C 0%, #6A747C 30%, #5A646C 60%, #4A545C 100%)",
    display: "flex",
    alignItems: "center",
    justifyContent: "center",
    boxShadow: "inset 0 1px 0 rgba(255,255,255,0.2)",
  },
  clockInnerDisplay: {
    display: "flex",
    flexDirection: "column",
    alignItems: "center",
    justifyContent: "center",
    padding: "8px 42px",
    background: "linear-gradient(180deg, #2A3040 0%, #1E2535 50%, #1A2030 100%)",
    borderRadius: "8px",
    border: "1px solid #4A5A6B",
    minWidth: "160px",
    height: "calc(100% - 8px)",
    boxShadow: "inset 0 2px 8px rgba(0,0,0,0.6), inset 0 -1px 0 rgba(255,255,255,0.05)",
  },
  periodBroadcast: {
    fontSize: "11px",
    color: "#7A8A9B",
    letterSpacing: "0.2em",
    fontWeight: "bold",
    marginBottom: "2px",
  },
  clockBroadcast: {
    fontSize: "18px",
    color: "#B0C0D0",
    letterSpacing: "0.08em",
    fontWeight: "bold",
    textShadow: "0 1px 3px rgba(0,0,0,0.6)",
    whiteSpace: "nowrap",
  },
  // Expand toggle
  expandToggle: {
    display: "flex",
    alignItems: "center",
    justifyContent: "center",
    gap: "8px",
    padding: "8px 24px",
    background: "rgba(0,0,0,0.4)",
    border: "none",
    borderTop: "1px solid rgba(255,255,255,0.05)",
    color: "#9CA3AF",
    cursor: "pointer",
    transition: "background 0.2s ease",
  },
  expandText: {
    fontSize: "10px",
    letterSpacing: "0.15em",
    color: "#CBD5E1",
  },
  // Stats panel - positioned as overlay with transparency
  statsPanel: {
    position: "absolute",
    top: "100%",
    left: 0,
    right: 0,
    zIndex: 20,
    overflow: "hidden",
    background: "linear-gradient(180deg, rgba(15, 23, 42, 0.78) 0%, rgba(10, 15, 30, 0.72) 100%)",
    backdropFilter: "blur(18px)",
    WebkitBackdropFilter: "blur(18px)",
    boxShadow: "0 8px 32px rgba(0, 0, 0, 0.35)",
    borderBottom: "1px solid rgba(100, 180, 255, 0.12)",
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
    gap: "8px",
    padding: "12px 16px",
    border: "none",
    cursor: "pointer",
    transition: "all 0.2s ease",
  },
  tabIndicator: {
    width: "3px",
    height: "16px",
    borderRadius: "2px",
  },
  tabName: {
    fontSize: "12px",
    color: "#E2E8F0",
    letterSpacing: "0.05em",
  },
  totalsBar: {
    display: "flex",
    justifyContent: "space-around",
    padding: "10px 20px",
    background: "rgba(0, 0, 0, 0.15)",
    borderBottom: "1px solid rgba(255, 255, 255, 0.05)",
  },
  totalItem: {
    display: "flex",
    flexDirection: "column",
    alignItems: "center",
    gap: "2px",
  },
  totalLabel: {
    fontSize: "9px",
    color: "#64748B",
    letterSpacing: "0.1em",
  },
  totalValue: {
    fontSize: "14px",
    color: "#E2E8F0",
    fontWeight: "600",
  },
  tableContainer: {
    maxHeight: "260px",
    overflow: "auto",
  },
  tableHeader: {
    display: "flex",
    alignItems: "center",
    padding: "8px 20px",
    background: "rgba(0, 0, 0, 0.2)",
    borderBottom: "1px solid rgba(255, 255, 255, 0.08)",
    position: "sticky",
    top: 0,
    zIndex: 5,
  },
  headerCell: {
    flex: 1,
    fontSize: "9px",
    color: "#64748B",
    letterSpacing: "0.1em",
    textAlign: "center",
  },
  tableBody: {
    padding: "4px 0",
  },
  playerRow: {
    display: "flex",
    alignItems: "center",
    padding: "8px 20px",
    borderBottom: "1px solid rgba(255, 255, 255, 0.03)",
  },
  playerCell: {
    display: "flex",
    alignItems: "center",
    gap: "10px",
  },
  playerInfo: {
    display: "flex",
    flexDirection: "column",
    gap: "1px",
  },
  playerName: {
    fontSize: "12px",
    color: "#E2E8F0",
    display: "flex",
    alignItems: "center",
    gap: "6px",
  },
  playerPosition: {
    fontSize: "9px",
    color: "#64748B",
    letterSpacing: "0.05em",
  },
  topBadge: {
    fontSize: "8px",
    padding: "1px 5px",
    background: "linear-gradient(135deg, #F59E0B 0%, #D97706 100%)",
    color: "#fff",
    borderRadius: "3px",
    letterSpacing: "0.05em",
    fontWeight: "bold",
  },
  statCell: {
    flex: 1,
    fontSize: "12px",
    color: "#9CA3AF",
    textAlign: "center",
  },
};
