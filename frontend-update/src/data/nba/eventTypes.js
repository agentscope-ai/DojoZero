/**
 * NBA Event Type Configurations
 * Defines action types and their visual/animation configurations
 */

// NBA Play-by-Play action configurations
export const actionConfigs = {
  // Scoring actions
  "3pt": {
    points: 3,
    effect: "fire",
    intensity: "high",
    duration: 2500,
    text: "THREE!",
    theme: "scoring",
  },
  "2pt": {
    points: 2,
    effect: "default",
    intensity: "normal",
    duration: 1800,
    text: "SCORE!",
    theme: "scoring",
  },
  dunk: {
    points: 2,
    effect: "shockwave",
    intensity: "high",
    duration: 2000,
    text: "SLAM DUNK!",
    theme: "scoring",
  },
  layup: {
    points: 2,
    effect: "default",
    intensity: "normal",
    duration: 1500,
    text: "LAYUP!",
    theme: "scoring",
  },
  freethrow: {
    points: 1,
    effect: "default",
    intensity: "light",
    duration: 1200,
    text: "",
    theme: "scoring",
  },

  // Defensive actions
  block: {
    points: 0,
    effect: "shield",
    intensity: "high",
    duration: 1800,
    text: "BLOCKED!",
    theme: "defense",
  },
  steal: {
    points: 0,
    effect: "speed",
    intensity: "normal",
    duration: 1500,
    text: "STEAL!",
    theme: "speed",
  },
  rebound: {
    points: 0,
    effect: "bounce",
    intensity: "light",
    duration: 1200,
    text: "REBOUND",
    theme: "neutral",
  },

  // Negative actions
  turnover: {
    points: 0,
    effect: "error",
    intensity: "normal",
    duration: 1500,
    text: "TURNOVER",
    theme: "negative",
  },
  foul: {
    points: 0,
    effect: "warning",
    intensity: "light",
    duration: 1500,
    text: "FOUL",
    theme: "negative",
  },
  violation: {
    points: 0,
    effect: "warning",
    intensity: "light",
    duration: 1200,
    text: "VIOLATION",
    theme: "negative",
  },

  // Game flow actions
  timeout: {
    points: 0,
    effect: "none",
    intensity: "light",
    duration: 1000,
    text: "TIMEOUT",
    theme: "neutral",
  },
  period: {
    points: 0,
    effect: "none",
    intensity: "light",
    duration: 1000,
    text: "",
    theme: "neutral",
  },
  jumpball: {
    points: 0,
    effect: "none",
    intensity: "light",
    duration: 800,
    text: "",
    theme: "neutral",
  },
  substitution: {
    points: 0,
    effect: "none",
    intensity: "none",
    duration: 0,
    text: "",
    theme: "neutral",
  },

  // Default
  default: {
    points: 0,
    effect: "none",
    intensity: "none",
    duration: 0,
    text: "",
    theme: "neutral",
  },
};

/**
 * Get action configuration by type
 * @param {string} actionType - The action type (e.g., "3pt", "block")
 * @returns {Object} Action configuration
 */
export const getActionConfig = (actionType) => {
  const normalizedType = actionType?.toLowerCase()?.trim() || "";
  
  // Direct match
  if (actionConfigs[normalizedType]) {
    return actionConfigs[normalizedType];
  }

  // Check for partial matches (e.g., "3pt made" -> "3pt")
  for (const [key, config] of Object.entries(actionConfigs)) {
    if (normalizedType.includes(key)) {
      return config;
    }
  }

  return actionConfigs.default;
};

/**
 * Check if an action type should trigger animation
 * @param {string} actionType - The action type
 * @returns {boolean}
 */
export const shouldAnimate = (actionType) => {
  const config = getActionConfig(actionType);
  return config.duration > 0 && config.effect !== "none";
};

/**
 * Get animation duration for an action
 * @param {string} actionType - The action type
 * @returns {number} Duration in milliseconds
 */
export const getAnimationDuration = (actionType) => {
  return getActionConfig(actionType).duration;
};
