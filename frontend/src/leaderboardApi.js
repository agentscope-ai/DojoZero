const ALL_LEAGUES = new Set(["", "All", "all"]);
const SUPPORTED_PERIODS = new Set(["7d", "14d", "30d"]);

export function buildLeaderboardEndpoint(filters = {}) {
  const params = new URLSearchParams();
  const league = filters.league || "All";
  const time = filters.time || "All Time";

  if (!ALL_LEAGUES.has(league)) {
    params.set("league", league);
  }

  if (SUPPORTED_PERIODS.has(time)) {
    params.set("period", time);
  }

  const query = params.toString();
  return query ? `/api/leaderboard?${query}` : "/api/leaderboard";
}
