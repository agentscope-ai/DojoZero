import { memo, useMemo, useRef, useEffect } from "react";

/**
 * DanmakuOverlay - Only for user messages
 * User comments fly smoothly across the screen from right to left
 */
function DanmakuOverlay({ userMessages = [] }) {
  const containerRef = useRef(null);

  // Assign lanes to user messages for vertical distribution
  const formattedUserMessages = useMemo(() => {
    return userMessages.map((msg, index) => ({
      ...msg,
      lane: (msg.id % 6) + 1, // Use 6 lanes (1-6) for user messages
    }));
  }, [userMessages]);

  // Calculate lane position (6 lanes for user messages)
  const getLaneTop = (lane) => {
    const laneHeight = 100 / 8; // 8 divisions for 6 lanes with margins
    return 10 + lane * laneHeight;
  };

  return (
    <div ref={containerRef} style={styles.overlay}>
      {formattedUserMessages.map((msg) => {
        const top = getLaneTop(msg.lane);
        
        return (
          <div
            key={msg.id}
            className="danmaku-text"
            style={{
              ...styles.danmakuItem,
              top: `${top}%`,
            }}
          >
            <span className="font-ui" style={styles.userDanmaku}>
              {msg.text}
            </span>
          </div>
        );
      })}
    </div>
  );
}

const styles = {
  overlay: {
    position: "absolute",
    inset: 0,
    zIndex: 20,
    pointerEvents: "none",
    overflow: "hidden",
  },
  danmakuItem: {
    position: "absolute",
    left: "100%", // Start from right edge
    whiteSpace: "nowrap",
    pointerEvents: "none",
    display: "flex",
    alignItems: "center",
  },
  userDanmaku: {
    display: "inline-block",
    padding: "8px 18px",
    background: "rgba(139, 92, 246, 0.25)",
    backdropFilter: "blur(8px)",
    WebkitBackdropFilter: "blur(8px)",
    borderRadius: "24px",
    color: "#fff",
    fontSize: "16px",
    fontWeight: "600",
    textShadow: "0 2px 8px rgba(0, 0, 0, 0.9)",
    border: "1px solid rgba(139, 92, 246, 0.4)",
    boxShadow: "0 4px 16px rgba(139, 92, 246, 0.2)",
  },
};

export default memo(DanmakuOverlay);
