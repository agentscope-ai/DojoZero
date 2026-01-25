/**
 * CourtAnimator - Basketball court animation with player movement
 * 
 * Features:
 * - Spring-based smooth player movement
 * - Ball animation following current player
 * - Game state driven positioning
 * - Raw player headshot display (game-style, no circular frames)
 * - Movement constrained to visible court area
 */

import { useState, useEffect, useMemo, useRef } from "react";
import { motion, useSpring, useTransform } from "framer-motion";
import { useGamePlayers } from "../../hooks/useGamePlayers";
import { NBA_CDN } from "../../data/nba/teams";

// =============================================================================
// CONSTANTS
// =============================================================================

// Player roles for positioning
const PLAYER_ROLES = ["pg", "sg", "sf", "pf", "c"];

// Court bounds - constrained to visible court area (based on screenshot)
// Y: 55-95% keeps players within the visible court area (moved down 20%)
const COURT_BOUNDS = {
  minX: 10,
  maxX: 90,
  minY: 55,  // below scoreboard/overlay area
  maxY: 95,  // near bottom of visible area
};

// Base court zones for each role (percentage coordinates)
// Adjusted to stay within COURT_BOUNDS
const BASE_ZONES = {
  home: {
    pg: { x: 28, y: 72 },
    sg: { x: 22, y: 60 },
    sf: { x: 22, y: 85 },
    pf: { x: 35, y: 68 },
    c: { x: 35, y: 78 },
  },
  away: {
    pg: { x: 72, y: 72 },
    sg: { x: 78, y: 60 },
    sf: { x: 78, y: 85 },
    pf: { x: 65, y: 68 },
    c: { x: 65, y: 78 },
  },
};

// Offense positions (attacking basket) - constrained to court bounds (moved down 20%)
const OFFENSE_ZONES = {
  home: {
    pg: { x: 70, y: 72 },
    sg: { x: 62, y: 58 },
    sf: { x: 62, y: 88 },
    pf: { x: 78, y: 68 },
    c: { x: 82, y: 76 },
  },
  away: {
    pg: { x: 30, y: 72 },
    sg: { x: 38, y: 58 },
    sf: { x: 38, y: 88 },
    pf: { x: 22, y: 68 },
    c: { x: 18, y: 76 },
  },
};

// Clamp position to court bounds
const clampPosition = (x, y) => ({
  x: Math.max(COURT_BOUNDS.minX, Math.min(COURT_BOUNDS.maxX, x)),
  y: Math.max(COURT_BOUNDS.minY, Math.min(COURT_BOUNDS.maxY, y)),
});

// =============================================================================
// SPRING-BASED PLAYER COMPONENT - Game Style (No Circular Frame)
// =============================================================================

// Pulsing Ring Effect for Ball Handler / Active Players
function PulsingRings({ color, isPlaying }) {
  return (
    <>
      {[0, 1, 2].map((i) => (
        <motion.div
          key={i}
          style={{
            position: "absolute",
            inset: -15 - i * 8,
            borderRadius: 16,
            border: `2px solid ${color}`,
            opacity: 0,
          }}
          animate={isPlaying ? {
            scale: [0.8, 1.1, 1.2],
            opacity: [0, 0.6, 0],
          } : { opacity: 0 }}
          transition={{
            duration: 1.2,
            delay: i * 0.3,
            repeat: Infinity,
            ease: "easeOut",
          }}
        />
      ))}
    </>
  );
}

function SpringPlayer({
  targetX,
  targetY,
  teamColor,
  direction,
  size,
  headshotUrl,
  hasBall,
  isActive,
  zIndex,
  isPlaying = true,
  playerName = "",
}) {
  const [imageError, setImageError] = useState(false);
  
  // Spring physics for smooth movement
  const springConfig = isPlaying 
    ? { stiffness: 50, damping: 20, mass: 1 }
    : { stiffness: 300, damping: 30, mass: 1 };
  const x = useSpring(targetX, springConfig);
  const y = useSpring(targetY, springConfig);

  useEffect(() => {
    x.set(targetX);
    y.set(targetY);
  }, [targetX, targetY, x, y]);

  const left = useTransform(x, (v) => `${v}%`);
  const top = useTransform(y, (v) => `${v}%`);

  // Player size based on ball possession and activity - MORE dramatic scaling
  const playerSize = hasBall ? size * 1.8 : (isActive ? size * 1.2 : size);

  return (
    <motion.div
      style={{
        position: "absolute",
        left,
        top,
        transform: "translate(-50%, -50%)",
        zIndex: 100 + zIndex + (hasBall ? 50 : 0) + (isActive ? 30 : 0),
      }}
    >
      {/* Player container - Game style */}
      <motion.div
        style={{
          position: "relative",
          width: playerSize,
          height: playerSize * 1.3, // Taller for headshot aspect ratio
        }}
        animate={(isActive || hasBall) && isPlaying 
          ? { scale: [1, 1.06, 1] } 
          : { scale: 1 }
        }
        transition={{ 
          duration: 0.6, 
          repeat: (isActive || hasBall) && isPlaying ? Infinity : 0,
          ease: "easeInOut"
        }}
      >

        {/* Enhanced glow effect for active/ball handler */}
        {(hasBall || isActive) && (
          <motion.div
            style={{
              position: "absolute",
              inset: hasBall ? -20 : -14,
              borderRadius: 12,
              background: `radial-gradient(ellipse, ${teamColor}60 0%, ${teamColor}30 40%, transparent 70%)`,
              filter: "blur(12px)",
            }}
            animate={isPlaying ? { 
              opacity: [0.7, 1, 0.7],
              scale: [0.95, 1.05, 0.95],
            } : { opacity: 0.7 }}
            transition={{ duration: 1.2, repeat: Infinity, ease: "easeInOut" }}
          />
        )}

        {/* Ground shadow - enhanced for highlighted players */}
        <div
          style={{
            position: "absolute",
            bottom: -10,
            left: "50%",
            transform: "translateX(-50%)",
            width: playerSize * (hasBall ? 0.9 : 0.7),
            height: hasBall ? 12 : 8,
            background: hasBall 
              ? `radial-gradient(ellipse, ${teamColor}60 0%, rgba(0,0,0,0.4) 40%, transparent 70%)`
              : "radial-gradient(ellipse, rgba(0,0,0,0.5) 0%, transparent 70%)",
            borderRadius: "50%",
          }}
        />

        {/* Player image - Raw headshot, no circular frame */}
        <div
          style={{
            width: "100%",
            height: "100%",
            position: "relative",
            overflow: "visible",
            transform: `scaleX(${direction === "left" ? -1 : 1})`,
          }}
        >
          {headshotUrl && !imageError ? (
            <img
              src={headshotUrl}
              alt={playerName || "Player"}
              style={{
                width: "100%",
                height: "100%",
                objectFit: "contain",
                objectPosition: "center top",
                filter: hasBall 
                  ? `drop-shadow(0 0 16px ${teamColor}) drop-shadow(0 0 24px ${teamColor}80) drop-shadow(0 4px 8px rgba(0,0,0,0.6))`
                  : isActive
                  ? `drop-shadow(0 0 10px ${teamColor}) drop-shadow(0 4px 8px rgba(0,0,0,0.5))`
                  : "drop-shadow(0 4px 8px rgba(0,0,0,0.4))",
              }}
              onError={() => setImageError(true)}
            />
          ) : (
            // Fallback: Simple player silhouette
            <div
              style={{
                width: "100%",
                height: "100%",
                display: "flex",
                alignItems: "center",
                justifyContent: "center",
                background: `linear-gradient(180deg, ${teamColor} 0%, ${teamColor}99 100%)`,
                borderRadius: 8,
                boxShadow: hasBall 
                  ? `0 0 30px ${teamColor}, 0 0 50px ${teamColor}60, 0 4px 12px rgba(0, 0, 0, 0.4)`
                  : isActive
                  ? `0 0 20px ${teamColor}80, 0 4px 12px rgba(0, 0, 0, 0.4)`
                  : "0 4px 12px rgba(0, 0, 0, 0.4)",
              }}
            >
              <span
                style={{
                  color: "#FFFFFF",
                  fontSize: playerSize * 0.6,
                  fontWeight: 900,
                  fontFamily: "'Bebas Neue', 'Impact', sans-serif",
                  textShadow: "0 2px 4px rgba(0,0,0,0.5)",
                }}
              >
                👤
              </span>
            </div>
          )}
        </div>
      </motion.div>
    </motion.div>
  );
}

// =============================================================================
// SPRING-BASED BALL COMPONENT
// =============================================================================

function SpringBall({ targetX, targetY, direction, isScoring, isPlaying = true }) {
  const springConfig = isPlaying 
    ? { stiffness: 80, damping: 15, mass: 0.5 }
    : { stiffness: 300, damping: 30, mass: 0.5 };
  const x = useSpring(targetX, springConfig);
  const y = useSpring(targetY, springConfig);

  useEffect(() => {
    x.set(targetX);
    y.set(targetY);
  }, [targetX, targetY, x, y]);

  const left = useTransform(x, (v) => `${v}%`);
  const top = useTransform(y, (v) => `${v}%`);

  return (
    <motion.div
      style={{
        position: "absolute",
        left,
        top,
        transform: "translate(-50%, -50%)",
        pointerEvents: "none",
        zIndex: 150,
      }}
    >
      <div
        style={{
          position: "relative",
          left: direction === "left" ? -20 : 20,
          top: 35,
        }}
      >
        {!isPlaying ? (
          // Static ball when paused
          <div style={ballStyles.ball}>🏀</div>
        ) : isScoring ? (
          <>
            {/* Ball trail effect during scoring */}
            <motion.div
              style={{
                position: "absolute",
                fontSize: 24,
                opacity: 0.4,
                filter: "blur(3px)",
              }}
              animate={{
                y: [0, -50, -80, -50, 0],
                x: [0, 25, 40, 25, 0],
                opacity: [0, 0.4, 0.3, 0.2, 0],
                scale: [0.8, 1, 0.9, 0.7, 0.5],
              }}
              transition={{
                duration: 1.8,
                ease: "easeInOut",
                repeat: Infinity,
                delay: 0.1,
              }}
            >
              🏀
            </motion.div>
            {/* Main scoring ball */}
            <motion.div
              style={ballStyles.scoringBall}
              animate={{
                y: [0, -70, -110, -70, 0],
                x: [0, 35, 55, 35, 0],
                rotate: [0, 360, 720, 1080, 1440],
                scale: [1, 1.4, 1.2, 1, 1],
              }}
              transition={{
                duration: 1.8,
                ease: [0.25, 0.1, 0.25, 1],
                repeat: Infinity,
              }}
            >
              🏀
            </motion.div>
            {/* Sparkle around scoring ball */}
            <motion.div
              style={{
                position: "absolute",
                width: 60,
                height: 60,
                left: -17,
                top: -17,
                background: "radial-gradient(circle, rgba(255,215,0,0.6) 0%, transparent 70%)",
                borderRadius: "50%",
                filter: "blur(8px)",
              }}
              animate={{
                y: [0, -70, -110, -70, 0],
                x: [0, 35, 55, 35, 0],
                scale: [0.5, 1.5, 1, 0.8, 0.5],
                opacity: [0.3, 0.8, 0.6, 0.4, 0.3],
              }}
              transition={{
                duration: 1.8,
                ease: "easeInOut",
                repeat: Infinity,
              }}
            />
          </>
        ) : (
          <>
            {/* Normal dribble ball */}
            <motion.div
              style={ballStyles.ball}
              animate={{
                y: [0, 14, 0],
                rotate: [0, 180, 360],
                scale: [1, 0.95, 1],
              }}
              transition={{
                duration: 0.4,
                ease: "easeInOut",
                repeat: Infinity,
              }}
            >
              🏀
            </motion.div>
            {/* Subtle shadow/reflection under ball */}
            <motion.div
              style={{
                position: "absolute",
                top: 28,
                left: 3,
                width: 20,
                height: 6,
                background: "radial-gradient(ellipse, rgba(0,0,0,0.3) 0%, transparent 70%)",
                borderRadius: "50%",
              }}
              animate={{
                scaleX: [1, 1.3, 1],
                opacity: [0.5, 0.3, 0.5],
              }}
              transition={{
                duration: 0.4,
                ease: "easeInOut",
                repeat: Infinity,
              }}
            />
          </>
        )}
      </div>
    </motion.div>
  );
}

const ballStyles = {
  ball: {
    fontSize: 28,
    filter: "drop-shadow(0 4px 10px rgba(0,0,0,0.5))",
  },
  scoringBall: {
    fontSize: 32,
    filter: "drop-shadow(0 0 15px rgba(255,215,0,0.8)) drop-shadow(0 4px 12px rgba(0,0,0,0.6))",
  },
};

// =============================================================================
// MAIN COURT ANIMATOR COMPONENT
// =============================================================================

export default function CourtAnimator({
  events = [],
  currentEventIndex = 0,
  homeTeam,
  awayTeam,
  isPlaying = true,
}) {
  const lastScoreRef = useRef({ home: 0, away: 0 });
  const phaseRef = useRef(0);

  // Core game state
  const [gameState, setGameState] = useState("warmup");
  const [possession, setPossession] = useState("home");
  const [scoringTeam, setScoringTeam] = useState(null);
  const [ballHandlerId, setBallHandlerId] = useState(0);
  const [activePlayerId, setActivePlayerId] = useState(null);

  // Animation cycle for movement
  const [movementCycle, setMovementCycle] = useState(0);

  // Team data
  const homeColor = homeTeam?.color || "#3B82F6";
  const awayColor = awayTeam?.color || "#EF4444";
  const homeTricode = homeTeam?.tricode || "";
  const awayTricode = awayTeam?.tricode || "";

  // Get player data from events using the hook
  const { homePlayers, awayPlayers, hasData } = useGamePlayers(
    events, 
    homeTricode, 
    awayTricode
  );

  // Current event
  const currentEvent = events[currentEventIndex] || null;

  // Continuous movement cycle - only when playing
  useEffect(() => {
    if (!isPlaying) return;
    
    const interval = setInterval(() => {
      setMovementCycle((c) => c + 1);
      phaseRef.current += 0.15;
    }, 3000);
    return () => clearInterval(interval);
  }, [isPlaying]);

  // Event analysis - updates game state
  useEffect(() => {
    if (!currentEvent) {
      setGameState("warmup");
      return;
    }

    const eventType = currentEvent.event_type;

    switch (eventType) {
      case "game_update": {
        const homeScore = currentEvent.home_team?.score || 0;
        const awayScore = currentEvent.away_team?.score || 0;

        if (homeScore > lastScoreRef.current.home) {
          setScoringTeam("home");
          setGameState("scored");
          setBallHandlerId(Math.floor(Math.random() * 5));
          setTimeout(() => {
            setGameState("playing");
            setPossession("away");
          }, 2000);
        } else if (awayScore > lastScoreRef.current.away) {
          setScoringTeam("away");
          setGameState("scored");
          setBallHandlerId(5 + Math.floor(Math.random() * 5));
          setTimeout(() => {
            setGameState("playing");
            setPossession("home");
          }, 2000);
        } else {
          setGameState("playing");
          setPossession(Math.floor(phaseRef.current * 2) % 2 === 0 ? "home" : "away");
        }

        lastScoreRef.current = { home: homeScore, away: awayScore };
        break;
      }

      case "play_by_play": {
        const actionType = currentEvent.action_type || "";
        const teamTricode = currentEvent.team_tricode || "";
        const isHomeTeam = teamTricode === homeTricode;
        const actingTeam = isHomeTeam ? "home" : "away";

        // Handle different play types
        if (["2pt", "3pt", "freethrow"].includes(actionType)) {
          setScoringTeam(actingTeam);
          setGameState("scored");
          setBallHandlerId(isHomeTeam ? Math.floor(Math.random() * 5) : 5 + Math.floor(Math.random() * 5));
          setActivePlayerId(currentEvent.person_id);
          setTimeout(() => {
            setGameState("playing");
            setPossession(isHomeTeam ? "away" : "home");
            setActivePlayerId(null);
          }, 2000);
        } else if (actionType === "steal" || actionType === "turnover") {
          setPossession(actingTeam === "home" ? "away" : "home");
          setGameState("playing");
          setActivePlayerId(currentEvent.person_id);
          setTimeout(() => setActivePlayerId(null), 1500);
        } else if (actionType === "rebound") {
          setPossession(actingTeam);
          setGameState("playing");
          setBallHandlerId(isHomeTeam ? 4 : 9);
        } else if (actionType === "block") {
          setActivePlayerId(currentEvent.person_id);
          setTimeout(() => setActivePlayerId(null), 1500);
        } else {
          setGameState("playing");
          setPossession(actingTeam);
        }
        break;
      }

      case "game_start":
        setGameState("tipoff");
        setTimeout(() => {
          setGameState("playing");
          setPossession("home");
        }, 2000);
        break;

      case "game_initialize":
        setGameState("warmup");
        lastScoreRef.current = { home: 0, away: 0 };
        break;

      case "game_result":
        setGameState("gameover");
        break;

      default:
        if (gameState === "warmup") {
          setGameState("playing");
        }
    }
  }, [currentEventIndex, currentEvent, homeTricode, awayTricode, gameState]);

  // Update ball handler based on possession
  useEffect(() => {
    if (gameState === "playing") {
      setBallHandlerId(possession === "home" ? 0 : 5);
    }
  }, [possession, gameState]);

  // Generate target positions for all players (constrained to court bounds)
  const playerTargets = useMemo(() => {
    const targets = [];
    const phase = phaseRef.current + movementCycle * 0.5;

    for (let i = 0; i < 10; i++) {
      const isHome = i < 5;
      const roleIndex = i % 5;
      const role = PLAYER_ROLES[roleIndex];
      const team = isHome ? "home" : "away";

      let baseZone;
      let wanderRadius = 6;

      switch (gameState) {
        case "playing": {
          const isOffense = (possession === "home" && isHome) || (possession === "away" && !isHome);
          if (isOffense) {
            baseZone = OFFENSE_ZONES[team][role];
            wanderRadius = 8;
            if (i === ballHandlerId) {
              baseZone = {
                x: isHome ? 68 : 32,
                y: 72 + Math.sin(phase) * 6, // Moved down 20%
              };
            }
          } else {
            baseZone = BASE_ZONES[team][role];
            wanderRadius = 5;
          }
          break;
        }

        case "scored": {
          const isScorer = (scoringTeam === "home" && isHome) || (scoringTeam === "away" && !isHome);
          if (isScorer) {
            baseZone = {
              x: isHome ? 55 : 45,
              y: 72 + (roleIndex - 2) * 5, // Moved down 20%
            };
            wanderRadius = 10;
          } else {
            baseZone = {
              x: isHome ? 28 : 72,
              y: BASE_ZONES[team][role].y,
            };
            wanderRadius = 4;
          }
          break;
        }

        case "gameover": {
          const isWinner = (scoringTeam === "home" && isHome) || (scoringTeam === "away" && !isHome);
          if (isWinner) {
            baseZone = {
              x: 50 + (Math.random() - 0.5) * 12,
              y: 72 + (roleIndex - 2) * 6, // Moved down 20%
            };
            wanderRadius = 12;
          } else {
            baseZone = {
              x: isHome ? 18 : 82,
              y: 62 + roleIndex * 7, // Moved down 20%
            };
            wanderRadius = 3;
          }
          break;
        }

        case "tipoff": {
          baseZone = BASE_ZONES[team][role];
          if (role === "c") {
            baseZone = { x: isHome ? 48 : 52, y: 72 }; // Moved down 20%
          }
          wanderRadius = 2;
          break;
        }

        default:
          baseZone = BASE_ZONES[team][role];
          wanderRadius = 8;
      }

      // Add natural wandering motion
      const wanderAngle = phase + i * 0.7;
      const wanderX = Math.sin(wanderAngle * 1.3) * wanderRadius;
      const wanderY = Math.cos(wanderAngle * 0.9) * wanderRadius * 0.6;

      // Clamp to court bounds
      const clamped = clampPosition(baseZone.x + wanderX, baseZone.y + wanderY);
      targets.push(clamped);
    }

    return targets;
  }, [gameState, possession, scoringTeam, movementCycle, ballHandlerId]);

  return (
    <div style={styles.container}>
      {/* Players */}
      <div style={styles.playersContainer}>
        {playerTargets.map((target, index) => {
          const isHome = index < 5;
          const roleIndex = index % 5;
          const players = isHome ? homePlayers : awayPlayers;
          const player = players[roleIndex];

          return (
            <SpringPlayer
              key={`player-${index}`}
              targetX={target.x}
              targetY={target.y}
              teamColor={isHome ? homeColor : awayColor}
              direction={isHome ? "right" : "left"}
              size={ballHandlerId === index ? 60 : 52}
              headshotUrl={player?.id ? NBA_CDN.playerHeadshot(player.id) : null}
              hasBall={ballHandlerId === index}
              isActive={activePlayerId === player?.id}
              zIndex={index}
              isPlaying={isPlaying}
              playerName={player?.name}
            />
          );
        })}

        {/* Ball */}
        {ballHandlerId !== null && (
          <SpringBall
            targetX={playerTargets[ballHandlerId]?.x || 50}
            targetY={playerTargets[ballHandlerId]?.y || 75}
            direction={ballHandlerId < 5 ? "right" : "left"}
            isScoring={gameState === "scored"}
            isPlaying={isPlaying}
          />
        )}
      </div>

      {/* Enhanced Score Flash with multiple layers */}
      {gameState === "scored" && (
        <>
          {/* Main radial flash */}
          <motion.div
            style={styles.scoreFlash}
            initial={{ opacity: 0, scale: 0.8 }}
            animate={{ 
              opacity: [0, 0.7, 0.4, 0],
              scale: [0.8, 1.2, 1.1, 1],
            }}
            transition={{ duration: 0.8, ease: "easeOut" }}
            key={`flash-${currentEventIndex}`}
          />
          {/* Expanding ring effect */}
          {[0, 1, 2].map((i) => (
            <motion.div
              key={`ring-${currentEventIndex}-${i}`}
              style={{
                position: "absolute",
                inset: 0,
                border: `3px solid ${scoringTeam === "home" ? homeColor : awayColor}`,
                borderRadius: "50%",
                opacity: 0,
                pointerEvents: "none",
              }}
              initial={{ scale: 0.3, opacity: 0 }}
              animate={{ 
                scale: [0.3, 2, 2.5],
                opacity: [0, 0.6, 0],
              }}
              transition={{ 
                duration: 1,
                delay: i * 0.15,
                ease: "easeOut",
              }}
            />
          ))}
          {/* Sparkle particles */}
          {[...Array(12)].map((_, i) => (
            <motion.div
              key={`sparkle-${currentEventIndex}-${i}`}
              style={{
                position: "absolute",
                left: "50%",
                top: "70%",
                width: 4,
                height: 4,
                borderRadius: "50%",
                background: i % 2 === 0 ? "#FFD700" : (scoringTeam === "home" ? homeColor : awayColor),
                boxShadow: `0 0 8px ${i % 2 === 0 ? "#FFD700" : (scoringTeam === "home" ? homeColor : awayColor)}`,
              }}
              initial={{ 
                x: 0, 
                y: 0, 
                scale: 0, 
                opacity: 0 
              }}
              animate={{ 
                x: Math.cos((i / 12) * Math.PI * 2) * (100 + Math.random() * 80),
                y: Math.sin((i / 12) * Math.PI * 2) * (60 + Math.random() * 40) - 30,
                scale: [0, 1.5, 0.5],
                opacity: [0, 1, 0],
              }}
              transition={{ 
                duration: 0.8 + Math.random() * 0.3,
                delay: 0.1 + Math.random() * 0.2,
                ease: "easeOut",
              }}
            />
          ))}
        </>
      )}
    </div>
  );
}

// =============================================================================
// STYLES
// =============================================================================

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
    // Moved flash center down to match player area (70% from top)
    background: "radial-gradient(ellipse 120% 80% at 50% 70%, rgba(255,255,255,0.8) 0%, rgba(255,215,0,0.4) 30%, transparent 60%)",
    pointerEvents: "none",
    zIndex: 200,
  },
};
