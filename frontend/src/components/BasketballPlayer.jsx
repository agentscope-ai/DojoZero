import { motion } from "framer-motion";

/**
 * Animated basketball player SVG character
 * Supports multiple actions: idle, dribble, shoot, celebrate, run, defend
 */
export default function BasketballPlayer({ 
  action = "idle", 
  teamColor = "#3B82F6",
  secondaryColor = "#ffffff",
  size = 80,
  position = { x: 50, y: 50 },
  direction = "right", // "left" or "right"
  jersey = "",
  delay = 0,
  imageUrl = null,
  teamLogo = null,
  showBall = false, // Control ball display externally
}) {
  const scale = size / 80;
  const flip = direction === "left" ? -1 : 1;

  // Animation variants for different actions (SVG Body)
  const bodyVariants = {
    idle: {
      y: [0, -2, 0],
      transition: { duration: 1.5, repeat: Infinity, ease: "easeInOut", delay }
    },
    dribble: {
      y: [0, -4, 0],
      transition: { duration: 0.4, repeat: Infinity, ease: "easeInOut", delay }
    },
    shoot: {
      y: [0, -15, -20, -15, 0],
      transition: { duration: 1.2, ease: "easeOut", delay }
    },
    celebrate: {
      y: [0, -10, 0],
      rotate: [0, -5, 5, 0],
      transition: { duration: 0.5, repeat: Infinity, ease: "easeInOut", delay }
    },
    run: {
      y: [0, -3, 0, -3, 0],
      x: [0, 2, 0, -2, 0],
      transition: { duration: 0.3, repeat: Infinity, ease: "linear", delay }
    },
    defend: {
      x: [-3, 3, -3],
      transition: { duration: 0.6, repeat: Infinity, ease: "easeInOut", delay }
    },
    injured: {
      rotate: [0, -5, 0],
      transition: { duration: 2, repeat: Infinity, ease: "easeInOut", delay }
    },
  };

  // Simplified variants for Image Avatar mode
  // All variants must explicitly set rotate, y, x, scale, opacity to ensure proper transitions
  const imageVariants = {
    idle: {
      y: [0, -5, 0],
      x: 0,
      rotate: 0,
      scale: 1,
      opacity: 1,
      transition: { duration: 2, repeat: Infinity, ease: "easeInOut", delay }
    },
    dribble: {
      y: [0, -8, 0],
      x: 0,
      rotate: [0, 2, 0, -2, 0],
      scale: 1,
      opacity: 1,
      transition: { duration: 0.4, repeat: Infinity, ease: "easeInOut", delay }
    },
    shoot: {
      y: [0, -40, -50, -40, 0],
      x: 0,
      rotate: 0,
      scale: [1, 1.1, 1, 1, 1],
      opacity: 1,
      transition: { duration: 1.0, ease: "easeOut", delay }
    },
    celebrate: {
      y: [0, -15, 0],
      x: 0,
      scale: [1, 1.2, 1],
      rotate: [0, -10, 10, 0],
      opacity: 1,
      transition: { duration: 0.6, repeat: Infinity, ease: "easeInOut", delay }
    },
    run: {
      y: [0, -5, 0],
      x: [0, 5, 0, -5, 0],
      rotate: 0,
      scale: 1,
      opacity: 1,
      transition: { duration: 0.3, repeat: Infinity, ease: "linear", delay }
    },
    defend: {
      x: [-10, 10, -10],
      y: 0,
      rotate: 0,
      scale: [1, 1.05, 1],
      opacity: 1,
      transition: { duration: 0.6, repeat: Infinity, ease: "easeInOut", delay }
    },
    injured: {
      rotate: [0, 45, 30, 45, 30],
      y: [0, 15, 10, 15, 10],
      x: 0,
      scale: 0.9,
      opacity: [1, 0.7, 0.8, 0.7, 0.8],
      transition: { duration: 2, repeat: Infinity, ease: "easeInOut", delay }
    },
  };

  const armVariants = {
    idle: { rotate: 0 },
    dribble: {
      rotate: [0, 30, 0],
      transition: { duration: 0.2, repeat: Infinity, ease: "easeInOut", delay }
    },
    shoot: {
      rotate: [0, -60, -120, -60, 0],
      transition: { duration: 1.2, ease: "easeOut", delay }
    },
    celebrate: {
      rotate: [0, -45, 0, 45, 0],
      transition: { duration: 0.4, repeat: Infinity, ease: "easeInOut", delay }
    },
    run: {
      rotate: [30, -30, 30],
      transition: { duration: 0.3, repeat: Infinity, ease: "linear", delay }
    },
    defend: {
      rotate: [45, 30, 45],
      transition: { duration: 0.6, repeat: Infinity, ease: "easeInOut", delay }
    },
    injured: { rotate: 0 },
  };

  const legVariants = {
    idle: { rotate: 0 },
    dribble: {
      rotate: [0, 5, 0],
      transition: { duration: 0.4, repeat: Infinity, ease: "easeInOut", delay }
    },
    shoot: {
      rotate: [0, -15, 0],
      transition: { duration: 1.2, ease: "easeOut", delay }
    },
    celebrate: {
      rotate: [0, 10, -10, 0],
      transition: { duration: 0.5, repeat: Infinity, ease: "easeInOut", delay }
    },
    run: {
      rotate: [30, -30, 30],
      transition: { duration: 0.3, repeat: Infinity, ease: "linear", delay }
    },
    defend: {
      rotate: [10, -10, 10],
      transition: { duration: 0.6, repeat: Infinity, ease: "easeInOut", delay }
    },
    injured: { rotate: 15 },
  };

  const ballVariants = {
    idle: { 
      y: 0, 
      opacity: action === "idle" ? 0 : 1 
    },
    dribble: {
      y: [0, 25, 0],
      transition: { duration: 0.2, repeat: Infinity, ease: "easeIn", delay }
    },
    shoot: {
      y: [0, -40, -80],
      x: [0, 10, 30],
      opacity: [1, 1, 0],
      transition: { duration: 1.2, ease: "easeOut", delay }
    },
    celebrate: { y: 0, opacity: 0 },
    run: { y: 0, opacity: 0 },
    defend: { y: 0, opacity: 0 },
    injured: { y: 0, opacity: 0 },
  };

  return (
    <motion.div
      style={{
        position: "absolute",
        left: `${position.x}%`,
        top: `${position.y}%`,
        transform: `translate(-50%, -50%) scaleX(${flip})`,
        width: size,
        height: size * 1.5,
        zIndex: 100 + Math.floor(position.y),
      }}
      initial={{ opacity: 0, scale: 0.5 }}
      animate={{ opacity: 1, scale: 1 }}
      exit={{ opacity: 0, scale: 0 }}
      transition={{ duration: 0.3, delay }}
    >
      {imageUrl ? (
        // Image-based Player with transparent background
        <motion.div
          style={{ width: "100%", height: "100%", position: "relative" }}
          variants={imageVariants}
          animate={action}
        >
          {/* Player Image */}
          <div style={{
            width: size,
            height: size * 1.2,
            position: "relative",
            zIndex: 2,
            filter: action === "injured" ? "grayscale(100%)" : "none",
          }}>
            <img 
              src={imageUrl} 
              alt="Player" 
              style={{ 
                width: "100%", 
                height: "100%", 
                objectFit: "contain",
                // Subtle rim light effect matching arena spotlights
                filter: "drop-shadow(0 2px 3px rgba(0,0,0,0.3))"
              }} 
              onError={(e) => { e.target.style.display = 'none'; }}
            />
          </div>

          {/* Contact Shadow - tight, dark shadow directly at feet for grounding */}
          <motion.div
            style={{
              position: "absolute",
              bottom: 8,
              left: "50%",
              width: size * 0.4,
              height: size * 0.08,
              background: "rgba(0,0,0,0.45)",
              borderRadius: "50%",
              transform: "translateX(-50%)",
              filter: "blur(2px)",
              zIndex: 1,
              pointerEvents: "none",
            }}
            animate={{
              scaleX: action === "shoot" ? [1, 0.5, 1] : 1,
              opacity: action === "shoot" ? [0.45, 0.15, 0.45] : 0.45,
            }}
            transition={{ duration: action === "shoot" ? 1.0 : 0.3 }}
          />

          {/* Cast Shadow - extends back/down matching overhead arena lighting */}
          <motion.div
            style={{
              position: "absolute",
              bottom: 2,
              left: "50%",
              width: size * 0.55,
              height: size * 0.14,
              background: "radial-gradient(ellipse 60% 100% at 50% 30%, rgba(0,0,0,0.25) 0%, rgba(0,0,0,0.12) 50%, transparent 100%)",
              borderRadius: "50%",
              transform: "translateX(-50%) skewX(-5deg)",
              zIndex: 0,
              pointerEvents: "none",
            }}
            animate={{
              scaleX: action === "shoot" ? [1, 0.6, 1] : action === "celebrate" ? [1, 1.1, 1] : 1,
              opacity: action === "shoot" ? [0.8, 0.3, 0.8] : 0.8,
            }}
            transition={{ duration: action === "shoot" ? 1.0 : 0.4 }}
          />

          {/* Jersey Number Badge - positioned closer */}
          {jersey && (
            <div style={{
              position: "absolute",
              bottom: size * 0.18,
              left: "50%",
              transform: "translateX(-50%)",
              background: `linear-gradient(135deg, ${teamColor} 0%, ${teamColor}dd 100%)`,
              color: secondaryColor,
              borderRadius: "6px",
              padding: "3px 10px",
              fontSize: "12px",
              fontWeight: "bold",
              border: "2px solid rgba(255,255,255,0.95)",
              boxShadow: "0 3px 8px rgba(0,0,0,0.4), inset 0 1px 0 rgba(255,255,255,0.15)",
              zIndex: 3,
              letterSpacing: "0.5px",
              textShadow: "0 1px 2px rgba(0,0,0,0.3)",
            }}>
              {jersey}
            </div>
          )}

          {/* Team Logo Badge - positioned closer to player, overlapping slightly */}
          {teamLogo && (
            <div style={{
              position: "absolute",
              top: 4,
              right: 4,
              width: 22,
              height: 22,
              background: `linear-gradient(135deg, ${teamColor}88 0%, ${teamColor}44 100%)`,
              borderRadius: "50%",
              padding: "3px",
              boxShadow: `0 4px 20px rgba(0, 0, 0, 0.3)`,
              zIndex: 4,
            }}>
              <img src={teamLogo} alt="Team" style={{ width: "100%", height: "100%", objectFit: "contain" }} />
            </div>
          )}

          {/* Basketball - now controlled globally, not per player */}

          {/* Victory Trophy Effect */}
          {action === "celebrate" && (
            <motion.div
              style={{
                position: "absolute",
                top: -40,
                left: "50%",
                transform: "translateX(-50%)",
                fontSize: "48px",
                zIndex: 5,
              }}
              initial={{ y: 20, opacity: 0, scale: 0 }}
              animate={{ 
                y: [20, -10, 0], 
                opacity: [0, 1, 1],
                scale: [0, 1.3, 1],
                rotate: [-20, 10, 0]
              }}
              transition={{ duration: 0.6, repeat: Infinity, repeatDelay: 0.5 }}
            >
              🏆
            </motion.div>
          )}

          {/* Fire/Impact Effect for scoring */}
          {action === "shoot" && (
            <motion.div
              style={{
                position: "absolute",
                top: "50%",
                left: "50%",
                fontSize: "36px",
                zIndex: 4,
              }}
              initial={{ scale: 0, opacity: 0 }}
              animate={{ 
                scale: [0, 2, 0],
                opacity: [0, 1, 0],
                rotate: [0, 180]
              }}
              transition={{ duration: 0.8, delay: 0.5 }}
            >
              🔥
            </motion.div>
          )}

          {/* Defeat Effect */}
          {action === "injured" && (
            <motion.div
              style={{
                position: "absolute",
                top: -20,
                left: "50%",
                transform: "translateX(-50%)",
                fontSize: "32px",
                zIndex: 5,
              }}
              animate={{ 
                y: [-5, 5],
                opacity: [0.6, 1]
              }}
              transition={{ duration: 1, repeat: Infinity, repeatType: "reverse" }}
            >
              💫
            </motion.div>
          )}
          
        </motion.div>
      ) : (
        // Original SVG Character Fallback
        <motion.svg
          viewBox="0 0 80 120"
          style={{ width: "100%", height: "100%", overflow: "visible" }}
          variants={bodyVariants}
          animate={action}
        >
        {/* Shadow */}
        <ellipse cx="40" cy="115" rx="20" ry="5" fill="rgba(0,0,0,0.3)" />
        
        {/* Legs */}
        <motion.g variants={legVariants} animate={action} style={{ originX: "40px", originY: "75px" }}>
          {/* Left leg */}
          <rect x="30" y="75" width="8" height="30" rx="3" fill={teamColor} />
          <rect x="29" y="100" width="10" height="8" rx="2" fill="#222" /> {/* Shoe */}
          
          {/* Right leg */}
          <rect x="42" y="75" width="8" height="30" rx="3" fill={teamColor} />
          <rect x="41" y="100" width="10" height="8" rx="2" fill="#222" /> {/* Shoe */}
        </motion.g>
        
        {/* Body / Jersey */}
        <rect x="25" y="40" width="30" height="38" rx="5" fill={teamColor} />
        
        {/* Jersey details */}
        <rect x="27" y="42" width="26" height="2" fill={secondaryColor} opacity="0.3" />
        <text 
          x="40" 
          y="65" 
          textAnchor="middle" 
          fontSize="14" 
          fontWeight="bold" 
          fill={secondaryColor}
          opacity="0.9"
        >
          {jersey}
        </text>
        
        {/* Arms */}
        <motion.g variants={armVariants} animate={action} style={{ originX: "25px", originY: "45px" }}>
          {/* Left arm */}
          <rect x="12" y="42" width="15" height="8" rx="3" fill={teamColor} />
          <circle cx="12" cy="46" r="5" fill="#DEB887" /> {/* Hand */}
        </motion.g>
        
        <motion.g 
          variants={armVariants} 
          animate={action} 
          style={{ originX: "55px", originY: "45px" }}
        >
          {/* Right arm */}
          <rect x="53" y="42" width="15" height="8" rx="3" fill={teamColor} />
          <circle cx="68" cy="46" r="5" fill="#DEB887" /> {/* Hand */}
          
          {/* Basketball (attached to hand for dribble/shoot) */}
          <motion.g variants={ballVariants} animate={action}>
            <circle cx="72" cy="46" r="8" fill="#FF6B35" />
            <path d="M64 46 Q72 40, 80 46" stroke="#333" strokeWidth="1" fill="none" />
            <path d="M72 38 L72 54" stroke="#333" strokeWidth="1" />
          </motion.g>
        </motion.g>
        
        {/* Head */}
        <circle cx="40" cy="28" r="18" fill="#DEB887" />
        
        {/* Hair */}
        <ellipse cx="40" cy="18" rx="15" ry="8" fill="#222" />
        
        {/* Face */}
        <circle cx="34" cy="26" r="2" fill="#333" /> {/* Left eye */}
        <circle cx="46" cy="26" r="2" fill="#333" /> {/* Right eye */}
        
        {/* Expression based on action */}
        {action === "celebrate" && (
          <path d="M34 34 Q40 38, 46 34" stroke="#333" strokeWidth="2" fill="none" />
        )}
        {action === "injured" && (
          <>
            <path d="M34 34 Q40 30, 46 34" stroke="#333" strokeWidth="2" fill="none" />
            <path d="M32 24 L36 28" stroke="#333" strokeWidth="1.5" />
            <path d="M36 24 L32 28" stroke="#333" strokeWidth="1.5" />
          </>
        )}
        {(action === "idle" || action === "dribble" || action === "run" || action === "defend") && (
          <line x1="35" y1="34" x2="45" y2="34" stroke="#333" strokeWidth="2" />
        )}
        {action === "shoot" && (
          <circle cx="40" cy="34" r="3" fill="#333" />
        )}
        
        {/* Headband */}
        <rect x="22" y="20" width="36" height="4" rx="2" fill={secondaryColor} opacity="0.8" />
      </motion.svg>
      )}
    </motion.div>
  );
}

