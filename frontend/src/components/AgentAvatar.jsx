/**
 * AgentAvatar - Renders an agent's avatar.
 *
 * When cdn_url is available (e.g., GitHub profile photo), renders an <img>.
 * Otherwise falls back to the colored-letter avatar.
 */
export default function AgentAvatar({ agent, size = 36, borderRadius = 10, fontSize = 14 }) {
  const hasCdnUrl = agent.cdn_url && agent.cdn_url.length > 0;

  if (hasCdnUrl) {
    return (
      <img
        src={agent.cdn_url}
        alt={agent.display_name || agent.persona || agent.agent_id}
        style={{
          width: size,
          height: size,
          borderRadius,
          objectFit: "cover",
          flexShrink: 0,
        }}
      />
    );
  }

  return (
    <div
      style={{
        width: size,
        height: size,
        borderRadius,
        background: agent.color || "#6B7280",
        display: "flex",
        alignItems: "center",
        justifyContent: "center",
        color: "white",
        fontSize,
        fontWeight: 600,
        flexShrink: 0,
      }}
    >
      {agent.avatar || agent.agent_id?.[0]?.toUpperCase() || "?"}
    </div>
  );
}

/**
 * Returns the best display name for an agent.
 * Priority: display_name > persona > agent_id
 */
export function getAgentDisplayName(agent) {
  return agent.display_name || agent.persona || agent.agent_id;
}
