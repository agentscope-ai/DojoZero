/**
 * Data source hook for fetching data from Arena API.
 *
 * Usage:
 *   VITE_API_URL=http://localhost:3001 npm run dev
 */

import { useState, useEffect, useCallback, createContext, useContext } from "react";

// API URL from environment variable
const API_URL = import.meta.env.VITE_API_URL || "http://localhost:3001";
const STATS_REFRESH_MS = Number(import.meta.env.VITE_STATS_REFRESH_MS || 10000);
const LANDING_REFRESH_MS = Number(import.meta.env.VITE_LANDING_REFRESH_MS || 10000);
const ACTIONS_REFRESH_MS = Number(import.meta.env.VITE_ACTIONS_REFRESH_MS || 10000);

// Data source context
const DataSourceContext = createContext(null);

export function useDataSource() {
  const context = useContext(DataSourceContext);
  if (!context) {
    throw new Error("useDataSource must be used within a DataSourceProvider");
  }
  return context;
}

/**
 * Provider component that manages data fetching from Arena API.
 */
export function DataSourceProvider({ children }) {
  const apiUrl = API_URL;
  const [isLoading, setIsLoading] = useState(false);
  const [error, setError] = useState(null);

  // Landing page data state - all initialized with empty data
  // Note: API returns snake_case keys (games_played, live_now, wagered_today)
  const [stats, setStats] = useState({ games_played: 0, live_now: 0, wagered_today: 0 });
  const [liveGames, setLiveGames] = useState([]);
  const [allGames, setAllGames] = useState([]);
  const [agentActions, setAgentActions] = useState([]);
  const [leaderboard, setLeaderboard] = useState([]);

  // Fetch data from API
  const fetchFromApi = useCallback(
    async (endpoint) => {
      const response = await fetch(`${apiUrl}${endpoint}`, {
        cache: "no-store",
      });
      if (!response.ok) {
        throw new Error(`API error: ${response.status} ${response.statusText}`);
      }
      return response.json();
    },
    [apiUrl]
  );

  // Fetch landing page data
  const fetchLandingData = useCallback(async () => {
    setIsLoading(true);
    setError(null);

    try {
      const data = await fetchFromApi("/api/landing");
      setStats(data.stats || { games_played: 0, live_now: 0, wagered_today: 0 });
      setLiveGames(data.live_games || []);
      setAllGames(data.all_games || []);
      setAgentActions(data.live_agent_actions || []);
    } catch (err) {
      console.error("Failed to fetch landing data:", err);
      setError(err.message);
      // Keep empty state on error
      setStats({ games_played: 0, live_now: 0, wagered_today: 0 });
      setLiveGames([]);
      setAllGames([]);
      setAgentActions([]);
    } finally {
      setIsLoading(false);
    }
  }, [fetchFromApi]);

  // Fetch leaderboard data
  const fetchLeaderboard = useCallback(async () => {
    setIsLoading(true);
    setError(null);

    try {
      const data = await fetchFromApi("/api/leaderboard");
      setLeaderboard(data.leaderboard || []);
    } catch (err) {
      console.error("Failed to fetch leaderboard:", err);
      setError(err.message);
      setLeaderboard([]);
    } finally {
      setIsLoading(false);
    }
  }, [fetchFromApi]);

  // Fetch stats only (for real-time updates)
  const fetchStats = useCallback(async () => {
    try {
      const data = await fetchFromApi("/api/stats");
      setStats(data);
    } catch (err) {
      console.error("Failed to fetch stats:", err);
    }
  }, [fetchFromApi]);

  // Fetch agent actions only (for live ticker)
  const fetchAgentActions = useCallback(async () => {
    try {
      const data = await fetchFromApi("/api/agent-actions");
      setAgentActions(data.actions || []);
    } catch (err) {
      console.error("Failed to fetch agent actions:", err);
    }
  }, [fetchFromApi]);

  // Initial data fetch on mount
  useEffect(() => {
    fetchLandingData();
    fetchLeaderboard();
  }, [fetchLandingData, fetchLeaderboard]);

  // Periodic refresh for core data
  useEffect(() => {
    // Refresh stats and landing page data at configurable intervals.
    const statsInterval = setInterval(fetchStats, STATS_REFRESH_MS);
    const fullRefresh = setInterval(fetchLandingData, LANDING_REFRESH_MS);

    return () => {
      clearInterval(statsInterval);
      clearInterval(fullRefresh);
    };
  }, [fetchStats, fetchLandingData]);

  // Conditional refresh for agent actions
  useEffect(() => {
    // Only refresh agent actions if there are live games
    let actionsInterval = null;
    if (liveGames.length > 0) {
      actionsInterval = setInterval(fetchAgentActions, ACTIONS_REFRESH_MS);
    }

    return () => {
      if (actionsInterval) {
        clearInterval(actionsInterval);
      }
    };
  }, [liveGames.length, fetchAgentActions]);

  const value = {
    // API configuration
    apiUrl,
    isLoading,
    error,

    // Data
    stats,
    liveGames,
    allGames,
    agentActions,
    leaderboard,

    // Actions
    refresh: fetchLandingData,
    errorMessage: error ? `Error loading data: ${error}` : null,
  };

  return (
    <DataSourceContext.Provider value={value}>
      {children}
    </DataSourceContext.Provider>
  );
}

export default useDataSource;
