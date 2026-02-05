import { useState, useEffect, useMemo } from "react";
import { useParams, useNavigate } from "react-router-dom";
import { ArrowLeft, Clock, TrendingUp, Users, Play, Activity } from "lucide-react";
import { motion } from "framer-motion";

export default function GameDetailPage() {
  const { trialId } = useParams();
  const navigate = useNavigate();
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState(null);
  const [trialData, setTrialData] = useState(null);

  useEffect(() => {
    const fetchGameDetail = async () => {
      try {
        setLoading(true);
        setError(null);
        const apiUrl = import.meta.env.VITE_API_URL || "http://localhost:3001";

        const controller = new AbortController();
        const timeoutId = setTimeout(() => controller.abort(), 120000);

        const response = await fetch(`${apiUrl}/api/trials/${trialId}`, {
          signal: controller.signal
        });
        clearTimeout(timeoutId);

        if (!response.ok) {
          throw new Error(`Failed to fetch game detail: ${response.status} ${response.statusText}`);
        }

        const data = await response.json();
        setTrialData(data);
      } catch (err) {
        console.error("Failed to fetch game detail:", err);
        if (err.name === 'AbortError') {
          setError("Request timed out. The backend may be slow or unavailable.");
        } else {
          setError(err.message);
        }
      } finally {
        setLoading(false);
      }
    };

    if (trialId) {
      fetchGameDetail();
    }
  }, [trialId]);

  // Parse game info and events
  const gameInfo = useMemo(() => {
    if (!trialData?.items) return null;
    return parseGameInfo(trialData.items);
  }, [trialData]);

  const events = useMemo(() => {
    if (!trialData?.items) return [];
    return parseEvents(trialData.items);
  }, [trialData]);

  if (loading) {
    return (
      <div style={styles.loadingScreen}>
        <div style={styles.loadingSpinner} />
        <p style={styles.loadingText}>LOADING ARENA...</p>
      </div>
    );
  }

  if (error) {
    return (
      <div style={styles.loadingScreen}>
        <p style={styles.errorText}>CONNECTION ERROR: {error}</p>
        <p style={styles.trialIdText}>Trial ID: {trialId}</p>
        <div style={{ display: "flex", gap: 12 }}>
          <motion.button
            onClick={() => window.location.reload()}
            style={styles.backButton}
            whileHover={{ scale: 1.05 }}
          >
            TRY AGAIN
          </motion.button>
          <motion.button
            onClick={() => navigate("/")}
            style={styles.backButton}
            whileHover={{ scale: 1.05 }}
          >
            <ArrowLeft size={16} />
            BACK TO LOBBY
          </motion.button>
        </div>
      </div>
    );
  }

  if (!gameInfo) {
    return (
      <div style={styles.loadingScreen}>
        <p style={styles.errorText}>NO GAME DATA FOUND</p>
        <motion.button
          onClick={() => navigate("/")}
          style={styles.backButton}
          whileHover={{ scale: 1.05 }}
        >
          <ArrowLeft size={16} />
          BACK TO LOBBY
        </motion.button>
      </div>
    );
  }

  return (
    <div style={styles.container}>
      {/* Header */}
      <header style={styles.header}>
        <motion.button
          onClick={() => navigate("/")}
          style={styles.backButton}
          whileHover={{ scale: 1.05 }}
          whileTap={{ scale: 0.95 }}
        >
          <ArrowLeft size={18} />
          <span>LOBBY</span>
        </motion.button>
      </header>

      {/* Main Content */}
      <main style={styles.main}>
        <div style={styles.mainGrid}>
          {/* Left: Game Info */}
          <section style={styles.gameSection}>
            {/* Compact Scoreboard */}
            <div style={styles.scoreboardCard}>
              <div style={styles.compactScoreboard}>
                <div style={styles.compactTeam}>
                  <TeamLogo team={gameInfo.homeTeam} size="small" />
                  <span style={styles.compactTeamName}>{gameInfo.homeTeam?.name}</span>
                  <span style={styles.compactScore}>{gameInfo.homeScore}</span>
                </div>
                <div style={styles.compactDivider}>
                  {gameInfo.status === "live" && (
                    <div style={styles.compactLive}>
                      <span style={styles.liveDot} />
                      <span style={styles.compactPeriod}>{gameInfo.period}</span>
                      <span style={styles.compactClock}>{gameInfo.gameClock}</span>
                    </div>
                  )}
                  {gameInfo.status !== "live" && (
                    <div style={styles.compactStatus}>{gameInfo.status.toUpperCase()}</div>
                  )}
                </div>
                <div style={styles.compactTeam}>
                  <span style={styles.compactScore}>{gameInfo.awayScore}</span>
                  <span style={styles.compactTeamName}>{gameInfo.awayTeam?.name}</span>
                  <TeamLogo team={gameInfo.awayTeam} size="small" />
                </div>
              </div>
            </div>

            {/* Agent Rankings (for completed games) */}
            {gameInfo.status === "completed" && gameInfo.agents.length > 0 && (
              <AgentRankings agents={gameInfo.agents} />
            )}

            {/* Social Feed Timeline */}
            <div style={styles.feedCard}>
              <div style={styles.feedHeader}>
                <Activity size={18} />
                <span>LIVE FEED</span>
                <span style={styles.eventCount}>{events.length} events</span>
              </div>
              <div style={styles.feedList}>
                {events.slice().reverse().map((event, index) => (
                  <FeedItem
                    key={`${event.type}-${event.data.timestamp || event.data.start_time || index}`}
                    event={event}
                    index={index}
                    gameInfo={gameInfo}
                  />
                ))}
              </div>
            </div>
          </section>

          {/* Right: Agents & Bets */}
          <aside style={styles.sidebar}>
            <div style={styles.sidebarCard}>
              <div style={styles.sidebarHeader}>
                <Users size={18} />
                <span>AI AGENTS</span>
              </div>
              <div style={styles.agentsList}>
                {gameInfo.agents.map((agent) => (
                  <AgentCard key={agent.id} agent={agent} />
                ))}
                {gameInfo.agents.length === 0 && (
                  <div style={styles.noAgents}>No agents participating</div>
                )}
              </div>
            </div>
          </aside>
        </div>
      </main>
    </div>
  );
}

function TeamLogo({ team, size = "large" }) {
  if (!team) {
    return <div style={styles.teamLogoPlaceholder}>?</div>;
  }

  const sizeStyles = size === "small" ? styles.teamLogoSmall : styles.teamLogo;
  const imgStyles = size === "small" ? styles.teamLogoImgSmall : styles.teamLogoImg;
  const textStyles = size === "small" ? styles.teamLogoTextSmall : styles.teamLogoText;

  return (
    <div style={{
      ...sizeStyles,
      background: `linear-gradient(135deg, ${team.color}88 0%, ${team.color}44 100%)`,
    }}>
      {team.logo_url ? (
        <img src={team.logo_url} alt={team.name} style={imgStyles} />
      ) : (
        <span style={textStyles}>{team.tricode || team.name?.charAt(0) || "?"}</span>
      )}
    </div>
  );
}

function FeedItem({ event, gameInfo }) {
  const [showDetails, setShowDetails] = useState(false);

  // Categorize event types
  const category = event.type;
  const data = event.data;

  // Agent info (for agent-related events)
  const agentName = data.agent_id || data.persona || data.agent_name || "AI Agent";
  const agentAvatar = agentName.charAt(0).toUpperCase();
  const agentColor = data.color || "#6B7280";

  // Agent Response (reasoning/thinking)
  // Check for response_message or content fields
  const agentContent = data.response_message || data.content;
  if (category.includes("agent.response") || category.includes("response") || agentContent) {
    return (
      <motion.div style={styles.feedItem} initial={{ opacity: 0, y: -10 }} animate={{ opacity: 1, y: 0 }} transition={{ duration: 0.3 }}>
        <div style={styles.feedPost}>
          <div style={styles.postHeader}>
            <div style={{ ...styles.postAvatar, background: agentColor }}>{agentAvatar}</div>
            <div style={styles.postMeta}>
              <div style={styles.postAuthor}>{agentName}</div>
              <div style={styles.postTime}>{event.time}</div>
            </div>
          </div>
          <div style={styles.postContent}>
            "{agentContent || "Thinking..."}"
          </div>
          {data.confidence && (
            <div style={styles.postFooter}>
              Confidence: {(data.confidence * 100).toFixed(0)}%
            </div>
          )}
        </div>
      </motion.div>
    );
  }

  // Agent Bet Execution
  if (category.includes("bet") && data.amount) {
    return (
      <motion.div style={styles.feedItem} initial={{ opacity: 0, y: -10 }} animate={{ opacity: 1, y: 0 }} transition={{ duration: 0.3 }}>
        <div style={styles.feedPost}>
          <div style={styles.postHeader}>
            <div style={{ ...styles.postAvatar, background: agentColor }}>
              <TrendingUp size={16} />
            </div>
            <div style={styles.postMeta}>
              <div style={styles.postAuthor}>{agentName}</div>
              <div style={styles.postTime}>{event.time}</div>
            </div>
          </div>
          <div style={styles.betContent}>
            <span style={styles.betAction}>placed a bet</span>
            <span style={styles.betAmount}>${data.amount}</span>
            <span style={styles.betTeam}>on {data.selection}</span>
          </div>
        </div>
      </motion.div>
    );
  }

  // Game Score Update
  if (category === "game_update" && data.home_score !== undefined) {
    return (
      <motion.div style={styles.feedItem} initial={{ opacity: 0, y: -10 }} animate={{ opacity: 1, y: 0 }} transition={{ duration: 0.3 }}>
        <div style={styles.feedUpdate}>
          <div style={styles.updateIcon}>
            <Play size={14} />
          </div>
          <div style={styles.updateText}>
            <span style={styles.updateScore}>
              {data.home_score} - {data.away_score}
            </span>
            {data.period && <span style={styles.updatePeriod}>{data.period}</span>}
            {data.game_clock && <span style={styles.updateClock}>{data.game_clock}</span>}
            <span style={styles.updateTime}>{event.time}</span>
          </div>
        </div>
      </motion.div>
    );
  }

  // Game Lifecycle (started, stopped, result)
  if (category.includes("game_initialize") || category.includes("game_result") || category.includes("trial.") || category.includes("trial_lifecycle") || category.includes("lifecycle")) {
    let icon = "▶️";
    let message = event.displayType;

    if (category.includes("result") || data.phase === "stopped" || data.phase === "completed") {
      icon = "🏁";
      message = "Game Finished";
    } else if (category.includes("initialize") || data.phase === "started") {
      icon = "🎮";
      message = "Game Started";
    } else if (data.phase) {
      message = `Game ${data.phase.charAt(0).toUpperCase() + data.phase.slice(1)}`;
    }

    return (
      <motion.div style={styles.feedItem} initial={{ opacity: 0, y: -10 }} animate={{ opacity: 1, y: 0 }} transition={{ duration: 0.3 }}>
        <div style={styles.feedLifecycle}>
          <span style={styles.lifecycleIcon}>{icon}</span>
          <span style={styles.lifecycleText}>{message}</span>
          <span style={styles.lifecycleTime}>{event.time}</span>
        </div>
      </motion.div>
    );
  }

  // Agent Initialize
  if (category.includes("agent_initialize") || category.includes("agent.initialize")) {
    const agents = data.agents || [];

    return (
      <motion.div style={styles.feedItem} initial={{ opacity: 0, y: -10 }} animate={{ opacity: 1, y: 0 }} transition={{ duration: 0.3 }}>
        <div style={styles.feedInfo}>
          <span style={styles.infoIcon}>🤖</span>
          <div style={styles.infoContent}>
            <span style={styles.infoText}>
              {agents.length} Agents Initialized
            </span>
            {agents.length > 0 && (
              <div style={{ marginTop: 8 }}>
                {agents.map((agent, idx) => (
                  <div key={agent.agent_id} style={{ ...styles.infoDetail, marginTop: idx > 0 ? 6 : 0, display: 'flex', alignItems: 'center', gap: 8 }}>
                    <div style={{
                      width: 24,
                      height: 24,
                      borderRadius: '50%',
                      background: agent.color || '#6B7280',
                      display: 'flex',
                      alignItems: 'center',
                      justifyContent: 'center',
                      color: 'white',
                      fontSize: 11,
                      fontWeight: 700,
                      flexShrink: 0,
                    }}>
                      {agent.avatar || agent.agent_id?.[0]?.toUpperCase() || '?'}
                    </div>
                    <div style={{ flex: 1, minWidth: 0 }}>
                      <div style={{ fontSize: 12, fontWeight: 600, color: 'var(--text-primary)' }}>
                        {agent.agent_id}
                      </div>
                      <div style={{ fontSize: 11, color: 'var(--text-muted)' }}>
                        {agent.model_display_name} ({agent.model})
                      </div>
                    </div>
                  </div>
                ))}
              </div>
            )}
          </div>
          <span style={styles.infoTime}>{event.time}</span>
        </div>
      </motion.div>
    );
  }

  // Pregame Stats
  if (category.includes("pregame_stats") || category.includes("pregame.stats")) {
    const seasonSeries = data.season_series;
    const homeForm = data.home_recent_form;
    const awayForm = data.away_recent_form;

    return (
      <motion.div style={styles.feedItem} initial={{ opacity: 0, y: -10 }} animate={{ opacity: 1, y: 0 }} transition={{ duration: 0.3 }}>
        <div style={styles.feedInfo}>
          <span style={styles.infoIcon}>📊</span>
          <div style={styles.infoContent}>
            <span style={styles.infoText}>Pregame Statistics</span>
            {data.summary && <div style={styles.infoDetail}>{data.summary}</div>}
            {seasonSeries && (
              <div style={styles.infoDetail}>
                <strong>Season Series:</strong> {seasonSeries.away_wins}-{seasonSeries.home_wins}
              </div>
            )}
            {homeForm && (
              <div style={styles.infoDetail}>
                <strong>{homeForm.team_name}:</strong> {homeForm.wins}-{homeForm.losses} (Last {homeForm.last_n} games)
              </div>
            )}
            {awayForm && (
              <div style={styles.infoDetail}>
                <strong>{awayForm.team_name}:</strong> {awayForm.wins}-{awayForm.losses} (Last {awayForm.last_n} games)
              </div>
            )}
          </div>
          <span style={styles.infoTime}>{event.time}</span>
        </div>
      </motion.div>
    );
  }

  // Injury Report
  if (category.includes("injury") || category.includes("injury_report")) {
    const injuredPlayers = data.injured_players || {};
    const teams = Object.keys(injuredPlayers);

    return (
      <motion.div style={styles.feedItem} initial={{ opacity: 0, y: -10 }} animate={{ opacity: 1, y: 0 }} transition={{ duration: 0.3 }}>
        <div style={styles.feedInfo}>
          <span style={styles.infoIcon}>🏥</span>
          <div style={styles.infoContent}>
            <span style={styles.infoText}>Injury Report</span>
            {data.summary && <div style={styles.infoDetail}>{data.summary}</div>}
            {teams.length > 0 && (
              <div style={styles.infoDetail}>
                {teams.map((team, idx) => (
                  <div key={idx} style={{ marginTop: 8 }}>
                    <strong>{team}:</strong> {injuredPlayers[team].join(', ')}
                  </div>
                ))}
              </div>
            )}
          </div>
          <span style={styles.infoTime}>{event.time}</span>
        </div>
      </motion.div>
    );
  }

  // Power Ranking
  if (category.includes("power_ranking") || category.includes("power.ranking")) {
    const rankings = data.rankings || {};
    const sources = Object.keys(rankings);

    // Find rankings for game teams (extract from first source)
    let teamRankings = [];
    if (sources.length > 0 && gameInfo) {
      const firstSource = rankings[sources[0]];
      const gameTeamNames = [gameInfo.homeTeam?.name, gameInfo.awayTeam?.name].filter(Boolean);
      const gameTeamCities = [gameInfo.homeTeam?.city, gameInfo.awayTeam?.city].filter(Boolean);
      const gameTeamAbbrevs = [gameInfo.homeTeam?.abbrev, gameInfo.awayTeam?.abbrev].filter(Boolean);

      teamRankings = firstSource.filter(r =>
        gameTeamNames.some(name => r.team.includes(name)) ||
        gameTeamCities.some(city => r.team.includes(city)) ||
        gameTeamAbbrevs.some(abbrev => r.team.includes(abbrev))
      );
    }

    return (
      <motion.div style={styles.feedItem} initial={{ opacity: 0, y: -10 }} animate={{ opacity: 1, y: 0 }} transition={{ duration: 0.3 }}>
        <div style={styles.feedInfo}>
          <span style={styles.infoIcon}>⭐</span>
          <div style={styles.infoContent}>
            <span style={styles.infoText}>Power Rankings</span>
            {data.summary && <div style={styles.infoDetail}>{data.summary}</div>}
            {teamRankings.length > 0 && (
              <div style={styles.infoDetail}>
                {teamRankings.map((r, idx) => (
                  <div key={idx} style={{ marginTop: 4 }}>
                    #{r.rank} {r.team} ({r.record})
                  </div>
                ))}
              </div>
            )}
            {sources.length > 0 && !teamRankings.length && (
              <div style={styles.infoDetail}>
                Source: {sources.join(', ')}
              </div>
            )}
          </div>
          <span style={styles.infoTime}>{event.time}</span>
        </div>
      </motion.div>
    );
  }

  // Expert Prediction
  if (category.includes("expert_prediction") || category.includes("expert.prediction") || category.includes("prediction")) {
    const predictions = data.predictions || [];

    return (
      <motion.div style={styles.feedItem} initial={{ opacity: 0, y: -10 }} animate={{ opacity: 1, y: 0 }} transition={{ duration: 0.3 }}>
        <div style={styles.feedInfo}>
          <span style={styles.infoIcon}>🎯</span>
          <div style={styles.infoContent}>
            <span style={styles.infoText}>Expert Predictions</span>
            {data.summary && <div style={styles.infoDetail}>{data.summary}</div>}
            {predictions.slice(0, 3).map((pred, idx) => (
              <div key={idx} style={{ ...styles.infoDetail, marginTop: 8 }}>
                <strong>{pred.expert || pred.source}:</strong> {pred.prediction}
                {pred.reasoning && (
                  <div style={{ fontSize: 11, color: 'var(--text-muted)', marginTop: 2 }}>
                    {pred.reasoning}
                  </div>
                )}
              </div>
            ))}
            {predictions.length > 3 && (
              <div style={{ fontSize: 11, color: 'var(--text-muted)', marginTop: 4 }}>
                +{predictions.length - 3} more predictions
              </div>
            )}
          </div>
          <span style={styles.infoTime}>{event.time}</span>
        </div>
      </motion.div>
    );
  }

  // Play-by-play events
  if (category.includes("play") && data.description) {
    return (
      <motion.div style={styles.feedItem} initial={{ opacity: 0, y: -10 }} animate={{ opacity: 1, y: 0 }} transition={{ duration: 0.3 }}>
        <div style={styles.feedPlay}>
          <div style={styles.playIcon}>🏀</div>
          <div style={styles.playContent}>
            <div style={styles.playText}>{data.description}</div>
            <div style={styles.playMeta}>
              {data.team && <span style={styles.playTeam}>{data.team}</span>}
              {data.player && <span style={styles.playPlayer}>{data.player}</span>}
              <span style={styles.playTime}>{event.time}</span>
            </div>
          </div>
        </div>
      </motion.div>
    );
  }

  // Odds updates
  if (category.includes("odds")) {
    return (
      <motion.div style={styles.feedItem} initial={{ opacity: 0, y: -10 }} animate={{ opacity: 1, y: 0 }} transition={{ duration: 0.3 }}>
        <div style={styles.feedOdds}>
          <span style={styles.oddsIcon}>📊</span>
          <span style={styles.oddsText}>Odds updated</span>
          {data.home_ml && <span style={styles.oddsValue}>Home: {data.home_ml}</span>}
          {data.away_ml && <span style={styles.oddsValue}>Away: {data.away_ml}</span>}
          <span style={styles.oddsTime}>{event.time}</span>
        </div>
      </motion.div>
    );
  }

  // Default: Generic event with expandable details
  return (
    <motion.div style={styles.feedItem} initial={{ opacity: 0, y: -10 }} animate={{ opacity: 1, y: 0 }} transition={{ duration: 0.3 }}>
      <div style={styles.feedSimple}>
        <div style={styles.simpleIcon}>
          <Clock size={12} />
        </div>
        <div style={styles.simpleContent}>
          <div style={styles.simpleHeader}>
            <span style={styles.simpleType}>{event.displayType}</span>
            <span style={styles.simpleTime}>{event.time}</span>
          </div>
          {event.description && (
            <div style={styles.simpleDesc}>{event.description}</div>
          )}
        </div>
        {data && Object.keys(data).length > 3 && (
          <button
            onClick={() => setShowDetails(!showDetails)}
            style={styles.simpleToggle}
          >
            {showDetails ? "−" : "+"}
          </button>
        )}
      </div>
      {showDetails && (
        <div style={styles.simpleDetails}>
          {JSON.stringify(data, null, 2)}
        </div>
      )}
    </motion.div>
  );
}

function AgentCard({ agent }) {
  const hasFinalStats = agent.finalStats && Object.keys(agent.finalStats).length > 0;

  return (
    <div style={styles.agentCard}>
      <div style={{
        ...styles.agentAvatar,
        background: agent.color || "#6B7280",
      }}>
        {agent.avatar || agent.name?.charAt(0) || "A"}
      </div>
      <div style={styles.agentInfo}>
        <div style={styles.agentName}>{agent.name || "Unknown Agent"}</div>

        {/* Show final statistics if game is completed */}
        {hasFinalStats ? (
          <div style={styles.agentStats}>
            <div style={styles.agentStatRow}>
              <span style={styles.agentStatLabel}>Net Profit:</span>
              <span style={{
                ...styles.agentStatValue,
                color: parseFloat(agent.finalStats.net_profit) >= 0 ? '#10B981' : '#EF4444',
              }}>
                ${parseFloat(agent.finalStats.net_profit || 0).toFixed(2)}
              </span>
            </div>
            <div style={styles.agentStatRow}>
              <span style={styles.agentStatLabel}>Record:</span>
              <span style={styles.agentStatValue}>
                {agent.finalStats.wins || 0}W - {agent.finalStats.losses || 0}L
              </span>
            </div>
            <div style={styles.agentStatRow}>
              <span style={styles.agentStatLabel}>ROI:</span>
              <span style={styles.agentStatValue}>
                {(parseFloat(agent.finalStats.roi || 0) * 100).toFixed(1)}%
              </span>
            </div>
            <div style={styles.agentStatRow}>
              <span style={styles.agentStatLabel}>Total Wagered:</span>
              <span style={styles.agentStatValue}>
                ${parseFloat(agent.finalStats.total_wagered || 0).toFixed(2)}
              </span>
            </div>
          </div>
        ) : (
          /* Show bets for live/ongoing games */
          <div style={styles.agentBets}>
            {agent.teamBets && Object.entries(agent.teamBets).map(([team, amount]) => (
              <div key={team} style={styles.agentBetRow}>
                <span style={styles.agentBetTeam}>{team}</span>
                <span style={styles.agentBetAmount}>${amount}</span>
              </div>
            ))}
            {(!agent.teamBets || Object.keys(agent.teamBets).length === 0) && (
              <div style={styles.agentNoBets}>No bets yet</div>
            )}
          </div>
        )}
      </div>
    </div>
  );
}

function AgentRankings({ agents }) {
  // Filter agents with final stats and sort by net profit (descending)
  const rankedAgents = agents
    .filter(agent => agent.finalStats && Object.keys(agent.finalStats).length > 0)
    .sort((a, b) => {
      // Sort by net profit (winning amount) descending
      const profitA = parseFloat(a.finalStats.net_profit || 0);
      const profitB = parseFloat(b.finalStats.net_profit || 0);
      return profitB - profitA;
    });

  if (rankedAgents.length === 0) {
    return null;
  }

  return (
    <motion.div
      style={styles.rankingsCard}
      initial={{ opacity: 0, y: -10 }}
      animate={{ opacity: 1, y: 0 }}
      transition={{ duration: 0.3 }}
    >
      <div style={styles.rankingsHeader}>
        <TrendingUp size={18} />
        <span>AGENT RANKINGS</span>
      </div>
      <div style={styles.rankingsList}>
        {rankedAgents.map((agent, index) => (
          <motion.div
            key={agent.id}
            style={styles.rankingRow}
            initial={{ opacity: 0, x: -20 }}
            animate={{ opacity: 1, x: 0 }}
            transition={{ duration: 0.3, delay: index * 0.1 }}
          >
            <div style={styles.rankingPosition}>
              <div style={{
                ...styles.rankingMedal,
                background: index === 0 ? '#FFD700' : index === 1 ? '#C0C0C0' : index === 2 ? '#CD7F32' : 'var(--bg-tertiary)',
              }}>
                {index + 1}
              </div>
            </div>
            <div style={{
              ...styles.rankingAvatar,
              background: agent.color || "#6B7280",
            }}>
              {agent.avatar || agent.name?.charAt(0) || "A"}
            </div>
            <div style={styles.rankingInfo}>
              <div style={styles.rankingName}>{agent.name || "Unknown Agent"}</div>
              <div style={styles.rankingRecord}>
                {agent.finalStats.wins || 0}W - {agent.finalStats.losses || 0}L
              </div>
            </div>
            <div style={styles.rankingStats}>
              <div style={{
                ...styles.rankingProfit,
                color: parseFloat(agent.finalStats.net_profit) >= 0 ? '#10B981' : '#EF4444',
              }}>
                {parseFloat(agent.finalStats.net_profit) >= 0 ? '+' : ''}
                ${parseFloat(agent.finalStats.net_profit || 0).toFixed(2)}
              </div>
              <div style={styles.rankingRoi}>
                ROI: {(parseFloat(agent.finalStats.roi || 0) * 100).toFixed(1)}%
              </div>
            </div>
          </motion.div>
        ))}
      </div>
    </motion.div>
  );
}

// Parse game info from items
function parseGameInfo(items) {
  const info = {
    homeTeam: null,
    awayTeam: null,
    homeScore: 0,
    awayScore: 0,
    period: null,
    gameClock: null,
    status: "unknown",
    league: null,
    agents: [],
  };

  if (!items || items.length === 0) return null;

  const agentsMap = new Map();

  for (const item of items) {
    const category = item.category || "";
    const data = item.data || {};

    // Game initialization
    if (category === "game_initialize") {
      // Determine league from the sport field
      if (data.sport) {
        info.league = data.sport.toUpperCase();
      }
      if (data.home_team) {
        info.homeTeam = data.home_team;
      }
      if (data.away_team) {
        info.awayTeam = data.away_team;
      }
    }

    // Game updates
    if (category === "game_update") {
      info.homeScore = data.home_score || 0;
      info.awayScore = data.away_score || 0;
      info.period = data.period;
      info.gameClock = data.game_clock;
      // Only set to live if not already completed (game_result may come before final game_update)
      if (info.status !== "completed") {
        info.status = "live";
      }
    }

    // Game result
    if (category === "game_result") {
      info.status = "completed";
    }

    // Track agents and their bets
    if (category.includes("bet") && data.agent_id) {
      if (!agentsMap.has(data.agent_id)) {
        agentsMap.set(data.agent_id, {
          id: data.agent_id,
          name: data.agent_id,
          avatar: data.agent_id?.[0]?.toUpperCase() || "A",
          color: data.color || "#6B7280",
          teamBets: {},
          finalStats: null, // Will be populated from broker.final_stats
        });
      }
      const agent = agentsMap.get(data.agent_id);

      // Parse team from selection (e.g., "LAL_ML" -> "LAL")
      const team = data.selection ? data.selection.split("_")[0] : "Unknown";
      const amount = parseFloat(data.amount) || 0;

      if (team) {
        agent.teamBets[team] = (agent.teamBets[team] || 0) + amount;
      }
    }

    // Extract broker final statistics
    if (category.includes("final_stats") && data.statistics) {
      // data.statistics is a JSON string of Dict[str, Statistics]
      try {
        const statistics = typeof data.statistics === 'string' ? JSON.parse(data.statistics) : data.statistics;

        // Merge statistics into agents
        for (const [agentId, stats] of Object.entries(statistics)) {
          if (!agentsMap.has(agentId)) {
            agentsMap.set(agentId, {
              id: agentId,
              name: agentId,
              avatar: agentId?.[0]?.toUpperCase() || "A",
              color: "#6B7280",
              teamBets: {},
              finalStats: stats,
            });
          } else {
            agentsMap.get(agentId).finalStats = stats;
          }
        }
      } catch (e) {
        console.error("Failed to parse broker statistics:", e);
      }
    }
  }

  info.agents = Array.from(agentsMap.values());
  return info;
}

// Parse events for timeline
function parseEvents(items) {
  if (!items || items.length === 0) return [];

  let lastGameScore = null;
  let lastLifecyclePhase = null;

  return items
    .filter(item => {
      const category = item.category || "";

      // Filter out odds updates
      if (category.includes("odds")) return false;

      // Filter out consecutive game updates with the same score
      if (category === "game_update") {
        const data = item.data || item;
        const currentScore = `${data.home_score || 0}-${data.away_score || 0}`;

        if (lastGameScore === currentScore) {
          return false; // Skip this update, score hasn't changed
        }

        lastGameScore = currentScore;
      }

      // Filter out consecutive lifecycle events with the same phase
      if (category.includes("lifecycle") || category.includes("trial.") ||
          category.includes("game_initialize") || category.includes("game_result")) {
        const data = item.data || item;
        const currentPhase = data.phase || category;

        if (lastLifecyclePhase === currentPhase) {
          return false; // Skip duplicate lifecycle event
        }

        lastLifecyclePhase = currentPhase;
      }

      return true;
    })
    .map(item => {
      const category = item.category || "";
      // Use item.data if it exists, otherwise use the item itself (excluding category)
      const data = item.data || { ...item };
      delete data.category; // Remove category from data to avoid duplication

      // Format event type for display
      const displayType = category
        .replace(/_/g, " ")
        .split(" ")
        .map(word => word.charAt(0).toUpperCase() + word.slice(1))
        .join(" ");

      // Get time - try different timestamp field names
      // OpenTelemetry spans can have different timestamp formats
      let timestamp = data.timestamp || data.start_time || data.time || data.end_time;

      let time = "N/A";
      if (timestamp) {
        // Try different timestamp formats
        if (typeof timestamp === 'number') {
          // Assume microseconds (OpenTelemetry format)
          const date = new Date(timestamp / 1000);
          time = date.toLocaleTimeString();
        } else if (typeof timestamp === 'string') {
          // ISO string format
          const date = new Date(timestamp);
          time = date.toLocaleTimeString();
        }
      }

      // Create description based on category
      let description = "";
      if (category.includes("bet") && data.amount && data.team) {
        description = `${data.agent_name || data.persona || "Agent"} bet $${data.amount} on ${data.team}`;
      } else if (category === "game_update" && data.home_score !== undefined) {
        description = `Score: ${data.home_score} - ${data.away_score}`;
      }

      return {
        type: category,
        displayType,
        time,
        description,
        data: data,
      };
    });
}

const styles = {
  container: {
    minHeight: "100vh",
    display: "flex",
    flexDirection: "column",
    background: "var(--bg-primary)",
  },
  loadingScreen: {
    minHeight: "100vh",
    display: "flex",
    flexDirection: "column",
    alignItems: "center",
    justifyContent: "center",
    gap: 20,
  },
  loadingSpinner: {
    width: 60,
    height: 60,
    border: "4px solid var(--border-default)",
    borderTop: "4px solid var(--accent-primary)",
    borderRadius: "50%",
    animation: "spin 1s linear infinite",
  },
  loadingText: {
    fontSize: 14,
    letterSpacing: "0.2em",
    color: "var(--text-secondary)",
    fontWeight: 600,
  },
  errorText: {
    fontSize: 16,
    color: "var(--text-secondary)",
    letterSpacing: "0.1em",
  },
  trialIdText: {
    fontSize: 12,
    color: "var(--text-muted)",
  },
  header: {
    padding: "16px 32px",
    borderBottom: "1px solid var(--border-subtle)",
    background: "var(--bg-secondary)",
    backdropFilter: "blur(20px)",
  },
  backButton: {
    display: "flex",
    alignItems: "center",
    gap: 8,
    padding: "10px 20px",
    background: "transparent",
    border: "1px solid var(--border-default)",
    borderRadius: 8,
    color: "var(--text-secondary)",
    cursor: "pointer",
    fontSize: 13,
    letterSpacing: "0.1em",
    fontWeight: 600,
    transition: "all 0.2s",
  },
  main: {
    flex: 1,
    padding: "24px 32px",
    maxWidth: 1600,
    width: "100%",
    margin: "0 auto",
  },
  mainGrid: {
    display: "grid",
    gridTemplateColumns: "1fr 360px",
    gap: 24,
    minHeight: 0,
  },
  gameSection: {
    display: "flex",
    flexDirection: "column",
    gap: 24,
  },
  scoreboardCard: {
    background: "var(--bg-card)",
    border: "1px solid var(--border-default)",
    borderRadius: 16,
    padding: 32,
  },
  scoreboardHeader: {
    display: "flex",
    justifyContent: "space-between",
    alignItems: "center",
    marginBottom: 32,
  },
  leagueBadge: {
    padding: "8px 16px",
    background: "var(--accent-primary)",
    color: "white",
    borderRadius: 8,
    fontSize: 12,
    fontWeight: 700,
    letterSpacing: "0.1em",
  },
  liveIndicator: {
    display: "flex",
    alignItems: "center",
    gap: 8,
    padding: "8px 16px",
    background: "rgba(16, 185, 129, 0.1)",
    border: "1px solid rgba(16, 185, 129, 0.3)",
    borderRadius: 8,
    fontSize: 12,
    color: "#10B981",
    fontWeight: 600,
    letterSpacing: "0.1em",
  },
  liveDot: {
    width: 8,
    height: 8,
    borderRadius: "50%",
    background: "#10B981",
    animation: "pulse 2s ease-in-out infinite",
  },
  teamsContainer: {
    display: "flex",
    justifyContent: "space-between",
    alignItems: "center",
    gap: 40,
  },
  teamCard: {
    display: "flex",
    flexDirection: "column",
    alignItems: "center",
    gap: 16,
    flex: 1,
  },
  teamLogo: {
    width: 80,
    height: 80,
    borderRadius: "50%",
    display: "flex",
    alignItems: "center",
    justifyContent: "center",
    boxShadow: "0 8px 32px rgba(0, 0, 0, 0.3)",
  },
  teamLogoImg: {
    width: "70%",
    height: "70%",
    objectFit: "contain",
  },
  teamLogoText: {
    fontSize: 28,
    fontWeight: 700,
    color: "white",
    textShadow: "0 2px 8px rgba(0, 0, 0, 0.5)",
  },
  teamLogoPlaceholder: {
    width: 80,
    height: 80,
    borderRadius: "50%",
    display: "flex",
    alignItems: "center",
    justifyContent: "center",
    background: "#6B7280",
    fontSize: 32,
    fontWeight: 700,
    color: "white",
  },
  teamInfo: {
    textAlign: "center",
  },
  teamCity: {
    fontSize: 12,
    color: "var(--text-muted)",
    textTransform: "uppercase",
    letterSpacing: "0.1em",
    marginBottom: 4,
  },
  teamName: {
    fontSize: 20,
    fontWeight: 700,
    color: "var(--text-primary)",
  },
  teamScore: {
    fontSize: 56,
    fontWeight: 800,
    color: "var(--accent-primary)",
    lineHeight: 1,
  },
  vsSection: {
    display: "flex",
    flexDirection: "column",
    alignItems: "center",
    gap: 12,
  },
  vsText: {
    fontSize: 16,
    fontWeight: 700,
    color: "var(--text-muted)",
    letterSpacing: "0.1em",
  },
  gameStatus: {
    textAlign: "center",
  },
  period: {
    fontSize: 14,
    color: "var(--text-secondary)",
    marginBottom: 4,
  },
  gameClock: {
    fontSize: 12,
    color: "var(--text-muted)",
    fontFamily: "monospace",
  },
  timelineCard: {
    background: "var(--bg-card)",
    border: "1px solid var(--border-default)",
    borderRadius: 16,
    padding: 24,
  },
  timelineHeader: {
    display: "flex",
    alignItems: "center",
    gap: 12,
    marginBottom: 20,
    fontSize: 14,
    fontWeight: 600,
    letterSpacing: "0.1em",
    color: "var(--text-primary)",
  },
  eventCount: {
    marginLeft: "auto",
    fontSize: 12,
    color: "var(--text-muted)",
  },
  timelineList: {
    display: "flex",
    flexDirection: "column",
    gap: 12,
    maxHeight: 600,
    overflowY: "auto",
  },
  eventItem: {
    display: "flex",
    gap: 12,
    padding: 12,
    background: "var(--bg-tertiary)",
    border: "1px solid var(--border-subtle)",
    borderRadius: 8,
  },
  eventIcon: {
    width: 32,
    height: 32,
    borderRadius: 8,
    display: "flex",
    alignItems: "center",
    justifyContent: "center",
    color: "white",
    flexShrink: 0,
  },
  eventContent: {
    flex: 1,
    minWidth: 0,
  },
  eventHeader: {
    display: "flex",
    justifyContent: "space-between",
    alignItems: "center",
    marginBottom: 4,
  },
  eventType: {
    fontSize: 13,
    fontWeight: 600,
    color: "var(--text-primary)",
  },
  eventTime: {
    fontSize: 11,
    color: "var(--text-muted)",
    fontFamily: "monospace",
  },
  eventDescription: {
    fontSize: 12,
    color: "var(--text-secondary)",
    marginBottom: 8,
  },
  detailsToggle: {
    fontSize: 11,
    padding: "4px 8px",
    background: "var(--bg-primary)",
    border: "1px solid var(--border-default)",
    borderRadius: 4,
    color: "var(--text-secondary)",
    cursor: "pointer",
  },
  eventData: {
    marginTop: 8,
    padding: 8,
    background: "var(--bg-primary)",
    borderRadius: 4,
    fontSize: 10,
    fontFamily: "monospace",
    color: "var(--text-secondary)",
    whiteSpace: "pre-wrap",
    wordBreak: "break-word",
    maxHeight: 200,
    overflowY: "auto",
  },
  sidebar: {
    display: "flex",
    flexDirection: "column",
    gap: 24,
  },
  sidebarCard: {
    background: "var(--bg-card)",
    border: "1px solid var(--border-default)",
    borderRadius: 16,
    padding: 20,
  },
  sidebarHeader: {
    display: "flex",
    alignItems: "center",
    gap: 10,
    marginBottom: 16,
    fontSize: 13,
    fontWeight: 600,
    letterSpacing: "0.1em",
    color: "var(--text-primary)",
  },
  agentsList: {
    display: "flex",
    flexDirection: "column",
    gap: 12,
  },
  agentCard: {
    display: "flex",
    alignItems: "center",
    gap: 12,
    padding: 12,
    background: "var(--bg-tertiary)",
    border: "1px solid var(--border-subtle)",
    borderRadius: 8,
  },
  agentAvatar: {
    width: 40,
    height: 40,
    borderRadius: "50%",
    display: "flex",
    alignItems: "center",
    justifyContent: "center",
    color: "white",
    fontSize: 16,
    fontWeight: 700,
    flexShrink: 0,
  },
  agentInfo: {
    flex: 1,
    minWidth: 0,
  },
  agentName: {
    fontSize: 14,
    fontWeight: 600,
    color: "var(--text-primary)",
    marginBottom: 4,
  },
  agentStats: {
    display: "flex",
    flexDirection: "column",
    gap: 6,
    fontSize: 12,
  },
  agentStatRow: {
    display: "flex",
    justifyContent: "space-between",
    alignItems: "center",
  },
  agentStatLabel: {
    fontSize: 11,
    color: "var(--text-muted)",
  },
  agentStatValue: {
    fontSize: 12,
    fontWeight: 600,
    color: "var(--text-primary)",
  },
  noAgents: {
    padding: 20,
    textAlign: "center",
    fontSize: 13,
    color: "var(--text-muted)",
  },
  // Compact Scoreboard Styles
  compactScoreboard: {
    display: "flex",
    alignItems: "center",
    justifyContent: "space-between",
    padding: "16px 24px",
    gap: 24,
  },
  compactTeam: {
    display: "flex",
    alignItems: "center",
    gap: 12,
    flex: 1,
  },
  compactTeamName: {
    fontSize: 16,
    fontWeight: 600,
    color: "var(--text-primary)",
  },
  compactScore: {
    fontSize: 32,
    fontWeight: 800,
    color: "var(--accent-primary)",
  },
  compactDivider: {
    display: "flex",
    flexDirection: "column",
    alignItems: "center",
    gap: 4,
  },
  compactLive: {
    display: "flex",
    alignItems: "center",
    gap: 6,
    padding: "4px 12px",
    background: "rgba(16, 185, 129, 0.1)",
    borderRadius: 6,
  },
  compactPeriod: {
    fontSize: 12,
    fontWeight: 600,
    color: "#10B981",
  },
  compactClock: {
    fontSize: 11,
    fontWeight: 500,
    color: "#10B981",
    fontFamily: "monospace",
  },
  compactStatus: {
    fontSize: 12,
    fontWeight: 600,
    color: "var(--text-muted)",
    textTransform: "uppercase",
  },
  teamLogoSmall: {
    width: 40,
    height: 40,
    borderRadius: "50%",
    display: "flex",
    alignItems: "center",
    justifyContent: "center",
    boxShadow: "0 4px 12px rgba(0, 0, 0, 0.2)",
  },
  teamLogoImgSmall: {
    width: "70%",
    height: "70%",
    objectFit: "contain",
  },
  teamLogoTextSmall: {
    fontSize: 16,
    fontWeight: 700,
    color: "white",
  },
  // Social Feed Styles
  feedCard: {
    background: "var(--bg-card)",
    border: "1px solid var(--border-default)",
    borderRadius: 16,
    padding: 24,
  },
  feedHeader: {
    display: "flex",
    alignItems: "center",
    gap: 12,
    marginBottom: 20,
    fontSize: 14,
    fontWeight: 600,
    letterSpacing: "0.1em",
    color: "var(--text-primary)",
  },
  feedList: {
    display: "flex",
    flexDirection: "column",
    gap: 16,
    maxHeight: 700,
    overflowY: "auto",
  },
  feedItem: {
    width: "100%",
  },
  feedPost: {
    background: "var(--bg-tertiary)",
    border: "1px solid var(--border-subtle)",
    borderRadius: 12,
    padding: 16,
  },
  postHeader: {
    display: "flex",
    alignItems: "center",
    gap: 12,
    marginBottom: 12,
  },
  postAvatar: {
    width: 40,
    height: 40,
    borderRadius: "50%",
    display: "flex",
    alignItems: "center",
    justifyContent: "center",
    color: "white",
    fontSize: 16,
    fontWeight: 700,
    flexShrink: 0,
  },
  postMeta: {
    flex: 1,
  },
  postAuthor: {
    fontSize: 14,
    fontWeight: 600,
    color: "var(--text-primary)",
    marginBottom: 2,
  },
  postTime: {
    fontSize: 11,
    color: "var(--text-muted)",
  },
  postContent: {
    fontSize: 15,
    lineHeight: 1.5,
    color: "var(--text-primary)",
    fontStyle: "italic",
    padding: "8px 12px",
    background: "var(--bg-primary)",
    borderRadius: 8,
    borderLeft: "3px solid var(--accent-primary)",
  },
  postFooter: {
    marginTop: 8,
    fontSize: 12,
    color: "var(--text-secondary)",
  },
  betContent: {
    display: "flex",
    alignItems: "center",
    gap: 8,
    fontSize: 14,
  },
  betAction: {
    color: "var(--text-secondary)",
  },
  betAmount: {
    fontSize: 16,
    fontWeight: 700,
    color: "#10B981",
  },
  betTeam: {
    color: "var(--text-primary)",
    fontWeight: 600,
  },
  feedUpdate: {
    display: "flex",
    alignItems: "center",
    gap: 12,
    padding: 12,
    background: "var(--bg-tertiary)",
    border: "1px solid var(--border-subtle)",
    borderRadius: 8,
  },
  updateIcon: {
    width: 28,
    height: 28,
    borderRadius: "50%",
    background: "#8B5CF6",
    display: "flex",
    alignItems: "center",
    justifyContent: "center",
    color: "white",
  },
  updateText: {
    display: "flex",
    alignItems: "center",
    gap: 12,
    flex: 1,
  },
  updateScore: {
    fontSize: 16,
    fontWeight: 700,
    color: "var(--text-primary)",
  },
  updateTime: {
    fontSize: 11,
    color: "var(--text-muted)",
    marginLeft: "auto",
  },
  updatePeriod: {
    fontSize: 12,
    fontWeight: 600,
    color: "var(--text-secondary)",
    padding: "2px 6px",
    background: "var(--bg-primary)",
    borderRadius: 4,
  },
  updateClock: {
    fontSize: 11,
    fontFamily: "monospace",
    color: "var(--text-muted)",
  },
  feedLifecycle: {
    display: "flex",
    alignItems: "center",
    gap: 10,
    padding: 10,
    background: "rgba(139, 92, 246, 0.1)",
    border: "1px solid rgba(139, 92, 246, 0.3)",
    borderRadius: 8,
  },
  lifecycleIcon: {
    fontSize: 18,
  },
  lifecycleText: {
    flex: 1,
    fontSize: 13,
    fontWeight: 600,
    color: "#8B5CF6",
  },
  lifecycleTime: {
    fontSize: 11,
    color: "var(--text-muted)",
  },
  feedInfo: {
    display: "flex",
    alignItems: "center",
    gap: 10,
    padding: 12,
    background: "var(--bg-tertiary)",
    border: "1px solid var(--border-subtle)",
    borderRadius: 8,
  },
  infoIcon: {
    fontSize: 18,
    flexShrink: 0,
  },
  infoContent: {
    flex: 1,
    display: "flex",
    flexDirection: "column",
    gap: 4,
  },
  infoText: {
    fontSize: 13,
    fontWeight: 600,
    color: "var(--text-primary)",
  },
  infoDetail: {
    fontSize: 12,
    color: "var(--text-secondary)",
    lineHeight: 1.4,
  },
  infoTime: {
    fontSize: 11,
    color: "var(--text-muted)",
    flexShrink: 0,
  },
  feedPlay: {
    display: "flex",
    alignItems: "flex-start",
    gap: 12,
    padding: 12,
    background: "var(--bg-tertiary)",
    border: "1px solid var(--border-subtle)",
    borderRadius: 8,
  },
  playIcon: {
    fontSize: 20,
    marginTop: 2,
  },
  playContent: {
    flex: 1,
  },
  playText: {
    fontSize: 13,
    color: "var(--text-primary)",
    marginBottom: 6,
  },
  playMeta: {
    display: "flex",
    alignItems: "center",
    gap: 8,
    fontSize: 11,
  },
  playTeam: {
    fontWeight: 600,
    color: "var(--accent-primary)",
  },
  playPlayer: {
    color: "var(--text-secondary)",
  },
  playTime: {
    color: "var(--text-muted)",
    marginLeft: "auto",
  },
  feedOdds: {
    display: "flex",
    alignItems: "center",
    gap: 10,
    padding: 10,
    background: "var(--bg-tertiary)",
    border: "1px solid var(--border-subtle)",
    borderRadius: 8,
  },
  oddsIcon: {
    fontSize: 16,
  },
  oddsText: {
    fontSize: 13,
    color: "var(--text-secondary)",
  },
  oddsValue: {
    fontSize: 12,
    fontWeight: 600,
    color: "var(--text-primary)",
    padding: "2px 6px",
    background: "var(--bg-primary)",
    borderRadius: 4,
  },
  oddsTime: {
    fontSize: 11,
    color: "var(--text-muted)",
    marginLeft: "auto",
  },
  feedSimple: {
    display: "flex",
    alignItems: "center",
    gap: 10,
    padding: 10,
    background: "var(--bg-tertiary)",
    border: "1px solid var(--border-subtle)",
    borderRadius: 8,
  },
  simpleIcon: {
    width: 24,
    height: 24,
    borderRadius: "50%",
    background: "#6B7280",
    display: "flex",
    alignItems: "center",
    justifyContent: "center",
    color: "white",
  },
  simpleContent: {
    flex: 1,
    minWidth: 0,
  },
  simpleHeader: {
    display: "flex",
    justifyContent: "space-between",
    alignItems: "center",
    marginBottom: 4,
  },
  simpleType: {
    fontSize: 13,
    fontWeight: 600,
    color: "var(--text-primary)",
  },
  simpleDesc: {
    fontSize: 12,
    color: "var(--text-secondary)",
    marginTop: 4,
  },
  simpleText: {
    flex: 1,
    display: "flex",
    alignItems: "center",
    gap: 12,
    fontSize: 13,
    color: "var(--text-secondary)",
  },
  simpleTime: {
    fontSize: 11,
    color: "var(--text-muted)",
    marginLeft: "auto",
  },
  simpleToggle: {
    width: 24,
    height: 24,
    background: "var(--bg-primary)",
    border: "1px solid var(--border-default)",
    borderRadius: 4,
    color: "var(--text-secondary)",
    cursor: "pointer",
    fontSize: 14,
    fontWeight: 700,
  },
  simpleDetails: {
    width: "100%",
    marginTop: 8,
    padding: 8,
    background: "var(--bg-primary)",
    borderRadius: 4,
    fontSize: 10,
    fontFamily: "monospace",
    color: "var(--text-secondary)",
    whiteSpace: "pre-wrap",
    wordBreak: "break-word",
    maxHeight: 150,
    overflowY: "auto",
  },
  // Agent Bets Styles
  agentBets: {
    display: "flex",
    flexDirection: "column",
    gap: 6,
    marginTop: 8,
  },
  agentBetRow: {
    display: "flex",
    justifyContent: "space-between",
    alignItems: "center",
    padding: "4px 8px",
    background: "var(--bg-primary)",
    borderRadius: 4,
  },
  agentBetTeam: {
    fontSize: 12,
    fontWeight: 600,
    color: "var(--text-primary)",
  },
  agentBetAmount: {
    fontSize: 13,
    fontWeight: 700,
    color: "#10B981",
  },
  agentNoBets: {
    fontSize: 12,
    color: "var(--text-muted)",
    fontStyle: "italic",
  },
  // Agent Rankings Styles
  rankingsCard: {
    background: "var(--bg-card)",
    border: "1px solid var(--border-default)",
    borderRadius: 16,
    padding: 20,
    marginBottom: 16,
  },
  rankingsHeader: {
    display: "flex",
    alignItems: "center",
    gap: 10,
    marginBottom: 16,
    fontSize: 14,
    fontWeight: 700,
    color: "var(--text-primary)",
    textTransform: "uppercase",
    letterSpacing: "0.5px",
  },
  rankingsList: {
    display: "flex",
    flexDirection: "column",
    gap: 12,
  },
  rankingRow: {
    display: "flex",
    alignItems: "center",
    gap: 12,
    padding: 12,
    background: "var(--bg-tertiary)",
    border: "1px solid var(--border-subtle)",
    borderRadius: 12,
    transition: "all 0.2s ease",
  },
  rankingPosition: {
    minWidth: 40,
    display: "flex",
    justifyContent: "center",
  },
  rankingMedal: {
    width: 32,
    height: 32,
    borderRadius: "50%",
    display: "flex",
    alignItems: "center",
    justifyContent: "center",
    fontSize: 14,
    fontWeight: 700,
    color: "#1F2937",
    boxShadow: "0 2px 8px rgba(0, 0, 0, 0.15)",
  },
  rankingAvatar: {
    width: 40,
    height: 40,
    borderRadius: "50%",
    display: "flex",
    alignItems: "center",
    justifyContent: "center",
    color: "white",
    fontSize: 16,
    fontWeight: 700,
    flexShrink: 0,
  },
  rankingInfo: {
    flex: 1,
    minWidth: 0,
  },
  rankingName: {
    fontSize: 14,
    fontWeight: 600,
    color: "var(--text-primary)",
    marginBottom: 2,
  },
  rankingRecord: {
    fontSize: 12,
    color: "var(--text-muted)",
    fontWeight: 500,
  },
  rankingStats: {
    textAlign: "right",
  },
  rankingProfit: {
    fontSize: 14,
    fontWeight: 700,
    marginBottom: 2,
  },
  rankingRoi: {
    fontSize: 11,
    color: "var(--text-muted)",
  },
};
