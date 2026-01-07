import { useState, useEffect, useCallback, useRef } from "react";
import { WS_BASE_URL, API_BASE_URL } from "../constants";

/**
 * WebSocket message types from the server
 */
const WSMessageType = {
  SNAPSHOT: "snapshot",
  EVENT: "event",
  TRIAL_ENDED: "trial_ended",
  HEARTBEAT: "heartbeat",
};

/**
 * Normalize an event to a consistent format.
 * Events can come in two formats:
 * 1. Live stream: { payload: {...}, metadata: {...}, emitted_at: ... }
 * 2. Checkpoint: { event_type: "...", timestamp: "...", ... } (flat structure)
 *
 * We normalize to the flat structure with event_type at the top level.
 */
function normalizeEvent(event) {
  // If event has payload, it's from live stream - extract and merge
  if (event.payload) {
    return {
      ...event.payload,
      ...event.metadata,
      emitted_at: event.emitted_at,
      stream_id: event.stream_id,
      sequence: event.sequence,
    };
  }
  // Already flat (from checkpoint) - return as-is
  return event;
}

/**
 * Extract game metadata from events.
 * Looks for game_initialize and game_update events to extract team info.
 * This is specific to NBA game events.
 */
function extractGameMetadata(events) {
  const metadata = {};

  for (const event of events) {
    // Extract from game_initialize event
    if (event.event_type === "game_initialize") {
      metadata.home_team = event.home_team || "";
      metadata.away_team = event.away_team || "";
      metadata.game_id = event.game_id || "";
    }
    // Extract team tricodes from game_update event
    if (event.event_type === "game_update") {
      const home = event.home_team;
      const away = event.away_team;
      if (home && typeof home === "object") {
        metadata.home_team_tricode = home.teamTricode || "";
        metadata.home_team_name = home.teamName || "";
      }
      if (away && typeof away === "object") {
        metadata.away_team_tricode = away.teamTricode || "";
        metadata.away_team_name = away.teamName || "";
      }
    }
  }

  return metadata;
}

/**
 * Hook for managing WebSocket connection to trial stream.
 * Handles live streaming for active trials and falls back to REST for completed trials.
 *
 * @param {string} trialId - The trial ID to connect to
 * @param {boolean} isLive - Whether the trial is live (uses WebSocket) or completed (uses REST)
 * @returns {Object} - Stream state and controls
 */
export function useTrialStream(trialId, isLive = true) {
  // Core state
  const [connected, setConnected] = useState(false);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState(null);
  const [trialEnded, setTrialEnded] = useState(false);

  // Data state from snapshot
  const [metadata, setMetadata] = useState({});
  const [phase, setPhase] = useState("");
  const [agents, setAgents] = useState([]);
  const [events, setEvents] = useState([]);
  const [agentStates, setAgentStates] = useState({}); // Agent conversation history

  // WebSocket ref
  const wsRef = useRef(null);
  const reconnectTimeoutRef = useRef(null);
  const reconnectAttempts = useRef(0);
  const maxReconnectAttempts = 5;

  // Process snapshot message
  const handleSnapshot = useCallback((data) => {
    // Normalize all events from snapshot
    const normalizedEvents = (data.recent_events || []).map(normalizeEvent);

    // Extract game metadata from events and merge with trial metadata
    const gameMetadata = extractGameMetadata(normalizedEvents);
    const mergedMetadata = { ...(data.metadata || {}), ...gameMetadata };

    setMetadata(mergedMetadata);
    setPhase(data.phase || "");
    setAgents(data.agents || []);
    setEvents(normalizedEvents);
    setLoading(false);
    setConnected(true);
    reconnectAttempts.current = 0;
  }, []);

  // Process event message - normalize and append new event to list
  const handleEvent = useCallback((eventData) => {
    const normalized = normalizeEvent(eventData);
    setEvents((prev) => [...prev, normalized]);

    // Update metadata if this is a game event with team info
    if (normalized.event_type === "game_initialize" || normalized.event_type === "game_update") {
      const newGameMeta = extractGameMetadata([normalized]);
      setMetadata((prev) => ({ ...prev, ...newGameMeta }));
    }
  }, []);

  // Process trial_ended message
  const handleTrialEnded = useCallback(() => {
    setTrialEnded(true);
    setPhase("stopped");
  }, []);

  // Connect to WebSocket for live trials
  const connectWebSocket = useCallback(() => {
    if (!trialId || !isLive) return;

    const wsUrl = `${WS_BASE_URL}/ws/trials/${trialId}/stream`;

    const ws = new WebSocket(wsUrl);
    wsRef.current = ws;

    ws.onopen = () => {
      console.log(`WebSocket connected to trial: ${trialId}`);
      setError(null);
    };

    ws.onmessage = (event) => {
      const message = JSON.parse(event.data);

      switch (message.type) {
        case WSMessageType.SNAPSHOT:
          handleSnapshot(message.data);
          break;
        case WSMessageType.EVENT:
          handleEvent(message.data);
          break;
        case WSMessageType.TRIAL_ENDED:
          handleTrialEnded();
          break;
        case WSMessageType.HEARTBEAT:
          // Heartbeat received, connection is alive
          break;
        default:
          console.warn("Unknown message type:", message.type);
      }
    };

    ws.onerror = (err) => {
      console.error("WebSocket error:", err);
      setError("Connection error");
    };

    ws.onclose = (event) => {
      console.log("WebSocket closed:", event.code, event.reason);
      setConnected(false);

      // Attempt reconnection if not intentionally closed and trial not ended
      if (!trialEnded && reconnectAttempts.current < maxReconnectAttempts) {
        const delay = Math.min(1000 * Math.pow(2, reconnectAttempts.current), 30000);
        reconnectAttempts.current += 1;
        console.log(`Reconnecting in ${delay}ms (attempt ${reconnectAttempts.current})`);

        reconnectTimeoutRef.current = setTimeout(() => {
          connectWebSocket();
        }, delay);
      }
    };
  }, [trialId, isLive, handleSnapshot, handleEvent, handleTrialEnded, trialEnded]);

  // Fetch replay data for completed trials
  const fetchReplayData = useCallback(async () => {
    if (!trialId) return;

    setLoading(true);
    try {
      const response = await fetch(`${API_BASE_URL}/trials/${trialId}/replay`);
      if (!response.ok) {
        throw new Error(`Failed to fetch replay: ${response.status}`);
      }
      const data = await response.json();

      // Normalize all events from replay response
      const normalizedEvents = (data.events || []).map(normalizeEvent);

      // Extract game metadata from events and merge with trial metadata
      const gameMetadata = extractGameMetadata(normalizedEvents);
      const mergedMetadata = { ...(data.metadata || {}), ...gameMetadata };

      setMetadata(mergedMetadata);
      setPhase(data.phase || "stopped");
      setEvents(normalizedEvents);
      setAgentStates(data.agent_states || {});
      setTrialEnded(true);
      setConnected(true);
    } catch (err) {
      console.error("Failed to fetch replay data:", err);
      setError(err.message);
    } finally {
      setLoading(false);
    }
  }, [trialId]);

  // Connect/fetch based on trial mode
  useEffect(() => {
    if (!trialId) return;

    if (isLive) {
      connectWebSocket();
    } else {
      fetchReplayData();
    }

    return () => {
      // Cleanup
      if (wsRef.current) {
        wsRef.current.close();
        wsRef.current = null;
      }
      if (reconnectTimeoutRef.current) {
        clearTimeout(reconnectTimeoutRef.current);
      }
    };
  }, [trialId, isLive, connectWebSocket, fetchReplayData]);

  // Disconnect handler
  const disconnect = useCallback(() => {
    if (wsRef.current) {
      wsRef.current.close();
      wsRef.current = null;
    }
    if (reconnectTimeoutRef.current) {
      clearTimeout(reconnectTimeoutRef.current);
    }
    setConnected(false);
  }, []);

  return {
    // Connection state
    connected,
    loading,
    error,
    trialEnded,

    // Trial data
    metadata,
    phase,
    agents,
    events,
    agentStates,

    // Controls
    disconnect,
  };
}

export default useTrialStream;

