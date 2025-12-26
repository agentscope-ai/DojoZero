// API Configuration
export const API_BASE_URL = "http://localhost:5001/api";

// Model provider logos and colors
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

// NBA Team data
export const nbaTeams = {
  MIA: {
    name: "Heat",
    city: "Miami",
    color: "#98002E",
    secondaryColor: "#F9A01B",
    logo: "https://cdn.nba.com/logos/nba/1610612748/global/L/logo.svg",
  },
  TOR: {
    name: "Raptors",
    city: "Toronto",
    color: "#CE1141",
    secondaryColor: "#000000",
    logo: "https://cdn.nba.com/logos/nba/1610612761/global/L/logo.svg",
  },
  LAL: {
    name: "Lakers",
    city: "Los Angeles",
    color: "#552583",
    secondaryColor: "#FDB927",
    logo: "https://cdn.nba.com/logos/nba/1610612747/global/L/logo.svg",
  },
  BOS: {
    name: "Celtics",
    city: "Boston",
    color: "#007A33",
    secondaryColor: "#BA9653",
    logo: "https://cdn.nba.com/logos/nba/1610612738/global/L/logo.svg",
  },
  GSW: {
    name: "Warriors",
    city: "Golden State",
    color: "#1D428A",
    secondaryColor: "#FFC72C",
    logo: "https://cdn.nba.com/logos/nba/1610612744/global/L/logo.svg",
  },
  NYK: {
    name: "Knicks",
    city: "New York",
    color: "#006BB6",
    secondaryColor: "#F58426",
    logo: "https://cdn.nba.com/logos/nba/1610612752/global/L/logo.svg",
  },
  CHI: {
    name: "Bulls",
    city: "Chicago",
    color: "#CE1141",
    secondaryColor: "#000000",
    logo: "https://cdn.nba.com/logos/nba/1610612741/global/L/logo.svg",
  },
  SAS: {
    name: "Spurs",
    city: "San Antonio",
    color: "#C4CED4",
    secondaryColor: "#000000",
    logo: "https://cdn.nba.com/logos/nba/1610612759/global/L/logo.svg",
  },
};

// Event type configurations for visual display
export const eventTypes = {
  game_update: {
    icon: "activity",
    label: "Game Update",
    color: "#3B82F6",
  },
  odds_update: {
    icon: "trending-up",
    label: "Odds Change",
    color: "#10B981",
  },
  game_start: {
    icon: "play",
    label: "Game Started",
    color: "#F59E0B",
  },
  game_result: {
    icon: "trophy",
    label: "Game Result",
    color: "#8B5CF6",
  },
  expert_prediction: {
    icon: "brain",
    label: "Expert Prediction",
    color: "#EC4899",
  },
  injury_summary: {
    icon: "alert-triangle",
    label: "Injury Report",
    color: "#EF4444",
  },
  power_ranking: {
    icon: "bar-chart-2",
    label: "Power Ranking",
    color: "#6366F1",
  },
  in_game_critical: {
    icon: "zap",
    label: "Critical Moment",
    color: "#F97316",
  },
};

// Theme colors
export const themes = {
  dark: {
    bg: "#0A0E17",
    bgSecondary: "#111827",
    bgTertiary: "#1F2937",
    glass: "rgba(17, 24, 39, 0.8)",
    glassBorder: "rgba(75, 85, 99, 0.4)",
    text: "#F9FAFB",
    textSecondary: "#9CA3AF",
    accent: "#3B82F6",
    accentGlow: "rgba(59, 130, 246, 0.5)",
  },
  light: {
    bg: "#F3F4F6",
    bgSecondary: "#FFFFFF",
    bgTertiary: "#E5E7EB",
    glass: "rgba(255, 255, 255, 0.8)",
    glassBorder: "rgba(209, 213, 219, 0.6)",
    text: "#111827",
    textSecondary: "#6B7280",
    accent: "#2563EB",
    accentGlow: "rgba(37, 99, 235, 0.3)",
  },
};
