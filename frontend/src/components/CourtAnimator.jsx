import { useState, useEffect, useRef, useMemo, useCallback } from "react";
import { motion, useSpring, useTransform } from "framer-motion";
import BasketballPlayer from "./BasketballPlayer";
import { useNBAPlayers } from "../hooks/useNBAPlayers";

// Player roles for positioning
const PLAYER_ROLES = ["pg", "sg", "sf", "pf", "c"];

// Base court zones for each role (percentage coordinates)
const BASE_ZONES = {
  home: {
    pg: { x: 28, y: 50 },
    sg: { x: 22, y: 32 },
    sf: { x: 22, y: 68 },
    pf: { x: 35, y: 42 },
    c: { x: 35, y: 58 },
  },
  away: {
    pg: { x: 72, y: 50 },
    sg: { x: 78, y: 32 },
    sf: { x: 78, y: 68 },
    pf: { x: 65, y: 42 },
    c: { x: 65, y: 58 },
  },
};

// Offense positions (attacking basket)
const OFFENSE_ZONES = {
  home: {
    pg: { x: 42, y: 50 },
    sg: { x: 35, y: 28 },
    sf: { x: 35, y: 72 },
    pf: { x: 28, y: 45 },
    c: { x: 25, y: 55 },
  },
  away: {
    pg: { x: 58, y: 50 },
    sg: { x: 65, y: 28 },
    sf: { x: 65, y: 72 },
    pf: { x: 72, y: 45 },
    c: { x: 75, y: 55 },
  },
};

/**
 * Continuous basketball court animation system
 * Uses spring physics for smooth, natural movement
 * Players never teleport - all movement is continuous from current position
 */
export default function CourtAnimator({ 
  events = [], 
  currentEventIndex = 0,
  homeTeam,
  awayTeam,
}) {
  const lastScoreRef = useRef({ home: 0, away: 0 });
  const phaseRef = useRef(0);
  
  // Core game state
  const [gameState, setGameState] = useState("warmup");
  const [possession, setPossession] = useState("home");
  const [scoringTeam, setScoringTeam] = useState(null);
  const [ballHandlerId, setBallHandlerId] = useState(0);
  
  // Animation cycle counter for generating new movement patterns
  const [movementCycle, setMovementCycle] = useState(0);

  // Get dynamic player data from NBA CDN
  const {
    homeHeadshots,
    awayHeadshots,
    homeJerseys,
    awayJerseys,
    homeLogo,
    awayLogo,
  } = useNBAPlayers(homeTeam, awayTeam);

  const homeColor = homeTeam?.color || "#3B82F6";
  const awayColor = awayTeam?.color || "#EF4444";
  const homeSecondary = homeTeam?.secondaryColor || "#ffffff";
  const awaySecondary = awayTeam?.secondaryColor || "#ffffff";
  
  // Home/Away tricodes for matching play_by_play events
  const homeTricode = homeTeam?.tricode || "";
  const awayTricode = awayTeam?.tricode || "";

  // Events are already normalized by useTrialStream
  const currentEvent = events[currentEventIndex] || null;

  // Continuous movement cycle - players always moving
  useEffect(() => {
    const interval = setInterval(() => {
      setMovementCycle(c => c + 1);
      phaseRef.current += 0.15;
    }, 3000);
    return () => clearInterval(interval);
  }, []);

  // Event analysis - updates game state based on event type
  useEffect(() => {
    if (!currentEvent) {
      setGameState("warmup");
      return;
    }

    const eventType = currentEvent.event_type;

    switch (eventType) {
      case "nba_game_update":
      case "nfl_game_update":
      case "espn_game_update":
      case "game_update": {
        const homeScore = (currentEvent.home_team || currentEvent.home_team_stats)?.score || 0;
        const awayScore = (currentEvent.away_team || currentEvent.away_team_stats)?.score || 0;
        
        if (homeScore > lastScoreRef.current.home) {
          setScoringTeam("home");
          setGameState("scored");
          setBallHandlerId(Math.floor(Math.random() * 5));
          setTimeout(() => {
            setGameState("playing");
            setPossession("away"); // Other team gets ball
          }, 2500);
        } else if (awayScore > lastScoreRef.current.away) {
          setScoringTeam("away");
          setGameState("scored");
          setBallHandlerId(5 + Math.floor(Math.random() * 5));
          setTimeout(() => {
            setGameState("playing");
            setPossession("home");
          }, 2500);
        } else {
          setGameState("playing");
          setPossession(Math.floor(phaseRef.current * 2) % 2 === 0 ? "home" : "away");
        }
        
        lastScoreRef.current = { home: homeScore, away: awayScore };
        break;
      }
      
      case "nba_play":
      case "nfl_play":
      case "espn_play":
      case "play_by_play": {
        // Handle play-by-play events for real-time animation
        const actionType = currentEvent.action_type || "";
        const teamTricode = currentEvent.team_tricode || "";
        const homeScore = currentEvent.home_score || lastScoreRef.current.home;
        const awayScore = currentEvent.away_score || lastScoreRef.current.away;
        
        // Determine which team is acting
        const isHomeTeam = teamTricode === homeTricode;
        const actingTeam = isHomeTeam ? "home" : "away";
        
        // Update score tracking
        if (homeScore > lastScoreRef.current.home || awayScore > lastScoreRef.current.away) {
          // A score happened!
          const scoringTeamNow = homeScore > lastScoreRef.current.home ? "home" : "away";
          setScoringTeam(scoringTeamNow);
          setGameState("scored");
          setBallHandlerId(scoringTeamNow === "home" ? Math.floor(Math.random() * 5) : 5 + Math.floor(Math.random() * 5));
          lastScoreRef.current = { home: homeScore, away: awayScore };
          setTimeout(() => {
            setGameState("playing");
            setPossession(scoringTeamNow === "home" ? "away" : "home");
          }, 1500);
        } else if (["2pt", "3pt", "freethrow"].includes(actionType) && !currentEvent.description?.includes("MISS")) {
          // Successful shot
          setScoringTeam(actingTeam);
          setGameState("scored");
          setBallHandlerId(isHomeTeam ? Math.floor(Math.random() * 5) : 5 + Math.floor(Math.random() * 5));
          setTimeout(() => {
            setGameState("playing");
            setPossession(isHomeTeam ? "away" : "home");
          }, 1500);
        } else if (actionType === "turnover" || actionType === "steal") {
          // Possession change
          setPossession(actingTeam === "home" ? "away" : "home");
          setGameState("playing");
          setBallHandlerId(actingTeam === "home" ? 5 : 0);
        } else if (actionType === "rebound") {
          // Rebound - team gets possession
          setPossession(actingTeam);
          setGameState("playing");
          setBallHandlerId(isHomeTeam ? 4 : 9); // Center/PF usually rebounds
        } else if (actionType === "foul") {
          // Foul - brief pause
          setGameState("playing");
        } else if (actionType === "period") {
          // Period start/end
          if (currentEvent.description?.includes("Start")) {
            setGameState("tipoff");
            setTimeout(() => {
              setGameState("playing");
              setPossession("home");
            }, 2000);
          }
        } else {
          // Default: keep playing
          setGameState("playing");
          setPossession(actingTeam);
          setBallHandlerId(isHomeTeam ? 0 : 5);
        }
        break;
      }
      
      case "game_start":
        setGameState("tipoff");
        setTimeout(() => {
          setGameState("playing");
          setPossession("home");
        }, 3000);
        break;
      
      case "game_initialize":
        setGameState("warmup");
        lastScoreRef.current = { home: 0, away: 0 };
        break;
        
      case "game_result": {
        const finalScore = currentEvent.final_score || {};
        const homeWon = (finalScore.home || 0) > (finalScore.away || 0);
        setScoringTeam(homeWon ? "home" : "away");
        setGameState("gameover");
        break;
      }
        
      default:
        // Keep current state for unknown events
        if (gameState === "warmup") {
          setGameState("playing");
        }
    }
  }, [currentEventIndex, currentEvent, homeTricode, awayTricode, gameState]);

  // Update ball handler based on possession
  useEffect(() => {
    if (gameState === "playing") {
      setBallHandlerId(possession === "home" ? 0 : 5); // Point guard has ball
    }
  }, [possession, gameState]);

  // Generate target positions for all players based on game state
  const playerTargets = useMemo(() => {
    const targets = [];
    const phase = phaseRef.current + movementCycle * 0.5;
    
    for (let i = 0; i < 10; i++) {
      const isHome = i < 5;
      const roleIndex = i % 5;
      const role = PLAYER_ROLES[roleIndex];
      const team = isHome ? "home" : "away";
      
      let baseZone;
      let wanderRadius = 8;
      
      switch (gameState) {
        case "playing": {
          const isOffense = (possession === "home" && isHome) || (possession === "away" && !isHome);
          if (isOffense) {
            // Offensive movement - push toward basket
            baseZone = OFFENSE_ZONES[team][role];
            wanderRadius = 10;
            // Point guard with ball moves more centrally
            if (i === ballHandlerId) {
              baseZone = { 
                x: isHome ? 40 : 60, 
                y: 45 + Math.sin(phase) * 8 
              };
            }
          } else {
            // Defensive positioning - mirror offense
            baseZone = {
              x: isHome ? BASE_ZONES.home[role].x : BASE_ZONES.away[role].x,
              y: BASE_ZONES[team][role].y,
            };
            wanderRadius = 6;
          }
          break;
        }
        
        case "scored": {
          const isScorer = (scoringTeam === "home" && isHome) || (scoringTeam === "away" && !isHome);
          if (isScorer) {
            // Celebration cluster
            baseZone = {
              x: isHome ? 42 : 58,
              y: 50 + (roleIndex - 2) * 8,
            };
            wanderRadius = 12;
          } else {
            // Defeated team retreats
            baseZone = {
              x: isHome ? 20 : 80,
              y: BASE_ZONES[team][role].y,
            };
            wanderRadius = 5;
          }
          break;
        }
        
        case "gameover": {
          const isWinner = (scoringTeam === "home" && isHome) || (scoringTeam === "away" && !isHome);
          if (isWinner) {
            // Big celebration at center court
            baseZone = {
              x: 50 + (Math.random() - 0.5) * 15,
              y: 50 + (roleIndex - 2) * 10,
            };
            wanderRadius = 15;
          } else {
            // Losers walk off
            baseZone = {
              x: isHome ? 15 : 85,
              y: 30 + roleIndex * 12,
            };
            wanderRadius = 3;
          }
          break;
        }
        
        case "tipoff": {
          // Everyone in starting positions, centers at middle
          baseZone = BASE_ZONES[team][role];
          if (role === "c") {
            baseZone = { x: isHome ? 48 : 52, y: 50 };
          }
          wanderRadius = 3;
          break;
        }
        
        default: // warmup
          baseZone = BASE_ZONES[team][role];
          wanderRadius = 10;
      }
      
      // Add natural wandering motion
      const wanderAngle = phase + i * 0.7;
      const wanderX = Math.sin(wanderAngle * 1.3) * wanderRadius;
      const wanderY = Math.cos(wanderAngle * 0.9) * wanderRadius * 0.7;
      
      targets.push({
        x: Math.max(8, Math.min(92, baseZone.x + wanderX)),
        y: Math.max(18, Math.min(82, baseZone.y + wanderY)),
      });
    }
    
    return targets;
  }, [gameState, possession, scoringTeam, movementCycle, ballHandlerId]);

  // Determine action for each player
  const getPlayerAction = useCallback((index) => {
    const isHome = index < 5;
    const hasBall = ballHandlerId === index;
    
    switch (gameState) {
      case "playing": {
        const isOffense = (possession === "home" && isHome) || (possession === "away" && !isHome);
        if (hasBall) return "dribble";
        if (isOffense) return "run";
        return "defend";
      }
      
      case "scored": {
        const isScorer = (scoringTeam === "home" && isHome) || (scoringTeam === "away" && !isHome);
        if (hasBall) return "shoot";
        if (isScorer) return "celebrate";
        return "injured";
      }
      
      case "gameover": {
        const isWinner = (scoringTeam === "home" && isHome) || (scoringTeam === "away" && !isHome);
        return isWinner ? "celebrate" : "injured";
      }
      
      case "tipoff":
        return index % 5 === 4 ? "shoot" : "idle"; // Centers jump
        
      default:
        return index === 0 || index === 5 ? "dribble" : "idle";
    }
  }, [gameState, possession, scoringTeam, ballHandlerId]);

  return (
    <div style={styles.container}>
      {/* Animated players - continuous spring-based movement */}
      <div style={styles.playersContainer}>
        {playerTargets.map((target, index) => {
          const isHome = index < 5;
          const roleIndex = index % 5;
          
          return (
            <SpringPlayer
              key={`player-${index}`}
              targetX={target.x}
              targetY={target.y}
              action={getPlayerAction(index)}
              teamColor={isHome ? homeColor : awayColor}
              secondaryColor={isHome ? homeSecondary : awaySecondary}
              jersey={isHome ? homeJerseys[roleIndex] : awayJerseys[roleIndex]}
              direction={isHome ? "right" : "left"}
              size={ballHandlerId === index ? 70 : 60}
              imageUrl={isHome ? homeHeadshots[roleIndex] : awayHeadshots[roleIndex]}
              teamLogo={isHome ? homeLogo : awayLogo}
              hasBall={ballHandlerId === index}
              zIndex={index}
            />
          );
        })}

        {/* Basketball follows ball handler with spring physics */}
        {ballHandlerId !== null && (
          <SpringBall
            targetX={playerTargets[ballHandlerId]?.x || 50}
            targetY={playerTargets[ballHandlerId]?.y || 50}
            direction={ballHandlerId < 5 ? "right" : "left"}
            isShoot={gameState === "scored"}
          />
        )}
      </div>

      {/* Score flash effect */}
      {gameState === "scored" && (
        <motion.div
          style={styles.scoreFlash}
          initial={{ opacity: 0 }}
          animate={{ opacity: [0, 0.5, 0] }}
          transition={{ duration: 0.8 }}
          key={`flash-${currentEventIndex}`}
        />
      )}

    </div>
  );
}

/**
 * Spring-based player component
 * Smoothly animates to target position using physics simulation
 * Never teleports - always continuous movement
 */
function SpringPlayer({
  targetX,
  targetY,
  action,
  teamColor,
  secondaryColor,
  jersey,
  direction,
  size,
  imageUrl,
  teamLogo,
  zIndex,
}) {
  // Spring physics for smooth continuous movement
  const springConfig = { stiffness: 50, damping: 20, mass: 1 };
  
  const x = useSpring(targetX, springConfig);
  const y = useSpring(targetY, springConfig);
  
  // Update targets - spring will smoothly animate
  useEffect(() => {
    x.set(targetX);
    y.set(targetY);
  }, [targetX, targetY, x, y]);
  
  // Transform to CSS percentage
  const left = useTransform(x, v => `${v}%`);
  const top = useTransform(y, v => `${v}%`);

  return (
    <motion.div
      style={{
        position: "absolute",
        left,
        top,
        zIndex: 100 + zIndex,
        willChange: "left, top",
      }}
    >
      <BasketballPlayer
        action={action}
        teamColor={teamColor}
        secondaryColor={secondaryColor}
        size={size}
        position={{ x: 50, y: 50 }}
        direction={direction}
        jersey={jersey}
        imageUrl={imageUrl}
        teamLogo={teamLogo}
        delay={0}
        showBall={false}
      />
    </motion.div>
  );
}

/**
 * Spring-based basketball component
 * Follows ball handler with natural physics-based lag
 */
function SpringBall({ targetX, targetY, direction, isShoot }) {
  const springConfig = { stiffness: 80, damping: 15, mass: 0.5 };
  
  const x = useSpring(targetX, springConfig);
  const y = useSpring(targetY, springConfig);
  
  useEffect(() => {
    x.set(targetX);
    y.set(targetY);
  }, [targetX, targetY, x, y]);
  
  const left = useTransform(x, v => `${v}%`);
  const top = useTransform(y, v => `${v}%`);

  return (
    <motion.div
      style={{
        position: "absolute",
        left,
        top,
        pointerEvents: "none",
        zIndex: 150,
      }}
    >
      <div style={{
        position: "relative",
        left: direction === "left" ? -20 : 20,
        top: 45,
      }}>
        {isShoot ? (
          <motion.img
            src="/assets/nba/basketball.png"
            alt="Basketball"
            style={{
              width: 24,
              height: 24,
              display: "block",
              filter: "drop-shadow(0 3px 6px rgba(0,0,0,0.4))"
            }}
            animate={{
              y: [0, -100, -140, -100, 0],
              x: [0, 50, 80, 50, 0],
              rotate: [0, 360, 720, 1080, 1440],
              scale: [1, 1.3, 1, 0.9, 1]
            }}
            transition={{
              duration: 2.5,
              ease: "easeInOut",
              repeat: Infinity,
            }}
          />
        ) : (
          <motion.img
            src="/assets/nba/basketball.png"
            alt="Basketball"
            style={{
              width: 24,
              height: 24,
              display: "block",
              filter: "drop-shadow(0 3px 6px rgba(0,0,0,0.4))"
            }}
            animate={{
              y: [0, 22, 0],
              rotate: [0, 180, 360],
            }}
            transition={{
              duration: 0.35,
              ease: "easeInOut",
              repeat: Infinity,
            }}
          />
        )}
      </div>
    </motion.div>
  );
}

const styles = {
  container: {
    position: "absolute",
    inset: 0,
    overflow: "hidden",
  },
  playersContainer: {
    position: "absolute",
    inset: 0,
    zIndex: 50,
    pointerEvents: "none",
  },
  scoreFlash: {
    position: "absolute",
    inset: 0,
    background: "radial-gradient(circle at center, rgba(255,255,255,0.5) 0%, transparent 70%)",
    pointerEvents: "none",
  },
};
