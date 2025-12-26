import { useMemo } from "react";
import { motion } from "framer-motion";

export default function OddsChart({ events, homeTeam, awayTeam, header }) {
  // Extract odds data from events
  const oddsData = useMemo(() => {
    const data = [];
    events.forEach((event, index) => {
      if (event.event_type === "odds_update") {
        data.push({
          index,
          timestamp: event.timestamp,
          homeOdds: event.home_odds,
          awayOdds: event.away_odds,
          homeProbability: event.home_probability * 100,
          awayProbability: event.away_probability * 100,
        });
      }
    });
    return data;
  }, [events]);

  // If no odds data, show placeholder
  if (oddsData.length === 0) {
    return (
      <div style={styles.container}>
        <div style={styles.backgroundOverlay} />
        <div style={styles.darkOverlay} />
        {header && <div style={styles.headerWrapper}>{header}</div>}
        <div style={styles.placeholder}>
          <div style={styles.placeholderIcon}>
          <svg
            width="28"
            height="28"
            viewBox="0 0 24 24"
            fill="none"
            stroke="currentColor"
            strokeWidth="1.5"
          >
            <polyline points="22 12 18 12 15 21 9 3 6 12 2 12" />
          </svg>
        </div>
        <span className="font-tech" style={styles.placeholderText}>
          WAITING FOR ODDS DATA...
        </span>
        </div>
      </div>
    );
  }

  const latestOdds = oddsData[oddsData.length - 1];
  
  // Chart dimensions with proper aspect ratio
  const chartWidth = 100;
  const chartHeight = 50; // Better aspect ratio for wider chart
  const padding = { top: 8, right: 28, bottom: 8, left: 8 };
  const innerWidth = chartWidth - padding.left - padding.right;
  const innerHeight = chartHeight - padding.top - padding.bottom;

  // Generate path for odds line
  const generatePath = (data, key) => {
    if (data.length === 0) return "";
    const maxY = 100;
    const minY = 0;
    const yScale = (val) =>
      padding.top + innerHeight - ((val - minY) / (maxY - minY)) * innerHeight;
    const xScale = (i) =>
      padding.left + (i / Math.max(data.length - 1, 1)) * innerWidth;

    return data
      .map((d, i) => {
        const x = xScale(i);
        const y = yScale(d[key]);
        return i === 0 ? `M ${x} ${y}` : `L ${x} ${y}`;
      })
      .join(" ");
  };

  const homePath = generatePath(oddsData, "homeProbability");
  const awayPath = generatePath(oddsData, "awayProbability");

  // Calculate trend
  const homeTrend = oddsData.length > 1
    ? oddsData[oddsData.length - 1].homeProbability - oddsData[0].homeProbability
    : 0;

  return (
    <div style={styles.container}>
      {/* Scoreboard background */}
      <div style={styles.backgroundOverlay} />
      {/* Dark gradient overlay for readability */}
      <div style={styles.darkOverlay} />
      
      {/* Header (ODDS HUD title) */}
      {header && <div style={styles.headerWrapper}>{header}</div>}
      
      {/* Top: Odds display */}
      <div style={styles.oddsDisplay}>
        <div style={styles.teamOdds}>
          <div
            style={{
              ...styles.teamIndicator,
              background: homeTeam.color,
            }}
          />
          <div style={styles.oddsInfo}>
            <span className="font-tech" style={styles.teamLabel}>
              {homeTeam.name}
            </span>
            <span
              className="font-tech"
              style={{ ...styles.probability, color: homeTeam.color }}
            >
              {latestOdds.homeProbability.toFixed(1)}%
            </span>
          </div>
        </div>

        <div style={styles.statsSection}>
          <div style={styles.stat}>
            <span className="font-tech" style={styles.statLabel}>TREND</span>
            <span
              className="font-tech"
              style={{
                ...styles.statValue,
                color: homeTrend > 0 ? "var(--success)" : homeTrend < 0 ? "var(--danger)" : "var(--text-secondary)",
              }}
            >
              {homeTrend > 0 ? "+" : ""}{homeTrend.toFixed(1)}%
            </span>
          </div>
          <div style={styles.stat}>
            <span className="font-tech" style={styles.statLabel}>SPREAD</span>
            <span className="font-tech" style={styles.statValue}>
              {(latestOdds.homeProbability - latestOdds.awayProbability).toFixed(1)}%
            </span>
          </div>
        </div>

        <div style={styles.teamOdds}>
          <div
            style={{
              ...styles.teamIndicator,
              background: awayTeam.color,
            }}
          />
          <div style={styles.oddsInfo}>
            <span className="font-tech" style={styles.teamLabel}>
              {awayTeam.name}
            </span>
            <span
              className="font-tech"
              style={{ ...styles.probability, color: awayTeam.color }}
            >
              {latestOdds.awayProbability.toFixed(1)}%
            </span>
          </div>
        </div>
      </div>

      {/* Bottom: Line chart */}
      <div style={styles.chartContainer}>
        <svg
          viewBox={`0 0 ${chartWidth} ${chartHeight}`}
          style={styles.chart}
          preserveAspectRatio="xMidYMid meet"
        >
          <defs>
            <filter id="glow">
              <feGaussianBlur stdDeviation="0.5" result="coloredBlur" />
              <feMerge>
                <feMergeNode in="coloredBlur" />
                <feMergeNode in="SourceGraphic" />
              </feMerge>
            </filter>
          </defs>

          {/* Grid lines */}
          {[0, 25, 50, 75, 100].map((val) => {
            const y = padding.top + innerHeight - (val / 100) * innerHeight;
            return (
              <line
                key={val}
                x1={padding.left}
                y1={y}
                x2={chartWidth - padding.right}
                y2={y}
                stroke="var(--glass-border)"
                strokeWidth="0.3"
                opacity={val === 50 ? "0.8" : "0.3"}
                strokeDasharray={val === 50 ? "1 1" : "none"}
              />
            );
          })}

          {/* Lines */}
          <motion.path
            d={homePath}
            fill="none"
            stroke={homeTeam.color}
            strokeWidth="1"
            filter="url(#glow)"
            initial={{ pathLength: 0 }}
            animate={{ pathLength: 1 }}
            transition={{ duration: 0.6 }}
          />
          <motion.path
            d={awayPath}
            fill="none"
            stroke={awayTeam.color}
            strokeWidth="1"
            filter="url(#glow)"
            initial={{ pathLength: 0 }}
            animate={{ pathLength: 1 }}
            transition={{ duration: 0.6, delay: 0.1 }}
          />

          {/* End points */}
          {oddsData.length > 0 && (
            <>
              <motion.circle
                cx={padding.left + ((oddsData.length - 1) / Math.max(oddsData.length - 1, 1)) * innerWidth}
                cy={padding.top + innerHeight - (latestOdds.homeProbability / 100) * innerHeight}
                r="1.5"
                fill={homeTeam.color}
                initial={{ scale: 0 }}
                animate={{ scale: 1 }}
                transition={{ delay: 0.6 }}
              />
              <motion.circle
                cx={padding.left + ((oddsData.length - 1) / Math.max(oddsData.length - 1, 1)) * innerWidth}
                cy={padding.top + innerHeight - (latestOdds.awayProbability / 100) * innerHeight}
                r="1.5"
                fill={awayTeam.color}
                initial={{ scale: 0 }}
                animate={{ scale: 1 }}
                transition={{ delay: 0.7 }}
              />
            </>
          )}

          {/* Y-axis labels */}
          <text x={chartWidth - padding.right + 2} y={padding.top + 2} style={styles.svgLabel}>100%</text>
          <text x={chartWidth - padding.right + 2} y={padding.top + innerHeight / 2 + 1} style={styles.svgLabel}>50%</text>
          <text x={chartWidth - padding.right + 2} y={padding.top + innerHeight + 2} style={styles.svgLabel}>0%</text>
        </svg>

        {/* Updates count */}
        <div style={styles.updatesCount}>
          <span className="font-tech" style={styles.updatesText}>
            {oddsData.length} updates
          </span>
        </div>
      </div>
    </div>
  );
}

const styles = {
  container: {
    position: "relative",
    display: "flex",
    flexDirection: "column",
    gap: "0",
    padding: "20px",
    borderRadius: "16px",
    overflow: "hidden",
    border: "1px solid rgba(100, 180, 255, 0.3)",
    boxShadow: "0 8px 32px rgba(0, 0, 0, 0.4), inset 0 0 60px rgba(0, 150, 255, 0.1)",
  },
  backgroundOverlay: {
    position: "absolute",
    top: 0,
    left: 0,
    right: 0,
    bottom: 0,
    backgroundImage: `url(/assets/nba/background/scoreboard.jpg)`,
    backgroundSize: "cover",
    backgroundPosition: "center",
    zIndex: 0,
  },
  // Dark gradient overlay for better text readability
  darkOverlay: {
    position: "absolute",
    top: 0,
    left: 0,
    right: 0,
    bottom: 0,
    background: "linear-gradient(180deg, rgba(15, 23, 42, 0.88) 0%, rgba(15, 23, 42, 0.78) 50%, rgba(15, 23, 42, 0.88) 100%)",
    zIndex: 1,
    pointerEvents: "none",
  },
  headerWrapper: {
    position: "relative",
    zIndex: 2,
    background: "linear-gradient(180deg, rgba(15, 23, 42, 0.6) 0%, rgba(15, 23, 42, 0.4) 100%)",
    margin: "-20px -20px 0 -20px",
    padding: "16px 20px",
    borderBottom: "1px solid rgba(100, 180, 255, 0.15)",
  },
  placeholder: {
    position: "relative",
    zIndex: 2,
    height: "200px",
    display: "flex",
    flexDirection: "column",
    alignItems: "center",
    justifyContent: "center",
    gap: "10px",
    color: "var(--text-muted)",
    background: "linear-gradient(180deg, rgba(15, 23, 42, 0.5) 0%, rgba(15, 23, 42, 0.4) 100%)",
    margin: "-20px",
    marginTop: "0",
  },
  placeholderIcon: {
    opacity: 0.5,
  },
  placeholderText: {
    fontSize: "10px",
    letterSpacing: "0.15em",
  },
  oddsDisplay: {
    position: "relative",
    zIndex: 2,
    display: "flex",
    alignItems: "center",
    justifyContent: "space-between",
    gap: "12px",
    background: "linear-gradient(145deg, rgba(30, 41, 59, 0.5) 0%, rgba(15, 23, 42, 0.4) 100%)",
    margin: "0 -20px",
    padding: "16px 20px",
    borderBottom: "1px solid rgba(100, 180, 255, 0.1)",
  },
  teamOdds: {
    display: "flex",
    alignItems: "center",
    gap: "8px",
    flex: 1,
  },
  teamIndicator: {
    width: "3px",
    height: "32px",
    borderRadius: "2px",
  },
  oddsInfo: {
    display: "flex",
    flexDirection: "column",
    gap: "1px",
  },
  teamLabel: {
    fontSize: "10px",
    color: "var(--text-secondary)",
    letterSpacing: "0.05em",
  },
  probability: {
    fontSize: "18px",
    fontWeight: "700",
  },
  statsSection: {
    display: "flex",
    gap: "16px",
    padding: "0 16px",
    borderLeft: "1px solid rgba(255, 255, 255, 0.2)",
    borderRight: "1px solid rgba(255, 255, 255, 0.2)",
  },
  stat: {
    display: "flex",
    flexDirection: "column",
    alignItems: "center",
    gap: "1px",
  },
  statLabel: {
    fontSize: "8px",
    color: "var(--text-muted)",
    letterSpacing: "0.1em",
  },
  statValue: {
    fontSize: "12px",
    color: "var(--text-secondary)",
    fontWeight: "600",
  },
  chartContainer: {
    position: "relative",
    zIndex: 2,
    height: "160px",
    background: "linear-gradient(180deg, rgba(15, 23, 42, 0.4) 0%, rgba(15, 23, 42, 0.6) 100%)",
    margin: "0 -20px -20px -20px",
    padding: "12px 20px",
  },
  chart: {
    width: "100%",
    height: "100%",
  },
  svgLabel: {
    fontSize: "3.5px",
    fill: "var(--text-muted)",
    fontFamily: "var(--font-tech)",
  },
  updatesCount: {
    position: "absolute",
    left: "20px",
    bottom: "12px",
  },
  updatesText: {
    fontSize: "9px",
    color: "var(--text-muted)",
    letterSpacing: "0.05em",
  },
};
