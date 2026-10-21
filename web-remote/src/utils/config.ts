export type Transport = "cloud" | "lan";

export interface Endpoint {
  transport: Transport;
  /** WebSocket URL for a returning phone. */
  tokenUrl(args: { roomId: string; token: string; name: string }): string;
  /** WebSocket URL for a phone that still has to send pair.request. */
  pairUrl(args: { roomId: string; name: string }): string;
}

interface LocationLike {
  protocol: string;
  hostname: string;
  host: string;
}

/** True for http pages served by the daemon itself (an IP literal, localhost or *.local). */
export function isLanHost(loc: LocationLike): boolean {
  if (loc.protocol !== "http:") return false;
  return (
    loc.hostname === "localhost" ||
    loc.hostname.endsWith(".local") ||
    /^\d{1,3}(\.\d{1,3}){3}$/.test(loc.hostname) ||
    loc.hostname.startsWith("[")
  );
}

/**
 * Cloud mode talks to the AWS relay named at build time (VITE_RELAY_WS_URL). When the page is
 * served by the daemon on the LAN - or no relay was configured - it talks to the daemon's /ws.
 */
export function resolveEndpoint(relayUrl: string | undefined, loc: LocationLike): Endpoint {
  const relay = relayUrl?.trim();
  if (!relay || isLanHost(loc)) {
    const scheme = loc.protocol === "https:" ? "wss" : "ws";
    const base = `${scheme}://${loc.host}/ws`;
    return {
      transport: "lan",
      tokenUrl: ({ token, name }) => `${base}?${new URLSearchParams({ token, name })}`,
      pairUrl: ({ name }) => `${base}?${new URLSearchParams({ pair: "1", name })}`,
    };
  }
  const sep = relay.includes("?") ? "&" : "?";
  return {
    transport: "cloud",
    tokenUrl: ({ roomId, token }) => `${relay}${sep}${new URLSearchParams({ room: roomId, role: "phone", token })}`,
    pairUrl: ({ roomId }) => `${relay}${sep}${new URLSearchParams({ room: roomId, role: "phone", pair: "1" })}`,
  };
}

/** Full-jitter exponential backoff in ms, never below 500 ms. */
export function backoffMs(attempt: number, rng: () => number = Math.random, baseMs = 1000, capMs = 30000): number {
  const ceiling = Math.min(capMs, baseMs * 2 ** Math.min(attempt, 20));
  return Math.max(500, Math.floor(rng() * ceiling));
}
