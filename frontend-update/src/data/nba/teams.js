/**
 * NBA Teams Database
 * All 30 NBA teams with team IDs for CDN logo URLs
 */

// NBA CDN URL helpers
export const NBA_CDN = {
  // Player headshot by player ID
  playerHeadshot: (playerId) =>
    `https://cdn.nba.com/headshots/nba/latest/260x190/${playerId}.png`,

  // Team logo by team ID
  teamLogo: (teamId) =>
    `https://cdn.nba.com/logos/nba/${teamId}/primary/L/logo.svg`,
};

// NBA Team data - All 30 teams with team IDs and colors
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

// Get team logo URL by tricode
export const getTeamLogo = (tricode) => {
  const team = nbaTeams[tricode];
  return team ? NBA_CDN.teamLogo(team.id) : null;
};

// Get player headshot URL by player ID
export const getPlayerHeadshot = (playerId) => {
  return playerId ? NBA_CDN.playerHeadshot(playerId) : null;
};

// Helper function to get team info with logo
export const getTeamInfo = (tricode) => {
  if (!tricode) return null;
  const team = nbaTeams[tricode.toUpperCase()];
  if (!team) return null;
  return {
    tricode: tricode.toUpperCase(),
    ...team,
    logo: NBA_CDN.teamLogo(team.id),
  };
};

// Helper function to find team by name or city
export const findTeamByName = (name) => {
  if (!name) return null;
  const normalizedName = name.toLowerCase().trim();

  // First try direct tricode match
  const tricode = name.toUpperCase();
  if (nbaTeams[tricode]) {
    return getTeamInfo(tricode);
  }

  // Search by team name or city
  for (const [tc, team] of Object.entries(nbaTeams)) {
    if (
      team.name.toLowerCase() === normalizedName ||
      team.city.toLowerCase() === normalizedName ||
      `${team.city} ${team.name}`.toLowerCase() === normalizedName
    ) {
      return getTeamInfo(tc);
    }
  }

  // Partial match fallback
  for (const [tc, team] of Object.entries(nbaTeams)) {
    if (
      normalizedName.includes(team.name.toLowerCase()) ||
      normalizedName.includes(team.city.toLowerCase())
    ) {
      return getTeamInfo(tc);
    }
  }

  return null;
};
