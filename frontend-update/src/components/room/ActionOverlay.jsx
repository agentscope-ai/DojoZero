/**
 * ActionOverlay - NBA 2K Style Event Effects
 * 
 * Displays dramatic visual effects based on game events:
 * - Bold scoring banners (3-POINTER!, AND 1!, SLAM DUNK!)
 * - Point indicators (+1, +2, +3)
 * - Defensive highlights (BLOCKED!, STEAL!)
 * - Shooting meter effects
 * - Basketball trajectory animations
 * - Event-specific icons (hoop, hand, shield, etc.)
 * 
 * Responsive:
 * - All elements scale based on container size
 * - Uses CSS clamp() and viewport units for fluid sizing
 */

import { useMemo, useState, useEffect } from "react";
import { motion, AnimatePresence } from "framer-motion";
import { getActionConfig } from "../../data/nba/eventTypes";
import { actionThemes } from "../../data/constants";

// =============================================================================
// RESPONSIVE UTILITIES
// =============================================================================

// Hook to detect if we're on a small screen
function useIsCompact() {
  const [isCompact, setIsCompact] = useState(() => {
    if (typeof window !== "undefined") {
      return window.innerWidth < 768 || window.innerHeight < 500;
    }
    return false;
  });

  useEffect(() => {
    const checkSize = () => {
      setIsCompact(window.innerWidth < 768 || window.innerHeight < 500);
    };
    
    window.addEventListener("resize", checkSize);
    return () => window.removeEventListener("resize", checkSize);
  }, []);

  return isCompact;
}

// Responsive scale factor based on screen size
function useScaleFactor() {
  const [scale, setScale] = useState(1);

  useEffect(() => {
    const calculateScale = () => {
      const width = window.innerWidth;
      const height = window.innerHeight;
      const minDimension = Math.min(width, height);
      
      // Scale from 0.5 (very small) to 1 (large screens)
      if (minDimension < 400) return setScale(0.5);
      if (minDimension < 500) return setScale(0.6);
      if (minDimension < 600) return setScale(0.7);
      if (minDimension < 768) return setScale(0.8);
      if (minDimension < 900) return setScale(0.9);
      setScale(1);
    };
    
    calculateScale();
    window.addEventListener("resize", calculateScale);
    return () => window.removeEventListener("resize", calculateScale);
  }, []);

  return scale;
}

// =============================================================================
// EVENT-SPECIFIC ICON COMPONENTS
// =============================================================================

// Basketball Hoop SVG for scoring events
function BasketballHoop({ color, size = 80 }) {
  return (
    <motion.svg
      width={size}
      height={size}
      viewBox="0 0 100 100"
      style={{ filter: `drop-shadow(0 0 12px ${color})` }}
      initial={{ scale: 0, rotate: -20 }}
      animate={{ scale: [0, 1.2, 1], rotate: 0 }}
      transition={{ duration: 0.4, ease: "backOut" }}
    >
      {/* Backboard */}
      <motion.rect
        x="20" y="5" width="60" height="8" rx="2"
        fill="#FFFFFF"
        stroke={color}
        strokeWidth="2"
        initial={{ scaleX: 0 }}
        animate={{ scaleX: 1 }}
        transition={{ delay: 0.1, duration: 0.2 }}
      />
      {/* Rim */}
      <motion.ellipse
        cx="50" cy="30" rx="22" ry="8"
        fill="none"
        stroke="#FF6B35"
        strokeWidth="4"
        initial={{ scale: 0 }}
        animate={{ scale: 1 }}
        transition={{ delay: 0.15, duration: 0.25 }}
      />
      {/* Net lines */}
      {[30, 40, 50, 60, 70].map((x, i) => (
        <motion.path
          key={i}
          d={`M${x} 35 Q${x + (i % 2 ? 3 : -3)} 55 ${x} 75`}
          fill="none"
          stroke="rgba(255,255,255,0.7)"
          strokeWidth="1.5"
          strokeDasharray="3 2"
          initial={{ pathLength: 0, opacity: 0 }}
          animate={{ pathLength: 1, opacity: 1 }}
          transition={{ delay: 0.2 + i * 0.03, duration: 0.3 }}
        />
      ))}
      {/* Net bottom */}
      <motion.ellipse
        cx="50" cy="75" rx="12" ry="4"
        fill="none"
        stroke="rgba(255,255,255,0.5)"
        strokeWidth="1"
        initial={{ scale: 0 }}
        animate={{ scale: 1 }}
        transition={{ delay: 0.4 }}
      />
    </motion.svg>
  );
}

// Blocking Hand SVG for block events
function BlockingHand({ color, size = 70 }) {
  return (
    <motion.svg
      width={size}
      height={size}
      viewBox="0 0 100 100"
      style={{ filter: `drop-shadow(0 0 10px ${color})` }}
      initial={{ scale: 0, y: 20 }}
      animate={{ scale: [0, 1.3, 1], y: 0 }}
      transition={{ duration: 0.35, ease: "backOut" }}
    >
      {/* Palm */}
      <motion.path
        d="M30 85 L30 45 Q30 35 40 35 L60 35 Q70 35 70 45 L70 85 Q70 95 50 95 Q30 95 30 85"
        fill={color}
        opacity="0.9"
      />
      {/* Fingers */}
      {[
        "M35 35 L35 15 Q35 8 40 8 Q45 8 45 15 L45 35",
        "M45 35 L45 5 Q45 -2 50 -2 Q55 -2 55 5 L55 35",
        "M55 35 L55 10 Q55 3 60 3 Q65 3 65 10 L65 35",
        "M65 40 L75 30 Q80 25 85 30 Q90 35 85 40 L70 50",
      ].map((d, i) => (
        <motion.path
          key={i}
          d={d}
          fill={color}
          opacity="0.9"
          initial={{ scaleY: 0 }}
          animate={{ scaleY: 1 }}
          transition={{ delay: 0.1 + i * 0.05, duration: 0.2 }}
          style={{ transformOrigin: "bottom" }}
        />
      ))}
      {/* Stop symbol */}
      <motion.circle
        cx="50" cy="65" r="15"
        fill="none"
        stroke="#FFFFFF"
        strokeWidth="3"
        initial={{ scale: 0 }}
        animate={{ scale: 1 }}
        transition={{ delay: 0.3 }}
      />
      <motion.line
        x1="40" y1="55" x2="60" y2="75"
        stroke="#FFFFFF"
        strokeWidth="3"
        strokeLinecap="round"
        initial={{ pathLength: 0 }}
        animate={{ pathLength: 1 }}
        transition={{ delay: 0.35, duration: 0.15 }}
      />
    </motion.svg>
  );
}

// Fast Hand SVG for steal events
function StealingHand({ color, size = 70 }) {
  return (
    <motion.svg
      width={size}
      height={size}
      viewBox="0 0 100 100"
      style={{ filter: `drop-shadow(0 0 10px ${color})` }}
      initial={{ x: -30, opacity: 0 }}
      animate={{ x: 0, opacity: 1 }}
      transition={{ duration: 0.25, ease: "easeOut" }}
    >
      {/* Grabbing hand */}
      <motion.path
        d="M20 50 Q15 45 20 40 L70 40 Q80 40 80 50 L80 60 Q80 70 70 70 L30 70 Q20 70 20 60 Z"
        fill={color}
        opacity="0.9"
      />
      {/* Fingers curling */}
      {[
        "M70 40 Q85 35 80 25 Q75 20 65 30",
        "M60 40 Q70 30 65 20 Q60 15 55 25",
        "M50 40 Q55 28 50 18 Q45 13 45 25",
      ].map((d, i) => (
        <motion.path
          key={i}
          d={d}
          fill={color}
          opacity="0.9"
          initial={{ opacity: 0 }}
          animate={{ opacity: 1 }}
          transition={{ delay: 0.1 + i * 0.04 }}
        />
      ))}
      {/* Speed lines */}
      {[0, 1, 2].map((i) => (
        <motion.line
          key={i}
          x1={5} y1={45 + i * 10} x2={20} y2={45 + i * 10}
          stroke="#FFFFFF"
          strokeWidth="2"
          strokeLinecap="round"
          initial={{ scaleX: 0, opacity: 0 }}
          animate={{ scaleX: 1, opacity: [0, 1, 0] }}
          transition={{ delay: i * 0.05, duration: 0.4 }}
        />
      ))}
      {/* Basketball */}
      <motion.circle
        cx="55" cy="55" r="12"
        fill="#FF6B35"
        stroke="#8B4513"
        strokeWidth="1"
        initial={{ scale: 0 }}
        animate={{ scale: [0, 1.2, 1] }}
        transition={{ delay: 0.2, duration: 0.25 }}
      />
      <motion.path
        d="M48 45 Q55 55 48 65 M62 45 Q55 55 62 65 M43 55 L67 55"
        fill="none"
        stroke="#8B4513"
        strokeWidth="1"
        initial={{ opacity: 0 }}
        animate={{ opacity: 1 }}
        transition={{ delay: 0.35 }}
      />
    </motion.svg>
  );
}

// Rebound Board SVG
function ReboundBoard({ color, size = 70 }) {
  return (
    <motion.svg
      width={size}
      height={size}
      viewBox="0 0 100 100"
      style={{ filter: `drop-shadow(0 0 10px ${color})` }}
      initial={{ scale: 0, rotate: 10 }}
      animate={{ scale: 1, rotate: 0 }}
      transition={{ duration: 0.3, ease: "backOut" }}
    >
      {/* Backboard */}
      <motion.rect
        x="10" y="20" width="80" height="50" rx="4"
        fill="rgba(255,255,255,0.1)"
        stroke={color}
        strokeWidth="3"
      />
      {/* Inner square */}
      <motion.rect
        x="30" y="35" width="40" height="25" rx="2"
        fill="none"
        stroke={color}
        strokeWidth="2"
        initial={{ scale: 0 }}
        animate={{ scale: 1 }}
        transition={{ delay: 0.15 }}
      />
      {/* Ball bouncing off */}
      <motion.circle
        cx="50" cy="45" r="10"
        fill="#FF6B35"
        initial={{ y: -30, opacity: 0 }}
        animate={{ y: [0, -15, 0], opacity: 1 }}
        transition={{ duration: 0.4, ease: "easeOut" }}
      />
      {/* Bounce arrows */}
      <motion.path
        d="M35 75 L50 85 L65 75"
        fill="none"
        stroke={color}
        strokeWidth="3"
        strokeLinecap="round"
        strokeLinejoin="round"
        initial={{ opacity: 0, y: -10 }}
        animate={{ opacity: 1, y: 0 }}
        transition={{ delay: 0.2 }}
      />
    </motion.svg>
  );
}

// Turnover/Error Icon
function TurnoverIcon({ color, size = 60 }) {
  return (
    <motion.svg
      width={size}
      height={size}
      viewBox="0 0 100 100"
      style={{ filter: `drop-shadow(0 0 8px ${color})` }}
      initial={{ scale: 0, rotate: -90 }}
      animate={{ scale: 1, rotate: 0 }}
      transition={{ duration: 0.3, ease: "backOut" }}
    >
      {/* Circular arrow */}
      <motion.path
        d="M50 15 A35 35 0 1 1 15 50"
        fill="none"
        stroke={color}
        strokeWidth="6"
        strokeLinecap="round"
        initial={{ pathLength: 0 }}
        animate={{ pathLength: 1 }}
        transition={{ duration: 0.4 }}
      />
      {/* Arrow head */}
      <motion.polygon
        points="15,35 5,50 25,50"
        fill={color}
        initial={{ opacity: 0 }}
        animate={{ opacity: 1 }}
        transition={{ delay: 0.3 }}
      />
      {/* X mark */}
      <motion.g initial={{ scale: 0 }} animate={{ scale: 1 }} transition={{ delay: 0.2 }}>
        <line x1="35" y1="40" x2="65" y2="60" stroke="#FFFFFF" strokeWidth="4" strokeLinecap="round" />
        <line x1="65" y1="40" x2="35" y2="60" stroke="#FFFFFF" strokeWidth="4" strokeLinecap="round" />
      </motion.g>
    </motion.svg>
  );
}

// Whistle Icon for fouls
function WhistleIcon({ color, size = 60 }) {
  return (
    <motion.svg
      width={size}
      height={size}
      viewBox="0 0 100 100"
      style={{ filter: `drop-shadow(0 0 8px ${color})` }}
      initial={{ scale: 0, rotate: 20 }}
      animate={{ scale: 1, rotate: 0 }}
      transition={{ duration: 0.3, ease: "backOut" }}
    >
      {/* Whistle body */}
      <motion.ellipse
        cx="55" cy="50" rx="30" ry="20"
        fill={color}
      />
      {/* Mouthpiece */}
      <motion.rect
        x="10" y="42" width="25" height="16" rx="3"
        fill={color}
      />
      {/* Sound waves */}
      {[0, 1, 2].map((i) => (
        <motion.path
          key={i}
          d={`M${78 + i * 8} 35 Q${85 + i * 8} 50 ${78 + i * 8} 65`}
          fill="none"
          stroke="#FFFFFF"
          strokeWidth="2"
          strokeLinecap="round"
          initial={{ opacity: 0, x: -5 }}
          animate={{ opacity: [0, 1, 0], x: 5 }}
          transition={{ delay: 0.2 + i * 0.1, duration: 0.5, repeat: 2 }}
        />
      ))}
      {/* Hole */}
      <circle cx="70" cy="50" r="5" fill="rgba(0,0,0,0.3)" />
    </motion.svg>
  );
}

// =============================================================================
// NBA 2K STYLE TEXT BANNER - REDESIGNED (RESPONSIVE)
// =============================================================================

function ActionBanner2K({
  text,
  subtext,
  color = "#FFFFFF",
  secondaryColor = "#FFD700",
  effect = "default",
  direction = "left",
  points = 0,
  icon = null,
  scale = 1,
  isCompact = false,
}) {
  // Dynamic entry animation based on direction (scaled for mobile)
  const slideDistance = 300 * scale;
  const slideAnim = {
    initial: { 
      x: direction === "left" ? -slideDistance : slideDistance, 
      opacity: 0, 
      scale: 0.6,
      rotateX: 45,
    },
    animate: { 
      x: 0, 
      opacity: 1, 
      scale: 1,
      rotateX: 0,
    },
    exit: { 
      x: direction === "left" ? slideDistance : -slideDistance, 
      opacity: 0, 
      scale: 0.8,
      rotateX: -20,
    },
  };

  // Responsive sizes
  const mainFontSize = isCompact ? 36 : Math.round(72 * scale);
  const subtextFontSize = isCompact ? 12 : Math.round(20 * scale);
  const pointsPlusFontSize = isCompact ? 18 : Math.round(28 * scale);
  const pointsNumberFontSize = isCompact ? 28 : Math.round(44 * scale);
  const cardPadding = isCompact ? "12px 20px" : `${Math.round(22 * scale)}px ${Math.round(36 * scale)}px`;
  const cardMinWidth = isCompact ? 200 : Math.round(300 * scale);
  const cardBorderRadius = isCompact ? 12 : Math.round(20 * scale);
  const contentGap = isCompact ? 12 : Math.round(24 * scale);

  return (
    <motion.div
      style={{
        ...banner2KStyles.container,
        top: isCompact ? "38%" : "42%",
      }}
      initial={slideAnim.initial}
      animate={slideAnim.animate}
      exit={slideAnim.exit}
      transition={{ 
        duration: 0.35, 
        ease: [0.22, 1, 0.36, 1],
        rotateX: { duration: 0.4 }
      }}
    >
      {/* Background burst effect - scaled */}
      <BurstEffect color={color} scale={scale} />
      
      {/* Unified Text Card with glassmorphism */}
      <motion.div
        style={{
          ...banner2KStyles.textCard,
          padding: cardPadding,
          minWidth: cardMinWidth,
          borderRadius: cardBorderRadius,
          background: `linear-gradient(135deg, rgba(255,255,255,0.12) 0%, rgba(255,255,255,0.06) 50%, rgba(255,255,255,0.1) 100%)`,
          backdropFilter: "blur(20px)",
          WebkitBackdropFilter: "blur(20px)",
          border: "1px solid rgba(255,255,255,0.18)",
          boxShadow: `
            0 8px 32px rgba(0,0,0,0.3),
            0 0 ${Math.round(60 * scale)}px ${color}25,
            inset 0 1px 0 rgba(255,255,255,0.2),
            inset 0 -1px 0 rgba(255,255,255,0.05)
          `,
        }}
        initial={{ scale: 0.5, opacity: 0 }}
        animate={{ scale: 1, opacity: 1 }}
        transition={{ duration: 0.3, ease: "backOut" }}
      >
        {/* Top accent glow line */}
        <motion.div
          style={{
            position: "absolute",
            top: -1,
            left: 15,
            right: 15,
            height: 2,
            background: `linear-gradient(90deg, transparent, ${color}90, ${secondaryColor}90, transparent)`,
            borderRadius: 1,
            filter: `blur(1px)`,
          }}
          initial={{ scaleX: 0, opacity: 0 }}
          animate={{ scaleX: 1, opacity: 1 }}
          transition={{ delay: 0.15, duration: 0.3 }}
        />

        {/* Content wrapper with icon */}
        <div style={{ ...banner2KStyles.contentWrapper, gap: contentGap }}>
          {/* Left icon area - hidden on very compact screens */}
          {icon && !isCompact && (
            <motion.div
              style={{
                ...banner2KStyles.iconArea,
                background: `radial-gradient(circle, ${color}20 0%, transparent 70%)`,
                borderRadius: Math.round(16 * scale),
                padding: Math.round(8 * scale),
              }}
              initial={{ scale: 0, x: -20 }}
              animate={{ scale: 1, x: 0 }}
              transition={{ delay: 0.2, duration: 0.25, ease: "backOut" }}
            >
              {icon}
            </motion.div>
          )}

          {/* Text area */}
          <div style={banner2KStyles.textArea}>
            {/* Main text */}
            <motion.div
              style={banner2KStyles.mainTextWrapper}
              initial={{ y: 20, opacity: 0 }}
              animate={{ y: 0, opacity: 1 }}
              transition={{ delay: 0.1, duration: 0.25 }}
            >
              <span
                style={{
                  ...banner2KStyles.mainText,
                  fontSize: mainFontSize,
                  color: "#FFFFFF",
                  textShadow: `
                    0 0 ${Math.round(20 * scale)}px ${color},
                    0 0 ${Math.round(40 * scale)}px ${color}80,
                    2px 2px 0 ${color}
                  `,
                }}
              >
                {text}
              </span>
            </motion.div>

            {/* Divider line - subtle glassmorphism style */}
            {subtext && !isCompact && (
              <motion.div
                style={{
                  width: "80%",
                  height: 1,
                  background: `linear-gradient(90deg, transparent, rgba(255,255,255,0.3), rgba(255,255,255,0.4), rgba(255,255,255,0.3), transparent)`,
                  margin: `${Math.round(8 * scale)}px 0 ${Math.round(6 * scale)}px 0`,
                }}
                initial={{ scaleX: 0, opacity: 0 }}
                animate={{ scaleX: 1, opacity: 1 }}
                transition={{ delay: 0.2, duration: 0.2 }}
              />
            )}

            {/* Subtext - unified style */}
            {subtext && (
              <motion.div
                style={{ ...banner2KStyles.subtextWrapper, marginTop: isCompact ? 4 : 2 }}
                initial={{ y: -10, opacity: 0 }}
                animate={{ y: 0, opacity: 1 }}
                transition={{ delay: 0.25, duration: 0.2 }}
              >
                <span
                  style={{
                    ...banner2KStyles.subtextText,
                    fontSize: subtextFontSize,
                    color: secondaryColor,
                    textShadow: `0 0 10px ${secondaryColor}60`,
                  }}
                >
                  {subtext}
                </span>
              </motion.div>
            )}
          </div>

          {/* Right points indicator (integrated) */}
          {points > 0 && (
            <motion.div
              style={{
                ...banner2KStyles.pointsArea,
                padding: isCompact ? "6px 10px" : `${Math.round(10 * scale)}px ${Math.round(14 * scale)}px`,
                borderRadius: Math.round(12 * scale),
                background: `linear-gradient(135deg, ${color}30 0%, ${secondaryColor}30 100%)`,
                backdropFilter: "blur(10px)",
                WebkitBackdropFilter: "blur(10px)",
                border: `1px solid ${color}50`,
                boxShadow: `0 0 20px ${color}40, inset 0 1px 0 rgba(255,255,255,0.15)`,
              }}
              initial={{ scale: 0, x: 20 }}
              animate={{ scale: 1, x: 0 }}
              transition={{ delay: 0.25, duration: 0.25, ease: "backOut" }}
            >
              <span style={{ ...banner2KStyles.pointsPlus, fontSize: pointsPlusFontSize, color: color }}>+</span>
              <span style={{ ...banner2KStyles.pointsNumber, fontSize: pointsNumberFontSize, color: "#FFFFFF", textShadow: `0 0 20px ${color}` }}>{points}</span>
            </motion.div>
          )}
        </div>

        {/* Bottom accent glow line */}
        <motion.div
          style={{
            position: "absolute",
            bottom: -1,
            left: 15,
            right: 15,
            height: 2,
            background: `linear-gradient(90deg, transparent, ${secondaryColor}70, ${color}70, transparent)`,
            borderRadius: 1,
            filter: `blur(1px)`,
          }}
          initial={{ scaleX: 0, opacity: 0 }}
          animate={{ scaleX: 1, opacity: 0.8 }}
          transition={{ delay: 0.15, duration: 0.3 }}
        />
      </motion.div>

      {/* Effect-specific decorations - hidden on compact screens for performance */}
      {!isCompact && (
        <>
          {effect === "fire" && <FireEffect2K color={color} scale={scale} />}
          {effect === "shockwave" && <ShockwaveEffect2K color={color} scale={scale} />}
          {effect === "speed" && <SpeedEffect2K color={color} direction={direction} scale={scale} />}
        </>
      )}
    </motion.div>
  );
}

const banner2KStyles = {
  container: {
    position: "absolute",
    left: "50%",
    top: "42%",
    transform: "translate(-50%, -50%)",
    zIndex: 300,
    textAlign: "center",
    pointerEvents: "none",
    perspective: "1000px",
    maxWidth: "95vw",
  },
  textCard: {
    position: "relative",
    overflow: "visible",
  },
  contentWrapper: {
    display: "flex",
    alignItems: "center",
    justifyContent: "center",
  },
  iconArea: {
    flexShrink: 0,
    display: "flex",
    alignItems: "center",
    justifyContent: "center",
  },
  textArea: {
    display: "flex",
    flexDirection: "column",
    alignItems: "center",
    flex: 1,
    minWidth: 0,
  },
  mainTextWrapper: {
    position: "relative",
    zIndex: 10,
  },
  mainText: {
    fontWeight: 900,
    fontFamily: "'Bebas Neue', 'Impact', 'Oswald', sans-serif",
    textTransform: "uppercase",
    letterSpacing: "0.04em",
    whiteSpace: "nowrap",
    display: "block",
    lineHeight: 1,
  },
  subtextWrapper: {
    marginTop: 2,
  },
  subtextText: {
    fontWeight: 700,
    letterSpacing: "0.15em",
    textTransform: "uppercase",
    fontFamily: "'Bebas Neue', 'Impact', sans-serif",
  },
  pointsArea: {
    flexShrink: 0,
    display: "flex",
    alignItems: "center",
    justifyContent: "center",
  },
  pointsPlus: {
    fontWeight: 900,
    color: "#FFFFFF",
    fontFamily: "'Bebas Neue', 'Impact', sans-serif",
    lineHeight: 1,
  },
  pointsNumber: {
    fontWeight: 900,
    color: "#FFFFFF",
    fontFamily: "'Bebas Neue', 'Impact', sans-serif",
    textShadow: "0 2px 4px rgba(0,0,0,0.3)",
    lineHeight: 1,
  },
};

// =============================================================================
// FLOATING POINT INDICATOR (for additional emphasis on big plays)
// =============================================================================

function FloatingPoints({ points, color, scale = 1, isCompact = false }) {
  const fontSize = isCompact ? 48 : Math.round(80 * scale);
  const yMovement = isCompact ? [-40, -60] : [-60, -100];
  
  return (
    <motion.div
      style={{
        ...pointStyles.floatingContainer,
        top: isCompact ? "55%" : "60%",
      }}
      initial={{ scale: 0, y: 0, opacity: 0 }}
      animate={{ 
        scale: [0, 1.3, 1],
        y: [0, ...yMovement],
        opacity: [0, 1, 1, 0],
      }}
      transition={{
        duration: 2,
        times: [0, 0.15, 0.5, 1],
        ease: "easeOut",
      }}
    >
      <span
        style={{
          ...pointStyles.floatingText,
          fontSize,
          color: "#FFFFFF",
          textShadow: `
            0 0 ${Math.round(30 * scale)}px ${color},
            0 0 ${Math.round(60 * scale)}px ${color}80,
            0 4px 12px rgba(0,0,0,0.4)
          `,
          WebkitTextStroke: `1px ${color}80`,
        }}
      >
        +{points}
      </span>
    </motion.div>
  );
}

const pointStyles = {
  floatingContainer: {
    position: "absolute",
    left: "50%",
    transform: "translateX(-50%)",
    zIndex: 320,
    pointerEvents: "none",
  },
  floatingText: {
    fontWeight: 900,
    fontFamily: "'Bebas Neue', 'Impact', sans-serif",
    letterSpacing: "0.02em",
  },
};

// =============================================================================
// BURST EFFECT BACKGROUND
// =============================================================================

function BurstEffect({ color, scale = 1 }) {
  const outerWidth = Math.round(800 * scale);
  const outerHeight = Math.round(400 * scale);
  const innerWidth = Math.round(500 * scale);
  const innerHeight = Math.round(250 * scale);
  
  return (
    <>
      {/* Outer soft glow */}
      <motion.div
        style={{
          position: "absolute",
          left: "50%",
          top: "50%",
          width: outerWidth,
          height: outerHeight,
          transform: "translate(-50%, -50%)",
          background: `radial-gradient(ellipse, ${color}20 0%, ${color}08 40%, transparent 70%)`,
          filter: `blur(${Math.round(40 * scale)}px)`,
          zIndex: -2,
        }}
        initial={{ scale: 0, opacity: 0 }}
        animate={{ scale: [0, 1.5, 1.2], opacity: [0, 0.8, 0.5] }}
        transition={{ duration: 0.5, ease: "easeOut" }}
      />
      {/* Inner concentrated glow */}
      <motion.div
        style={{
          position: "absolute",
          left: "50%",
          top: "50%",
          width: innerWidth,
          height: innerHeight,
          transform: "translate(-50%, -50%)",
          background: `radial-gradient(ellipse, ${color}30 0%, transparent 60%)`,
          filter: `blur(${Math.round(20 * scale)}px)`,
          zIndex: -1,
        }}
        initial={{ scale: 0, opacity: 0 }}
        animate={{ scale: [0, 1.3, 1], opacity: [0, 1, 0.7] }}
        transition={{ duration: 0.4, ease: "easeOut" }}
      />
    </>
  );
}

// =============================================================================
// EFFECT COMPONENTS - NBA 2K STYLE (Redesigned with event-specific elements)
// =============================================================================

function FireEffect2K({ color, scale = 1 }) {
  const particleCount = scale < 0.7 ? 8 : 16;
  const starCount = scale < 0.7 ? 3 : 6;
  
  return (
    <>
      {/* Flame particles around the banner */}
      {[...Array(particleCount)].map((_, i) => {
        const angle = (i / particleCount) * Math.PI * 2;
        const radius = (180 + Math.random() * 60) * scale;
        const x = Math.cos(angle) * radius;
        const y = Math.sin(angle) * radius * 0.5;
        
        return (
          <motion.div
            key={i}
            style={{
              position: "absolute",
              left: "50%",
              top: "50%",
              width: (8 + Math.random() * 6) * scale,
              height: (20 + Math.random() * 15) * scale,
              background: `linear-gradient(to top, ${color}, #FFD700, #FF6B35, transparent)`,
              borderRadius: "50% 50% 30% 30%",
              filter: "blur(1px)",
              transformOrigin: "bottom center",
            }}
            initial={{ x: 0, y: 0, opacity: 0, scale: 0 }}
            animate={{
              x: [0, x],
              y: [0, y - 30 * scale],
              opacity: [0, 1, 0.7, 0],
              scale: [0.2, 1, 0.6],
              rotate: [0, Math.random() * 40 - 20],
            }}
            transition={{
              duration: 0.8 + Math.random() * 0.3,
              delay: Math.random() * 0.15,
              ease: "easeOut",
            }}
          />
        );
      })}
      
      {/* "SWISH" net swoosh effect */}
      <motion.div
        style={{
          position: "absolute",
          right: -120 * scale,
          top: "30%",
          fontSize: 50 * scale,
          filter: `drop-shadow(0 0 ${15 * scale}px ${color})`,
        }}
        initial={{ scale: 0, rotate: -30, opacity: 0 }}
        animate={{ scale: [0, 1.3, 1], rotate: 0, opacity: [0, 1, 0.8] }}
        transition={{ delay: 0.2, duration: 0.4 }}
      >
        🏀
      </motion.div>
      
      {/* Sparkle stars */}
      {[...Array(starCount)].map((_, i) => (
        <motion.div
          key={`star-${i}`}
          style={{
            position: "absolute",
            left: `${15 + i * 14}%`,
            top: `${20 + (i % 3) * 25}%`,
            fontSize: 24 * scale,
          }}
          initial={{ scale: 0, opacity: 0, rotate: 0 }}
          animate={{ 
            scale: [0, 1.2, 0.8, 0],
            opacity: [0, 1, 0.8, 0],
            rotate: [0, 180],
          }}
          transition={{ delay: 0.1 + i * 0.05, duration: 0.6 }}
        >
          ✨
        </motion.div>
      ))}
    </>
  );
}

function ShockwaveEffect2K({ color, scale = 1 }) {
  return (
    <>
      {/* Impact shockwave rings */}
      {[0, 1, 2].map((i) => (
        <motion.div
          key={i}
          style={{
            position: "absolute",
            left: "50%",
            top: "50%",
            width: 300 * scale,
            height: 120 * scale,
            border: `${3 * scale}px solid ${color}`,
            borderRadius: "50%",
            transform: "translate(-50%, -50%)",
            boxShadow: `0 0 ${15 * scale}px ${color}`,
          }}
          initial={{ scale: 0.3, opacity: 0 }}
          animate={{ scale: [0.3, 2.2, 2.8], opacity: [0, 0.9, 0] }}
          transition={{ duration: 0.7, delay: i * 0.1, ease: "easeOut" }}
        />
      ))}
      
      {/* Rim shake effect */}
      <motion.div
        style={{
          position: "absolute",
          right: -100 * scale,
          top: "20%",
        }}
        initial={{ x: 0 }}
        animate={{ x: [-5, 5, -4, 4, -2, 2, 0] }}
        transition={{ duration: 0.5, ease: "easeOut" }}
      >
        <svg width={80 * scale} height={60 * scale} viewBox="0 0 100 75">
          {/* Rim */}
          <motion.ellipse
            cx="50" cy="20" rx="30" ry="10"
            fill="none"
            stroke="#FF6B35"
            strokeWidth="5"
            initial={{ scale: 1 }}
            animate={{ scale: [1, 1.1, 1] }}
            transition={{ duration: 0.3 }}
          />
          {/* Net shaking */}
          {[25, 40, 50, 60, 75].map((x, i) => (
            <motion.path
              key={i}
              d={`M${x} 28 Q${x + (i % 2 ? 5 : -5)} 45 ${x} 65`}
              fill="none"
              stroke="rgba(255,255,255,0.6)"
              strokeWidth="2"
              initial={{ d: `M${x} 28 Q${x} 45 ${x} 65` }}
              animate={{ 
                d: [
                  `M${x} 28 Q${x + 8} 45 ${x} 65`,
                  `M${x} 28 Q${x - 8} 45 ${x} 65`,
                  `M${x} 28 Q${x + 4} 45 ${x} 65`,
                  `M${x} 28 Q${x} 45 ${x} 65`,
                ]
              }}
              transition={{ duration: 0.6 }}
            />
          ))}
        </svg>
      </motion.div>
      
      {/* Impact explosion emoji */}
      <motion.span
        style={{
          position: "absolute",
          left: -80 * scale,
          top: "40%",
          fontSize: 44 * scale,
          filter: `drop-shadow(0 0 ${10 * scale}px ${color})`,
        }}
        initial={{ scale: 0, opacity: 0 }}
        animate={{ scale: [0, 1.4, 1], opacity: [0, 1, 0] }}
        transition={{ duration: 0.5 }}
      >
        💥
      </motion.span>
      
      {/* Poster text effect */}
      <motion.div
        style={{
          position: "absolute",
          right: -140 * scale,
          bottom: "30%",
          fontSize: 28 * scale,
          fontWeight: 900,
          fontFamily: "'Bebas Neue', 'Impact', sans-serif",
          color: "#FFFFFF",
          textShadow: `0 0 ${15 * scale}px ${color}, 2px 2px 0 ${color}`,
          letterSpacing: "0.1em",
          writingMode: "vertical-rl",
          textOrientation: "mixed",
        }}
        initial={{ opacity: 0, x: 20 }}
        animate={{ opacity: [0, 1, 0.8], x: 0 }}
        transition={{ delay: 0.2, duration: 0.4 }}
      >
        BOOM!
      </motion.div>
    </>
  );
}

function SpeedEffect2K({ color, direction, scale = 1 }) {
  const lineCount = scale < 0.7 ? 5 : 10;
  
  return (
    <>
      {/* Dynamic speed lines */}
      {[...Array(lineCount)].map((_, i) => (
        <motion.div
          key={i}
          style={{
            position: "absolute",
            left: direction === "left" ? "105%" : "auto",
            right: direction === "right" ? "105%" : "auto",
            top: `${15 + i * (80 / lineCount)}%`,
            width: (60 + Math.random() * 80) * scale,
            height: 3 * scale,
            background: `linear-gradient(${direction === "left" ? "to left" : "to right"}, ${color}, transparent)`,
            borderRadius: 2,
            boxShadow: `0 0 ${6 * scale}px ${color}60`,
          }}
          initial={{ scaleX: 0, x: direction === "left" ? -30 : 30, opacity: 0 }}
          animate={{ 
            scaleX: [0, 1, 0.5, 0], 
            x: 0,
            opacity: [0, 0.9, 0.6, 0] 
          }}
          transition={{ duration: 0.35, delay: i * 0.02 }}
        />
      ))}
      
      {/* Ball trajectory showing steal */}
      <motion.div
        style={{
          position: "absolute",
          left: direction === "left" ? "auto" : -100 * scale,
          right: direction === "left" ? -100 * scale : "auto",
          top: "30%",
        }}
      >
        {/* Ball being stolen */}
        <motion.span
          style={{
            fontSize: 36 * scale,
            display: "block",
            filter: `drop-shadow(0 0 ${8 * scale}px ${color})`,
          }}
          initial={{ 
            x: direction === "left" ? 80 * scale : -80 * scale, 
            y: 0,
            rotate: 0,
            opacity: 0 
          }}
          animate={{ 
            x: 0, 
            y: [0, -10, 0],
            rotate: direction === "left" ? -360 : 360,
            opacity: [0, 1, 1, 0.8]
          }}
          transition={{ duration: 0.6 }}
        >
          🏀
        </motion.span>
      </motion.div>
      
      {/* Quick hands indicator - hidden on small screens */}
      {scale >= 0.7 && (
        <motion.div
          style={{
            position: "absolute",
            left: direction === "left" ? -90 * scale : "auto",
            right: direction === "right" ? -90 * scale : "auto",
            top: "60%",
            fontSize: 16 * scale,
            fontWeight: 800,
            fontFamily: "'Bebas Neue', 'Impact', sans-serif",
            color: color,
            textShadow: `0 0 ${10 * scale}px ${color}`,
            letterSpacing: "0.15em",
            whiteSpace: "nowrap",
          }}
          initial={{ opacity: 0, scale: 0.5 }}
          animate={{ opacity: [0, 1, 0], scale: 1 }}
          transition={{ delay: 0.2, duration: 0.6 }}
        >
          QUICK HANDS!
        </motion.div>
      )}
      
      {/* Lightning accent */}
      <motion.span
        style={{
          position: "absolute",
          left: direction === "left" ? -50 * scale : "auto",
          right: direction === "right" ? -50 * scale : "auto",
          top: "25%",
          fontSize: 40 * scale,
        }}
        initial={{ scale: 0, opacity: 0, rotate: direction === "left" ? 15 : -15 }}
        animate={{ scale: [0, 1.2, 1], opacity: [0, 1, 0], rotate: 0 }}
        transition={{ duration: 0.4, delay: 0.1 }}
      >
        ⚡
      </motion.span>
    </>
  );
}

function BlockEffect2K({ color }) {
  return (
    <>
      {/* Wall/barrier effect */}
      <motion.div
        style={{
          position: "absolute",
          left: "50%",
          top: "50%",
          width: 4,
          height: 200,
          background: `linear-gradient(to bottom, transparent, ${color}, #FFFFFF, ${color}, transparent)`,
          transform: "translate(-50%, -50%)",
          boxShadow: `0 0 20px ${color}, 0 0 40px ${color}50`,
        }}
        initial={{ scaleY: 0, opacity: 0 }}
        animate={{ scaleY: [0, 1, 0.9], opacity: [0, 1, 0.8] }}
        transition={{ duration: 0.3 }}
      />
      
      {/* Impact ripples */}
      {[0, 1, 2].map((i) => (
        <motion.div
          key={i}
          style={{
            position: "absolute",
            left: "50%",
            top: "50%",
            width: 80,
            height: 80,
            border: `3px solid ${color}`,
            borderRadius: "50%",
            transform: "translate(-50%, -50%)",
          }}
          initial={{ scale: 0.5, opacity: 0 }}
          animate={{ scale: [0.5, 2, 2.5], opacity: [0, 0.8, 0] }}
          transition={{ duration: 0.5, delay: i * 0.08 }}
        />
      ))}
      
      {/* Ball rejection effect */}
      <motion.span
        style={{
          position: "absolute",
          right: -80,
          top: "35%",
          fontSize: 32,
          filter: `drop-shadow(0 0 8px ${color})`,
        }}
        initial={{ x: 0, y: 0, opacity: 0 }}
        animate={{ 
          x: [0, 50],
          y: [0, -40, 20],
          rotate: [0, -360],
          opacity: [0, 1, 1, 0]
        }}
        transition={{ duration: 0.7 }}
      >
        🏀
      </motion.span>
      
      {/* NO! text */}
      <motion.div
        style={{
          position: "absolute",
          left: -100,
          top: "40%",
          fontSize: 32,
          fontWeight: 900,
          fontFamily: "'Bebas Neue', 'Impact', sans-serif",
          color: color,
          textShadow: `0 0 15px ${color}, 2px 2px 0 rgba(0,0,0,0.5)`,
        }}
        initial={{ scale: 0, opacity: 0, rotate: -10 }}
        animate={{ scale: [0, 1.3, 1], opacity: [0, 1, 0], rotate: 0 }}
        transition={{ delay: 0.15, duration: 0.5 }}
      >
        NO!
      </motion.div>
      
      {/* Hand stop gesture */}
      <motion.span
        style={{
          position: "absolute",
          left: -70,
          top: "55%",
          fontSize: 48,
          transform: "scaleX(-1)",
        }}
        initial={{ scale: 0, opacity: 0, x: -20 }}
        animate={{ scale: [0, 1.2, 1], opacity: [0, 1, 0.8, 0], x: 0 }}
        transition={{ delay: 0.1, duration: 0.6 }}
      >
        ✋
      </motion.span>
    </>
  );
}

// =============================================================================
// SHOOTING METER (for free throws)
// =============================================================================

function ShootingMeter({ success = true, color, scale = 1 }) {
  return (
    <motion.div
      style={{
        ...meterStyles.container,
        bottom: Math.round(150 * scale),
        right: Math.round(60 * scale),
        width: Math.round(200 * scale),
      }}
      initial={{ opacity: 0, y: 20 }}
      animate={{ opacity: 1, y: 0 }}
      exit={{ opacity: 0, y: -20 }}
      transition={{ duration: 0.3 }}
    >
      <div style={{
        ...meterStyles.meterTrack,
        height: Math.round(16 * scale),
        borderRadius: Math.round(8 * scale),
      }}>
        <motion.div
          style={{
            ...meterStyles.meterFill,
            borderRadius: Math.round(6 * scale),
            background: success 
              ? `linear-gradient(90deg, #22C55E, #4ADE80)` 
              : `linear-gradient(90deg, #EF4444, #F87171)`,
          }}
          initial={{ width: "0%" }}
          animate={{ width: success ? "85%" : "40%" }}
          transition={{ duration: 0.8, ease: "easeOut" }}
        />
        <div style={{
          ...meterStyles.sweetSpot,
          width: Math.round(8 * scale),
        }} />
      </div>
      <div style={{
        ...meterStyles.label,
        marginTop: Math.round(8 * scale),
        fontSize: Math.round(11 * scale),
      }}>SHOOTING METER</div>
      <div style={{
        ...meterStyles.result,
        marginTop: Math.round(4 * scale),
        fontSize: Math.round(14 * scale),
      }}>
        {success ? "✓ 1 of 1" : "✗ MISS"}
      </div>
    </motion.div>
  );
}

const meterStyles = {
  container: {
    position: "absolute",
    textAlign: "center",
    zIndex: 280,
  },
  meterTrack: {
    width: "100%",
    background: "rgba(0,0,0,0.6)",
    overflow: "hidden",
    position: "relative",
    border: "2px solid rgba(255,255,255,0.3)",
  },
  meterFill: {
    height: "100%",
    boxShadow: "0 0 10px rgba(74, 222, 128, 0.5)",
  },
  sweetSpot: {
    position: "absolute",
    right: "12%",
    top: 0,
    bottom: 0,
    background: "#22C55E",
    borderLeft: "2px solid #FFFFFF",
    borderRight: "2px solid #FFFFFF",
  },
  label: {
    fontWeight: 700,
    color: "rgba(255,255,255,0.6)",
    letterSpacing: "0.1em",
  },
  result: {
    fontWeight: 800,
    color: "#FFFFFF",
  },
};

// =============================================================================
// SCREEN FLASH
// =============================================================================

function ScreenFlash({ color, intensity = "normal" }) {
  const opacityMax = intensity === "high" ? 0.5 : 0.25;

  return (
    <motion.div
      style={{
        position: "absolute",
        inset: 0,
        background: `radial-gradient(ellipse 100% 70% at 50% 45%, ${color}70 0%, ${color}30 40%, transparent 70%)`,
        pointerEvents: "none",
        zIndex: 200,
      }}
      initial={{ opacity: 0 }}
      animate={{ opacity: [0, opacityMax, 0] }}
      transition={{ duration: 0.5, ease: "easeOut" }}
    />
  );
}

// =============================================================================
// BASKETBALL TRAJECTORY EFFECT
// =============================================================================

function BallTrajectory({ color, isThreePoint = false, scale = 1 }) {
  const arcHeight = isThreePoint ? -150 : -100;
  const containerWidth = Math.round(300 * scale);
  const containerHeight = Math.round(200 * scale);
  
  return (
    <motion.div style={{
      ...trajectoryStyles.container,
      width: containerWidth,
      height: containerHeight,
    }}>
      {/* Ball path line */}
      <svg 
        style={trajectoryStyles.svg} 
        viewBox="0 0 300 200"
        preserveAspectRatio="none"
      >
        <motion.path
          d={`M 50 180 Q 150 ${50 + arcHeight} 250 60`}
          fill="none"
          stroke={color}
          strokeWidth="4"
          strokeDasharray="12 6"
          initial={{ pathLength: 0, opacity: 0 }}
          animate={{ pathLength: 1, opacity: [0, 1, 0.6] }}
          transition={{ duration: 0.8, ease: "easeOut" }}
        />
      </svg>
      {/* Ball */}
      <motion.div
        style={{
          ...trajectoryStyles.ball,
          fontSize: 32 * scale,
        }}
        initial={{ x: 50, y: 180 }}
        animate={{
          x: [50, 150, 250],
          y: [180, 50, 60],
          rotate: [0, 360, 720],
        }}
        transition={{ duration: 0.8, ease: "easeOut" }}
      >
        🏀
      </motion.div>
    </motion.div>
  );
}

const trajectoryStyles = {
  container: {
    position: "absolute",
    top: "20%",
    right: "10%",
    pointerEvents: "none",
    zIndex: 250,
  },
  svg: {
    position: "absolute",
    inset: 0,
    width: "100%",
    height: "100%",
  },
  ball: {
    position: "absolute",
    filter: "drop-shadow(0 4px 8px rgba(0,0,0,0.4))",
  },
};

// =============================================================================
// MAIN ACTION OVERLAY COMPONENT
// =============================================================================

export default function ActionOverlay({
  currentEvent,
  isAnimating = false,
  homeTeam,
  awayTeam,
}) {
  // Responsive scaling
  const scale = useScaleFactor();
  const isCompact = useIsCompact();
  
  // Determine action type and team color
  const actionData = useMemo(() => {
    if (!currentEvent || !isAnimating) return null;

    const eventType = currentEvent.event_type;
    
    // Only show effects for play_by_play events
    if (eventType !== "play_by_play") return null;

    const actionType = currentEvent.action_type?.toLowerCase() || "";
    const teamTricode = currentEvent.team_tricode;
    const isHome = teamTricode === homeTeam?.tricode;
    const teamColor = isHome ? homeTeam?.color : awayTeam?.color;
    const secondaryColor = isHome ? homeTeam?.secondaryColor : awayTeam?.secondaryColor;
    const description = currentEvent.description || "";

    // Get action configuration
    const config = getActionConfig(actionType);

    // Determine if it's a scoring action
    const isMiss = description.toLowerCase().includes("miss");
    const isScoring = config.points > 0 && !isMiss;

    // Check for "And 1" situation
    const isAnd1 = description.toLowerCase().includes("and 1") || 
                   description.toLowerCase().includes("and-1") ||
                   description.toLowerCase().includes("foul");

    return {
      actionType,
      config,
      teamColor: teamColor || "#3B82F6",
      secondaryColor: secondaryColor || "#FFD700",
      isScoring,
      isMiss,
      isAnd1,
      points: config.points,
      description,
      direction: isHome ? "left" : "right",
    };
  }, [currentEvent, isAnimating, homeTeam, awayTeam]);

  if (!actionData) return null;

  const { 
    actionType, 
    config, 
    teamColor, 
    secondaryColor,
    isScoring, 
    isMiss,
    isAnd1,
    points, 
    direction 
  } = actionData;
  
  // Responsive icon sizes
  const iconSize = isCompact ? 40 : Math.round(70 * scale);
  const hoopIconSize = isCompact ? 45 : Math.round(75 * scale);

  return (
    <div style={overlayStyles.container}>
      <AnimatePresence mode="wait">
        {/* 3-Point */}
        {actionType === "3pt" && isScoring && (
          <motion.div key="3pt" style={overlayStyles.scene}>
            <ScreenFlash color={teamColor} intensity="high" />
            {!isCompact && <BallTrajectory color={teamColor} isThreePoint={true} scale={scale} />}
            <ActionBanner2K
              text={isAnd1 ? "AND 1!" : "3-POINTER!"}
              subtext={isAnd1 ? "PLUS THE FOUL" : "FROM DOWNTOWN"}
              color={teamColor}
              secondaryColor={secondaryColor}
              effect="fire"
              direction={direction}
              points={isAnd1 ? 4 : 3}
              icon={<BasketballHoop color={teamColor} size={hoopIconSize} />}
              scale={scale}
              isCompact={isCompact}
            />
            <FloatingPoints points={isAnd1 ? 4 : 3} color={teamColor} scale={scale} isCompact={isCompact} />
          </motion.div>
        )}

        {/* Dunk */}
        {actionType === "dunk" && isScoring && (
          <motion.div key="dunk" style={overlayStyles.scene}>
            <ScreenFlash color={teamColor} intensity="high" />
            <ActionBanner2K
              text={isAnd1 ? "AND 1!" : "SLAM DUNK!"}
              subtext={isAnd1 ? "PLUS THE FOUL" : "POSTERIZED"}
              color={teamColor}
              secondaryColor={secondaryColor}
              effect="shockwave"
              direction={direction}
              points={isAnd1 ? 3 : 2}
              icon={<BasketballHoop color={teamColor} size={hoopIconSize} />}
              scale={scale}
              isCompact={isCompact}
            />
            <FloatingPoints points={isAnd1 ? 3 : 2} color={teamColor} scale={scale} isCompact={isCompact} />
          </motion.div>
        )}

        {/* 2-Point */}
        {actionType === "2pt" && isScoring && (
          <motion.div key="2pt" style={overlayStyles.scene}>
            <ScreenFlash color={teamColor} />
            {!isCompact && <BallTrajectory color={teamColor} isThreePoint={false} scale={scale} />}
            <ActionBanner2K
              text={isAnd1 ? "AND 1!" : "BUCKET!"}
              subtext={isAnd1 ? "PLUS THE FOUL" : "2 POINTS"}
              color={teamColor}
              secondaryColor={secondaryColor}
              effect="default"
              direction={direction}
              points={isAnd1 ? 3 : 2}
              icon={<BasketballHoop color={teamColor} size={iconSize} />}
              scale={scale}
              isCompact={isCompact}
            />
          </motion.div>
        )}

        {/* Free Throw */}
        {actionType === "freethrow" && isScoring && (
          <motion.div key="ft" style={overlayStyles.scene}>
            <ScreenFlash color={teamColor} />
            <ActionBanner2K
              text="FREE THROW"
              subtext="FROM THE LINE"
              color={teamColor}
              secondaryColor={secondaryColor}
              effect="default"
              direction={direction}
              points={1}
              icon={<BasketballHoop color={teamColor} size={isCompact ? 40 : Math.round(65 * scale)} />}
              scale={scale}
              isCompact={isCompact}
            />
            {!isCompact && <ShootingMeter success={true} color={teamColor} scale={scale} />}
          </motion.div>
        )}

        {/* Block */}
        {actionType === "block" && (
          <motion.div key="block" style={overlayStyles.scene}>
            <ScreenFlash color={actionThemes.defense.primary} intensity="high" />
            <ActionBanner2K
              text="BLOCKED!"
              subtext="GET THAT OUT!"
              color={actionThemes.defense.primary}
              secondaryColor="#FFFFFF"
              effect="shield"
              direction={direction}
              points={0}
              icon={<BlockingHand color={actionThemes.defense.primary} size={iconSize} />}
              scale={scale}
              isCompact={isCompact}
            />
          </motion.div>
        )}

        {/* Steal */}
        {actionType === "steal" && (
          <motion.div key="steal" style={overlayStyles.scene}>
            <ScreenFlash color={actionThemes.speed.primary} />
            <ActionBanner2K
              text="STEAL!"
              subtext="TURNOVER FORCED"
              color={actionThemes.speed.primary}
              secondaryColor="#FFC107"
              effect="speed"
              direction={direction}
              points={0}
              icon={<StealingHand color={actionThemes.speed.primary} size={iconSize} />}
              scale={scale}
              isCompact={isCompact}
            />
          </motion.div>
        )}

        {/* Rebound */}
        {actionType === "rebound" && (
          <motion.div key="rebound" style={overlayStyles.scene}>
            <ActionBanner2K
              text="REBOUND!"
              subtext="BOARDS"
              color={teamColor}
              secondaryColor={secondaryColor}
              effect="default"
              direction={direction}
              points={0}
              icon={<ReboundBoard color={teamColor} size={iconSize} />}
              scale={scale}
              isCompact={isCompact}
            />
          </motion.div>
        )}

        {/* Turnover */}
        {actionType === "turnover" && (
          <motion.div key="turnover" style={overlayStyles.scene}>
            <motion.div
              style={{
                position: "absolute",
                inset: 0,
                background: "rgba(239, 68, 68, 0.15)",
              }}
              initial={{ opacity: 0 }}
              animate={{ opacity: [0, 1, 0] }}
              transition={{ duration: 0.8 }}
            />
            <ActionBanner2K
              text="TURNOVER"
              subtext="POSSESSION LOST"
              color={actionThemes.negative.primary}
              secondaryColor="#FF6B6B"
              effect="default"
              direction={direction}
              points={0}
              icon={<TurnoverIcon color={actionThemes.negative.primary} size={isCompact ? 36 : Math.round(60 * scale)} />}
              scale={scale}
              isCompact={isCompact}
            />
          </motion.div>
        )}

        {/* Foul */}
        {actionType === "foul" && (
          <motion.div key="foul" style={overlayStyles.scene}>
            <motion.div
              style={{
                position: "absolute",
                inset: 0,
                background: "rgba(245, 158, 11, 0.12)",
              }}
              initial={{ opacity: 0 }}
              animate={{ opacity: [0, 1, 0] }}
              transition={{ duration: 0.8 }}
            />
            <ActionBanner2K
              text="FOUL"
              subtext="PERSONAL FOUL"
              color={actionThemes.negative.secondary}
              secondaryColor="#FBBF24"
              effect="default"
              direction={direction}
              points={0}
              icon={<WhistleIcon color={actionThemes.negative.secondary} size={isCompact ? 36 : Math.round(60 * scale)} />}
              scale={scale}
              isCompact={isCompact}
            />
          </motion.div>
        )}
      </AnimatePresence>
    </div>
  );
}

const overlayStyles = {
  container: {
    position: "absolute",
    inset: 0,
    pointerEvents: "none",
    zIndex: 100,
    overflow: "hidden",
  },
  scene: {
    position: "absolute",
    inset: 0,
  },
};
