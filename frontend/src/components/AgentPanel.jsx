import { useState, useMemo, useEffect, useRef, useCallback } from "react";
import { motion, AnimatePresence } from "framer-motion";
import { modelProviders, DOJOZERO_CDN } from "../constants";

// Maximum text length before truncation
const MAX_TEXT_LENGTH = 1000;
// Display truncation threshold
const DISPLAY_TRUNCATE_LENGTH = 300;

/**
 * AgentPanel component using unified span protocol.
 * 
 * Props from useTrialStream:
 * - agents: Array of agent metadata from registration spans
 *   Format: [{ id, type, name, model, modelProvider, systemPrompt, tools }]
 * - agentStates: Grouped conversations from message spans
 *   Format: { actorId: { streamId: [{ role, content, name, timestamp, ... }] } }
 * - events: All events for timeline
 * - currentEventIndex: Current playback position
 */
export default function AgentPanel({ agents: agentsList = [], events = [], currentEventIndex = 0, agentStates = {} }) {
  const [visibleBubbles, setVisibleBubbles] = useState([]);
  const [expandedBubbles, setExpandedBubbles] = useState(new Set());
  const bubbleStreamRef = useRef(null);
  const lastEventIndexRef = useRef(-1);

  // Toggle expand/collapse for a bubble
  const toggleExpand = useCallback((bubbleId) => {
    setExpandedBubbles((prev) => {
      const next = new Set(prev);
      if (next.has(bubbleId)) {
        next.delete(bubbleId);
      } else {
        next.add(bubbleId);
      }
      return next;
    });
  }, []);

  // Helper to safely convert any value to a displayable string
  const toDisplayString = (value) => {
    if (value === null || value === undefined) return "";
    if (typeof value === "string") return value;
    if (typeof value === "number" || typeof value === "boolean") return String(value);
    if (typeof value === "object") return JSON.stringify(value);
    return String(value);
  };

  // Process agents from unified protocol
  // agentsList comes from registration spans via useTrialStream.extractActors
  const agents = useMemo(() => {
    return agentsList.map((agent) => {
      const actorId = agent.id || "unknown";
      const modelName = agent.model || agent.name || actorId;
      
      // Try to find provider info by model name or provider
      let providerInfo = modelProviders.default;
      if (agent.modelProvider) {
        // Check if we have a provider-specific config
        const providerKey = agent.modelProvider.toLowerCase();
        if (modelProviders[providerKey]) {
          providerInfo = modelProviders[providerKey];
        }
      }
      // Also try matching by model name
      const modelKey = modelName.toLowerCase().replace(/[.-]/g, "_");
      if (modelProviders[modelKey]) {
        providerInfo = modelProviders[modelKey];
      }
      
      // Count messages for this agent from agentStates
      let messageCount = 0;
      const agentConversations = agentStates[actorId] || {};
      for (const [streamId, messages] of Object.entries(agentConversations)) {
        if (Array.isArray(messages)) {
          messageCount += messages.filter((m) => m.role === "assistant").length;
        }
      }
      
      return {
        id: actorId,
        modelName,
        displayName: agent.name || actorId,
        model: agent.model,
        modelProvider: agent.modelProvider,
        systemPrompt: agent.systemPrompt,
        tools: agent.tools || [],
        providerInfo,
        totalMessages: messageCount,
      };
    });
  }, [agentsList, agentStates]);

  // Extract agent actions from agentStates (grouped conversations)
  // agentStates format: { actorId: { streamId: [messages] } }
  const agentActions = useMemo(() => {
    const actions = [];
    let globalIdx = 0;
    
    for (const [actorId, conversations] of Object.entries(agentStates)) {
      const agent = agents.find((a) => a.id === actorId);
      if (!agent) continue;
      
      for (const [streamId, messages] of Object.entries(conversations)) {
        if (!Array.isArray(messages)) continue;
        
        for (const msg of messages) {
          // Only show assistant messages with content
          if (msg.role !== "assistant") continue;
          
          const text = toDisplayString(msg.content);
          if (!text || text.length === 0) continue;
          
          actions.push({
            id: `${actorId}-${streamId}-${globalIdx}`,
            agentId: actorId,
            text: text.substring(0, MAX_TEXT_LENGTH),
            actionType: msg.toolCalls ? "tool" : "message",
            eventIndex: globalIdx,
            agentColor: agent.providerInfo.color,
            agentName: agent.displayName || agent.providerInfo.name || agent.modelName,
            agentInitials: (agent.displayName || agent.modelName)
              .split(/[-_\s]/)
              .map((w) => w[0]?.toUpperCase())
              .join("")
              .slice(0, 2) || "AI",
            timestamp: msg.timestamp,
            toolCalls: msg.toolCalls,
          });
          globalIdx++;
        }
      }
    }
    
    // Sort by timestamp if available
    actions.sort((a, b) => (a.timestamp || "").localeCompare(b.timestamp || ""));
    
    return actions;
  }, [agentStates, agents]);

  // Update visible bubbles based on current event index
  useEffect(() => {
    // Reset when going back (restart scenario)
    if (currentEventIndex < lastEventIndexRef.current) {
      setVisibleBubbles([]);
      lastEventIndexRef.current = -1;
    }
    
    // Skip if we're at the same index
    if (currentEventIndex === lastEventIndexRef.current) {
      return;
    }

    // Find new actions to show
    const newBubbles = agentActions
      .filter((action) => 
        action.eventIndex > lastEventIndexRef.current && 
        action.eventIndex <= currentEventIndex
      )
      .filter((action) => !visibleBubbles.some((b) => b.id === action.id))
      .map((action) => ({
        ...action,
        showTime: Date.now(),
      }));

    if (newBubbles.length > 0) {
      setVisibleBubbles((prev) => [...prev, ...newBubbles].slice(-15)); // Keep last 15 bubbles
    }

    lastEventIndexRef.current = currentEventIndex;
  }, [currentEventIndex, agentActions, visibleBubbles]);

  // Auto-scroll to bottom when new bubbles appear
  useEffect(() => {
    if (bubbleStreamRef.current) {
      bubbleStreamRef.current.scrollTop = bubbleStreamRef.current.scrollHeight;
    }
  }, [visibleBubbles]);

  const getAgentAvatar = (modelName, color) => {
    const initials = (modelName || "AI")
      .split(/[-_\s]/)
      .map((w) => w[0]?.toUpperCase())
      .join("")
      .slice(0, 2);

    return (
      <div
        style={{
          ...styles.avatar,
          background: color,
          boxShadow: `0 0 20px ${color}55`,
        }}
      >
        <span className="font-display" style={styles.avatarText}>
          {initials}
        </span>
        <div style={styles.avatarRing} />
      </div>
    );
  };


  return (
    <div style={styles.container}>
      {/* Background overlay for readability */}
      <div style={styles.overlay} />

      {/* Agent Cards Row */}
      <div style={styles.agentsRow}>
        {agents.map((agent, index) => (
          <motion.div
            key={agent.id}
            initial={{ opacity: 0, y: 10 }}
            animate={{ opacity: 1, y: 0 }}
            transition={{ delay: index * 0.1 }}
            style={styles.agentCard}
          >
            {/* Avatar section */}
            <div style={styles.avatarSection}>
              {getAgentAvatar(agent.displayName || agent.modelName, agent.providerInfo.color)}
              <div style={styles.providerBadge}>
                {agent.providerInfo.logo && (
                  <img
                    src={agent.providerInfo.logo}
                    alt={agent.providerInfo.provider}
                    style={styles.providerLogo}
                    onError={(e) => (e.target.style.display = "none")}
                  />
                )}
              </div>
            </div>

            {/* Info section */}
            <div style={styles.infoSection}>
              <span className="font-display" style={styles.agentName}>
                {agent.displayName || agent.providerInfo?.name || "Agent"}
              </span>
              <span className="font-tech" style={styles.modelId}>
                {agent.model || agent.modelName}
              </span>
              {agent.modelProvider && (
                <span className="font-tech" style={styles.providerText}>
                  {agent.modelProvider}
                </span>
              )}
            </div>

            {/* Stats section */}
            <div style={styles.statsSection}>
              <div style={styles.statItem}>
                <span className="font-tech" style={styles.statLabel}>
                  MSGS
                </span>
                <span
                  className="font-tech"
                  style={{
                    ...styles.statValue,
                    color: agent.providerInfo.color,
                  }}
                >
                  {agent.totalMessages}
                </span>
              </div>
              {agent.tools && agent.tools.length > 0 && (
                <div style={styles.statItem}>
                  <span className="font-tech" style={styles.statLabel}>
                    TOOLS
                  </span>
                  <span className="font-tech" style={styles.statValue}>
                    {agent.tools.length}
                  </span>
                </div>
              )}
            </div>

            {/* Accent line */}
            <div
              style={{
                ...styles.accentLine,
                background: agent.providerInfo.bgGradient,
              }}
            />
          </motion.div>
        ))}

        {agents.length === 0 && (
          <div style={styles.emptyState}>
            <svg
              width="32"
              height="32"
              viewBox="0 0 24 24"
              fill="none"
              stroke="var(--text-muted)"
              strokeWidth="1.5"
            >
              <path d="M16 21v-2a4 4 0 0 0-4-4H6a4 4 0 0 0-4 4v2" />
              <circle cx="9" cy="7" r="4" />
              <path d="M22 21v-2a4 4 0 0 0-3-3.87" />
              <path d="M16 3.13a4 4 0 0 1 0 7.75" />
            </svg>
            <span className="font-tech" style={styles.emptyText}>
              NO AGENTS
            </span>
          </div>
        )}
      </div>

      {/* Bubble Stream Section */}
      <div style={styles.bubbleStreamSection}>
        <div style={styles.bubbleStreamHeader}>
          <span className="font-tech" style={styles.streamTitle}>
            AGENT ACTIVITY STREAM
          </span>
          <span className="font-tech" style={styles.bubbleCount}>
            {visibleBubbles.length} actions
          </span>
        </div>

        <div ref={bubbleStreamRef} style={styles.bubbleStream}>
          <AnimatePresence>
            {visibleBubbles.map((bubble, index) => {
              const isExpanded = expandedBubbles.has(bubble.id);
              const needsTruncation = bubble.text.length > DISPLAY_TRUNCATE_LENGTH;
              const displayText = isExpanded || !needsTruncation 
                ? bubble.text 
                : bubble.text.substring(0, DISPLAY_TRUNCATE_LENGTH) + "...";

              return (
                <motion.div
                  key={bubble.id}
                  initial={{ opacity: 0, y: 8 }}
                  animate={{ opacity: 1, y: 0 }}
                  exit={{ opacity: 0, y: -8 }}
                  transition={{ duration: 0.2 }}
                  style={{
                    ...styles.bubbleItem,
                    borderLeft: `2px solid ${bubble.agentColor}`,
                  }}
                >
                  {/* Minimal header with agent name */}
                  <div style={styles.bubbleHeader}>
                    <span style={{ ...styles.bubbleAgentName, color: bubble.agentColor }}>
                      {bubble.agentName}
                    </span>
                    {bubble.actionType === "tool" && (
                      <span style={styles.toolBadge}>TOOL</span>
                    )}
                  </div>

                  {/* Message text */}
                  <p style={styles.bubbleText}>{displayText}</p>

                  {/* Expand/Collapse button */}
                  {needsTruncation && (
                    <button
                      onClick={() => toggleExpand(bubble.id)}
                      style={styles.expandButton}
                    >
                      {isExpanded ? "Collapse" : "Expand"}
                    </button>
                  )}
                </motion.div>
              );
            })}
          </AnimatePresence>

          {visibleBubbles.length === 0 && (
            <div style={styles.streamEmpty}>
              <svg
                width="24"
                height="24"
                viewBox="0 0 24 24"
                fill="none"
                stroke="var(--text-muted)"
                strokeWidth="1.5"
              >
                <path d="M21 15a2 2 0 0 1-2 2H7l-4 4V5a2 2 0 0 1 2-2h14a2 2 0 0 1 2 2z" />
              </svg>
              <span className="font-tech" style={styles.streamEmptyText}>
                Play to see agent actions
              </span>
            </div>
          )}
        </div>
      </div>
    </div>
  );
}

const styles = {
  container: {
    flex: 1,
    display: "flex",
    flexDirection: "column",
    overflow: "hidden",
    marginTop: "4px",
    gap: "6px",
    backgroundImage: `url(${DOJOZERO_CDN.agentboard})`,
    backgroundSize: "cover",
    backgroundPosition: "center",
    backgroundRepeat: "no-repeat",
    borderRadius: "8px",
    padding: "8px",
    position: "relative",
  },
  overlay: {
    position: "absolute",
    top: 0,
    left: 0,
    right: 0,
    bottom: 0,
    background: "linear-gradient(180deg, rgba(15, 23, 42, 0.88) 0%, rgba(15, 23, 42, 0.82) 50%, rgba(15, 23, 42, 0.88) 100%)",
    borderRadius: "8px",
    pointerEvents: "none",
    zIndex: 0,
  },
  agentsRow: {
    display: "flex",
    gap: "6px",
    flexShrink: 0,
    flexWrap: "wrap",
    position: "relative",
    zIndex: 1,
  },
  agentCard: {
    flex: "1 1 auto",
    minWidth: "140px",
    display: "flex",
    flexDirection: "column",
    alignItems: "center",
    gap: "4px",
    padding: "8px 6px",
    background: "rgba(30, 41, 59, 0.7)",
    border: "1px solid rgba(255, 255, 255, 0.08)",
    borderRadius: "6px",
    position: "relative",
    overflow: "hidden",
  },
  avatarSection: {
    position: "relative",
    flexShrink: 0,
  },
  avatar: {
    width: "32px",
    height: "32px",
    borderRadius: "50%",
    display: "flex",
    alignItems: "center",
    justifyContent: "center",
    position: "relative",
  },
  avatarText: {
    fontSize: "12px",
    color: "#fff",
    fontWeight: "700",
  },
  avatarRing: {
    position: "absolute",
    inset: "-2px",
    borderRadius: "50%",
    border: "1px solid rgba(255, 255, 255, 0.15)",
  },
  providerBadge: {
    position: "absolute",
    bottom: "-2px",
    right: "-2px",
    width: "16px",
    height: "16px",
    borderRadius: "50%",
    background: "var(--bg-secondary)",
    border: "1px solid var(--bg-tertiary)",
    display: "flex",
    alignItems: "center",
    justifyContent: "center",
    overflow: "hidden",
  },
  providerLogo: {
    width: "11px",
    height: "11px",
    objectFit: "contain",
  },
  infoSection: {
    display: "flex",
    flexDirection: "column",
    alignItems: "center",
    gap: "1px",
  },
  agentName: {
    fontSize: "12px",
    color: "var(--text-primary)",
    letterSpacing: "0.03em",
    textAlign: "center",
  },
  modelId: {
    fontSize: "9px",
    color: "var(--text-muted)",
    letterSpacing: "0.03em",
    textAlign: "center",
    maxWidth: "130px",
    overflow: "hidden",
    textOverflow: "ellipsis",
    whiteSpace: "nowrap",
  },
  providerText: {
    fontSize: "8px",
    color: "var(--text-secondary)",
    letterSpacing: "0.03em",
    textTransform: "uppercase",
  },
  statsSection: {
    display: "flex",
    gap: "12px",
    marginTop: "2px",
  },
  statItem: {
    display: "flex",
    flexDirection: "column",
    alignItems: "center",
    gap: "0px",
  },
  statLabel: {
    fontSize: "8px",
    color: "var(--text-muted)",
    letterSpacing: "0.08em",
  },
  statValue: {
    fontSize: "13px",
    color: "var(--text-primary)",
    fontWeight: "600",
  },
  accentLine: {
    position: "absolute",
    bottom: 0,
    left: 0,
    right: 0,
    height: "2px",
  },
  emptyState: {
    flex: 1,
    display: "flex",
    flexDirection: "column",
    alignItems: "center",
    justifyContent: "center",
    gap: "8px",
    padding: "16px",
    opacity: 0.6,
    position: "relative",
    zIndex: 1,
  },
  emptyText: {
    fontSize: "10px",
    color: "var(--text-muted)",
    letterSpacing: "0.1em",
  },

  // Bubble Stream Styles
  bubbleStreamSection: {
    flex: 1,
    display: "flex",
    flexDirection: "column",
    overflow: "hidden",
    background: "rgba(15, 23, 42, 0.5)",
    border: "1px solid rgba(255, 255, 255, 0.06)",
    borderRadius: "6px",
    position: "relative",
    zIndex: 1,
  },
  bubbleStreamHeader: {
    display: "flex",
    justifyContent: "space-between",
    alignItems: "center",
    padding: "6px 10px",
    borderBottom: "1px solid rgba(255, 255, 255, 0.06)",
    flexShrink: 0,
  },
  streamTitle: {
    fontSize: "9px",
    color: "var(--text-muted)",
    letterSpacing: "0.1em",
  },
  bubbleCount: {
    fontSize: "9px",
    color: "var(--text-secondary)",
    letterSpacing: "0.03em",
  },
  bubbleStream: {
    flex: 1,
    overflowY: "auto",
    padding: "6px",
    display: "flex",
    flexDirection: "column",
    gap: "4px",
  },
  bubbleItem: {
    display: "flex",
    flexDirection: "column",
    gap: "4px",
    padding: "8px 10px",
    background: "rgba(30, 41, 59, 0.4)",
    borderRadius: "4px",
  },
  bubbleHeader: {
    display: "flex",
    alignItems: "center",
    gap: "6px",
  },
  bubbleAgentName: {
    fontSize: "11px",
    fontWeight: "600",
    letterSpacing: "0.02em",
  },
  toolBadge: {
    fontSize: "8px",
    color: "#a78bfa",
    background: "rgba(167, 139, 250, 0.15)",
    padding: "1px 5px",
    borderRadius: "3px",
    letterSpacing: "0.05em",
  },
  bubbleText: {
    margin: 0,
    fontSize: "13px",
    color: "#e2e8f0",
    lineHeight: 1.55,
    wordBreak: "break-word",
    whiteSpace: "pre-wrap",
  },
  expandButton: {
    alignSelf: "flex-start",
    marginTop: "2px",
    padding: "2px 8px",
    fontSize: "10px",
    color: "#94a3b8",
    background: "rgba(148, 163, 184, 0.1)",
    border: "1px solid rgba(148, 163, 184, 0.2)",
    borderRadius: "3px",
    cursor: "pointer",
    transition: "all 0.15s ease",
  },
  streamEmpty: {
    flex: 1,
    display: "flex",
    flexDirection: "column",
    alignItems: "center",
    justifyContent: "center",
    gap: "8px",
    opacity: 0.5,
  },
  streamEmptyText: {
    fontSize: "10px",
    color: "var(--text-muted)",
    letterSpacing: "0.08em",
  },
};
