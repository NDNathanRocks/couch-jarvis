import { describe, expect, it } from "vitest";
import { backoffMs, isLanHost, resolveEndpoint } from "../utils/config";

const vercel = { protocol: "https:", hostname: "jarvis.vercel.app", host: "jarvis.vercel.app" };
const lan = { protocol: "http:", hostname: "192.168.1.20", host: "192.168.1.20:8765" };
const RELAY = "wss://abc123.execute-api.us-east-1.amazonaws.com/prod";

describe("resolveEndpoint", () => {
  it("uses the AWS relay for the hosted PWA", () => {
    const ep = resolveEndpoint(RELAY, vercel);
    expect(ep.transport).toBe("cloud");
    const url = new URL(ep.tokenUrl({ roomId: "room-abcdef123", token: "a.b.c", name: "Pixel" }));
    expect(url.origin + url.pathname).toBe("wss://abc123.execute-api.us-east-1.amazonaws.com/prod");
    expect(Object.fromEntries(url.searchParams)).toEqual({ room: "room-abcdef123", role: "phone", token: "a.b.c" });
    const pair = new URL(ep.pairUrl({ roomId: "room-abcdef123", name: "Pixel" }));
    expect(Object.fromEntries(pair.searchParams)).toEqual({ room: "room-abcdef123", role: "phone", pair: "1" });
  });

  it("talks to the daemon directly when served from the LAN", () => {
    const ep = resolveEndpoint(RELAY, lan);
    expect(ep.transport).toBe("lan");
    expect(ep.tokenUrl({ roomId: "r", token: "tok", name: "Pixel 9" })).toBe("ws://192.168.1.20:8765/ws?token=tok&name=Pixel+9");
    expect(ep.pairUrl({ roomId: "r", name: "P" })).toBe("ws://192.168.1.20:8765/ws?pair=1&name=P");
  });

  it("falls back to the daemon when no relay was configured at build time", () => {
    expect(resolveEndpoint(undefined, vercel).transport).toBe("lan");
    expect(resolveEndpoint("  ", vercel).transport).toBe("lan");
    expect(resolveEndpoint("", vercel).tokenUrl({ roomId: "r", token: "t", name: "n" }).startsWith("wss://jarvis.vercel.app/ws")).toBe(true);
  });

  it("url-encodes tokens and names so they cannot inject parameters", () => {
    const ep = resolveEndpoint(RELAY, vercel);
    const url = new URL(ep.tokenUrl({ roomId: "r", token: "x&role=pc", name: "n" }));
    expect(url.searchParams.get("role")).toBe("phone");
    expect(url.searchParams.get("token")).toBe("x&role=pc");
  });

  it("appends to a relay URL that already has a query string", () => {
    const ep = resolveEndpoint(`${RELAY}?x=1`, vercel);
    expect(new URL(ep.pairUrl({ roomId: "room-abcdef123", name: "n" })).searchParams.get("x")).toBe("1");
  });
});

describe("isLanHost", () => {
  it.each([
    [{ protocol: "http:", hostname: "localhost", host: "localhost:5173" }, true],
    [{ protocol: "http:", hostname: "10.0.0.5", host: "10.0.0.5:8765" }, true],
    [{ protocol: "http:", hostname: "tv.local", host: "tv.local:8765" }, true],
    [{ protocol: "https:", hostname: "10.0.0.5", host: "10.0.0.5" }, false],
    [{ protocol: "http:", hostname: "example.com", host: "example.com" }, false],
  ])("%j -> %s", (loc, expected) => {
    expect(isLanHost(loc)).toBe(expected);
  });
});

describe("backoffMs", () => {
  it("is full-jitter, exponential, capped and never a hot loop", () => {
    expect(backoffMs(0, () => 1)).toBe(1000);
    expect(backoffMs(3, () => 1)).toBe(8000);
    expect(backoffMs(12, () => 1)).toBe(30000);
    expect(backoffMs(100, () => 1)).toBe(30000);
    expect(backoffMs(4, () => 0)).toBe(500);
    expect(backoffMs(4, () => 0.5)).toBe(8000);
  });
});
