import { useState, useEffect, useCallback, useRef } from "react";
import { WS_BASE_URL, API_BASE_URL } from "../constants";

/**
 * WebSocket message types from the server
 */
const WSMessageType = {
  SNAPSHOT: "snapshot",
  SPAN: "span",
  TRIAL_ENDED: "trial_ended",
  HEARTBEAT: "heartbeat",
};

/**
 * Process a list of typed items (from snapshot or accumulated spans) to
 * extract all derived state.
 *
 * Each item has the shape: { category: string, data: {...} }
 *
 * Categories:
 *   - "actor_registration": actor metadata (agents, datastreams)
 *   - "agent_message": agent messages grouped into conversations
 *   - "trial_lifecycle": trial phase (started/stopped/terminated)
 *   - "event": DataStream events for EventTicker / EventReplay
 *   - "broker_state": broker state updates
 *   - "betting_result": betting result spans
 */
function processTypedItems(items) {
  const actors = {};
  const conversations = {};
  const events = [];
  let phase = "unknown";
  let latestStartTime = 0;
  let latestStopTime = 0;
  let hasStarted = false;
  let hasStopped = false;
  const trialMetadata = {};

  for (const item of items) {
    const { category, data } = item;

    switch (category) {
      case "actor_registration": {
        const actorId = data.actor_id;
        if (actorId) {
          actors[actorId] = {
            id: actorId,
            type: data.actor_type || "",
            name: data.name || actorId,
            model: data.model || "",
            modelProvider: data.model_provider || "",
            systemPrompt: data.system_prompt || "",
            tools: data.tools || [],
            sourceType: data.source_type || "",
          };
        }
        break;
      }

      case "agent_message": {
        const actorId = data.actor_id;
        const streamId = data.stream_id || "default";
        if (actorId) {
          if (!conversations[actorId]) conversations[actorId] = {};
          if (!conversations[actorId][streamId])
            conversations[actorId][streamId] = [];
          conversations[actorId][streamId].push({
            role: data.role || "",
            content: data.content || "",
            name: data.name || "",
            toolCalls: data.tool_calls || [],
            toolCallId: data.tool_call_id || "",
            messageId: data.message_id || "",
            timestamp: data.start_time
              ? new Date(data.start_time / 1000).toISOString()
              : null,
            sequence: data.sequence || 0,
          });
        }
        break;
      }

      case "trial_lifecycle": {
        const trialPhase = data.phase || "";
        const startTime = data.start_time || 0;

        if (trialPhase === "started") {
          hasStarted = true;
          if (startTime > latestStartTime) latestStartTime = startTime;
          // Extract metadata fields
          for (const key of [
            "home_team_tricode",
            "away_team_tricode",
            "home_team_name",
            "away_team_name",
            "league",
            "game_date",
            "sport_type",
            "espn_game_id",
          ]) {
            if (data[key]) trialMetadata[key] = data[key];
          }
          // Include extra_metadata
          if (data.extra_metadata) {
            Object.assign(trialMetadata, data.extra_metadata);
          }
        } else if (
          trialPhase === "stopped" ||
          trialPhase === "terminated"
        ) {
          hasStopped = true;
          if (startTime > latestStopTime) latestStopTime = startTime;
        }
        break;
      }

      case "event": {
        // Data events come pre-parsed from the backend.
        // Strip "event." prefix from event_type for UI compatibility.
        const rawType = data.event_type || "";
        const event = {
          ...data,
          event_type: rawType.replace(/^event\./, ""),
        };
        events.push(event);
        break;
      }

      // broker_state and betting_result are available but not currently
      // consumed by UI components — just skip them.
      default:
        break;
    }
  }

  // Determine phase
  if (hasStopped && latestStopTime >= latestStartTime) {
    phase = "stopped";
  } else if (hasStarted && !hasStopped) {
    phase = "running";
  } else if (hasStopped) {
    phase = "stopped";
  } else if (items.length > 0) {
    phase = "running";
  }

  // Extract game metadata from events (e.g. game_initialize, game_update)
  const gameMetadata = {};
  for (const event of events) {
    if (event.event_type === "game_initialize") {
      gameMetadata.home_team = event.home_team || "";
      gameMetadata.away_team = event.away_team || "";
      gameMetadata.game_id = event.game_id || "";
    }
    if (
      event.event_type === "game_update" ||
      event.event_type === "nba_game_update"
    ) {
      const home = event.home_team;
      const away = event.away_team;
      if (home && typeof home === "object") {
        gameMetadata.home_team_tricode = home.teamTricode || home.team_tricode || "";
        gameMetadata.home_team_name = home.teamName || home.team_name || "";
      }
      if (away && typeof away === "object") {
        gameMetadata.away_team_tricode = away.teamTricode || away.team_tricode || "";
        gameMetadata.away_team_name = away.teamName || away.team_name || "";
      }
    }
  }

  const metadata = { ...trialMetadata, ...gameMetadata };
  const agentsList = Object.values(actors).filter((a) => a.type === "agent");

  return { actors, agents: agentsList, conversations, events, metadata, phase };
}

/**
 * Hook for managing WebSocket connection to trial stream.
 * Handles live streaming for active trials and falls back to REST for completed trials.
 *
 * Uses the typed item protocol where ALL data flows as typed items:
 *   { category: "<category>", data: {...typed fields...} }
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

  // Data state
  const [metadata, setMetadata] = useState({});
  const [phase, setPhase] = useState("");
  const [agents, setAgents] = useState([]);
  const [events, setEvents] = useState([]);
  const [spans, setSpans] = useState([]); // Now stores typed items
  const [agentStates, setAgentStates] = useState({});
  const [actors, setActors] = useState({});

  // WebSocket ref
  const wsRef = useRef(null);
  const reconnectTimeoutRef = useRef(null);
  const reconnectAttempts = useRef(0);
  const maxReconnectAttempts = 5;
  const isMountedRef = useRef(true);
  const isCleaningUpRef = useRef(false);

  // Process snapshot message (contains all typed items)
  const handleSnapshot = useCallback((data) => {
    const items = data.items || [];
    setSpans(items);

    const processed = processTypedItems(items);
    setActors(processed.actors);
    setAgents(processed.agents);
    setAgentStates(processed.conversations);
    setEvents(processed.events);
    setMetadata(processed.metadata);
    setPhase(processed.phase);
    if (processed.phase === "stopped") {
      setTrialEnded(true);
    }

    setLoading(false);
    setConnected(true);
    reconnectAttempts.current = 0;
  }, []);

  // Process span message — a single typed item {category, data}
  const handleSpan = useCallback((message) => {
    // The message itself contains {type, trial_id, timestamp, category, data}
    const item = { category: message.category, data: message.data };

    setSpans((prev) => {
      const newItems = [...prev, item];

      // Re-process all items to update derived state
      const processed = processTypedItems(newItems);
      setActors(processed.actors);
      setAgents(processed.agents);
      setAgentStates(processed.conversations);
      setEvents(processed.events);
      setMetadata((currentMeta) => ({ ...currentMeta, ...processed.metadata }));
      setPhase(processed.phase);
      if (processed.phase === "stopped") {
        setTrialEnded(true);
      }

      return newItems;
    });
  }, []);

  // Process trial_ended message (legacy, now handled via spans)
  const handleTrialEnded = useCallback(() => {
    setTrialEnded(true);
    setPhase("stopped");
  }, []);

  // Connect to WebSocket for live trials
  const connectWebSocket = useCallback(() => {
    if (!trialId || !isLive || !isMountedRef.current || isCleaningUpRef.current)
      return;

    const wsUrl = `${WS_BASE_URL}/ws/trials/${trialId}/stream`;
    const ws = new WebSocket(wsUrl);
    wsRef.current = ws;

    ws.onopen = () => {
      if (isMountedRef.current) {
        console.log(`WebSocket connected to trial: ${trialId}`);
        setError(null);
      }
    };

    ws.onmessage = (event) => {
      if (!isMountedRef.current) return;

      const message = JSON.parse(event.data);

      switch (message.type) {
        case WSMessageType.SNAPSHOT:
          handleSnapshot(message.data);
          break;
        case WSMessageType.SPAN:
          // Span messages now have {type, trial_id, timestamp, category, data}
          handleSpan(message);
          break;
        case WSMessageType.TRIAL_ENDED:
          handleTrialEnded();
          break;
        case WSMessageType.HEARTBEAT:
          break;
        default:
          console.warn("Unknown message type:", message.type);
      }
    };

    ws.onerror = (err) => {
      if (!isCleaningUpRef.current && isMountedRef.current) {
        console.error("WebSocket error:", err);
        setError("Connection error");
      }
    };

    ws.onclose = (event) => {
      if (isCleaningUpRef.current || !isMountedRef.current) return;

      console.log("WebSocket closed:", event.code, event.reason);
      setConnected(false);

      if (!trialEnded && reconnectAttempts.current < maxReconnectAttempts) {
        const delay = Math.min(
          1000 * Math.pow(2, reconnectAttempts.current),
          30000
        );
        reconnectAttempts.current += 1;
        console.log(
          `Reconnecting in ${delay}ms (attempt ${reconnectAttempts.current})`
        );

        reconnectTimeoutRef.current = setTimeout(() => {
          connectWebSocket();
        }, delay);
      }
    };
  }, [
    trialId,
    isLive,
    handleSnapshot,
    handleSpan,
    handleTrialEnded,
    trialEnded,
  ]);

  // Fetch trace data for completed trials (or any trial via REST)
  const fetchTraceData = useCallback(async () => {
    if (!trialId) return;

    setLoading(true);
    try {
      const response = await fetch(`${API_BASE_URL}/trials/${trialId}`);
      if (!response.ok) {
        throw new Error(`Failed to fetch trace: ${response.status}`);
      }
      const data = await response.json();

      const items = data.items || [];
      setSpans(items);

      const processed = processTypedItems(items);
      setActors(processed.actors);
      setAgents(processed.agents);
      setAgentStates(processed.conversations);
      setEvents(processed.events);
      setMetadata(processed.metadata);
      setPhase(processed.phase);
      if (processed.phase === "stopped") {
        setTrialEnded(true);
      }
      setConnected(true);
    } catch (err) {
      console.error("Failed to fetch trace data:", err);
      setError(err.message);
    } finally {
      setLoading(false);
    }
  }, [trialId]);

  // Connect/fetch based on trial mode
  useEffect(() => {
    isMountedRef.current = true;
    isCleaningUpRef.current = false;

    if (!trialId) return;

    if (isLive) {
      connectWebSocket();
    } else {
      fetchTraceData();
    }

    return () => {
      isCleaningUpRef.current = true;
      isMountedRef.current = false;

      if (reconnectTimeoutRef.current) {
        clearTimeout(reconnectTimeoutRef.current);
        reconnectTimeoutRef.current = null;
      }

      if (wsRef.current) {
        if (
          wsRef.current.readyState === WebSocket.OPEN ||
          wsRef.current.readyState === WebSocket.CONNECTING
        ) {
          wsRef.current.close();
        }
        wsRef.current = null;
      }
    };
  }, [trialId, isLive, connectWebSocket, fetchTraceData]);

  // Disconnect handler
  const disconnect = useCallback(() => {
    isCleaningUpRef.current = true;

    if (reconnectTimeoutRef.current) {
      clearTimeout(reconnectTimeoutRef.current);
      reconnectTimeoutRef.current = null;
    }

    if (wsRef.current) {
      if (
        wsRef.current.readyState === WebSocket.OPEN ||
        wsRef.current.readyState === WebSocket.CONNECTING
      ) {
        wsRef.current.close();
      }
      wsRef.current = null;
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
    spans, // Now typed items, not raw SpanData
    agentStates,
    actors,

    // Controls
    disconnect,
  };
}

export default useTrialStream;
