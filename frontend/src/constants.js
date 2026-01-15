// API Configuration
// Frontend UI should only interact with Frontend Server (port 3001)
// These values are loaded from .env file (VITE_API_BASE_URL, VITE_WS_BASE_URL)
export const API_BASE_URL = import.meta.env.VITE_API_BASE_URL || "http://localhost:3001/api";
export const WS_BASE_URL = import.meta.env.VITE_WS_BASE_URL || "ws://localhost:3001";

// Dojozero CDN Assets
export const DOJOZERO_CDN = {
  room_background: "https://img.alicdn.com/imgextra/i4/O1CN01rGeB7i1Ma9PbVLTIw_!!6000000001450-2-tps-2176-1952.png",
  agentboard: "https://img.alicdn.com/imgextra/i1/O1CN01Y5wkVa1pKEVJ21St9_!!6000000005341-0-tps-1378-1476.jpg",
  scoreboard: "https://img.alicdn.com/imgextra/i4/O1CN01b1fuBG1w3TJrxcXQz_!!6000000006252-0-tps-1618-1028.jpg",
};

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

// NBA Team data - All 30 teams with team IDs for API calls
// Logo URLs are generated dynamically via getTeamLogo(tricode)
export const nbaTeams = {
  // Atlantic Division
  BOS: { id: 1610612738, name: "Celtics", city: "Boston", color: "#007A33", secondaryColor: "#BA9653" },
  BKN: { id: 1610612751, name: "Nets", city: "Brooklyn", color: "#000000", secondaryColor: "#FFFFFF" },
  NYK: { id: 1610612752, name: "Knicks", city: "New York", color: "#006BB6", secondaryColor: "#F58426" },
  PHI: { id: 1610612755, name: "76ers", city: "Philadelphia", color: "#006BB6", secondaryColor: "#ED174C" },
  TOR: { id: 1610612761, name: "Raptors", city: "Toronto", color: "#CE1141", secondaryColor: "#000000" },
  // Central Division
  CHI: { id: 1610612741, name: "Bulls", city: "Chicago", color: "#CE1141", secondaryColor: "#000000" },
  CLE: { id: 1610612739, name: "Cavaliers", city: "Cleveland", color: "#860038", secondaryColor: "#FDBB30" },
  DET: { id: 1610612765, name: "Pistons", city: "Detroit", color: "#C8102E", secondaryColor: "#1D42BA" },
  IND: { id: 1610612754, name: "Pacers", city: "Indiana", color: "#002D62", secondaryColor: "#FDBB30" },
  MIL: { id: 1610612749, name: "Bucks", city: "Milwaukee", color: "#00471B", secondaryColor: "#EEE1C6" },
  // Southeast Division
  ATL: { id: 1610612737, name: "Hawks", city: "Atlanta", color: "#E03A3E", secondaryColor: "#C1D32F" },
  CHA: { id: 1610612766, name: "Hornets", city: "Charlotte", color: "#1D1160", secondaryColor: "#00788C" },
  MIA: { id: 1610612748, name: "Heat", city: "Miami", color: "#98002E", secondaryColor: "#F9A01B" },
  ORL: { id: 1610612753, name: "Magic", city: "Orlando", color: "#0077C0", secondaryColor: "#C4CED4" },
  WAS: { id: 1610612764, name: "Wizards", city: "Washington", color: "#002B5C", secondaryColor: "#E31837" },
  // Northwest Division
  DEN: { id: 1610612743, name: "Nuggets", city: "Denver", color: "#0E2240", secondaryColor: "#FEC524" },
  MIN: { id: 1610612750, name: "Timberwolves", city: "Minnesota", color: "#0C2340", secondaryColor: "#236192" },
  OKC: { id: 1610612760, name: "Thunder", city: "Oklahoma City", color: "#007AC1", secondaryColor: "#EF3B24" },
  POR: { id: 1610612757, name: "Trail Blazers", city: "Portland", color: "#E03A3E", secondaryColor: "#000000" },
  UTA: { id: 1610612762, name: "Jazz", city: "Utah", color: "#002B5C", secondaryColor: "#00471B" },
  // Pacific Division
  GSW: { id: 1610612744, name: "Warriors", city: "Golden State", color: "#1D428A", secondaryColor: "#FFC72C" },
  LAC: { id: 1610612746, name: "Clippers", city: "Los Angeles", color: "#C8102E", secondaryColor: "#1D428A" },
  LAL: { id: 1610612747, name: "Lakers", city: "Los Angeles", color: "#552583", secondaryColor: "#FDB927" },
  PHX: { id: 1610612756, name: "Suns", city: "Phoenix", color: "#1D1160", secondaryColor: "#E56020" },
  SAC: { id: 1610612758, name: "Kings", city: "Sacramento", color: "#5A2D81", secondaryColor: "#63727A" },
  // Southwest Division
  DAL: { id: 1610612742, name: "Mavericks", city: "Dallas", color: "#00538C", secondaryColor: "#002B5E" },
  HOU: { id: 1610612745, name: "Rockets", city: "Houston", color: "#CE1141", secondaryColor: "#000000" },
  MEM: { id: 1610612763, name: "Grizzlies", city: "Memphis", color: "#5D76A9", secondaryColor: "#12173F" },
  NOP: { id: 1610612740, name: "Pelicans", city: "New Orleans", color: "#0C2340", secondaryColor: "#C8102E" },
  SAS: { id: 1610612759, name: "Spurs", city: "San Antonio", color: "#C4CED4", secondaryColor: "#000000" },
};


/**
 * NBA Players database with player IDs for CDN headshots
 * Organized by team tricode, each team has 5+ players
 * Player IDs sourced from stats.nba.com
 */
export const NBA_PLAYERS = {
    // Atlantic Division
    BOS: [
        {id: 1628369, name: "Jayson Tatum", position: "F", number: "0"},
        {id: 1627759, name: "Jaylen Brown", position: "G", number: "7"},
        {id: 1629684, name: "Derrick White", position: "G", number: "9"},
        {id: 203935, name: "Marcus Smart", position: "G", number: "36"},
        {id: 1628464, name: "Al Horford", position: "C", number: "42"},
        {id: 1630202, name: "Payton Pritchard", position: "G", number: "11"},
    ],
    BKN: [
        {id: 1629648, name: "Cam Thomas", position: "G", number: "24"},
        {id: 1630556, name: "Day'Ron Sharpe", position: "C", number: "5"},
        {id: 1630228, name: "Nic Claxton", position: "C", number: "33"},
        {id: 1628381, name: "Dennis Smith Jr.", position: "G", number: "4"},
        {id: 1630532, name: "Cameron Johnson", position: "F", number: "2"},
        {id: 1629649, name: "Trendon Watford", position: "F", number: "9"},
    ],
    NYK: [
        {id: 1628973, name: "Jalen Brunson", position: "G", number: "11"},
        {id: 1626157, name: "Karl-Anthony Towns", position: "C", number: "32"},
        {id: 203944, name: "Julius Randle", position: "F", number: "30"},
        {id: 1629628, name: "RJ Barrett", position: "F", number: "9"},
        {id: 1629011, name: "Mitchell Robinson", position: "C", number: "23"},
        {id: 1630560, name: "Quentin Grimes", position: "G", number: "6"},
    ],
    PHI: [
        {id: 203954, name: "Joel Embiid", position: "C", number: "21"},
        {id: 202331, name: "Paul George", position: "F", number: "8"},
        {id: 1629003, name: "Tyrese Maxey", position: "G", number: "0"},
        {id: 201942, name: "Tobias Harris", position: "F", number: "12"},
        {id: 1630178, name: "Jared McCain", position: "G", number: "20"},
        {id: 1629001, name: "De'Anthony Melton", position: "G", number: "8"},
    ],
    TOR: [
        {id: 1630567, name: "Scottie Barnes", position: "F", number: "4"},
        {id: 1630169, name: "RJ Barrett", position: "F", number: "9"},
        {id: 1629056, name: "Immanuel Quickley", position: "G", number: "5"},
        {id: 1628384, name: "OG Anunoby", position: "F", number: "3"},
        {id: 1630532, name: "Gradey Dick", position: "G", number: "1"},
        {id: 1630680, name: "Christian Koloko", position: "C", number: "35"},
    ],

    // Central Division
    CHI: [
        {id: 203897, name: "Zach LaVine", position: "G", number: "8"},
        {id: 1628374, name: "Coby White", position: "G", number: "0"},
        {id: 1629632, name: "Patrick Williams", position: "F", number: "44"},
        {id: 1626192, name: "Nikola Vučević", position: "C", number: "9"},
        {id: 1630224, name: "Ayo Dosunmu", position: "G", number: "12"},
        {id: 1629655, name: "Josh Giddey", position: "G", number: "3"},
    ],
    CLE: [
        {id: 1628378, name: "Donovan Mitchell", position: "G", number: "45"},
        {id: 1629636, name: "Darius Garland", position: "G", number: "10"},
        {id: 1629631, name: "Jarrett Allen", position: "C", number: "31"},
        {id: 1628386, name: "Evan Mobley", position: "F", number: "4"},
        {id: 1628371, name: "Isaac Okoro", position: "F", number: "35"},
        {id: 1629659, name: "Max Strus", position: "G", number: "1"},
    ],
    DET: [
        {id: 1630595, name: "Cade Cunningham", position: "G", number: "2"},
        {id: 1631095, name: "Jaden Ivey", position: "G", number: "23"},
        {id: 1630685, name: "Ausar Thompson", position: "F", number: "9"},
        {id: 1628396, name: "Isaiah Stewart", position: "C", number: "28"},
        {id: 1630688, name: "Jalen Duren", position: "C", number: "0"},
        {id: 1630693, name: "Marcus Sasser", position: "G", number: "25"},
    ],
    IND: [
        {id: 1630169, name: "Tyrese Haliburton", position: "G", number: "0"},
        {id: 1630170, name: "Bennedict Mathurin", position: "G", number: "00"},
        {id: 203926, name: "Myles Turner", position: "C", number: "33"},
        {id: 1630183, name: "Pascal Siakam", position: "F", number: "43"},
        {id: 1629652, name: "Obi Toppin", position: "F", number: "1"},
        {id: 1629660, name: "Andrew Nembhard", position: "G", number: "2"},
    ],
    MIL: [
        {id: 203507, name: "Giannis Antetokounmpo", position: "F", number: "34"},
        {id: 203081, name: "Damian Lillard", position: "G", number: "0"},
        {id: 203114, name: "Khris Middleton", position: "F", number: "22"},
        {id: 202083, name: "Brook Lopez", position: "C", number: "11"},
        {id: 1630245, name: "MarJon Beauchamp", position: "F", number: "0"},
        {id: 203992, name: "Bobby Portis", position: "F", number: "9"},
    ],

    // Southeast Division
    ATL: [
        {id: 1629027, name: "Trae Young", position: "G", number: "11"},
        {id: 1629677, name: "De'Andre Hunter", position: "F", number: "12"},
        {id: 1630214, name: "Onyeka Okongwu", position: "C", number: "17"},
        {id: 1629634, name: "Jalen Johnson", position: "F", number: "1"},
        {id: 203939, name: "Clint Capela", position: "C", number: "15"},
        {id: 1630598, name: "Bogdan Bogdanović", position: "G", number: "13"},
    ],
    CHA: [
        {id: 1630163, name: "LaMelo Ball", position: "G", number: "1"},
        {id: 1628980, name: "Miles Bridges", position: "F", number: "0"},
        {id: 1630532, name: "Brandon Miller", position: "F", number: "24"},
        {id: 1628966, name: "P.J. Washington", position: "F", number: "25"},
        {id: 1629607, name: "Mark Williams", position: "C", number: "5"},
        {id: 1630200, name: "Tre Mann", position: "G", number: "2"},
    ],
    MIA: [
        {id: 202710, name: "Jimmy Butler", position: "F", number: "22"},
        {id: 1628389, name: "Bam Adebayo", position: "C", number: "13"},
        {id: 1630253, name: "Tyler Herro", position: "G", number: "14"},
        {id: 1628407, name: "Duncan Robinson", position: "F", number: "55"},
        {id: 1641706, name: "Jaime Jaquez Jr.", position: "F", number: "11"},
        {id: 1627790, name: "Kyle Lowry", position: "G", number: "7"},
    ],
    ORL: [
        {id: 1631094, name: "Paolo Banchero", position: "F", number: "5"},
        {id: 1630532, name: "Franz Wagner", position: "F", number: "22"},
        {id: 1629648, name: "Jalen Suggs", position: "G", number: "4"},
        {id: 1630234, name: "Wendell Carter Jr.", position: "C", number: "34"},
        {id: 1629750, name: "Cole Anthony", position: "G", number: "50"},
        {id: 1630680, name: "Jonathan Isaac", position: "F", number: "1"},
    ],
    WAS: [
        {id: 1630162, name: "Jordan Poole", position: "G", number: "13"},
        {id: 1630526, name: "Bilal Coulibaly", position: "F", number: "0"},
        {id: 1631096, name: "Kyle Kuzma", position: "F", number: "33"},
        {id: 1629661, name: "Deni Avdija", position: "F", number: "9"},
        {id: 1629647, name: "Daniel Gafford", position: "C", number: "21"},
        {id: 1629065, name: "Corey Kispert", position: "F", number: "24"},
    ],

    // Northwest Division
    DEN: [
        {id: 203999, name: "Nikola Jokić", position: "C", number: "15"},
        {id: 203095, name: "Michael Porter Jr.", position: "F", number: "1"},
        {id: 1628398, name: "Aaron Gordon", position: "F", number: "50"},
        {id: 1629008, name: "Jamal Murray", position: "G", number: "27"},
        {id: 1628370, name: "Kentavious Caldwell-Pope", position: "G", number: "5"},
        {id: 1630224, name: "Christian Braun", position: "G", number: "0"},
    ],
    MIN: [
        {id: 1630162, name: "Anthony Edwards", position: "G", number: "5"},
        {id: 203497, name: "Rudy Gobert", position: "C", number: "27"},
        {id: 1629027, name: "Jaden McDaniels", position: "F", number: "3"},
        {id: 1627750, name: "Mike Conley", position: "G", number: "10"},
        {id: 1630163, name: "Naz Reid", position: "C", number: "11"},
        {id: 1629717, name: "Nickeil Alexander-Walker", position: "G", number: "9"},
    ],
    OKC: [
        {id: 1628983, name: "Shai Gilgeous-Alexander", position: "G", number: "2"},
        {id: 1631096, name: "Chet Holmgren", position: "C", number: "7"},
        {id: 1629655, name: "Josh Giddey", position: "G", number: "3"},
        {id: 1630578, name: "Jalen Williams", position: "F", number: "8"},
        {id: 1628425, name: "Luguentz Dort", position: "G", number: "5"},
        {id: 1629718, name: "Isaiah Joe", position: "G", number: "11"},
    ],
    POR: [
        {id: 1630703, name: "Scoot Henderson", position: "G", number: "0"},
        {id: 1630579, name: "Shaedon Sharpe", position: "G", number: "17"},
        {id: 203200, name: "Jerami Grant", position: "F", number: "9"},
        {id: 1629673, name: "Anfernee Simons", position: "G", number: "1"},
        {id: 1630581, name: "Deandre Ayton", position: "C", number: "2"},
        {id: 203918, name: "Robert Williams III", position: "C", number: "44"},
    ],
    UTA: [
        {id: 1629636, name: "Lauri Markkanen", position: "F", number: "23"},
        {id: 1628978, name: "Collin Sexton", position: "G", number: "2"},
        {id: 1628380, name: "John Collins", position: "F", number: "20"},
        {id: 1629640, name: "Jordan Clarkson", position: "G", number: "00"},
        {id: 1628467, name: "Walker Kessler", position: "C", number: "24"},
        {id: 1630702, name: "Keyonte George", position: "G", number: "3"},
    ],

    // Pacific Division
    GSW: [
        {id: 201939, name: "Stephen Curry", position: "G", number: "30"},
        {id: 1628398, name: "Andrew Wiggins", position: "F", number: "22"},
        {id: 1627775, name: "Draymond Green", position: "F", number: "23"},
        {id: 1627780, name: "Gary Payton II", position: "G", number: "8"},
        {id: 1628369, name: "Jonathan Kuminga", position: "F", number: "00"},
        {id: 1630224, name: "Brandin Podziemski", position: "G", number: "2"},
    ],
    LAC: [
        {id: 202695, name: "Kawhi Leonard", position: "F", number: "2"},
        {id: 201935, name: "James Harden", position: "G", number: "1"},
        {id: 1627826, name: "Ivica Zubac", position: "C", number: "40"},
        {id: 203468, name: "Norman Powell", position: "G", number: "24"},
        {id: 1629655, name: "Terance Mann", position: "G", number: "14"},
        {id: 1629726, name: "Amir Coffey", position: "F", number: "7"},
    ],
    LAL: [
        {id: 2544, name: "LeBron James", position: "F", number: "23"},
        {id: 203076, name: "Anthony Davis", position: "C", number: "3"},
        {id: 1629029, name: "Austin Reaves", position: "G", number: "15"},
        {id: 1626167, name: "D'Angelo Russell", position: "G", number: "1"},
        {id: 1630559, name: "Rui Hachimura", position: "F", number: "28"},
        {id: 1628389, name: "Jarred Vanderbilt", position: "F", number: "2"},
    ],
    PHX: [
        {id: 201142, name: "Kevin Durant", position: "F", number: "35"},
        {id: 1626164, name: "Devin Booker", position: "G", number: "1"},
        {id: 201566, name: "Bradley Beal", position: "G", number: "3"},
        {id: 1629028, name: "Jusuf Nurkić", position: "C", number: "20"},
        {id: 1630532, name: "Grayson Allen", position: "G", number: "8"},
        {id: 1630193, name: "Royce O'Neale", position: "F", number: "00"},
    ],
    SAC: [
        {id: 1628368, name: "De'Aaron Fox", position: "G", number: "5"},
        {id: 1627734, name: "Domantas Sabonis", position: "C", number: "10"},
        {id: 201942, name: "DeMar DeRozan", position: "F", number: "10"},
        {id: 1630169, name: "Keegan Murray", position: "F", number: "13"},
        {id: 1628381, name: "Kevin Huerter", position: "G", number: "9"},
        {id: 1630552, name: "Malik Monk", position: "G", number: "0"},
    ],

    // Southwest Division
    DAL: [
        {id: 1629029, name: "Luka Dončić", position: "G", number: "77"},
        {id: 202681, name: "Kyrie Irving", position: "G", number: "11"},
        {id: 1627747, name: "Daniel Gafford", position: "C", number: "21"},
        {id: 203918, name: "P.J. Washington", position: "F", number: "25"},
        {id: 1630183, name: "Dereck Lively II", position: "C", number: "2"},
        {id: 1629661, name: "Jaden Hardy", position: "G", number: "1"},
    ],
    HOU: [
        {id: 1630224, name: "Jalen Green", position: "G", number: "4"},
        {id: 1630578, name: "Jabari Smith Jr.", position: "F", number: "10"},
        {id: 1630200, name: "Alperen Şengün", position: "C", number: "28"},
        {id: 1629634, name: "Amen Thompson", position: "G", number: "1"},
        {id: 1629750, name: "Fred VanVleet", position: "G", number: "5"},
        {id: 1628398, name: "Dillon Brooks", position: "F", number: "9"},
    ],
    MEM: [
        {id: 1629630, name: "Ja Morant", position: "G", number: "12"},
        {id: 1629028, name: "Jaren Jackson Jr.", position: "F", number: "13"},
        {id: 1629738, name: "Desmond Bane", position: "G", number: "22"},
        {id: 1628382, name: "Marcus Smart", position: "G", number: "36"},
        {id: 1630596, name: "Ziaire Williams", position: "F", number: "8"},
        {id: 1628963, name: "Brandon Clarke", position: "F", number: "15"},
    ],
    NOP: [
        {id: 1629627, name: "Zion Williamson", position: "F", number: "1"},
        {id: 1627742, name: "Brandon Ingram", position: "F", number: "14"},
        {id: 1629638, name: "CJ McCollum", position: "G", number: "3"},
        {id: 203897, name: "Jonas Valančiūnas", position: "C", number: "17"},
        {id: 1629680, name: "Trey Murphy III", position: "F", number: "25"},
        {id: 1630593, name: "Herbert Jones", position: "F", number: "5"},
    ],
    SAS: [
        {id: 1641705, name: "Victor Wembanyama", position: "C", number: "1"},
        {id: 1629683, name: "Devin Vassell", position: "G", number: "24"},
        {id: 1628389, name: "Keldon Johnson", position: "F", number: "3"},
        {id: 1630596, name: "Jeremy Sochan", position: "F", number: "10"},
        {id: 203503, name: "Tre Jones", position: "G", number: "33"},
        {id: 1630693, name: "Malaki Branham", position: "G", number: "22"},
    ],
};

// NBA CDN URL helpers
// All resources loaded directly from NBA CDN
export const NBA_CDN = {
  // Player headshot by player ID
  playerHeadshot: (playerId) =>
    `https://cdn.nba.com/headshots/nba/latest/260x190/${playerId}.png`,
  
  // Team logo by team ID
  teamLogoCDN: (teamId) =>
    `https://cdn.nba.com/logos/nba/${teamId}/primary/L/logo.svg`,
};

// Get team logo URL by tricode
export const getTeamLogo = (tricode) => {
  const team = nbaTeams[tricode];
  return team ? NBA_CDN.teamLogoCDN(team.id) : null;
};

// Get player headshot URL by player ID
export const getPlayerHeadshot = (playerId) => {
  return playerId ? NBA_CDN.playerHeadshot(playerId) : null;
};

// Helper function to find team by name or city
// Returns team data with dynamically generated logo URL from CDN
export const findTeamByName = (name) => {
  if (!name) return null;
  const normalizedName = name.toLowerCase().trim();
  
  // Helper to add logo to team data
  const withLogo = (tricode, team) => ({
    tricode,
    ...team,
    logo: NBA_CDN.teamLogoCDN(team.id),
  });
  
  // First try direct tricode match
  const tricode = name.toUpperCase();
  if (nbaTeams[tricode]) {
    return withLogo(tricode, nbaTeams[tricode]);
  }
  
  // Search by team name or city
  for (const [tc, team] of Object.entries(nbaTeams)) {
    if (
      team.name.toLowerCase() === normalizedName ||
      team.city.toLowerCase() === normalizedName ||
      `${team.city} ${team.name}`.toLowerCase() === normalizedName
    ) {
      return withLogo(tc, team);
    }
  }
  
  // Partial match fallback
  for (const [tc, team] of Object.entries(nbaTeams)) {
    if (
      normalizedName.includes(team.name.toLowerCase()) ||
      normalizedName.includes(team.city.toLowerCase())
    ) {
      return withLogo(tc, team);
    }
  }
  
  return null;
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
  game_initialize: {
    icon: "settings",
    label: "Game Setup",
    color: "#6B7280",
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
  play_by_play: {
    icon: "basketball",
    label: "Play",
    color: "#10B981",
  },
  raw_web_search: {
    icon: "search",
    label: "Web Search",
    color: "#6366F1",
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
