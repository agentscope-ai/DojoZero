import { useState, useEffect, useCallback, useRef } from "react";

import { arenaWsBase } from "../arenaEnv.js";

/**
 * Hook for connecting to the trial WebSocket stream endpoint.
 * Provides real-time updates for trial events.
 *
 * @param {string} trialId - The trial ID to connect to
 * @returns {Object} WebSocket state and controls
 */
export function useTrialWebSocket(trialId) {
  const [items, setItems] = useState([]);
  const [isConnected, setIsConnected] = useState(false);
  const [isLoading, setIsLoading] = useState(true);
  const [error, setError] = useState(null);
  const [isCompleted, setIsCompleted] = useState(false);

  const wsRef = useRef(null);
  const reconnectTimeoutRef = useRef(null);
  const reconnectAttemptsRef = useRef(0);
  const isCompletedRef = useRef(false);  // Use ref to avoid dependency cycle
  const maxReconnectAttempts = 5;

  const getWebSocketUrl = useCallback(() => {
    const base = arenaWsBase();
    return `${base}/ws/trials/${trialId}/stream`;
  }, [trialId]);

  const connect = useCallback(() => {
    if (!trialId) return;

    // Skip if already connected or connecting
    if (wsRef.current && wsRef.current.readyState !== WebSocket.CLOSED) {
      console.log("[WebSocket] Already connected or connecting, skipping");
      return;
    }

    const wsUrl = getWebSocketUrl();
    console.log(`[WebSocket] Connecting to ${wsUrl}`);

    try {
      const ws = new WebSocket(wsUrl);
      wsRef.current = ws;

      ws.onopen = () => {
        console.log("[WebSocket] Connected");
        setIsConnected(true);
        setError(null);
        reconnectAttemptsRef.current = 0;
      };

      ws.onmessage = (event) => {
        try {
          const message = JSON.parse(event.data);
          handleMessage(message);
        } catch (e) {
          console.error("[WebSocket] Failed to parse message:", e);
        }
      };

      ws.onerror = (event) => {
        console.error("[WebSocket] Error:", event);
        setError("WebSocket connection error");
      };

      ws.onclose = (event) => {
        console.log(`[WebSocket] Closed: code=${event.code}, reason=${event.reason}`);
        setIsConnected(false);
        wsRef.current = null;

        // Attempt reconnect if not completed and not intentionally closed
        // Use ref to check completion to avoid dependency cycle
        if (!isCompletedRef.current && event.code !== 1000) {
          scheduleReconnect();
        }
      };
    } catch (e) {
      console.error("[WebSocket] Failed to create connection:", e);
      setError(`Failed to connect: ${e.message}`);
      setIsLoading(false);
    }
  }, [trialId, getWebSocketUrl]);

  const handleMessage = useCallback((message) => {
    const { type, data, category } = message;

    switch (type) {
      case "snapshot":
        // Initial batch of items
        console.log(`[WebSocket] Received snapshot with ${data?.items?.length || 0} items`);
        setItems(data?.items || []);
        setIsLoading(false);
        break;

      case "span":
        // Single new span - append to items
        console.log(`[WebSocket] Received span: ${category}`);
        setItems((prev) => [...prev, { category, data }]);
        break;

      case "trial_ended":
        // Trial has completed
        console.log("[WebSocket] Trial ended");
        isCompletedRef.current = true;  // Update ref first
        setIsCompleted(true);
        break;

      case "heartbeat":
        // Keep-alive signal - no action needed
        break;

      case "stream_status":
        // Response to control commands
        console.log("[WebSocket] Stream status:", message);
        break;

      default:
        console.log(`[WebSocket] Unknown message type: ${type}`, message);
    }
  }, []);

  const scheduleReconnect = useCallback(() => {
    if (reconnectAttemptsRef.current >= maxReconnectAttempts) {
      console.log("[WebSocket] Max reconnect attempts reached");
      setError("Connection lost. Please refresh the page.");
      return;
    }

    // Exponential backoff: 1s, 2s, 4s, 8s, 16s
    const delay = Math.pow(2, reconnectAttemptsRef.current) * 1000;
    console.log(`[WebSocket] Scheduling reconnect in ${delay}ms (attempt ${reconnectAttemptsRef.current + 1})`);

    reconnectTimeoutRef.current = setTimeout(() => {
      reconnectAttemptsRef.current += 1;
      connect();
    }, delay);
  }, [connect]);

  const sendCommand = useCallback((command) => {
    if (wsRef.current && wsRef.current.readyState === WebSocket.OPEN) {
      wsRef.current.send(JSON.stringify(command));
    } else {
      console.warn("[WebSocket] Cannot send command - not connected");
    }
  }, []);

  const disconnect = useCallback(() => {
    if (reconnectTimeoutRef.current) {
      clearTimeout(reconnectTimeoutRef.current);
      reconnectTimeoutRef.current = null;
    }
    if (wsRef.current) {
      wsRef.current.close(1000, "Component unmounted");
      wsRef.current = null;
    }
  }, []);

  // Connect on mount, disconnect on unmount
  useEffect(() => {
    connect();

    return () => {
      disconnect();
    };
  }, [connect, disconnect]);

  // Reset state when trialId changes
  useEffect(() => {
    setItems([]);
    setIsLoading(true);
    setError(null);
    setIsCompleted(false);
    isCompletedRef.current = false;
    reconnectAttemptsRef.current = 0;
  }, [trialId]);

  return {
    items,
    isConnected,
    isLoading,
    error,
    isCompleted,
    sendCommand,
  };
}
