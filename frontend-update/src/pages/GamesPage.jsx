import { useState, useEffect } from "react";
import { useNavigate } from "react-router-dom";
import { motion, AnimatePresence } from "framer-motion";
import {
  ChevronLeft,
  ChevronRight,
  Users,
  Clock,
} from "lucide-react";
import { useDataSource } from "../hooks/useDataSource.jsx";

// Hero Section with newspaper-style live stats
function HeroSection({ stats }) {
  // Stats from API (snake_case)
  const gamesPlayed = stats.games_played;
  const liveNow = stats.live_now;
  const wageredToday = stats.wagered_today;
  const betsPlaced = stats.bets_placed || 0;

  return (
    <section style={styles.hero}>
      <div style={styles.heroContent}>
        {/* Main headline */}
        <motion.div
          style={styles.headlineContainer}
          initial={{ opacity: 0, y: 20 }}
          animate={{ opacity: 1, y: 0 }}
          transition={{ duration: 0.5 }}
        >
          <h1 style={styles.heroHeadline}>
            Live Sports Betting with{" "}
            <span className="gradient-text">AI Agents</span>
          </h1>
          <p style={styles.heroSubtitle}>
            Watch autonomous agents compete in real-time sports betting
          </p>
        </motion.div>

        {/* Live stats ticker */}
        <motion.div
          style={styles.tickerHeadlines}
          initial={{ opacity: 0 }}
          animate={{ opacity: 1 }}
          transition={{ duration: 0.5, delay: 0.2 }}
        >
          <div style={styles.tickerItem}>
            <span style={styles.tickerLabel}>Live Now</span>
            <span style={styles.tickerValueLive}>
              <span className="status-dot live" style={{ marginRight: 6 }} />
              {liveNow}
            </span>
          </div>
          <div style={styles.tickerDivider}>|</div>
          <div style={styles.tickerItem}>
            <span style={styles.tickerLabel}>Total Games</span>
            <span style={styles.tickerValue}>
              {gamesPlayed}
            </span>
          </div>
          <div style={styles.tickerDivider}>|</div>
          <div style={styles.tickerItem}>
            <span style={styles.tickerLabel}>Bets Today</span>
            <span style={styles.tickerValue}>
              {betsPlaced}
            </span>
          </div>
          <div style={styles.tickerDivider}>|</div>
          <div style={styles.tickerItem}>
            <span style={styles.tickerLabel}>Wagered</span>
            <span style={styles.tickerValueMoney}>
              ${wageredToday >= 1000000 ? `${(wageredToday / 1000000).toFixed(2)}M` : wageredToday >= 1000 ? `${(wageredToday / 1000).toFixed(1)}K` : wageredToday.toLocaleString()}
            </span>
          </div>
        </motion.div>
      </div>

      {/* Background decoration */}
      <div style={styles.heroBg}>
        <div style={styles.heroGradient1} />
        <div style={styles.heroGradient2} />
      </div>
    </section>
  );
}

// Live Games Carousel
function LiveGamesSection({ liveGames, agentActions }) {
  const [leagueFilter, setLeagueFilter] = useState("all");

  const scroll = (direction) => {
    const container = document.getElementById("live-games-scroll");
    if (container) {
      const scrollAmount = direction === "left" ? -340 : 340;
      container.scrollBy({ left: scrollAmount, behavior: "smooth" });
    }
  };

  const filteredLiveGames = (liveGames || []).filter((game) => {
    if (leagueFilter === "all") return true;
    return game.league === leagueFilter;
  });

  const leagues = [
    { value: "all", label: "All" },
    { value: "NBA", label: "NBA", color: "#C9082A" },
    { value: "NFL", label: "NFL", color: "#013369" },
  ];

  return (
    <section style={styles.section}>
      <div style={styles.sectionHeader}>
        <h2 style={styles.sectionTitle}>
          <span className="status-dot live" style={{ marginRight: 8 }} />
          Live Games
        </h2>
        <div style={styles.liveGamesControls}>
          {/* League Filter */}
          <div style={styles.liveLeagueFilter}>
            {leagues.map((l) => (
              <button
                key={l.value}
                onClick={() => setLeagueFilter(l.value)}
                style={{
                  ...styles.liveLeagueBtn,
                  ...(leagueFilter === l.value ? styles.liveLeagueBtnActive : {}),
                }}
              >
                {l.color && (
                  <span style={{
                    ...styles.liveLeagueIcon,
                    background: l.color,
                  }}>
                    {l.value.charAt(0)}
                  </span>
                )}
                {l.label}
              </button>
            ))}
          </div>
          {/* Scroll Buttons */}
          <div style={styles.scrollButtons}>
            <button onClick={() => scroll("left")} style={styles.scrollBtn}>
              <ChevronLeft size={20} />
            </button>
            <button onClick={() => scroll("right")} style={styles.scrollBtn}>
              <ChevronRight size={20} />
            </button>
          </div>
        </div>
      </div>

      <div id="live-games-scroll" className="scroll-container" style={styles.liveGamesScroll}>
        {filteredLiveGames.length > 0 ? (
          filteredLiveGames.map((game, index) => (
            <LiveGameCard key={game.id} game={game} index={index} />
          ))
        ) : (
          <div style={styles.noGamesMessage}>
            No live {leagueFilter} games right now
          </div>
        )}
      </div>

      {/* Live Agent Actions Ticker */}
      <LiveActionsTicker agentActions={agentActions} />
    </section>
  );
}

// Helper: Format timestamp (microseconds) as relative time string
function formatRelativeTime(timestampUs) {
  if (!timestampUs) return "";
  const nowUs = Date.now() * 1000;
  const diffUs = nowUs - timestampUs;
  const diffS = diffUs / 1_000_000;

  if (diffS < 60) return `${Math.floor(diffS)}s ago`;
  if (diffS < 3600) return `${Math.floor(diffS / 60)}m ago`;
  return `${Math.floor(diffS / 3600)}h ago`;
}

// Helper: Format AgentResponseMessage as human-readable action string
function formatActionString(response) {
  if (!response) return "analyzing...";
  if (response.content) return `"${response.content}"`;
  if (response.bet_amount && response.bet_amount > 0) {
    const betType = response.bet_type?.toLowerCase() || "bet";
    const selection = response.bet_selection || "unknown";
    return `placed $${Math.floor(response.bet_amount)} on ${selection} ${betType}`;
  }
  return "analyzing...";
}

// Rolling Live Agent Actions Ticker
function LiveActionsTicker({ agentActions }) {
  const liveAgentActions = agentActions || [];
  const [currentIndex, setCurrentIndex] = useState(0);

  useEffect(() => {
    if (liveAgentActions.length === 0) return;

    const interval = setInterval(() => {
      setCurrentIndex((prev) => {
        const next = prev + 1;
        // Reset to beginning when we've gone through one full set
        if (next >= liveAgentActions.length) {
          return 0;
        }
        return next;
      });
    }, 2500); // Roll every 2.5 seconds

    return () => clearInterval(interval);
  }, [liveAgentActions.length]);

  // Get the 4 visible actions based on current index
  // Transform AgentAction format to display format
  const getVisibleActions = () => {
    if (liveAgentActions.length === 0) return [];
    const actions = [];
    for (let i = 0; i < 4; i++) {
      const idx = (currentIndex + i) % liveAgentActions.length;
      const raw = liveAgentActions[idx];
      // Transform AgentAction to display format
      actions.push({
        displayKey: `${currentIndex}-${i}`,
        agent: {
          name: raw.agent?.persona || raw.agent?.agent_id || "Agent",
          avatar: raw.agent?.avatar || raw.agent?.persona?.[0]?.toUpperCase() || "?",
          color: raw.agent?.color || "#6B7280",
        },
        action: formatActionString(raw.response),
        time: formatRelativeTime(raw.timestamp),
      });
    }
    return actions;
  };

  return (
    <div style={styles.actionsTicker}>
      <div style={styles.actionsHeader}>
        <Clock size={14} />
        <span>Live Agent Actions</span>
        <span style={styles.actionsLive}>
          <span className="status-dot live" />
          Rolling
        </span>
      </div>
      <div style={styles.actionsListContainer}>
        <AnimatePresence mode="popLayout">
          {getVisibleActions().map((action, index) => (
            <motion.div
              key={action.displayKey}
              style={styles.actionItem}
              initial={{ opacity: 0, y: -20, scale: 0.95 }}
              animate={{ opacity: 1, y: 0, scale: 1 }}
              exit={{ opacity: 0, y: 20, scale: 0.95 }}
              transition={{
                duration: 0.4,
                delay: index * 0.05,
                ease: "easeOut"
              }}
              layout
            >
              <span style={{
                ...styles.agentAvatar,
                background: action.agent.color,
              }}>
                {action.agent.avatar}
              </span>
              <span style={styles.actionAgent}>{action.agent.name}</span>
              <span style={styles.actionText}>{action.action}</span>
              <span style={styles.actionTime}>{action.time}</span>
            </motion.div>
          ))}
        </AnimatePresence>
      </div>
    </div>
  );
}

function LiveGameCard({ game, index }) {
  const navigate = useNavigate();
  const leagueColor = game.league === "NBA" ? "#C9082A" : "#013369";

  const handleClick = () => navigate(`/games/${game.id}`);
  const handleKeyDown = (e) => {
    if (e.key === 'Enter' || e.key === ' ') {
      e.preventDefault();
      handleClick();
    }
  };

  return (
    <motion.div
      style={styles.liveGameCard}
      initial={{ opacity: 0, x: 20 }}
      animate={{ opacity: 1, x: 0 }}
      transition={{ duration: 0.3, delay: index * 0.1 }}
      className="hover-lift"
      role="button"
      tabIndex={0}
      onClick={handleClick}
      onKeyDown={handleKeyDown}
      aria-label={`View game details for ${game.home_team.name} vs ${game.away_team.name}`}
    >
      {/* Top badges row */}
      <div style={styles.cardBadges}>
        {/* League badge */}
        <div style={{
          ...styles.leagueBadge,
          background: leagueColor,
        }}>
          {game.league}
        </div>
        {/* Live badge */}
        <div className="badge badge-live" style={styles.liveBadgeInline}>
          <span className="status-dot live" />
          LIVE
        </div>
      </div>

      {/* Teams */}
      <div style={styles.teamsContainer}>
        <TeamDisplay team={game.home_team} score={game.home_score} />
        <div style={styles.vsContainer}>
          <span style={styles.vsText}>VS</span>
          <span style={styles.gameTime}>{game.quarter} {game.clock}</span>
        </div>
        <TeamDisplay team={game.away_team} score={game.away_score} />
      </div>

      {/* Agent Bets */}
      <div style={styles.betsContainer}>
        <div style={styles.betsHeader}>
          Agent Bets
          <span style={styles.betsLiveIndicator}>
            <span className="status-dot live" style={{ width: 6, height: 6 }} />
          </span>
        </div>
        {game.bets && game.bets.slice(0, 3).map((bet, i) => (
          <div
            key={`${bet.agent.id}-${bet.team}-${i}`}
            style={styles.betRow}
          >
            <span style={{
              ...styles.agentAvatarSmall,
              background: bet.agent.color,
            }}>
              {bet.agent.avatar}
            </span>
            <span style={styles.betTeam}>{bet.team}</span>
            <span style={styles.betAmount}>
              ${bet.amount}
            </span>
          </div>
        ))}
      </div>
    </motion.div>
  );
}

function TeamDisplay({ team, score }) {
  return (
    <div style={styles.teamDisplay}>
      <div style={{
        ...styles.teamLogo,
        background: `linear-gradient(135deg, ${team.color}88 0%, ${team.color}44 100%)`,
      }}>
        {team.logo_url ? (
          <img src={team.logo_url} alt={team.name} style={styles.teamLogoImg} />
        ) : (
          <span style={styles.teamLogoText}>{team.abbrev}</span>
        )}
      </div>
      <div style={styles.teamInfo}>
        <span style={styles.teamCity}>{team.city}</span>
        <span style={styles.teamName}>{team.name}</span>
      </div>
      {score !== undefined && (
        <div style={styles.teamScore}>
          {score}
        </div>
      )}
    </div>
  );
}

// All Games Section
function AllGamesSection({ allGames }) {
  const [filter, setFilter] = useState("all");
  const [league, setLeague] = useState("all");
  const [timeRange, setTimeRange] = useState("7d");

  const filteredGames = (allGames || []).filter((game) => {
    // Filter by status
    if (filter !== "all" && game.status !== filter) return false;
    // Filter by league
    if (league !== "all" && game.league !== league) return false;
    return true;
  });

  const leagues = [
    { value: "all", label: "All Leagues" },
    { value: "NBA", label: "NBA" },
    { value: "NFL", label: "NFL" },
  ];

  return (
    <section style={styles.section}>
      <div style={styles.sectionHeader}>
        <h2 style={styles.sectionTitle}>All Games</h2>
        <div style={styles.filters}>
          {/* League Filter */}
          <div style={styles.leagueFilter}>
            {leagues.map((l) => (
              <button
                key={l.value}
                onClick={() => setLeague(l.value)}
                style={{
                  ...styles.leagueBtn,
                  ...(league === l.value ? styles.leagueBtnActive : {}),
                }}
              >
                {l.value !== "all" && (
                  <span style={{
                    ...styles.leagueIcon,
                    background: l.value === "NBA" ? "#C9082A" : "#013369",
                  }}>
                    {l.value.charAt(0)}
                  </span>
                )}
                {l.label}
              </button>
            ))}
          </div>
          {/* Status Filter */}
          <div style={styles.filterGroup}>
            {["all", "upcoming", "live", "completed"].map((f) => (
              <button
                key={f}
                onClick={() => setFilter(f)}
                style={{
                  ...styles.filterBtn,
                  ...(filter === f ? styles.filterBtnActive : {}),
                }}
              >
                {f.charAt(0).toUpperCase() + f.slice(1)}
              </button>
            ))}
          </div>
          {/* Time Range Filter */}
          <div style={styles.filterGroup}>
            {["1d", "7d", "30d"].map((t) => (
              <button
                key={t}
                onClick={() => setTimeRange(t)}
                style={{
                  ...styles.timeBtn,
                  ...(timeRange === t ? styles.timeBtnActive : {}),
                }}
              >
                {t}
              </button>
            ))}
          </div>
        </div>
      </div>

      <div style={styles.gamesList}>
        {filteredGames.map((game, index) => (
          <GameRow key={game.id} game={game} index={index} />
        ))}
      </div>

      <button style={styles.loadMoreBtn}>
        Load More Games...
      </button>
    </section>
  );
}

function GameRow({ game, index }) {
  const navigate = useNavigate();

  const getStatusBadge = () => {
    switch (game.status) {
      case "live":
        return (
          <span className="badge badge-live">
            <span className="status-dot live" />
            LIVE
          </span>
        );
      case "upcoming":
        return (
          <span className="badge badge-warning">
            UPCOMING
          </span>
        );
      case "completed":
        return (
          <span className="badge badge-success">
            COMPLETED
          </span>
        );
      default:
        return null;
    }
  };

  const handleClick = () => navigate(`/games/${game.id}`);
  const handleKeyDown = (e) => {
    if (e.key === 'Enter' || e.key === ' ') {
      e.preventDefault();
      handleClick();
    }
  };

  return (
    <motion.div
      style={styles.gameRow}
      initial={{ opacity: 0, y: 10 }}
      animate={{ opacity: 1, y: 0 }}
      transition={{ duration: 0.2, delay: index * 0.05 }}
      role="button"
      tabIndex={0}
      onClick={handleClick}
      onKeyDown={handleKeyDown}
      aria-label={`View game details for ${game.home_team.name} vs ${game.away_team.name}`}
    >
      <div style={styles.gameRowStatus}>
        {getStatusBadge()}
      </div>

      <div style={styles.gameRowTeams}>
        <div style={styles.gameRowTeam}>
          <span style={{
            ...styles.teamLogSmall,
            background: `linear-gradient(135deg, ${game.home_team.color}88 0%, ${game.home_team.color}44 100%)`,
          }}>
            {game.home_team.logo_url ? (
              <img src={game.home_team.logo_url} alt={game.home_team.name} style={styles.teamLogSmallImg} />
            ) : (
              game.home_team.abbrev
            )}
          </span>
          <span>{game.home_team.name}</span>
        </div>
        <span style={styles.gameRowVs}>vs</span>
        <div style={styles.gameRowTeam}>
          <span style={{
            ...styles.teamLogSmall,
            background: `linear-gradient(135deg, ${game.away_team.color}88 0%, ${game.away_team.color}44 100%)`,
          }}>
            {game.away_team.logo_url ? (
              <img src={game.away_team.logo_url} alt={game.away_team.name} style={styles.teamLogSmallImg} />
            ) : (
              game.away_team.abbrev
            )}
          </span>
          <span>{game.away_team.name}</span>
        </div>
      </div>

      <div style={styles.gameRowInfo}>
        {game.status === "live" && (
          <span style={styles.liveScore}>
            {game.home_score} - {game.away_score}
          </span>
        )}
        {game.status === "upcoming" && (
          <span style={styles.gameDateTime}>
            {game.date} • {game.time}
          </span>
        )}
        {game.status === "completed" && (
          <span style={styles.finalScore}>
            Final: {game.home_score} - {game.away_score}
          </span>
        )}
      </div>

      <div style={styles.gameRowResult}>
        {game.status === "completed" && game.winner && (
          <div style={styles.winnerInfo}>
            <span style={{
              ...styles.agentAvatarSmall,
              background: game.winner.color,
            }}>
              {game.winner.avatar}
            </span>
            <span style={styles.winnerName}>{game.winner.name}</span>
            <span style={styles.winAmount}>+${game.win_amount}</span>
          </div>
        )}
        {game.status === "upcoming" && (
          <span style={styles.agentCount}>
            <Users size={14} />
            {game.agent_count} agents
          </span>
        )}
      </div>
    </motion.div>
  );
}

export default function GamesPage() {
  const { stats, liveGames, allGames, agentActions } = useDataSource();

  return (
    <div style={styles.page}>
      <div className="container">
        <HeroSection stats={stats} />
        <LiveGamesSection liveGames={liveGames} agentActions={agentActions} />
        <AllGamesSection allGames={allGames} />
      </div>
    </div>
  );
}

const styles = {
  page: {
    paddingBottom: 60,
  },
  // Hero
  hero: {
    position: "relative",
    padding: "60px 0 40px",
    overflow: "hidden",
  },
  heroContent: {
    position: "relative",
    zIndex: 1,
  },
  headlineContainer: {
    marginBottom: 32,
  },
  heroHeadline: {
    fontSize: 48,
    fontWeight: 700,
    lineHeight: 1.1,
    marginBottom: 16,
  },
  heroSubtitle: {
    fontSize: 18,
    color: "var(--text-secondary)",
  },
  tickerHeadlines: {
    display: "flex",
    alignItems: "center",
    gap: 24,
    padding: "16px 24px",
    background: "var(--bg-card)",
    border: "1px solid var(--border-default)",
    borderRadius: 12,
    flexWrap: "wrap",
  },
  tickerItem: {
    display: "flex",
    alignItems: "center",
    gap: 10,
  },
  tickerLabel: {
    fontSize: 12,
    color: "var(--text-muted)",
    textTransform: "uppercase",
    letterSpacing: "0.5px",
    fontWeight: 500,
  },
  tickerValue: {
    fontSize: 24,
    fontWeight: 700,
    fontFamily: "'JetBrains Mono', monospace",
    transition: "color 0.3s ease",
  },
  tickerValueLive: {
    fontSize: 24,
    fontWeight: 700,
    fontFamily: "'JetBrains Mono', monospace",
    color: "#EF4444",
    display: "flex",
    alignItems: "center",
    transition: "color 0.3s ease",
  },
  tickerValueMoney: {
    fontSize: 24,
    fontWeight: 700,
    fontFamily: "'JetBrains Mono', monospace",
    color: "#10B981",
    transition: "color 0.3s ease",
  },
  tickerValueFlash: {
    transform: "scale(1.1)",
  },
  tickerDivider: {
    color: "var(--border-default)",
    fontSize: 20,
    fontWeight: 300,
  },
  heroBg: {
    position: "absolute",
    inset: 0,
    overflow: "hidden",
    pointerEvents: "none",
  },
  heroGradient1: {
    position: "absolute",
    top: "-50%",
    right: "-20%",
    width: "60%",
    height: "100%",
    background: "radial-gradient(ellipse at center, rgba(59, 130, 246, 0.1) 0%, transparent 70%)",
  },
  heroGradient2: {
    position: "absolute",
    bottom: "-30%",
    left: "-10%",
    width: "50%",
    height: "80%",
    background: "radial-gradient(ellipse at center, rgba(139, 92, 246, 0.08) 0%, transparent 70%)",
  },
  // Section
  section: {
    marginBottom: 48,
  },
  sectionHeader: {
    display: "flex",
    justifyContent: "space-between",
    alignItems: "center",
    marginBottom: 20,
    flexWrap: "wrap",
    gap: 16,
  },
  sectionTitle: {
    fontSize: 20,
    fontWeight: 600,
    display: "flex",
    alignItems: "center",
  },
  liveGamesControls: {
    display: "flex",
    alignItems: "center",
    gap: 16,
  },
  liveLeagueFilter: {
    display: "flex",
    gap: 6,
  },
  liveLeagueBtn: {
    display: "flex",
    alignItems: "center",
    gap: 5,
    padding: "6px 12px",
    background: "var(--bg-tertiary)",
    border: "1px solid var(--border-subtle)",
    borderRadius: 6,
    color: "var(--text-secondary)",
    fontSize: 12,
    fontWeight: 500,
    cursor: "pointer",
    transition: "all 0.2s ease",
  },
  liveLeagueBtnActive: {
    background: "var(--accent-primary)",
    borderColor: "var(--accent-primary)",
    color: "white",
  },
  liveLeagueIcon: {
    width: 16,
    height: 16,
    borderRadius: 3,
    display: "flex",
    alignItems: "center",
    justifyContent: "center",
    color: "white",
    fontSize: 9,
    fontWeight: 700,
  },
  scrollButtons: {
    display: "flex",
    gap: 8,
  },
  scrollBtn: {
    width: 36,
    height: 36,
    display: "flex",
    alignItems: "center",
    justifyContent: "center",
    background: "var(--bg-tertiary)",
    border: "1px solid var(--border-default)",
    borderRadius: 8,
    color: "var(--text-secondary)",
    cursor: "pointer",
  },
  // Live Games
  liveGamesScroll: {
    paddingBottom: 16,
    minHeight: 280,
  },
  noGamesMessage: {
    display: "flex",
    alignItems: "center",
    justifyContent: "center",
    width: "100%",
    padding: "60px 20px",
    color: "var(--text-muted)",
    fontSize: 14,
    fontStyle: "italic",
  },
  liveGameCard: {
    width: 320,
    background: "var(--bg-card)",
    border: "1px solid var(--border-default)",
    borderRadius: 16,
    padding: 20,
    position: "relative",
    cursor: "pointer",
  },
  cardBadges: {
    display: "flex",
    justifyContent: "space-between",
    alignItems: "center",
    marginBottom: 8,
  },
  leagueBadge: {
    padding: "4px 10px",
    borderRadius: 6,
    color: "white",
    fontSize: 11,
    fontWeight: 700,
    letterSpacing: "0.5px",
  },
  liveBadgeInline: {
    // Uses the badge class styles
  },
  liveBadge: {
    position: "absolute",
    top: 12,
    right: 12,
  },
  teamsContainer: {
    display: "flex",
    alignItems: "center",
    justifyContent: "space-between",
    marginBottom: 20,
    marginTop: 8,
  },
  teamDisplay: {
    display: "flex",
    flexDirection: "column",
    alignItems: "center",
    gap: 8,
    flex: 1,
  },
  teamLogo: {
    width: 48,
    height: 48,
    borderRadius: 12,
    display: "flex",
    alignItems: "center",
    justifyContent: "center",
    color: "white",
    fontWeight: 700,
    fontSize: 14,
    boxShadow: "0 4px 12px rgba(0, 0, 0, 0.2)",
  },
  teamLogoImg: {
    width: "75%",
    height: "75%",
    objectFit: "contain",
  },
  teamLogoText: {
    fontSize: 14,
    fontWeight: 700,
    color: "white",
  },
  teamInfo: {
    textAlign: "center",
  },
  teamCity: {
    fontSize: 11,
    color: "var(--text-muted)",
    display: "block",
  },
  teamName: {
    fontSize: 14,
    fontWeight: 600,
  },
  teamScore: {
    fontSize: 28,
    fontWeight: 700,
    position: "relative",
  },
  teamScoreFlash: {
    color: "#10B981",
  },
  scoreIncrement: {
    position: "absolute",
    top: -10,
    right: -20,
    fontSize: 14,
    fontWeight: 600,
    color: "#10B981",
  },
  vsContainer: {
    display: "flex",
    flexDirection: "column",
    alignItems: "center",
    gap: 4,
    padding: "0 12px",
  },
  vsText: {
    fontSize: 12,
    color: "var(--text-muted)",
    fontWeight: 600,
  },
  gameTime: {
    fontSize: 12,
    color: "var(--accent-primary)",
    fontWeight: 500,
  },
  betsContainer: {
    borderTop: "1px solid var(--border-subtle)",
    paddingTop: 16,
  },
  betsHeader: {
    fontSize: 12,
    color: "var(--text-muted)",
    marginBottom: 12,
    textTransform: "uppercase",
    letterSpacing: "0.5px",
    display: "flex",
    alignItems: "center",
    gap: 8,
  },
  betsLiveIndicator: {
    display: "flex",
    alignItems: "center",
  },
  betRow: {
    display: "flex",
    alignItems: "center",
    gap: 8,
    marginBottom: 8,
    padding: "6px 8px",
    borderRadius: 6,
    background: "transparent",
    transition: "all 0.3s ease",
  },
  betRowNew: {
    background: "rgba(16, 185, 129, 0.15)",
    border: "1px solid rgba(16, 185, 129, 0.3)",
  },
  newBetBadge: {
    fontSize: 9,
    fontWeight: 700,
    color: "#10B981",
    background: "rgba(16, 185, 129, 0.2)",
    padding: "2px 6px",
    borderRadius: 4,
    marginLeft: "auto",
  },
  agentAvatarSmall: {
    width: 24,
    height: 24,
    borderRadius: 6,
    display: "flex",
    alignItems: "center",
    justifyContent: "center",
    color: "white",
    fontSize: 12,
    fontWeight: 600,
  },
  betTeam: {
    flex: 1,
    fontSize: 14,
  },
  betAmount: {
    fontSize: 14,
    fontWeight: 600,
    color: "var(--success)",
  },
  // Actions Ticker
  actionsTicker: {
    marginTop: 20,
    background: "var(--bg-card)",
    border: "1px solid var(--border-default)",
    borderRadius: 12,
    padding: 16,
    overflow: "hidden",
  },
  actionsHeader: {
    display: "flex",
    alignItems: "center",
    gap: 8,
    fontSize: 12,
    color: "var(--text-muted)",
    marginBottom: 12,
    textTransform: "uppercase",
    letterSpacing: "0.5px",
  },
  actionsLive: {
    marginLeft: "auto",
    display: "flex",
    alignItems: "center",
    gap: 6,
    color: "var(--accent-primary)",
    fontSize: 11,
    fontWeight: 500,
  },
  actionsListContainer: {
    display: "flex",
    flexDirection: "column",
    gap: 8,
    minHeight: 180,
    position: "relative",
  },
  actionItem: {
    display: "flex",
    alignItems: "center",
    gap: 10,
    padding: "10px 12px",
    borderRadius: 8,
    background: "var(--bg-tertiary)",
    border: "1px solid var(--border-subtle)",
  },
  agentAvatar: {
    width: 28,
    height: 28,
    borderRadius: 6,
    display: "flex",
    alignItems: "center",
    justifyContent: "center",
    color: "white",
    fontSize: 12,
    fontWeight: 600,
  },
  actionAgent: {
    fontWeight: 600,
    fontSize: 14,
  },
  actionText: {
    flex: 1,
    fontSize: 14,
    color: "var(--text-secondary)",
  },
  actionTime: {
    fontSize: 12,
    color: "var(--text-muted)",
  },
  // Filters
  filters: {
    display: "flex",
    gap: 12,
    flexWrap: "wrap",
    alignItems: "center",
  },
  leagueFilter: {
    display: "flex",
    gap: 8,
  },
  leagueBtn: {
    display: "flex",
    alignItems: "center",
    gap: 6,
    padding: "8px 14px",
    background: "var(--bg-card)",
    border: "1px solid var(--border-default)",
    borderRadius: 8,
    color: "var(--text-secondary)",
    fontSize: 13,
    fontWeight: 500,
    cursor: "pointer",
    transition: "all 0.2s ease",
  },
  leagueBtnActive: {
    background: "var(--accent-primary)",
    borderColor: "var(--accent-primary)",
    color: "white",
  },
  leagueIcon: {
    width: 18,
    height: 18,
    borderRadius: 4,
    display: "flex",
    alignItems: "center",
    justifyContent: "center",
    color: "white",
    fontSize: 10,
    fontWeight: 700,
  },
  filterGroup: {
    display: "flex",
    background: "var(--bg-tertiary)",
    borderRadius: 8,
    padding: 4,
    gap: 4,
  },
  filterBtn: {
    padding: "6px 14px",
    background: "transparent",
    border: "none",
    borderRadius: 6,
    color: "var(--text-secondary)",
    fontSize: 13,
    fontWeight: 500,
    cursor: "pointer",
    transition: "all 0.2s ease",
  },
  filterBtnActive: {
    background: "var(--accent-primary)",
    color: "white",
  },
  timeBtn: {
    padding: "6px 12px",
    background: "transparent",
    border: "none",
    borderRadius: 6,
    color: "var(--text-secondary)",
    fontSize: 13,
    fontWeight: 500,
    cursor: "pointer",
    fontFamily: "'JetBrains Mono', monospace",
  },
  timeBtnActive: {
    background: "var(--bg-elevated)",
    color: "var(--text-primary)",
  },
  // Games List
  gamesList: {
    display: "flex",
    flexDirection: "column",
    gap: 8,
  },
  gameRow: {
    display: "grid",
    gridTemplateColumns: "120px 1fr 160px 180px",
    alignItems: "center",
    gap: 16,
    padding: "16px 20px",
    background: "var(--bg-card)",
    border: "1px solid var(--border-subtle)",
    borderRadius: 12,
    transition: "all 0.2s ease",
    cursor: "pointer",
  },
  gameRowStatus: {},
  gameRowTeams: {
    display: "flex",
    alignItems: "center",
    gap: 12,
  },
  gameRowTeam: {
    display: "flex",
    alignItems: "center",
    gap: 8,
  },
  teamLogSmall: {
    width: 32,
    height: 32,
    borderRadius: 8,
    display: "flex",
    alignItems: "center",
    justifyContent: "center",
    color: "white",
    fontSize: 11,
    fontWeight: 700,
    boxShadow: "0 2px 8px rgba(0, 0, 0, 0.15)",
  },
  teamLogSmallImg: {
    width: "75%",
    height: "75%",
    objectFit: "contain",
  },
  gameRowVs: {
    color: "var(--text-muted)",
    fontSize: 12,
  },
  gameRowInfo: {
    textAlign: "center",
  },
  liveScore: {
    fontSize: 18,
    fontWeight: 700,
    color: "var(--accent-primary)",
  },
  gameDateTime: {
    fontSize: 14,
    color: "var(--text-secondary)",
  },
  finalScore: {
    fontSize: 14,
    color: "var(--text-secondary)",
  },
  gameRowResult: {},
  winnerInfo: {
    display: "flex",
    alignItems: "center",
    gap: 8,
  },
  winnerName: {
    fontSize: 14,
    fontWeight: 500,
  },
  winAmount: {
    fontSize: 14,
    fontWeight: 600,
    color: "var(--success)",
  },
  agentCount: {
    display: "flex",
    alignItems: "center",
    gap: 6,
    fontSize: 14,
    color: "var(--text-secondary)",
  },
  loadMoreBtn: {
    width: "100%",
    padding: "14px",
    marginTop: 16,
    background: "transparent",
    border: "1px dashed var(--border-default)",
    borderRadius: 12,
    color: "var(--text-secondary)",
    fontSize: 14,
    fontWeight: 500,
    cursor: "pointer",
    transition: "all 0.2s ease",
  },
};
