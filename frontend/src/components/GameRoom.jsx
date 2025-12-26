import { useState, useEffect, useCallback, useMemo } from "react";
import { useParams, useNavigate } from "react-router-dom";
import { motion } from "framer-motion";
import { API_BASE_URL, nbaTeams, eventTypes } from "../constants";
import ThemeToggle from "./ThemeToggle";
import OddsChart from "./OddsChart";
import AgentPanel from "./AgentPanel";
import DanmakuOverlay from "./DanmakuOverlay";
import ArenaBackground from "./ArenaBackground";
import CourtAnimator from "./CourtAnimator";
import EventTicker from "./EventTicker";

export default function GameRoom() {
  const { trialId } = useParams();
  const navigate = useNavigate();

  const [trialData, setTrialData] = useState(null);
  const [events, setEvents] = useState([]);
  const [checkpoint, setCheckpoint] = useState(null);
  const [agentLogs, setAgentLogs] = useState([]);
  const [loading, setLoading] = useState(true);

  // Replay state
  const [isPlaying, setIsPlaying] = useState(false);
  const [currentEventIndex, setCurrentEventIndex] = useState(0);
  const [playbackSpeed, setPlaybackSpeed] = useState(1);

  // User danmaku state
  const [userDanmaku, setUserDanmaku] = useState([]);
  const [danmakuInput, setDanmakuInput] = useState("");

  useEffect(() => {
    fetchTrialData();
  }, [trialId]);

  const fetchTrialData = async () => {
    setLoading(true);
    try {
      const [trialRes, eventsRes, checkpointRes, logsRes] = await Promise.all([
        fetch(`${API_BASE_URL}/trials/${trialId}`).catch(() => null),
        fetch(`${API_BASE_URL}/trials/${trialId}/events`).catch(() => null),
        fetch(`${API_BASE_URL}/trials/${trialId}/checkpoint`).catch(() => null),
        fetch(`${API_BASE_URL}/trials/${trialId}/agent-logs`).catch(() => null),
      ]);

      if (trialRes?.ok) setTrialData(await trialRes.json());
      if (eventsRes?.ok) setEvents(await eventsRes.json());
      if (checkpointRes?.ok) setCheckpoint(await checkpointRes.json());
      if (logsRes?.ok) {
        const logsJson = await logsRes.json();
        setAgentLogs(Array.isArray(logsJson) ? logsJson : []);
      }
    } catch (error) {
      console.error("Failed to fetch trial data:", error);
    } finally {
      setLoading(false);
    }
  };

  // Replay control
  useEffect(() => {
    let interval;
    if (isPlaying && currentEventIndex < events.length - 1) {
      interval = setInterval(() => {
        setCurrentEventIndex((prev) => {
          if (prev >= events.length - 1) {
            setIsPlaying(false);
            return prev;
          }
          return prev + 1;
        });
      }, 1000 / playbackSpeed);
    }
    return () => clearInterval(interval);
  }, [isPlaying, currentEventIndex, events.length, playbackSpeed]);

  const handlePlayPause = () => {
    if (currentEventIndex >= events.length - 1) {
      setCurrentEventIndex(0);
    }
    setIsPlaying(!isPlaying);
  };

  const handleRestart = () => {
    setCurrentEventIndex(0);
    setIsPlaying(false);
  };

  const handleSendDanmaku = useCallback(
    async (e) => {
      e.preventDefault();
      if (!danmakuInput.trim()) return;

      const newMessage = {
        id: Date.now(),
        text: danmakuInput,
        timestamp: new Date().toISOString(),
      };

      setUserDanmaku((prev) => [...prev, newMessage]);
      setDanmakuInput("");

      try {
        await fetch(`${API_BASE_URL}/danmaku`, {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({
            message: danmakuInput,
            trial_id: trialId,
            timestamp: newMessage.timestamp,
          }),
        });
      } catch (error) {
        console.error("Failed to send danmaku:", error);
      }
    },
    [danmakuInput, trialId]
  );

  // Remove old user danmaku after animation
  useEffect(() => {
    const cleanup = setInterval(() => {
      setUserDanmaku((prev) => prev.filter((msg) => Date.now() - msg.id < 12000));
    }, 1000);
    return () => clearInterval(cleanup);
  }, []);

  const metadata = trialData?.spec?.metadata || trialData?.status?.metadata || {};
  const homeTeam = nbaTeams[metadata.home_team_tricode] || {
    name: metadata.home_team_tricode || "HOME",
    color: "#3B82F6",
  };
  const awayTeam = nbaTeams[metadata.away_team_tricode] || {
    name: metadata.away_team_tricode || "AWAY",
    color: "#EF4444",
  };

  const currentEvents = events.slice(0, currentEventIndex + 1);

  // Get current score from latest game_update event
  const currentScore = useMemo(() => {
    const gameUpdates = currentEvents.filter((e) => e.event_type === "game_update");
    const latest = gameUpdates[gameUpdates.length - 1];
    if (latest) {
      return {
        home: latest.home_team?.score || 0,
        away: latest.away_team?.score || 0,
        period: latest.period || 1,
        clock: latest.game_status_text || "",
      };
    }
    return { home: 0, away: 0, period: 1, clock: "" };
  }, [currentEvents]);

  // Get latest odds
  const currentOdds = useMemo(() => {
    const oddsUpdates = currentEvents.filter((e) => e.event_type === "odds_update");
    const latest = oddsUpdates[oddsUpdates.length - 1];
    if (latest) {
      return {
        homeProb: (latest.home_probability * 100).toFixed(1),
        awayProb: (latest.away_probability * 100).toFixed(1),
      };
    }
    return { homeProb: "50.0", awayProb: "50.0" };
  }, [currentEvents]);

  if (loading) {
    return (
      <div style={styles.loadingScreen}>
        <div className="shimmer" style={styles.loadingSpinner} />
        <p className="font-tech" style={styles.loadingText}>LOADING ARENA...</p>
      </div>
    );
  }

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
              {/* Scoreboard - Broadcast Style */}
              <div style={styles.scoreboard}>
                {/* VS Label */}
                <div style={styles.vsLabel}>
                  <span className="font-tech" style={styles.vsText}>VS</span>
                </div>
                
                {/* Home Team Panel - Left side, converges toward center */}
                <div style={styles.teamPanelWrapper}>
                  {/* Silver metallic frame - outer */}
                  <div style={{
                    ...styles.panelOuterFrame,
                    background: "linear-gradient(180deg, #D0D8E0 0%, #C0C8D0 8%, #A8B0B8 20%, #8A949C 35%, #6A7A8B 55%, #5A646C 75%, #4A545C 90%, #3A444C 100%)",
                    clipPath: "polygon(0 0, 94% 0, 100% 100%, 0 100%)",
                  }}>
                    {/* Inner dark panel */}
                    <div style={{
                      ...styles.teamPanelInner,
                      background: `linear-gradient(135deg, rgba(25, 30, 40, 0.98) 0%, rgba(15, 20, 30, 0.99) 100%)`,
                      clipPath: "polygon(0 0, 94% 0, 100% 100%, 0 100%)",
                    }}>
                      <div style={styles.teamPanelContent}>
                        <div style={{ 
                          ...styles.teamLogo, 
                          background: `linear-gradient(135deg, ${homeTeam.color}88 0%, ${homeTeam.color}44 100%)`,
                          border: 'none',
                          boxShadow: `0 4px 20px rgba(0, 0, 0, 0.3)` 
                        }}>
                          <img 
                            src={homeTeam.logo || `https://cdn.nba.com/logos/nba/1610612748/global/L/logo.svg`} 
                            alt={homeTeam.name} 
                            style={styles.teamLogoImg}
                            onError={(e) => { 
                              e.target.style.display = 'none'; 
                              e.target.parentElement.innerHTML = `<span style="font-size: 24px; color: ${homeTeam.color}; font-weight: bold">${homeTeam.name?.charAt(0) || 'H'}</span>`;
                            }}
                          />
                        </div>
                        <div style={styles.teamInfo}>
                          <span className="font-display" style={styles.teamName}>{homeTeam.name}</span>
                          <span className="font-tech" style={{ ...styles.teamProb, color: homeTeam.color }}>
                            {currentOdds.homeProb}%
                          </span>
                        </div>
                        <motion.span
                          className="font-display"
                          style={styles.broadcastScore}
                          key={currentScore.home}
                          initial={{ scale: 1.3, opacity: 0 }}
                          animate={{ scale: 1, opacity: 1 }}
                        >
                          {currentScore.home}
                        </motion.span>
                      </div>
                      {/* Bottom glow bar */}
                      <div style={{ 
                        ...styles.panelBottomGlow, 
                        background: `linear-gradient(90deg, ${homeTeam.color}00 0%, ${homeTeam.color} 50%, ${homeTeam.color}80 100%)` 
                      }} />
                    </div>
                  </div>
                </div>

                {/* Center Clock Box with Trapezoidal Frame */}
                <div style={styles.centerClockContainer}>
                  {/* Unified trapezoidal metallic frame */}
                  <div style={styles.clockTrapezoidOuter}>
                    <div style={styles.clockTrapezoidInner}>
                      <div style={styles.clockInnerDisplay}>
                        <span className="font-tech" style={styles.periodBroadcast}>Q{currentScore.period}</span>
                        <span className="font-tech" style={styles.clockBroadcast}>{currentScore.clock || "00:00"}</span>
                      </div>
                    </div>
                  </div>
                </div>

                {/* Away Team Panel - Right side, converges toward center */}
                <div style={{ ...styles.teamPanelWrapper, flexDirection: "row-reverse" }}>
                  {/* Silver metallic frame - outer */}
                  <div style={{
                    ...styles.panelOuterFrame,
                    background: "linear-gradient(180deg, #D0D8E0 0%, #C0C8D0 8%, #A8B0B8 20%, #8A949C 35%, #6A7A8B 55%, #5A646C 75%, #4A545C 90%, #3A444C 100%)",
                    clipPath: "polygon(6% 0, 100% 0, 100% 100%, 0 100%)",
                  }}>
                    {/* Inner dark panel */}
                    <div style={{
                      ...styles.teamPanelInner,
                      background: `linear-gradient(225deg, rgba(25, 30, 40, 0.98) 0%, rgba(15, 20, 30, 0.99) 100%)`,
                      clipPath: "polygon(6% 0, 100% 0, 100% 100%, 0 100%)",
                    }}>
                      <div style={{ ...styles.teamPanelContent, flexDirection: "row-reverse" }}>
                        <div style={{ 
                          ...styles.teamLogo, 
                          background: `linear-gradient(135deg, ${awayTeam.color}88 0%, ${awayTeam.color}44 100%)`,
                          border: 'none',
                          boxShadow: `0 4px 20px rgba(0, 0, 0, 0.3)` 
                        }}>
                          <img 
                            src={awayTeam.logo || `https://cdn.nba.com/logos/nba/1610612761/global/L/logo.svg`} 
                            alt={awayTeam.name} 
                            style={styles.teamLogoImg}
                            onError={(e) => { 
                              e.target.style.display = 'none'; 
                              e.target.parentElement.innerHTML = `<span style="font-size: 24px; color: ${awayTeam.color}; font-weight: bold">${awayTeam.name?.charAt(0) || 'A'}</span>`;
                            }}
                          />
                        </div>
                        <div style={{ ...styles.teamInfo, alignItems: "flex-end" }}>
                          <span className="font-display" style={styles.teamName}>{awayTeam.name}</span>
                          <span className="font-tech" style={{ ...styles.teamProb, color: awayTeam.color }}>
                            {currentOdds.awayProb}%
                          </span>
                        </div>
                        <motion.span
                          className="font-display"
                          style={styles.broadcastScore}
                          key={currentScore.away}
                          initial={{ scale: 1.3, opacity: 0 }}
                          animate={{ scale: 1, opacity: 1 }}
                        >
                          {currentScore.away}
                        </motion.span>
                      </div>
                      {/* Bottom glow bar */}
                      <div style={{ 
                        ...styles.panelBottomGlow, 
                        background: `linear-gradient(270deg, ${awayTeam.color}00 0%, ${awayTeam.color} 50%, ${awayTeam.color}80 100%)` 
                      }} />
                    </div>
                  </div>
                </div>
              </div>

              {/* Animated Court with Players */}
              <div style={styles.danmakuArea}>
                {/* Court with animated basketball players */}
                <div style={styles.courtLayer}>
                  <CourtAnimator
                    events={events}
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

          {/* Right: Odds + Agents */}
          <div style={styles.rightColumn}>
            {/* Odds HUD */}
            <section style={styles.oddsSection}>
              <OddsChart 
                events={currentEvents} 
                homeTeam={homeTeam} 
                awayTeam={awayTeam}
                header={
                  <div style={styles.sectionHeader}>
                    <h3 className="font-display" style={styles.sectionTitle}>ODDS HUD</h3>
                    <span className="font-tech" style={styles.liveIndicator}>
                      <span style={styles.liveDotSmall} />
                      LIVE
                    </span>
                  </div>
                }
              />
            </section>

            {/* Agent Panel */}
            <section style={styles.agentSection}>
              <div className="metal-card" style={styles.agentCard}>
                <h3 className="font-display" style={styles.sectionTitle}>AI AGENTS</h3>
                <AgentPanel checkpoint={checkpoint} agentLogs={agentLogs} currentEventIndex={currentEventIndex} />
              </div>
            </section>
          </div>
        </div>
      </main>

      {/* Footer: Controls + Danmaku input */}
      <footer style={styles.footer}>
        {/* Replay controls */}
        <div style={styles.replayControls}>
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
              {currentEventIndex + 1} / {events.length}
            </span>
            <input
              type="range"
              min={0}
              max={events.length - 1}
              value={currentEventIndex}
              onChange={(e) => setCurrentEventIndex(Number(e.target.value))}
              style={styles.slider}
            />
          </div>
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
    gridTemplateColumns: "1fr 360px",
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
    background: `url(/assets/background/room_background.png) center center / cover no-repeat`,
    borderRadius: "16px",
    border: "1px solid var(--glass-border)",
    overflow: "hidden",
    position: "relative",
  },
  scoreboard: {
    display: "flex",
    alignItems: "center",
    justifyContent: "center",
    padding: "40px 24px 30px",
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
    boxShadow: "0 6px 25px rgba(0,0,0,0.6), inset 0 2px 0 rgba(255,255,255,0.5), inset 0 -1px 0 rgba(0,0,0,0.3)",
  },
  teamPanelInner: {
    display: "flex",
    flexDirection: "column",
    justifyContent: "center",
    minWidth: "300px",
    height: "100%",
    position: "relative",
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
  teamLogo: {
    width: "54px",
    height: "54px",
    display: "flex",
    alignItems: "center",
    justifyContent: "center",
    borderRadius: "50%",
    overflow: "hidden",
    flexShrink: 0,
  },
  teamLogoImg: {
    width: "40px",
    height: "40px",
    objectFit: "contain",
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
  teamProb: {
    fontSize: "15px",
    letterSpacing: "0.05em",
    fontWeight: "bold",
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
  liveTagBroadcast: {
    display: "flex",
    alignItems: "center",
    gap: "6px",
    padding: "5px 14px",
    background: "linear-gradient(180deg, rgba(180, 70, 50, 0.95) 0%, rgba(140, 45, 35, 0.95) 100%)",
    borderRadius: "4px",
    fontSize: "11px",
    color: "#FFB090",
    letterSpacing: "0.18em",
    fontWeight: "bold",
    border: "1px solid rgba(255, 130, 90, 0.4)",
    boxShadow: "0 3px 10px rgba(180, 70, 50, 0.5), inset 0 1px 0 rgba(255,255,255,0.1)",
  },
  liveDot: {
    width: "8px",
    height: "8px",
    borderRadius: "50%",
    background: "#FF6B4A",
    boxShadow: "0 0 10px #FF6B4A, 0 0 20px #FF6B4A50",
    animation: "pulse-glow 1.5s ease-in-out infinite",
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
    gap: "16px",
    minHeight: 0,
    overflow: "hidden",
  },
  oddsSection: {
    flexShrink: 0,
  },
  oddsCard: {
    padding: "14px 18px",
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
  },
  agentCard: {
    height: "100%",
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
};
