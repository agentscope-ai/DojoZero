import {useEffect, useMemo, useState} from "react";
import {findTeamByName, getPlayerHeadshot, getTeamLogo, NBA_CDN, NBA_PLAYERS, nbaTeams} from "../constants";

/**
 * Get players for a team by tricode
 * @param {string} tricode - Team tricode (e.g., "LAL", "BOS")
 * @returns {Array} Array of player objects with headshot URLs
 */
export function getTeamPlayers(tricode) {
  const players = NBA_PLAYERS[tricode] || [];
  return players.map((player) => ({
    ...player,
    headshot: getPlayerHeadshot(player.id),
  }));
}

/**
 * Get 5 starting players for a team
 * @param {string} tricode - Team tricode
 * @returns {Array} Array of 5 player objects
 */
export function getTeamLineup(tricode) {
  const players = getTeamPlayers(tricode);
  
  // If we have enough players, return first 5
  if (players.length >= 5) {
    return players.slice(0, 5);
  }
  
  // Fill with placeholders if needed
  const lineup = [...players];
  const positions = ["G", "G", "F", "F", "C"];
  
  while (lineup.length < 5) {
    const idx = lineup.length;
    lineup.push({
      id: null,
      name: `Player ${idx + 1}`,
      position: positions[idx],
      number: String((idx + 1) * 10),
      headshot: NBA_CDN.fallbackHeadshot,
    });
  }
  
  return lineup;
}

/**
 * Hook to get player lineups for two teams
 * @param {Object} homeTeam - Home team object with tricode
 * @param {Object} awayTeam - Away team object with tricode
 * @returns {Object} { homePlayers, awayPlayers, homeHeadshots, awayHeadshots, homeJerseys, awayJerseys, homeLogo, awayLogo, loading }
 */
export function useNBAPlayers(homeTeam, awayTeam) {
  const [loading, setLoading] = useState(true);

  // Get team tricodes from team objects
  const homeTricode = useMemo(() => {
    if (!homeTeam) return "LAL";
    if (homeTeam.tricode) return homeTeam.tricode;
    const found = findTeamByName(homeTeam.name || homeTeam.city);
    return found?.tricode || "LAL";
  }, [homeTeam]);

  const awayTricode = useMemo(() => {
    if (!awayTeam) return "BOS";
    if (awayTeam.tricode) return awayTeam.tricode;
    const found = findTeamByName(awayTeam.name || awayTeam.city);
    return found?.tricode || "BOS";
  }, [awayTeam]);

  // Get lineups
  const homePlayers = useMemo(() => getTeamLineup(homeTricode), [homeTricode]);
  const awayPlayers = useMemo(() => getTeamLineup(awayTricode), [awayTricode]);

  // Extract headshot URLs and jersey numbers
  const homeHeadshots = useMemo(
    () => homePlayers.map((p) => p.headshot),
    [homePlayers]
  );
  const awayHeadshots = useMemo(
    () => awayPlayers.map((p) => p.headshot),
    [awayPlayers]
  );
  const homeJerseys = useMemo(
    () => homePlayers.map((p) => p.number),
    [homePlayers]
  );
  const awayJerseys = useMemo(
    () => awayPlayers.map((p) => p.number),
    [awayPlayers]
  );

  // Get team logos by ID
  const homeLogo = useMemo(() => getTeamLogo(homeTricode), [homeTricode]);
  const awayLogo = useMemo(() => getTeamLogo(awayTricode), [awayTricode]);

  useEffect(() => {
    setLoading(false);
  }, [homeTricode, awayTricode]);

  return {
    homePlayers,
    awayPlayers,
    homeHeadshots,
    awayHeadshots,
    homeJerseys,
    awayJerseys,
    homeLogo,
    awayLogo,
    loading,
  };
}

/**
 * Find player by name across all teams
 * @param {string} name - Player name (e.g., "LeBron James")
 * @returns {Object|null} Player info or null
 */
export function findPlayerByName(name) {
  const normalized = name.toLowerCase().trim();
  
  for (const [tricode, players] of Object.entries(NBA_PLAYERS)) {
    for (const player of players) {
      if (player.name.toLowerCase() === normalized) {
        return {
          ...player,
          team: tricode,
          headshot: getPlayerHeadshot(player.id),
        };
      }
    }
  }
  
  return null;
}

/**
 * Get all players for a given team ID
 * @param {number} teamId - NBA team ID
 * @returns {Array} Array of player objects
 */
export function getPlayersByTeamId(teamId) {
  // Find tricode by team ID
  for (const [tricode, team] of Object.entries(nbaTeams)) {
    if (team.id === teamId) {
      return getTeamPlayers(tricode);
    }
  }
  return [];
}

export default useNBAPlayers;
