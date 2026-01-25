/**
 * useGamePlayers - Extract dynamic player data from game events
 * 
 * Instead of static NBA_PLAYERS, we use real-time player_stats from events.
 * This hook processes game_update events to extract active players.
 */

import { useMemo, useCallback } from "react";
import { NBA_CDN, nbaTeams } from "../data/nba/teams";

/**
 * Hook to extract dynamic player data from game_update events
 * 
 * @param {Array} events - All events from useRoomStream
 * @param {string} homeTricode - Home team tricode
 * @param {string} awayTricode - Away team tricode
 */
export function useGamePlayers(events, homeTricode, awayTricode) {
  // Find the latest game_update event with player_stats
  const latestGameUpdate = useMemo(() => {
    if (!events || events.length === 0) return null;
    
    // Search from the end to find the most recent game_update
    for (let i = events.length - 1; i >= 0; i--) {
      const event = events[i];
      if (event.event_type === "game_update" && event.player_stats) {
        return event;
      }
    }
    return null;
  }, [events]);

  // Extract team info from game_update
  const teamInfo = useMemo(() => {
    if (!latestGameUpdate) {
      return {
        home: { 
          tricode: homeTricode || "LAL", 
          name: "Home", 
          color: "#552583",
          secondaryColor: "#FDB927"
        },
        away: { 
          tricode: awayTricode || "BOS", 
          name: "Away", 
          color: "#007A33",
          secondaryColor: "#BA9653"
        },
      };
    }

    const homeTeam = latestGameUpdate.home_team || {};
    const awayTeam = latestGameUpdate.away_team || {};

    // Get colors from nbaTeams constant
    const homeTeamData = nbaTeams[homeTeam.teamTricode] || {};
    const awayTeamData = nbaTeams[awayTeam.teamTricode] || {};

    return {
      home: {
        tricode: homeTeam.teamTricode || homeTricode || "LAL",
        name: homeTeam.teamName || "Home",
        city: homeTeam.teamCity || "",
        color: homeTeamData.color || "#552583",
        secondaryColor: homeTeamData.secondaryColor || "#FDB927",
        score: homeTeam.score || 0,
        teamId: homeTeam.teamId,
      },
      away: {
        tricode: awayTeam.teamTricode || awayTricode || "BOS",
        name: awayTeam.teamName || "Away",
        city: awayTeam.teamCity || "",
        color: awayTeamData.color || "#007A33",
        secondaryColor: awayTeamData.secondaryColor || "#BA9653",
        score: awayTeam.score || 0,
        teamId: awayTeam.teamId,
      },
    };
  }, [latestGameUpdate, homeTricode, awayTricode]);

  // Process players from player_stats - get active players (those with minutes played)
  const processPlayers = useCallback((playerStats, team) => {
    if (!playerStats || !Array.isArray(playerStats)) return [];

    // Filter players with actual playing time, sort by points
    const activePlayers = playerStats
      .filter(p => p.statistics?.minutes && p.statistics.minutes !== "")
      .sort((a, b) => (b.statistics?.points || 0) - (a.statistics?.points || 0));

    // Take top 5 players (or all if less than 5)
    const lineup = activePlayers.slice(0, 5);

    return lineup.map((player, index) => ({
      id: player.personId,
      name: player.nameI || `${player.firstName?.charAt(0)}. ${player.familyName}`,
      fullName: `${player.firstName} ${player.familyName}`,
      position: player.position || ["G", "G", "F", "F", "C"][index] || "F",
      number: player.jerseyNum || "",
      headshot: NBA_CDN.playerHeadshot(player.personId),
      team,
      slotIndex: index,
      stats: player.statistics || {},
    }));
  }, []);

  // Get home and away players
  const homePlayers = useMemo(() => {
    if (!latestGameUpdate?.player_stats?.home) return [];
    return processPlayers(latestGameUpdate.player_stats.home, "home");
  }, [latestGameUpdate, processPlayers]);

  const awayPlayers = useMemo(() => {
    if (!latestGameUpdate?.player_stats?.away) return [];
    return processPlayers(latestGameUpdate.player_stats.away, "away");
  }, [latestGameUpdate, processPlayers]);

  // Create a map of all players by personId for quick lookup
  const playerMap = useMemo(() => {
    const map = new Map();
    
    homePlayers.forEach(player => {
      map.set(player.id, { ...player, teamSide: "home" });
    });
    
    awayPlayers.forEach(player => {
      map.set(player.id, { ...player, teamSide: "away" });
    });
    
    return map;
  }, [homePlayers, awayPlayers]);

  // Find player by personId from play_by_play event
  const findPlayer = useCallback((personId, teamTricode, playerName = null) => {
    if (!personId || personId === 0) return null;

    // First try direct lookup in active players
    if (playerMap.has(personId)) {
      return playerMap.get(personId);
    }

    // If not found, try to find by searching all player_stats
    // (for bench players not in top 5)
    const allPlayers = latestGameUpdate?.player_stats;
    if (allPlayers) {
      const searchIn = [...(allPlayers.home || []), ...(allPlayers.away || [])];
      const found = searchIn.find(p => p.personId === personId);
      if (found) {
        const isHome = teamTricode === teamInfo.home.tricode;
        return {
          id: personId,
          name: found.nameI || `${found.firstName?.charAt(0) || ""}. ${found.familyName || ""}`.trim() || playerName || "Player",
          fullName: `${found.firstName || ""} ${found.familyName || ""}`.trim() || "Player",
          position: found.position || "F",
          number: found.jerseyNum || "",
          headshot: NBA_CDN.playerHeadshot(personId),
          team: isHome ? "home" : "away",
          teamSide: isHome ? "home" : "away",
          slotIndex: -1,
          stats: found.statistics || {},
        };
      }
    }

    // Last resort: use player name from event if provided
    const isHome = teamTricode === teamInfo.home.tricode;
    return {
      id: personId,
      name: playerName || "Player",
      fullName: playerName || "Player",
      position: "F",
      number: "",
      headshot: NBA_CDN.playerHeadshot(personId),
      team: isHome ? "home" : "away",
      teamSide: isHome ? "home" : "away",
      slotIndex: -1,
      stats: {},
    };
  }, [playerMap, teamInfo, latestGameUpdate]);

  // Extract headshots for display
  const homeHeadshots = useMemo(() => homePlayers.map(p => p.headshot), [homePlayers]);
  const awayHeadshots = useMemo(() => awayPlayers.map(p => p.headshot), [awayPlayers]);

  // Get team logos
  const homeLogo = useMemo(() => {
    const teamId = teamInfo.home.teamId || nbaTeams[teamInfo.home.tricode]?.id;
    return teamId ? NBA_CDN.teamLogo(teamId) : null;
  }, [teamInfo.home]);

  const awayLogo = useMemo(() => {
    const teamId = teamInfo.away.teamId || nbaTeams[teamInfo.away.tricode]?.id;
    return teamId ? NBA_CDN.teamLogo(teamId) : null;
  }, [teamInfo.away]);

  return {
    // Team info
    teamInfo,
    homeTeam: teamInfo.home,
    awayTeam: teamInfo.away,

    // Player arrays
    homePlayers,
    awayPlayers,
    
    // Player lookup
    playerMap,
    findPlayer,

    // Headshots for display
    homeHeadshots,
    awayHeadshots,

    // Team logos
    homeLogo,
    awayLogo,

    // Data availability
    hasData: !!latestGameUpdate,
    latestGameUpdate,
  };
}

export default useGamePlayers;
