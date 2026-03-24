/**
 * Resolve Arena base URLs for HTTP and WebSocket.
 *
 * - Dev: default http://localhost:3001 (or VITE_API_URL when set).
 * - Prod: empty HTTP base = same origin when the SPA is served by Arena.
 */

/** Arena HTTP API base (no trailing slash). Empty = same origin. */
export function arenaHttpBase() {
  const v = import.meta.env.VITE_API_URL;
  if (typeof v === "string" && v.length > 0) {
    return v.replace(/\/+$/, "");
  }
  if (import.meta.env.PROD) {
    return "";
  }
  return "http://localhost:3001";
}

/** WebSocket origin (ws:// or wss:// + host[:port], no path). */
export function arenaWsBase() {
  const v = import.meta.env.VITE_API_URL;
  if (typeof v === "string" && v.length > 0) {
    return v.replace(/^http/, "ws").replace(/\/+$/, "");
  }
  if (
    import.meta.env.PROD &&
    typeof globalThis !== "undefined" &&
    globalThis.location
  ) {
    const loc = globalThis.location;
    const proto = loc.protocol === "https:" ? "wss:" : "ws:";
    return `${proto}//${loc.host}`;
  }
  return "ws://localhost:3001";
}
