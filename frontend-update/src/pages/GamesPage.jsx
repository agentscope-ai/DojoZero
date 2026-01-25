import { useState, useEffect } from "react";
import { useNavigate } from "react-router-dom";
import { motion, AnimatePresence } from "framer-motion";
import {
  ChevronLeft,
  ChevronRight,
  Users,
  Trophy,
  TrendingUp,
  Clock,
  Filter,
  Play
} from "lucide-react";
import { useDataSource } from "../hooks/useDataSource.jsx";
import { agents } from "../data/mockData";

// Hero Section with newspaper-style live stats
function HeroSection({ stats, useMockData }) {
  // Live updating stats - initialize from props
  const [gamesPlayed, setGamesPlayed] = useState(stats.gamesPlayed);
  const [liveNow, setLiveNow] = useState(stats.liveNow);
  const [wageredToday, setWageredToday] = useState(stats.wageredToday);
  const [betsPlaced, setBetsPlaced] = useState(342);
  const [flashStat, setFlashStat] = useState(null);

  // Update from props when they change (for live API data)
  useEffect(() => {
    setGamesPlayed(stats.gamesPlayed);
    setLiveNow(stats.liveNow);
    setWageredToday(stats.wageredToday);
  }, [stats]);

  // Simulate real-time updates (only when using mock data)
  useEffect(() => {
    if (!useMockData) return; // Don't simulate when using live API
    
    const interval = setInterval(() => {
      const rand = Math.random();
      if (rand < 0.3) {
        // Wagered amount increases
        const increase = Math.floor(Math.random() * 500 + 100);
        setWageredToday((prev) => prev + increase);
        setFlashStat("wagered");
        setTimeout(() => setFlashStat(null), 600);
      } else if (rand < 0.5) {
        // Bets placed increases
        setBetsPlaced((prev) => prev + 1);
        setFlashStat("bets");
        setTimeout(() => setFlashStat(null), 600);
      } else if (rand < 0.6) {
        // Games played occasionally increases
        setGamesPlayed((prev) => prev + 1);
        setFlashStat("games");
        setTimeout(() => setFlashStat(null), 600);
      }
    }, 2000);

    return () => clearInterval(interval);
  }, [useMockData]);

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
            <span style={{
              ...styles.tickerValueLive,
              ...(flashStat === "live" ? styles.tickerValueFlash : {}),
            }}>
              <span className="status-dot live" style={{ marginRight: 6 }} />
              <AnimatedNumber value={liveNow} />
            </span>
          </div>
          <div style={styles.tickerDivider}>|</div>
          <div style={styles.tickerItem}>
            <span style={styles.tickerLabel}>Total Games</span>
            <span style={{
              ...styles.tickerValue,
              ...(flashStat === "games" ? styles.tickerValueFlash : {}),
            }}>
              <AnimatedNumber value={gamesPlayed} />
            </span>
          </div>
          <div style={styles.tickerDivider}>|</div>
          <div style={styles.tickerItem}>
            <span style={styles.tickerLabel}>Bets Today</span>
            <span style={{
              ...styles.tickerValue,
              ...(flashStat === "bets" ? styles.tickerValueFlash : {}),
            }}>
              <AnimatedNumber value={betsPlaced} />
            </span>
          </div>
          <div style={styles.tickerDivider}>|</div>
          <div style={styles.tickerItem}>
            <span style={styles.tickerLabel}>Wagered</span>
            <span style={{
              ...styles.tickerValueMoney,
              ...(flashStat === "wagered" ? styles.tickerValueFlash : {}),
            }}>
              $<AnimatedNumber value={wageredToday} format="money" />
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

// Animated number component with counting effect
function AnimatedNumber({ value, flash, format }) {
  const [displayValue, setDisplayValue] = useState(value);
  const [isAnimating, setIsAnimating] = useState(false);

  useEffect(() => {
    if (value !== displayValue) {
      setIsAnimating(true);
      const diff = value - displayValue;
      const steps = 10;
      const stepValue = diff / steps;
      let current = displayValue;
      let step = 0;

      const animate = setInterval(() => {
        step++;
        current += stepValue;
        if (step >= steps) {
          setDisplayValue(value);
          setIsAnimating(false);
          clearInterval(animate);
        } else {
          setDisplayValue(Math.round(current));
        }
      }, 30);

      return () => clearInterval(animate);
    }
  }, [value, displayValue]);

  const formatValue = (val) => {
    if (format === "money") {
      if (val >= 1000000) {
        return `${(val / 1000000).toFixed(2)}M`;
      } else if (val >= 1000) {
        return `${(val / 1000).toFixed(1)}K`;
      }
      return val.toLocaleString();
    }
    return val.toLocaleString();
  };

  return (
    <span style={{
      ...(isAnimating ? { color: "#10B981" } : {}),
      transition: "color 0.3s ease",
    }}>
      {formatValue(displayValue)}
    </span>
  );
}

// Live Games Carousel
function LiveGamesSection({ liveGames, agentActions }) {
  const [scrollPosition, setScrollPosition] = useState(0);
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

// Rolling Live Agent Actions Ticker
function LiveActionsTicker({ agentActions }) {
  const liveAgentActions = agentActions || [];
  const [visibleActions, setVisibleActions] = useState(liveAgentActions.slice(0, 4));
  const [currentIndex, setCurrentIndex] = useState(0);

  // Create an extended list of actions for continuous rolling effect
  const extendedActions = [
    ...liveAgentActions,
    ...liveAgentActions,
    ...liveAgentActions,
  ];

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
  const getVisibleActions = () => {
    if (liveAgentActions.length === 0) return [];
    const actions = [];
    for (let i = 0; i < 4; i++) {
      const idx = (currentIndex + i) % liveAgentActions.length;
      actions.push({ ...liveAgentActions[idx], displayKey: `${currentIndex}-${i}` });
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
  
  // Simulate live score changes
  const [homeScore, setHomeScore] = useState(game.homeScore);
  const [awayScore, setAwayScore] = useState(game.awayScore);
  const [clock, setClock] = useState(game.clock);
  const [bets, setBets] = useState(game.bets);
  const [scoreFlash, setScoreFlash] = useState(null); // 'home' | 'away' | null
  const [newBetFlash, setNewBetFlash] = useState(null);
  const [isHovered, setIsHovered] = useState(false);
  
  // Navigate to room on click
  const handleCardClick = () => {
    // Use game.id or game.trialId depending on your data structure
    const roomId = game.trialId || game.id;
    navigate(`/games/${roomId}`);
  };

  // Simulate clock countdown
  useEffect(() => {
    const clockInterval = setInterval(() => {
      setClock((prev) => {
        const [mins, secs] = prev.split(":").map(Number);
        if (secs > 0) {
          return `${mins}:${String(secs - 1).padStart(2, "0")}`;
        } else if (mins > 0) {
          return `${mins - 1}:59`;
        }
        return prev;
      });
    }, 1000);

    return () => clearInterval(clockInterval);
  }, []);

  // Simulate random score changes (exaggerated for demo)
  useEffect(() => {
    const scoreInterval = setInterval(() => {
      const rand = Math.random();
      if (rand < 0.3) {
        // Home team scores
        const points = Math.random() < 0.4 ? 3 : 2;
        setHomeScore((prev) => prev + points);
        setScoreFlash("home");
        setTimeout(() => setScoreFlash(null), 800);
      } else if (rand < 0.6) {
        // Away team scores
        const points = Math.random() < 0.4 ? 3 : 2;
        setAwayScore((prev) => prev + points);
        setScoreFlash("away");
        setTimeout(() => setScoreFlash(null), 800);
      }
    }, 3000 + Math.random() * 2000); // Every 3-5 seconds

    return () => clearInterval(scoreInterval);
  }, []);

  // Simulate new bets being placed
  useEffect(() => {
    const betInterval = setInterval(() => {
      if (Math.random() < 0.4) {
        const randomAgent = agents[Math.floor(Math.random() * agents.length)];
        const randomTeam = Math.random() < 0.5 ? game.homeTeam.abbrev : game.awayTeam.abbrev;
        const randomAmount = Math.floor(Math.random() * 80 + 20);

        const newBet = {
          agent: randomAgent,
          team: randomTeam,
          amount: randomAmount,
          type: "moneyline",
          isNew: true,
        };

        setBets((prev) => {
          const updated = [newBet, ...prev.slice(0, 2)];
          return updated;
        });
        setNewBetFlash(randomAgent.id);
        setTimeout(() => setNewBetFlash(null), 1000);
      }
    }, 4000 + Math.random() * 3000); // Every 4-7 seconds

    return () => clearInterval(betInterval);
  }, [game.homeTeam.abbrev, game.awayTeam.abbrev]);

  const leagueColor = game.league === "NBA" ? "#C9082A" : "#013369";

  return (
    <motion.div
      style={{
        ...styles.liveGameCard,
        ...(isHovered ? styles.liveGameCardHover : {}),
      }}
      initial={{ opacity: 0, x: 20 }}
      animate={{ opacity: 1, x: 0 }}
      transition={{ duration: 0.3, delay: index * 0.1 }}
      className="hover-lift"
      onClick={handleCardClick}
      onMouseEnter={() => setIsHovered(true)}
      onMouseLeave={() => setIsHovered(false)}
    >
      {/* Enter Room Overlay */}
      <AnimatePresence>
        {isHovered && (
          <motion.div
            style={styles.enterRoomOverlay}
            initial={{ opacity: 0 }}
            animate={{ opacity: 1 }}
            exit={{ opacity: 0 }}
            transition={{ duration: 0.2 }}
          >
            <div style={styles.enterRoomContent}>
              <Play size={32} fill="white" />
              <span style={styles.enterRoomText}>ENTER ROOM</span>
            </div>
          </motion.div>
        )}
      </AnimatePresence>

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
        <TeamDisplay team={game.homeTeam} score={homeScore} flash={scoreFlash === "home"} />
        <div style={styles.vsContainer}>
          <span style={styles.vsText}>VS</span>
          <span style={styles.gameTime}>{game.quarter} {clock}</span>
        </div>
        <TeamDisplay team={game.awayTeam} score={awayScore} flash={scoreFlash === "away"} />
      </div>

      {/* Agent Bets */}
      <div style={styles.betsContainer}>
        <div style={styles.betsHeader}>
          Agent Bets
          <span style={styles.betsLiveIndicator}>
            <span className="status-dot live" style={{ width: 6, height: 6 }} />
          </span>
        </div>
        <AnimatePresence mode="popLayout">
          {bets.slice(0, 3).map((bet, i) => (
            <motion.div
              key={`${bet.agent.id}-${bet.team}-${i}`}
              style={{
                ...styles.betRow,
                ...(newBetFlash === bet.agent.id ? styles.betRowNew : {}),
              }}
              initial={bet.isNew ? { opacity: 0, x: -20, scale: 0.9 } : false}
              animate={{ opacity: 1, x: 0, scale: 1 }}
              exit={{ opacity: 0, x: 20 }}
              transition={{ duration: 0.3 }}
              layout
            >
              <span style={{
                ...styles.agentAvatarSmall,
                background: bet.agent.color,
              }}>
                {bet.agent.avatar}
              </span>
              <span style={styles.betTeam}>{bet.team}</span>
              <motion.span
                style={styles.betAmount}
                initial={bet.isNew ? { scale: 1.3 } : false}
                animate={{ scale: 1 }}
              >
                ${bet.amount}
              </motion.span>
              {bet.isNew && (
                <motion.span
                  style={styles.newBetBadge}
                  initial={{ opacity: 1 }}
                  animate={{ opacity: 0 }}
                  transition={{ duration: 2 }}
                >
                  NEW
                </motion.span>
              )}
            </motion.div>
          ))}
        </AnimatePresence>
      </div>
    </motion.div>
  );
}

function TeamDisplay({ team, score, flash }) {
  return (
    <div style={styles.teamDisplay}>
      <div style={{
        ...styles.teamLogo,
        background: team.color,
      }}>
        {team.abbrev}
      </div>
      <div style={styles.teamInfo}>
        <span style={styles.teamCity}>{team.city}</span>
        <span style={styles.teamName}>{team.name}</span>
      </div>
      {score !== undefined && (
        <motion.div
          style={{
            ...styles.teamScore,
            ...(flash ? styles.teamScoreFlash : {}),
          }}
          animate={flash ? {
            scale: [1, 1.3, 1],
            color: ["var(--text-primary)", "#10B981", "var(--text-primary)"],
          } : {}}
          transition={{ duration: 0.5 }}
        >
          {score}
          {flash && (
            <motion.span
              style={styles.scoreIncrement}
              initial={{ opacity: 1, y: 0 }}
              animate={{ opacity: 0, y: -20 }}
              transition={{ duration: 0.8 }}
            >
              +2
            </motion.span>
          )}
        </motion.div>
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

  // Navigate to room for live and completed games
  const handleRowClick = () => {
    if (game.status === "upcoming") return; // Can't enter upcoming games
    const roomId = game.trialId || game.id;
    navigate(`/games/${roomId}`);
  };

  const isClickable = game.status === "live" || game.status === "completed";

  return (
    <motion.div
      style={{
        ...styles.gameRow,
        ...(isClickable ? styles.gameRowClickable : {}),
      }}
      initial={{ opacity: 0, y: 10 }}
      animate={{ opacity: 1, y: 0 }}
      transition={{ duration: 0.2, delay: index * 0.05 }}
      onClick={handleRowClick}
      whileHover={isClickable ? { scale: 1.01, x: 4 } : {}}
    >
      <div style={styles.gameRowStatus}>
        {getStatusBadge()}
      </div>

      <div style={styles.gameRowTeams}>
        <div style={styles.gameRowTeam}>
          <span style={{
            ...styles.teamLogSmall,
            background: game.homeTeam.color,
          }}>
            {game.homeTeam.abbrev}
          </span>
          <span>{game.homeTeam.name}</span>
        </div>
        <span style={styles.gameRowVs}>vs</span>
        <div style={styles.gameRowTeam}>
          <span style={{
            ...styles.teamLogSmall,
            background: game.awayTeam.color,
          }}>
            {game.awayTeam.abbrev}
          </span>
          <span>{game.awayTeam.name}</span>
        </div>
      </div>

      <div style={styles.gameRowInfo}>
        {game.status === "live" && (
          <span style={styles.liveScore}>
            {game.homeScore} - {game.awayScore}
          </span>
        )}
        {game.status === "upcoming" && (
          <span style={styles.gameDateTime}>
            {game.date} • {game.time}
          </span>
        )}
        {game.status === "completed" && (
          <span style={styles.finalScore}>
            Final: {game.homeScore} - {game.awayScore}
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
            <span style={styles.winAmount}>+${game.winAmount}</span>
          </div>
        )}
        {game.status === "upcoming" && (
          <span style={styles.agentCount}>
            <Users size={14} />
            {game.agentCount} agents
          </span>
        )}
      </div>
    </motion.div>
  );
}

export default function GamesPage() {
  const { stats, liveGames, allGames, agentActions, useMockData } = useDataSource();
  
  return (
    <div style={styles.page}>
      <div className="container">
        <HeroSection stats={stats} useMockData={useMockData} />
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
    transition: "all 0.3s ease",
    overflow: "hidden",
  },
  liveGameCardHover: {
    borderColor: "var(--accent-primary)",
    boxShadow: "0 0 0 1px var(--accent-primary), 0 8px 24px rgba(59, 130, 246, 0.15)",
  },
  enterRoomOverlay: {
    position: "absolute",
    inset: 0,
    background: "rgba(0, 0, 0, 0.75)",
    backdropFilter: "blur(4px)",
    display: "flex",
    alignItems: "center",
    justifyContent: "center",
    zIndex: 10,
    borderRadius: 15,
  },
  enterRoomContent: {
    display: "flex",
    flexDirection: "column",
    alignItems: "center",
    gap: 8,
    color: "white",
  },
  enterRoomText: {
    fontSize: 14,
    fontWeight: 700,
    letterSpacing: "0.15em",
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
  },
  gameRowClickable: {
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
