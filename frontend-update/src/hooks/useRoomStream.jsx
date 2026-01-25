/**
 * useRoomStream - WebSocket hook for game room data streaming
 * 
 * Connects to the trial WebSocket stream and processes spans into usable game data.
 * This is a simplified version adapted for the new room architecture.
 */

import { useState, useEffect, useCallback, useRef } from "react";

const API_URL = import.meta.env.VITE_API_URL || "http://localhost:3001";

// WebSocket message types
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
 * Check span types
 */
function isRegistrationSpan(span) {
  return span.operationName && span.operationName.endsWith(".registered");
}

function isAgentSpan(span) {
  return span.operationName && span.operationName.startsWith("agent.");
}

function isTrialLifecycleSpan(span) {
  return span.operationName && (
    span.operationName === "trial.started" ||
    span.operationName === "trial.stopped" ||
    span.operationName === "trial.terminated"
  );
}

function isDataStreamEvent(span) {
  return !isRegistrationSpan(span) && !isAgentSpan(span) && !isTrialLifecycleSpan(span);
}

/**
 * Determine trial phase from spans.
 */
function determineTrialPhase(spans) {
  let hasStarted = false;
  let hasStopped = false;
  let latestStartTime = 0;
  let latestStopTime = 0;

  for (const span of spans) {
    if (span.operationName === "trial.started") {
      hasStarted = true;
      if (span.startTime > latestStartTime) {
        latestStartTime = span.startTime;
      }
    } else if (span.operationName === "trial.stopped" || span.operationName === "trial.terminated") {
      hasStopped = true;
      if (span.startTime > latestStopTime) {
        latestStopTime = span.startTime;
      }
    }
  }

  if (hasStopped && latestStopTime >= latestStartTime) return "stopped";
  if (hasStarted && !hasStopped) return "running";
  if (hasStopped) return "stopped";
  return spans.length > 0 ? "running" : "unknown";
}

/**
 * Extract actors (agents) from registration spans.
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
 */
function groupConversations(spans) {
  const conversations = {};

  for (const span of spans) {
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
 * Convert a span to event format for UI.
 */
function spanToEvent(span) {
  const tagsMap = spanTagsToMap(span.tags);

  const timestamp = span.startTime
    ? new Date(span.startTime / 1000).toISOString()
    : null;

  const event = {
    event_type: span.operationName || "unknown",
    timestamp,
    span_id: span.spanID,
    trace_id: span.traceID,
    actor_id: tagsMap["dojozero.actor.id"],
    actor_type: tagsMap["dojozero.actor.type"],
    sequence: tagsMap["dojozero.event.sequence"],
  };

  // Extract resource.* tags
  for (const [key, value] of Object.entries(tagsMap)) {
    if (key.startsWith("resource.")) {
      const fieldName = key.slice(9);
      event[fieldName] = tryParseJSON(value);
    }
  }

  // Extract event.* tags
  for (const [key, value] of Object.entries(tagsMap)) {
    if (key.startsWith("event.")) {
      const fieldName = key.slice(6);
      event[fieldName] = tryParseJSON(value);
    }
  }

  return event;
}

/**
 * Extract trial metadata from trial.started span.
 */
function extractTrialMetadata(spans) {
  const metadata = {};

  for (const span of spans) {
    if (span.operationName === "trial.started") {
      const tags = span.tags || [];
      const tagsMap = Array.isArray(tags)
        ? Object.fromEntries(tags.map((t) => [t.key, t.value]))
        : tags;

      for (const [key, value] of Object.entries(tagsMap)) {
        if (key.startsWith("trial.") && key !== "trial.phase") {
          const metadataKey = key.slice(6);
          metadata[metadataKey] = value;
        }
      }
      break;
    }
  }

  return metadata;
}

/**
 * Extract game metadata from events.
 */
function extractGameMetadata(events) {
  const metadata = {};

  for (const event of events) {
    if (event.event_type === "game_initialize") {
      metadata.home_team = event.home_team || "";
      metadata.away_team = event.away_team || "";
      metadata.game_id = event.game_id || "";
      metadata.league = event.league || "NBA";
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
 */
function processSpans(rawSpans) {
  const actors = extractActors(rawSpans);
  const agentsList = Object.values(actors).filter((a) => a.type === "agent");
  const conversations = groupConversations(rawSpans);
  
  const dataStreamSpans = rawSpans.filter(isDataStreamEvent);
  const events = dataStreamSpans.map(spanToEvent);

  const trialMetadata = extractTrialMetadata(rawSpans);
  const gameMetadata = extractGameMetadata(events);
  const metadata = { ...trialMetadata, ...gameMetadata };

  const phase = determineTrialPhase(rawSpans);

  return {
    actors,
    agents: agentsList,
    conversations,
    events,
    metadata,
    phase,
  };
}

/**
 * Main hook for room data streaming
 * 
 * @param {string} trialId - The trial/game ID to connect to
 * @returns {Object} - Stream state and data
 */
export function useRoomStream(trialId) {
  // Connection state
  const [connected, setConnected] = useState(false);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState(null);
  const [trialEnded, setTrialEnded] = useState(false);

  // Data state
  const [metadata, setMetadata] = useState({});
  const [phase, setPhase] = useState("");
  const [agents, setAgents] = useState([]);
  const [events, setEvents] = useState([]);
  const [agentStates, setAgentStates] = useState({});

  // WebSocket ref
  const wsRef = useRef(null);
  const reconnectTimeoutRef = useRef(null);
  const reconnectAttempts = useRef(0);
  const isMountedRef = useRef(true);
  const isCleaningUpRef = useRef(false);

  const maxReconnectAttempts = 5;

  // Process snapshot
  const handleSnapshot = useCallback((data) => {
    const rawSpans = data.spans || [];
    const processed = processSpans(rawSpans);

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

  // Process new span
  const handleSpan = useCallback((spanData) => {
    // Re-process including the new span
    setEvents((prevEvents) => {
      // Convert span to event if it's a data stream event
      if (isDataStreamEvent(spanData)) {
        const newEvent = spanToEvent(spanData);
        // Check for duplicate
        if (prevEvents.some((e) => e.span_id === newEvent.span_id)) {
          return prevEvents;
        }
        return [...prevEvents, newEvent];
      }
      return prevEvents;
    });

    // Handle agent spans
    if (isAgentSpan(spanData)) {
      const tagsMap = spanTagsToMap(spanData.tags);
      const actorId = tagsMap["dojozero.actor.id"];
      const streamId = tagsMap["event.stream_id"] || "default";

      if (actorId && tagsMap["event.role"]) {
        setAgentStates((prev) => {
          const updated = { ...prev };
          if (!updated[actorId]) updated[actorId] = {};
          if (!updated[actorId][streamId]) updated[actorId][streamId] = [];

          const newMessage = {
            spanId: spanData.spanID,
            role: tagsMap["event.role"],
            content: tagsMap["event.content"],
            name: tagsMap["event.name"],
            toolCalls: tryParseJSON(tagsMap["event.tool_calls"]),
            timestamp: spanData.startTime ? new Date(spanData.startTime / 1000).toISOString() : null,
          };

          // Check for duplicate
          if (!updated[actorId][streamId].some((m) => m.spanId === newMessage.spanId)) {
            updated[actorId][streamId] = [...updated[actorId][streamId], newMessage];
          }

          return updated;
        });
      }
    }

    // Handle registration spans
    if (isRegistrationSpan(spanData)) {
      const tagsMap = spanTagsToMap(spanData.tags);
      const actorId = tagsMap["dojozero.actor.id"];
      const actorType = tagsMap["dojozero.actor.type"];

      if (actorId && actorType === "agent") {
        setAgents((prev) => {
          if (prev.some((a) => a.id === actorId)) return prev;
          return [...prev, {
            id: actorId,
            type: actorType,
            name: tagsMap["resource.name"] || actorId,
            model: tagsMap["resource.model"],
            modelProvider: tagsMap["resource.model_provider"],
            tools: tryParseJSON(tagsMap["resource.tools"]) || [],
          }];
        });
      }
    }

    // Handle lifecycle spans
    if (spanData.operationName === "trial.stopped" || spanData.operationName === "trial.terminated") {
      setTrialEnded(true);
      setPhase("stopped");
    }
  }, []);

  // Handle trial ended
  const handleTrialEnded = useCallback(() => {
    setTrialEnded(true);
    setPhase("stopped");
  }, []);

  // Connect WebSocket
  const connectWebSocket = useCallback(() => {
    if (!trialId || !isMountedRef.current || isCleaningUpRef.current) return;

    const wsUrl = `${API_URL}/ws/trials/${trialId}/stream`;
    const ws = new WebSocket(wsUrl);
    wsRef.current = ws;

    ws.onopen = () => {
      if (isMountedRef.current) {
        console.log(`[Room] WebSocket connected: ${trialId}`);
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
          handleSpan(message.data);
          break;
        case WSMessageType.TRIAL_ENDED:
          handleTrialEnded();
          break;
        case WSMessageType.HEARTBEAT:
          // Keep alive
          break;
        default:
          console.warn("[Room] Unknown message type:", message.type);
      }
    };

    ws.onerror = (err) => {
      if (!isCleaningUpRef.current && isMountedRef.current) {
        console.error("[Room] WebSocket error:", err);
        setError("Connection error");
      }
    };

    ws.onclose = (event) => {
      if (isCleaningUpRef.current || !isMountedRef.current) return;

      console.log("[Room] WebSocket closed:", event.code);
      setConnected(false);

      // Reconnect if not ended
      if (!trialEnded && reconnectAttempts.current < maxReconnectAttempts) {
        const delay = Math.min(1000 * Math.pow(2, reconnectAttempts.current), 30000);
        reconnectAttempts.current += 1;
        console.log(`[Room] Reconnecting in ${delay}ms (attempt ${reconnectAttempts.current})`);

        reconnectTimeoutRef.current = setTimeout(() => {
          connectWebSocket();
        }, delay);
      }
    };
  }, [trialId, handleSnapshot, handleSpan, handleTrialEnded, trialEnded]);

  // Fetch REST data (for completed trials)
  const fetchTraceData = useCallback(async () => {
    if (!trialId) return;

    setLoading(true);
    try {
      const response = await fetch(`${API_URL}/api/trials/${trialId}`);
      if (!response.ok) {
        throw new Error(`Failed to fetch trace: ${response.status}`);
      }
      const data = await response.json();

      const rawSpans = data.spans || [];
      const processed = processSpans(rawSpans);

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
      console.error("[Room] Failed to fetch trace:", err);
      setError(err.message);
    } finally {
      setLoading(false);
    }
  }, [trialId]);

  // Connect on mount
  useEffect(() => {
    isMountedRef.current = true;
    isCleaningUpRef.current = false;

    if (!trialId) return;

    // Always try WebSocket first (live connection)
    connectWebSocket();

    return () => {
      isCleaningUpRef.current = true;
      isMountedRef.current = false;

      if (reconnectTimeoutRef.current) {
        clearTimeout(reconnectTimeoutRef.current);
        reconnectTimeoutRef.current = null;
      }

      if (wsRef.current) {
        if (wsRef.current.readyState === WebSocket.OPEN ||
            wsRef.current.readyState === WebSocket.CONNECTING) {
          wsRef.current.close();
        }
        wsRef.current = null;
      }
    };
  }, [trialId, connectWebSocket]);

  // Disconnect handler
  const disconnect = useCallback(() => {
    isCleaningUpRef.current = true;

    if (reconnectTimeoutRef.current) {
      clearTimeout(reconnectTimeoutRef.current);
      reconnectTimeoutRef.current = null;
    }

    if (wsRef.current) {
      if (wsRef.current.readyState === WebSocket.OPEN ||
          wsRef.current.readyState === WebSocket.CONNECTING) {
        wsRef.current.close();
      }
      wsRef.current = null;
    }
    setConnected(false);
  }, []);

  // Retry connection (for error recovery)
  const retry = useCallback(() => {
    setError(null);
    reconnectAttempts.current = 0;
    connectWebSocket();
  }, [connectWebSocket]);

  return {
    // Connection state
    connected,
    loading,
    error,
    trialEnded,
    isLive: connected && !trialEnded,

    // Game data
    metadata,
    phase,
    events,

    // Agent data
    agents,
    agentStates,

    // Controls
    disconnect,
    retry,
  };
}

export default useRoomStream;
