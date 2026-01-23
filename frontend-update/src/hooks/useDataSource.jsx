/**
 * Data source hook for switching between mock data and Arena API.
 *
 * Usage:
 *   npm run dev          # Uses mock data (default)
 *   npm run dev:live     # Uses Arena API at http://localhost:3001
 *
 * Or set environment variable:
 *   VITE_USE_MOCK_DATA=false npm run dev
 *   VITE_API_URL=http://localhost:3001 npm run dev
 */

import { useState, useEffect, useCallback, createContext, useContext } from "react";
import {
  liveGames as mockLiveGames,
  allGames as mockAllGames,
  liveAgentActions as mockAgentActions,
  leaderboardData as mockLeaderboard,
  stats as mockStats,
} from "../data/mockData";

// Check environment variables set by Vite
const USE_MOCK_DATA = import.meta.env.VITE_USE_MOCK_DATA !== "false";
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
 * Provider component that manages data source (controlled via CLI env vars).
 */
export function DataSourceProvider({ children }) {
  // Data source is fixed at build/start time via environment variables
  const useMockData = USE_MOCK_DATA;
  const apiUrl = API_URL;
  const [isLoading, setIsLoading] = useState(false);
  const [error, setError] = useState(null);

  // Landing page data state
  const [stats, setStats] = useState(mockStats);
  const [liveGames, setLiveGames] = useState(mockLiveGames);
  const [allGames, setAllGames] = useState(mockAllGames);
  const [agentActions, setAgentActions] = useState(mockAgentActions);
  const [leaderboard, setLeaderboard] = useState(mockLeaderboard);

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
    if (useMockData) {
      // Use mock data directly
      setStats(mockStats);
      setLiveGames(mockLiveGames);
      setAllGames(mockAllGames);
      setAgentActions(mockAgentActions);
      return;
    }

    setIsLoading(true);
    setError(null);

    try {
      const data = await fetchFromApi("/api/landing");
      setStats(data.stats || mockStats);
      setLiveGames(data.liveGames || []);
      setAllGames(data.allGames || []);
      setAgentActions(data.liveAgentActions || []);
    } catch (err) {
      console.error("Failed to fetch landing data:", err);
      setError(err.message);
      // Fallback to mock data on error
      setStats(mockStats);
      setLiveGames(mockLiveGames);
      setAllGames(mockAllGames);
      setAgentActions(mockAgentActions);
    } finally {
      setIsLoading(false);
    }
  }, [useMockData, fetchFromApi]);

  // Fetch leaderboard data
  const fetchLeaderboard = useCallback(async () => {
    if (useMockData) {
      setLeaderboard(mockLeaderboard);
      return;
    }

    setIsLoading(true);
    setError(null);

    try {
      const data = await fetchFromApi("/api/leaderboard");
      setLeaderboard(data.leaderboard || []);
    } catch (err) {
      console.error("Failed to fetch leaderboard:", err);
      setError(err.message);
      setLeaderboard(mockLeaderboard);
    } finally {
      setIsLoading(false);
    }
  }, [useMockData, fetchFromApi]);

  // Fetch stats only (for real-time updates)
  const fetchStats = useCallback(async () => {
    if (useMockData) return;

    try {
      const data = await fetchFromApi("/api/stats");
      setStats(data);
    } catch (err) {
      console.error("Failed to fetch stats:", err);
    }
  }, [useMockData, fetchFromApi]);

  // Fetch agent actions only (for live ticker)
  const fetchAgentActions = useCallback(async () => {
    if (useMockData) return;

    try {
      const data = await fetchFromApi("/api/agent-actions");
      setAgentActions(data.actions || []);
    } catch (err) {
      console.error("Failed to fetch agent actions:", err);
    }
  }, [useMockData, fetchFromApi]);

  // Initial data fetch on mount
  useEffect(() => {
    fetchLandingData();
    fetchLeaderboard();
  }, [useMockData, fetchLandingData, fetchLeaderboard]);

  // Periodic refresh for live data (only when using API)
  useEffect(() => {
    if (useMockData) return;

    // Refresh stats every 5 seconds
    const statsInterval = setInterval(fetchStats, 5000);
    // Refresh agent actions every 2 seconds
    const actionsInterval = setInterval(fetchAgentActions, 2000);
    // Refresh all data every 30 seconds
    const fullRefresh = setInterval(fetchLandingData, 30000);

    return () => {
      clearInterval(statsInterval);
      clearInterval(actionsInterval);
      clearInterval(fullRefresh);
    };
  }, [useMockData, fetchStats, fetchAgentActions, fetchLandingData]);

  const value = {
    // Data source state (read-only, controlled via CLI env vars)
    useMockData,
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
    // UI helper: show error message if not in mock mode
    errorMessage: error && !useMockData ? `Error loading data: ${error}` : null,
  };

  return (
    <DataSourceContext.Provider value={value}>
      {children}
    </DataSourceContext.Provider>
  );
}

export default useDataSource;