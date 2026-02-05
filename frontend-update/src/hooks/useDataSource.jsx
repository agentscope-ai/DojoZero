/**
 * Data source hook for fetching data from Arena API.
 *
 * Usage:
 *   VITE_API_URL=http://localhost:3001 npm run dev
 */

import { useState, useEffect, useCallback, createContext, useContext } from "react";

// API URL from environment variable
const API_URL = import.meta.env.VITE_API_URL || "http://localhost:3001";

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
  const [stats, setStats] = useState({ gamesPlayed: 0, liveNow: 0, wageredToday: 0 });
  const [liveGames, setLiveGames] = useState([]);
  const [allGames, setAllGames] = useState([]);
  const [agentActions, setAgentActions] = useState([]);
  const [leaderboard, setLeaderboard] = useState([]);

  // Fetch data from API
  const fetchFromApi = useCallback(
    async (endpoint) => {
      const response = await fetch(`${apiUrl}${endpoint}`);
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
      setStats(data.stats || { gamesPlayed: 0, liveNow: 0, wageredToday: 0 });
      setLiveGames(data.liveGames || []);
      setAllGames(data.allGames || []);
      setAgentActions(data.liveAgentActions || []);
    } catch (err) {
      console.error("Failed to fetch landing data:", err);
      setError(err.message);
      // Keep empty state on error
      setStats({ gamesPlayed: 0, liveNow: 0, wageredToday: 0 });
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

  // Periodic refresh for live data
  // Intervals aligned with server-side cache TTLs to reduce load
  useEffect(() => {
    // Refresh stats every 10 seconds (matches server cache TTL)
    const statsInterval = setInterval(fetchStats, 10000);
    // Refresh all data every 60 seconds (matches server cache TTL)
    const fullRefresh = setInterval(fetchLandingData, 60000);

    // Only refresh agent actions if there are live games
    // Use 30-second interval since actions now include completed games
    let actionsInterval = null;
    if (liveGames.length > 0) {
      actionsInterval = setInterval(fetchAgentActions, 30000);
    }

    return () => {
      clearInterval(statsInterval);
      clearInterval(fullRefresh);
      if (actionsInterval) {
        clearInterval(actionsInterval);
      }
    };
  }, [fetchStats, fetchAgentActions, fetchLandingData, liveGames]);

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
