/**
 * Shared constants for DojoZero Arena
 * Sport-agnostic configurations
 */

// DojoZero CDN Assets
export const DOJOZERO_CDN = {
  room_background: "https://img.alicdn.com/imgextra/i4/O1CN01rGeB7i1Ma9PbVLTIw_!!6000000001450-2-tps-2176-1952.png",
  agentboard: "https://img.alicdn.com/imgextra/i1/O1CN01Y5wkVa1pKEVJ21St9_!!6000000005341-0-tps-1378-1476.jpg",
  scoreboard: "https://img.alicdn.com/imgextra/i4/O1CN01b1fuBG1w3TJrxcXQz_!!6000000006252-0-tps-1618-1028.jpg",
};

// Model provider logos and colors (for AI Agents display)
export const modelProviders = {
  "qwen-flash": {
    name: "Qwen Flash",
    provider: "Alibaba",
    logo: "https://img.alicdn.com/imgextra/i4/O1CN01mTs8oZ1gsHOj0xy7O_!!6000000004197-0-tps-204-192.jpg",
    color: "#FF6A00",
    bgGradient: "linear-gradient(135deg, #FF6A00 0%, #FF9500 100%)",
  },
  "qwen-plus": {
    name: "Qwen Plus",
    provider: "Alibaba",
    logo: "https://img.alicdn.com/imgextra/i4/O1CN01mTs8oZ1gsHOj0xy7O_!!6000000004197-0-tps-204-192.jpg",
    color: "#FF6A00",
    bgGradient: "linear-gradient(135deg, #FF6A00 0%, #FF9500 100%)",
  },
  "gpt-4": {
    name: "GPT-4",
    provider: "OpenAI",
    logo: "https://img.alicdn.com/imgextra/i3/O1CN01T1eaM8287qU0nZm91_!!6000000007886-2-tps-148-148.png",
    color: "#10A37F",
    bgGradient: "linear-gradient(135deg, #10A37F 0%, #1ED898 100%)",
  },
  "gpt-4o": {
    name: "GPT-4o",
    provider: "OpenAI",
    logo: "https://img.alicdn.com/imgextra/i3/O1CN01T1eaM8287qU0nZm91_!!6000000007886-2-tps-148-148.png",
    color: "#10A37F",
    bgGradient: "linear-gradient(135deg, #10A37F 0%, #1ED898 100%)",
  },
  claude: {
    name: "Claude",
    provider: "Anthropic",
    logo: "https://img.alicdn.com/imgextra/i4/O1CN01Sg8gbo1HKVnoU16rm_!!6000000000739-2-tps-148-148.png",
    color: "#D97706",
    bgGradient: "linear-gradient(135deg, #D97706 0%, #F59E0B 100%)",
  },
  gemini: {
    name: "Gemini",
    provider: "Google",
    logo: "https://img.alicdn.com/imgextra/i1/O1CN01fZwVYk1caBHdzh9qh_!!6000000003616-0-tps-148-148.jpg",
    color: "#4285F4",
    bgGradient: "linear-gradient(135deg, #4285F4 0%, #34A853 50%, #FBBC05 100%)",
  },
  deepseek: {
    name: "DeepSeek",
    provider: "DeepSeek",
    logo: "https://img.alicdn.com/imgextra/i3/O1CN01ocd9iO1D7S2qgEIXQ_!!6000000000169-2-tps-203-148.png",
    color: "#6366F1",
    bgGradient: "linear-gradient(135deg, #6366F1 0%, #8B5CF6 100%)",
  },
  default: {
    name: "AI Agent",
    provider: "Unknown",
    logo: "",
    color: "#64748B",
    bgGradient: "linear-gradient(135deg, #64748B 0%, #94A3B8 100%)",
  },
};

// Supported sports/leagues
export const SUPPORTED_LEAGUES = {
  NBA: {
    name: "NBA",
    sport: "basketball",
    color: "#C9082A",
    periodName: "Quarter",
    periodCount: 4,
    clockFormat: "mm:ss",
  },
  NFL: {
    name: "NFL",
    sport: "football",
    color: "#013369",
    periodName: "Quarter",
    periodCount: 4,
    clockFormat: "mm:ss",
  },
};

// Action effect themes (sport-agnostic)
export const actionThemes = {
  // Scoring - Fire/Gold
  scoring: {
    primary: "#FF6B35",
    secondary: "#FFD700",
    glow: "rgba(255, 107, 53, 0.6)",
  },
  // Defense - Electric Blue
  defense: {
    primary: "#00D4FF",
    secondary: "#FFFFFF",
    glow: "rgba(0, 212, 255, 0.6)",
  },
  // Speed - Lightning Yellow
  speed: {
    primary: "#FFEB3B",
    secondary: "#FFC107",
    glow: "rgba(255, 235, 59, 0.6)",
  },
  // Negative - Warning Red
  negative: {
    primary: "#FF4757",
    secondary: "#FF6B6B",
    glow: "rgba(255, 71, 87, 0.4)",
  },
  // Neutral - Cool Gray
  neutral: {
    primary: "#64748B",
    secondary: "#94A3B8",
    glow: "rgba(100, 116, 139, 0.4)",
  },
};
