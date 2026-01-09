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
 * Try to parse a JSON string, return original value if parsing fails.
 */
function tryParseJSON(value) {
  if (typeof value !== "string") return value;
  if (!value.startsWith("{") && !value.startsWith("[")) return value;
  try {
    return JSON.parse(value);
  } catch {
    return value;
  }
}

/**
 * Convert span tags array to a map for easy access.
 * Handles both array format [{key, value}] and object format {key: value}.
 */
function spanTagsToMap(tags) {
  const tagsMap = {};
  if (Array.isArray(tags)) {
    for (const tag of tags) {
      if (tag.key && tag.value !== undefined) {
        tagsMap[tag.key] = tag.value;
      }
    }
  } else if (typeof tags === "object" && tags !== null) {
    Object.assign(tagsMap, tags);
  }
  return tagsMap;
}

/**
 * Check if a span is a registration span (actor metadata).
 */
function isRegistrationSpan(span) {
  return span.operationName && span.operationName.endsWith(".registered");
}

/**
 * Check if a span is an agent message span.
 * Agent spans have operationName starting with "agent." (e.g., agent.response, agent.input)
 */
function isAgentSpan(span) {
  return span.operationName && span.operationName.startsWith("agent.");
}

/**
 * Check if a span is a DataStream event (for EventTicker).
 * DataStream events are NOT registration spans and NOT agent spans.
 */
function isDataStreamEvent(span) {
  return !isRegistrationSpan(span) && !isAgentSpan(span);
}

/**
 * Extract actors (agents/datastreams) from registration spans.
 * Registration spans have operationName like "agent.registered" or "datastream.registered"
 * and contain resource.* tags with actor metadata.
 */
function extractActors(spans) {
  const actors = {};

  for (const span of spans) {
    if (!isRegistrationSpan(span)) continue;

    const tagsMap = spanTagsToMap(span.tags);
    const actorId = tagsMap["dojozero.actor.id"];
    const actorType = tagsMap["dojozero.actor.type"];

    if (!actorId) continue;

    actors[actorId] = {
      id: actorId,
      type: actorType,
      name: tagsMap["resource.name"] || actorId,
      model: tagsMap["resource.model"],
      modelProvider: tagsMap["resource.model_provider"],
      systemPrompt: tagsMap["resource.system_prompt"],
      tools: tryParseJSON(tagsMap["resource.tools"]) || [],
      sourceType: tagsMap["resource.source_type"],
    };
  }

  return actors;
}

/**
 * Group agent message spans by actor and stream for conversation view.
 * Returns: { actorId: { streamId: [messages] } }
 */
function groupConversations(spans) {
  const conversations = {};

  for (const span of spans) {
    // Skip registration spans and non-agent spans
    if (isRegistrationSpan(span)) continue;
    if (!span.operationName || !span.operationName.startsWith("agent.")) continue;

    const tagsMap = spanTagsToMap(span.tags);
    const actorId = tagsMap["dojozero.actor.id"];
    const streamId = tagsMap["event.stream_id"] || "default";

    if (!actorId) continue;

    if (!conversations[actorId]) {
      conversations[actorId] = {};
    }
    if (!conversations[actorId][streamId]) {
      conversations[actorId][streamId] = [];
    }

    conversations[actorId][streamId].push({
      spanId: span.spanID,
      role: tagsMap["event.role"],
      content: tagsMap["event.content"],
      name: tagsMap["event.name"],
      toolCalls: tryParseJSON(tagsMap["event.tool_calls"]),
      toolCallId: tagsMap["event.tool_call_id"],
      messageId: tagsMap["event.message_id"],
      timestamp: span.startTime ? new Date(span.startTime / 1000).toISOString() : null,
      sequence: tagsMap["dojozero.event.sequence"],
    });
  }

  return conversations;
}

/**
 * Convert a span to event format for UI compatibility.
 * Spans have format: { operationName, startTime, tags: [{key, value}], ... }
 * We convert to flat event format: { event_type, timestamp, ... }
 */
function spanToEvent(span) {
  const tagsMap = spanTagsToMap(span.tags);

  // Convert microsecond timestamp to ISO string
  const timestamp = span.startTime
    ? new Date(span.startTime / 1000).toISOString()
    : null;

  // Build event object
  const event = {
    event_type: span.operationName || "unknown",
    timestamp,
    span_id: span.spanID,
    trace_id: span.traceID,
    actor_id: tagsMap["dojozero.actor.id"],
    actor_type: tagsMap["dojozero.actor.type"],
    sequence: tagsMap["dojozero.event.sequence"],
  };

  // Extract resource.* tags (for registration spans)
  for (const [key, value] of Object.entries(tagsMap)) {
    if (key.startsWith("resource.")) {
      const fieldName = key.slice(9); // Remove "resource." prefix
      event[fieldName] = tryParseJSON(value);
    }
  }

  // Extract business data from tags (event.* prefix)
  for (const [key, value] of Object.entries(tagsMap)) {
    if (key.startsWith("event.")) {
      const fieldName = key.slice(6); // Remove "event." prefix
      event[fieldName] = tryParseJSON(value);
    }
  }

  return event;
}

/**
 * Extract game metadata from events.
 * Looks for game_initialize and game_update events to extract team info.
 */
function extractGameMetadata(events) {
  const metadata = {};

  for (const event of events) {
    if (event.event_type === "game_initialize") {
      metadata.home_team = event.home_team || "";
      metadata.away_team = event.away_team || "";
      metadata.game_id = event.game_id || "";
    }
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
 * Process spans to extract all derived state.
 * This is the central function that processes the unified span protocol.
 *
 * Separation of concerns:
 * - events: Only DataStream events (game_update, odds_update, etc.) for EventTicker
 * - conversations: Agent messages grouped by actor/stream for AgentPanel
 * - agents: Agent metadata from registration spans
 */
function processSpans(rawSpans) {
  // Extract actors from registration spans
  const actors = extractActors(rawSpans);
  const agentsList = Object.values(actors).filter((a) => a.type === "agent");

  // Group conversations for agent panel (agent messages only)
  const conversations = groupConversations(rawSpans);

  // Convert ONLY DataStream events for EventTicker timeline
  // Filter out: registration spans, agent spans
  const dataStreamSpans = rawSpans.filter(isDataStreamEvent);
  const events = dataStreamSpans.map(spanToEvent);

  // Extract game metadata from DataStream events
  const gameMetadata = extractGameMetadata(events);

  return {
    actors,
    agents: agentsList,
    conversations,
    events,
    metadata: gameMetadata,
  };
}

/**
 * Hook for managing WebSocket connection to trial stream.
 * Handles live streaming for active trials and falls back to REST for completed trials.
 *
 * Uses the unified span protocol where ALL data flows through spans:
 * - Resource Spans (*.registered): Actor metadata
 * - Event Spans: Runtime events with business data
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
  const [spans, setSpans] = useState([]);
  const [agentStates, setAgentStates] = useState({});
  const [actors, setActors] = useState({});

  // WebSocket ref
  const wsRef = useRef(null);
  const reconnectTimeoutRef = useRef(null);
  const reconnectAttempts = useRef(0);
  const maxReconnectAttempts = 5;

  // Process snapshot message (contains all spans)
  const handleSnapshot = useCallback((data) => {
    const rawSpans = data.spans || [];
    setSpans(rawSpans);

    // Process spans using unified protocol
    const processed = processSpans(rawSpans);
    setActors(processed.actors);
    setAgents(processed.agents);
    setAgentStates(processed.conversations);
    setEvents(processed.events);
    setMetadata(processed.metadata);

    setLoading(false);
    setConnected(true);
    reconnectAttempts.current = 0;
  }, []);

  // Process span message - convert and append new span to list
  const handleSpan = useCallback((spanData) => {
    setSpans((prev) => {
      const newSpans = [...prev, spanData];

      // Re-process all spans to update derived state
      const processed = processSpans(newSpans);
      setActors(processed.actors);
      setAgents(processed.agents);
      setAgentStates(processed.conversations);
      setEvents(processed.events);
      setMetadata((currentMeta) => ({ ...currentMeta, ...processed.metadata }));

      return newSpans;
    });
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
        case WSMessageType.SPAN:
          handleSpan(message.data);
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
  }, [trialId, isLive, handleSnapshot, handleSpan, handleTrialEnded, trialEnded]);

  // Fetch trace data for completed trials
  const fetchTraceData = useCallback(async () => {
    if (!trialId) return;

    setLoading(true);
    try {
      const response = await fetch(`${API_BASE_URL}/traces/${trialId}`);
      if (!response.ok) {
        throw new Error(`Failed to fetch trace: ${response.status}`);
      }
      const data = await response.json();

      const rawSpans = data.spans || [];
      setSpans(rawSpans);

      // Process spans using unified protocol
      const processed = processSpans(rawSpans);
      setActors(processed.actors);
      setAgents(processed.agents);
      setAgentStates(processed.conversations);
      setEvents(processed.events);
      setMetadata(processed.metadata);

      setPhase("stopped");
      setTrialEnded(true);
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
    if (!trialId) return;

    if (isLive) {
      connectWebSocket();
    } else {
      fetchTraceData();
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
  }, [trialId, isLive, connectWebSocket, fetchTraceData]);

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
    spans,
    agentStates,  // Now contains grouped conversations: { actorId: { streamId: [messages] } }
    actors,       // New: all actors with metadata: { actorId: { id, type, name, model, ... } }

    // Controls
    disconnect,
  };
}

export default useTrialStream;
