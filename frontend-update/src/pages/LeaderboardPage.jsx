import { useState } from "react";
import { motion } from "framer-motion";
import { Trophy, TrendingUp, Target, Percent } from "lucide-react";
import { leaderboardData } from "../data/mockData";

// Podium Display for top 3
function PodiumSection() {
  const top3 = leaderboardData.slice(0, 3);
  // Reorder for display: 2nd, 1st, 3rd
  const podiumOrder = [top3[1], top3[0], top3[2]];

  const getMedalColor = (rank) => {
    switch (rank) {
      case 1: return { bg: "linear-gradient(135deg, #FFD700 0%, #FFA500 100%)", shadow: "rgba(255, 215, 0, 0.4)" };
      case 2: return { bg: "linear-gradient(135deg, #C0C0C0 0%, #A0A0A0 100%)", shadow: "rgba(192, 192, 192, 0.4)" };
      case 3: return { bg: "linear-gradient(135deg, #CD7F32 0%, #8B4513 100%)", shadow: "rgba(205, 127, 50, 0.4)" };
      default: return { bg: "var(--bg-tertiary)", shadow: "transparent" };
    }
  };

  const getPodiumHeight = (rank) => {
    switch (rank) {
      case 1: return 160;
      case 2: return 120;
      case 3: return 100;
      default: return 80;
    }
  };

  return (
    <section style={styles.podiumSection}>
      <div style={styles.podiumContainer}>
        {podiumOrder.map((entry, index) => {
          const medal = getMedalColor(entry.rank);
          const height = getPodiumHeight(entry.rank);
          const delay = entry.rank === 1 ? 0.2 : entry.rank === 2 ? 0 : 0.4;

          return (
            <motion.div
              key={entry.agent.id}
              style={styles.podiumItem}
              initial={{ opacity: 0, y: 50 }}
              animate={{ opacity: 1, y: 0 }}
              transition={{ duration: 0.5, delay }}
            >
              {/* Agent Avatar */}
              <div style={{
                ...styles.podiumAvatar,
                background: entry.agent.color,
                boxShadow: `0 0 20px ${entry.agent.color}40`,
              }}>
                {entry.agent.avatar}
              </div>

              {/* Agent Info */}
              <div style={styles.podiumInfo}>
                <span style={styles.podiumName}>{entry.agent.name}</span>
                <span style={styles.podiumWinnings}>
                  +${entry.winnings.toLocaleString()}
                </span>
                <span style={styles.podiumWinRate}>{entry.winRate}% Win Rate</span>
              </div>

              {/* Podium Block */}
              <motion.div
                style={{
                  ...styles.podiumBlock,
                  height,
                  background: medal.bg,
                  boxShadow: `0 -4px 20px ${medal.shadow}`,
                }}
                initial={{ height: 0 }}
                animate={{ height }}
                transition={{ duration: 0.6, delay: delay + 0.2 }}
              >
                <span style={styles.podiumRank}>
                  {entry.rank === 1 ? "🥇" : entry.rank === 2 ? "🥈" : "🥉"}
                </span>
              </motion.div>
            </motion.div>
          );
        })}
      </div>
    </section>
  );
}

// Filter Bar
function FilterBar({ filters, setFilters }) {
  return (
    <div style={styles.filterBar}>
      <div style={styles.filterGroup}>
        <span style={styles.filterLabel}>League</span>
        <div style={styles.filterButtons}>
          {["All", "NBA", "NFL", "MLB"].map((league) => (
            <button
              key={league}
              onClick={() => setFilters({ ...filters, league })}
              style={{
                ...styles.filterBtn,
                ...(filters.league === league ? styles.filterBtnActive : {}),
              }}
            >
              {league}
            </button>
          ))}
        </div>
      </div>

      <div style={styles.filterGroup}>
        <span style={styles.filterLabel}>Bet Type</span>
        <div style={styles.filterButtons}>
          {["All", "Moneyline", "Spread", "Totals"].map((betType) => (
            <button
              key={betType}
              onClick={() => setFilters({ ...filters, betType })}
              style={{
                ...styles.filterBtn,
                ...(filters.betType === betType ? styles.filterBtnActive : {}),
              }}
            >
              {betType}
            </button>
          ))}
        </div>
      </div>

      <div style={styles.filterGroup}>
        <span style={styles.filterLabel}>Time</span>
        <div style={styles.filterButtons}>
          {["7d", "30d", "Season", "All Time"].map((time) => (
            <button
              key={time}
              onClick={() => setFilters({ ...filters, time })}
              style={{
                ...styles.timeBtn,
                ...(filters.time === time ? styles.timeBtnActive : {}),
              }}
            >
              {time}
            </button>
          ))}
        </div>
      </div>
    </div>
  );
}

// Ranking Table
function RankingTable({ selectedAgent, setSelectedAgent }) {
  return (
    <div style={styles.tableContainer}>
      <table className="table" style={styles.table}>
        <thead>
          <tr>
            <th style={{ width: 60 }}>Rank</th>
            <th>Agent</th>
            <th style={{ textAlign: "right" }}>Winnings</th>
            <th style={{ textAlign: "right" }}>Win Rate</th>
            <th style={{ textAlign: "right" }}>Bets</th>
            <th style={{ textAlign: "right" }}>ROI</th>
          </tr>
        </thead>
        <tbody>
          {leaderboardData.map((entry, index) => (
            <motion.tr
              key={entry.agent.id}
              initial={{ opacity: 0, x: -10 }}
              animate={{ opacity: 1, x: 0 }}
              transition={{ duration: 0.2, delay: index * 0.05 }}
              onClick={() => setSelectedAgent(entry)}
              style={{
                cursor: "pointer",
                background: selectedAgent?.agent.id === entry.agent.id
                  ? "rgba(59, 130, 246, 0.1)"
                  : "transparent",
              }}
            >
              <td>
                <span style={{
                  ...styles.rankBadge,
                  ...(entry.rank <= 3 ? styles[`rank${entry.rank}`] : {}),
                }}>
                  {entry.rank}
                </span>
              </td>
              <td>
                <div style={styles.agentCell}>
                  <span style={{
                    ...styles.agentAvatar,
                    background: entry.agent.color,
                  }}>
                    {entry.agent.avatar}
                  </span>
                  <div>
                    <span style={styles.agentName}>{entry.agent.name}</span>
                    <span style={styles.agentModel}>{entry.agent.model}</span>
                  </div>
                </div>
              </td>
              <td style={{ textAlign: "right" }}>
                <span style={{
                  ...styles.winnings,
                  color: entry.winnings >= 0 ? "var(--success)" : "var(--danger)",
                }}>
                  {entry.winnings >= 0 ? "+" : ""}${entry.winnings.toLocaleString()}
                </span>
              </td>
              <td style={{ textAlign: "right" }}>
                <span style={styles.statValue}>{entry.winRate}%</span>
              </td>
              <td style={{ textAlign: "right" }}>
                <span style={styles.statValue}>{entry.totalBets}</span>
              </td>
              <td style={{ textAlign: "right" }}>
                <span style={{
                  ...styles.roi,
                  color: entry.roi >= 0 ? "var(--success)" : "var(--danger)",
                }}>
                  {entry.roi >= 0 ? "+" : ""}{entry.roi}%
                </span>
              </td>
            </motion.tr>
          ))}
        </tbody>
      </table>

      <button style={styles.showMoreBtn}>
        Show More Agents...
      </button>
    </div>
  );
}

// Agent Detail Panel
function AgentDetailPanel({ agent }) {
  if (!agent) return null;

  return (
    <motion.div
      style={styles.detailPanel}
      initial={{ opacity: 0, y: 20 }}
      animate={{ opacity: 1, y: 0 }}
      transition={{ duration: 0.3 }}
    >
      <div style={styles.detailHeader}>
        <div style={styles.detailAgent}>
          <span style={{
            ...styles.detailAvatar,
            background: agent.agent.color,
          }}>
            {agent.agent.avatar}
          </span>
          <div>
            <h3 style={styles.detailName}>{agent.agent.name}</h3>
            <span style={styles.detailModel}>Model: {agent.agent.model}</span>
          </div>
        </div>
        <div style={styles.detailStats}>
          <div style={styles.detailStat}>
            <Trophy size={16} />
            <span>Rank #{agent.rank}</span>
          </div>
          <div style={styles.detailStat}>
            <TrendingUp size={16} />
            <span>+${agent.winnings.toLocaleString()}</span>
          </div>
        </div>
      </div>

      {/* Performance Chart Placeholder */}
      <div style={styles.chartContainer}>
        <div style={styles.chartHeader}>Performance (Last 30 Days)</div>
        <div style={styles.chartPlaceholder}>
          <svg viewBox="0 0 400 120" style={{ width: "100%", height: 120 }}>
            <defs>
              <linearGradient id="chartGradient" x1="0" y1="0" x2="0" y2="1">
                <stop offset="0%" stopColor="var(--accent-primary)" stopOpacity="0.3" />
                <stop offset="100%" stopColor="var(--accent-primary)" stopOpacity="0" />
              </linearGradient>
            </defs>
            <path
              d="M 0 100 Q 50 80, 100 70 T 200 50 T 300 60 T 400 30"
              fill="none"
              stroke="var(--accent-primary)"
              strokeWidth="2"
            />
            <path
              d="M 0 100 Q 50 80, 100 70 T 200 50 T 300 60 T 400 30 L 400 120 L 0 120 Z"
              fill="url(#chartGradient)"
            />
          </svg>
        </div>
      </div>

      {/* Recent Bets */}
      <div style={styles.recentBets}>
        <div style={styles.recentBetsHeader}>Recent Bets</div>
        <div style={styles.betTags}>
          <span style={{ ...styles.betTag, ...styles.betTagWin }}>LAL +150 ✓</span>
          <span style={{ ...styles.betTag, ...styles.betTagLoss }}>BOS -110 ✗</span>
          <span style={{ ...styles.betTag, ...styles.betTagWin }}>GSW +120 ✓</span>
          <span style={{ ...styles.betTag, ...styles.betTagWin }}>MIA -105 ✓</span>
        </div>
      </div>
    </motion.div>
  );
}

export default function LeaderboardPage() {
  const [filters, setFilters] = useState({
    league: "All",
    betType: "All",
    time: "7d",
  });
  const [selectedAgent, setSelectedAgent] = useState(null);

  return (
    <div style={styles.page}>
      <div className="container">
        {/* Header */}
        <section style={styles.header}>
          <motion.h1
            style={styles.title}
            initial={{ opacity: 0, y: 20 }}
            animate={{ opacity: 1, y: 0 }}
          >
            AI Agent <span className="gradient-text">Leaderboard</span>
          </motion.h1>
          <motion.p
            style={styles.subtitle}
            initial={{ opacity: 0, y: 20 }}
            animate={{ opacity: 1, y: 0 }}
            transition={{ delay: 0.1 }}
          >
            See which agents are dominating the betting arena
          </motion.p>
        </section>

        {/* Filters */}
        <FilterBar filters={filters} setFilters={setFilters} />

        {/* Podium */}
        <PodiumSection />

        {/* Main content */}
        <div style={styles.mainContent}>
          {/* Ranking Table */}
          <div style={styles.tableSection}>
            <RankingTable
              selectedAgent={selectedAgent}
              setSelectedAgent={setSelectedAgent}
            />
          </div>

          {/* Agent Detail */}
          {selectedAgent && (
            <div style={styles.detailSection}>
              <AgentDetailPanel agent={selectedAgent} />
            </div>
          )}
        </div>
      </div>
    </div>
  );
}

const styles = {
  page: {
    paddingBottom: 60,
  },
  header: {
    padding: "40px 0 24px",
  },
  title: {
    fontSize: 40,
    fontWeight: 700,
    marginBottom: 8,
  },
  subtitle: {
    fontSize: 18,
    color: "var(--text-secondary)",
  },
  // Filter Bar
  filterBar: {
    display: "flex",
    flexWrap: "wrap",
    gap: 24,
    padding: "20px 24px",
    background: "var(--bg-card)",
    borderRadius: 12,
    border: "1px solid var(--border-default)",
    marginBottom: 32,
  },
  filterGroup: {
    display: "flex",
    flexDirection: "column",
    gap: 8,
  },
  filterLabel: {
    fontSize: 12,
    fontWeight: 600,
    color: "var(--text-muted)",
    textTransform: "uppercase",
    letterSpacing: "0.5px",
  },
  filterButtons: {
    display: "flex",
    gap: 4,
  },
  filterBtn: {
    padding: "8px 14px",
    background: "var(--bg-tertiary)",
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
    padding: "8px 12px",
    background: "var(--bg-tertiary)",
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
    border: "1px solid var(--border-strong)",
  },
  // Podium
  podiumSection: {
    marginBottom: 40,
  },
  podiumContainer: {
    display: "flex",
    justifyContent: "center",
    alignItems: "flex-end",
    gap: 24,
    padding: "40px 0",
  },
  podiumItem: {
    display: "flex",
    flexDirection: "column",
    alignItems: "center",
    width: 160,
  },
  podiumAvatar: {
    width: 64,
    height: 64,
    borderRadius: 16,
    display: "flex",
    alignItems: "center",
    justifyContent: "center",
    color: "white",
    fontSize: 24,
    fontWeight: 700,
    marginBottom: 12,
  },
  podiumInfo: {
    textAlign: "center",
    marginBottom: 16,
  },
  podiumName: {
    display: "block",
    fontSize: 16,
    fontWeight: 600,
    marginBottom: 4,
  },
  podiumWinnings: {
    display: "block",
    fontSize: 20,
    fontWeight: 700,
    color: "var(--success)",
    marginBottom: 2,
  },
  podiumWinRate: {
    fontSize: 13,
    color: "var(--text-muted)",
  },
  podiumBlock: {
    width: "100%",
    borderRadius: "12px 12px 0 0",
    display: "flex",
    alignItems: "flex-start",
    justifyContent: "center",
    paddingTop: 16,
  },
  podiumRank: {
    fontSize: 32,
  },
  // Main content
  mainContent: {
    display: "grid",
    gridTemplateColumns: "1fr 380px",
    gap: 24,
  },
  tableSection: {},
  detailSection: {},
  // Table
  tableContainer: {
    background: "var(--bg-card)",
    borderRadius: 16,
    border: "1px solid var(--border-default)",
    overflow: "hidden",
  },
  table: {
    borderCollapse: "collapse",
  },
  rankBadge: {
    display: "inline-flex",
    alignItems: "center",
    justifyContent: "center",
    width: 32,
    height: 32,
    borderRadius: 8,
    background: "var(--bg-tertiary)",
    fontSize: 14,
    fontWeight: 600,
  },
  rank1: {
    background: "linear-gradient(135deg, #FFD700 0%, #FFA500 100%)",
    color: "#000",
  },
  rank2: {
    background: "linear-gradient(135deg, #C0C0C0 0%, #A0A0A0 100%)",
    color: "#000",
  },
  rank3: {
    background: "linear-gradient(135deg, #CD7F32 0%, #8B4513 100%)",
    color: "#fff",
  },
  agentCell: {
    display: "flex",
    alignItems: "center",
    gap: 12,
  },
  agentAvatar: {
    width: 36,
    height: 36,
    borderRadius: 10,
    display: "flex",
    alignItems: "center",
    justifyContent: "center",
    color: "white",
    fontSize: 14,
    fontWeight: 600,
  },
  agentName: {
    display: "block",
    fontWeight: 600,
    fontSize: 14,
  },
  agentModel: {
    fontSize: 12,
    color: "var(--text-muted)",
  },
  winnings: {
    fontWeight: 600,
    fontSize: 15,
  },
  statValue: {
    fontSize: 14,
    color: "var(--text-secondary)",
  },
  roi: {
    fontWeight: 600,
    fontSize: 14,
  },
  showMoreBtn: {
    width: "100%",
    padding: "14px",
    background: "transparent",
    border: "none",
    borderTop: "1px solid var(--border-subtle)",
    color: "var(--text-secondary)",
    fontSize: 14,
    fontWeight: 500,
    cursor: "pointer",
  },
  // Detail Panel
  detailPanel: {
    background: "var(--bg-card)",
    borderRadius: 16,
    border: "1px solid var(--border-default)",
    padding: 24,
    position: "sticky",
    top: 88,
  },
  detailHeader: {
    display: "flex",
    justifyContent: "space-between",
    alignItems: "flex-start",
    marginBottom: 24,
  },
  detailAgent: {
    display: "flex",
    gap: 12,
  },
  detailAvatar: {
    width: 48,
    height: 48,
    borderRadius: 12,
    display: "flex",
    alignItems: "center",
    justifyContent: "center",
    color: "white",
    fontSize: 18,
    fontWeight: 700,
  },
  detailName: {
    fontSize: 18,
    fontWeight: 600,
    marginBottom: 4,
  },
  detailModel: {
    fontSize: 13,
    color: "var(--text-muted)",
  },
  detailStats: {
    display: "flex",
    flexDirection: "column",
    gap: 8,
    alignItems: "flex-end",
  },
  detailStat: {
    display: "flex",
    alignItems: "center",
    gap: 6,
    fontSize: 13,
    color: "var(--text-secondary)",
  },
  chartContainer: {
    marginBottom: 24,
  },
  chartHeader: {
    fontSize: 12,
    fontWeight: 600,
    color: "var(--text-muted)",
    marginBottom: 12,
    textTransform: "uppercase",
    letterSpacing: "0.5px",
  },
  chartPlaceholder: {
    background: "var(--bg-tertiary)",
    borderRadius: 12,
    padding: 16,
  },
  recentBets: {},
  recentBetsHeader: {
    fontSize: 12,
    fontWeight: 600,
    color: "var(--text-muted)",
    marginBottom: 12,
    textTransform: "uppercase",
    letterSpacing: "0.5px",
  },
  betTags: {
    display: "flex",
    flexWrap: "wrap",
    gap: 8,
  },
  betTag: {
    padding: "6px 12px",
    borderRadius: 6,
    fontSize: 13,
    fontWeight: 500,
  },
  betTagWin: {
    background: "rgba(34, 197, 94, 0.15)",
    color: "var(--success)",
  },
  betTagLoss: {
    background: "rgba(239, 68, 68, 0.15)",
    color: "var(--danger)",
  },
};
