import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { RoomConnection, type PairParams } from "../connection/roomConnection";
import type { RoomAction } from "../state/roomReducer";
import { resolveEndpoint } from "../utils/config";
import type { Pairing, PairingStorage } from "../utils/storage";
import { FakeSocket, flush, incoming } from "./helpers";

const endpoint = resolveEndpoint("wss://relay.example/prod", { protocol: "https:", hostname: "app.test", host: "app.test" });
const saved: Pairing = {
  roomId: "room-abcdef123",
  deviceId: "dev-1",
  deviceToken: "tok.en.1",
  expiresAt: 2_000_000_000,
  deviceName: "Pixel",
};
const params: PairParams = { roomId: "room-abcdef123", secret: "qr-secret-value-123456" };

interface Rig {
  conn: RoomConnection;
  storage: PairingStorage & { saved: Pairing | null; save: ReturnType<typeof vi.fn>; clear: ReturnType<typeof vi.fn> };
  actions: RoomAction[];
  paired: Pairing[];
  unpaired: number[];
  advance(ms: number): void;
}

function setup(initial: Pairing | null = saved, rng: () => number = () => 1): Rig {
  FakeSocket.reset();
  let clock = 1_000_000;
  const storage = {
    saved: initial,
    load: vi.fn(async () => storage.saved),
    save: vi.fn(async (p: Pairing) => {
      storage.saved = p;
    }),
    clear: vi.fn(async () => {
      storage.saved = null;
    }),
  };
  const actions: RoomAction[] = [];
  const paired: Pairing[] = [];
  const unpaired: number[] = [];
  const conn = new RoomConnection({
    endpoint,
    storage,
    dispatch: (a) => actions.push(a),
    createSocket: (url) => new FakeSocket(url),
    deviceName: "Pixel",
    onPaired: (p) => paired.push(p),
    onUnpaired: () => unpaired.push(1),
    rng,
    now: () => clock,
  });
  return {
    conn, storage, actions, paired, unpaired,
    advance: (ms) => {
      clock += ms;
      vi.advanceTimersByTime(ms);
    },
  };
}

const connections = (r: Rig) => r.actions.filter((a) => a.type === "connection").map((a) => (a as { connection: string }).connection);

beforeEach(() => vi.useFakeTimers());
afterEach(() => vi.useRealTimers());

describe("connecting", () => {
  it("returning phone: connects with its token and asks for state on open", async () => {
    const r = setup();
    await r.conn.start(null);
    const sock = FakeSocket.last;
    const url = new URL(sock.url);
    expect(url.searchParams.get("token")).toBe("tok.en.1");
    expect(url.searchParams.get("room")).toBe("room-abcdef123");
    expect(url.searchParams.has("pair")).toBe(false);
    sock.open();
    expect(sock.sent).toHaveLength(1);
    expect(sock.sent[0]).toMatchObject({ v: 1, type: "state.sync", from: "dev-1", room_id: "room-abcdef123", payload: {} });
    expect(connections(r)).toEqual(["connecting", "online"]);
  });

  it("an unpaired phone with no QR stays idle and opens no socket", async () => {
    const r = setup(null);
    await r.conn.start(null);
    expect(FakeSocket.instances).toHaveLength(0);
    expect(connections(r)).toEqual(["idle"]);
  });

  it("starting twice (React StrictMode) leaves exactly one live connection", async () => {
    const r = setup();
    const first = r.conn.start(null);
    const second = r.conn.start(null);
    await Promise.all([first, second]);
    expect(FakeSocket.instances).toHaveLength(1);
  });
});

describe("pairing", () => {
  it("scanning the QR pairs, stores the token, then connects with the token afterwards", async () => {
    const r = setup(null);
    await r.conn.start(params);
    const first = FakeSocket.last;
    const url = new URL(first.url);
    expect(url.searchParams.get("pair")).toBe("1");
    expect(url.searchParams.has("token")).toBe(false);
    first.open();
    expect(first.sent[0]).toMatchObject({
      type: "pair.request",
      payload: { room_secret: "qr-secret-value-123456", device_name: "Pixel" },
    });
    first.receive(incoming("pair.granted", { device_id: "dev-9", device_token: "new.jwt.tok", expires_at: 2_100_000_000 }));
    await flush();
    expect(r.storage.save).toHaveBeenCalledWith({
      roomId: "room-abcdef123", deviceId: "dev-9", deviceToken: "new.jwt.tok", expiresAt: 2_100_000_000, deviceName: "Pixel",
    });
    expect(r.paired).toHaveLength(1);
    expect(first.types()).toEqual(["pair.request", "state.sync"]); // then fetch the TV state
    expect(first.sent[1]).toMatchObject({ from: "dev-9" });

    first.drop();
    r.advance(1000);
    const second = FakeSocket.last;
    expect(second).not.toBe(first);
    expect(new URL(second.url).searchParams.get("token")).toBe("new.jwt.tok");
    expect(new URL(second.url).searchParams.has("pair")).toBe(false); // the secret is never reused
  });

  it("a wrong QR (pair.denied) stops retrying and reports rejection", async () => {
    const r = setup(null);
    await r.conn.start(params);
    FakeSocket.last.open();
    FakeSocket.last.receive(incoming("pair.denied", { reason: "invalid_secret" }));
    await flush();
    expect(r.actions.some((a) => a.type === "msg" && a.msg.type === "pair.denied")).toBe(true);
    expect(r.unpaired).toHaveLength(1);
    r.advance(120_000);
    expect(FakeSocket.instances).toHaveLength(1); // no reconnect storm with a bad secret
    expect(r.storage.save).not.toHaveBeenCalled();
  });

  it("scanning a QR re-pairs even if a (possibly stale) pairing exists", async () => {
    const r = setup(saved);
    await r.conn.start(params);
    expect(new URL(FakeSocket.last.url).searchParams.get("pair")).toBe("1");
  });

  it("refreshed tokens pushed by the relay replace the stored one", async () => {
    const r = setup();
    await r.conn.start(null);
    FakeSocket.last.open();
    FakeSocket.last.receive(incoming("pair.granted", { device_id: "dev-1", device_token: "fresh.tok.2", expires_at: 2_200_000_000 }, { from: "relay" }));
    await flush();
    expect(r.storage.saved?.deviceToken).toBe("fresh.tok.2");
    expect(FakeSocket.last.types()).toEqual(["state.sync"]); // not re-sent: this was not first pairing
  });
});

describe("reconnecting", () => {
  it("backs off exponentially while the network is down, and resets once a message arrives", async () => {
    const r = setup();
    await r.conn.start(null);
    FakeSocket.last.open();
    FakeSocket.last.drop();
    expect(connections(r).at(-1)).toBe("offline");
    r.advance(999);
    expect(FakeSocket.instances).toHaveLength(1);
    r.advance(1); // attempt 0 -> 1000 ms (rng=1)
    expect(FakeSocket.instances).toHaveLength(2);
    FakeSocket.last.open();
    FakeSocket.last.drop(); // dropped again with no traffic -> attempt 1 -> 2000 ms
    r.advance(1999);
    expect(FakeSocket.instances).toHaveLength(2);
    r.advance(1);
    expect(FakeSocket.instances).toHaveLength(3);
    FakeSocket.last.open();
    FakeSocket.last.receive(incoming("state.snapshot", { rev: 1 }));
    FakeSocket.last.drop();
    r.advance(1000); // a healthy session resets the ladder back to 1 s
    expect(FakeSocket.instances).toHaveLength(4);
  });

  it("jitter spreads reconnects so friends do not stampede after an outage", async () => {
    const delays = new Set<number>();
    for (const roll of [0.1, 0.4, 0.9]) {
      const r = setup(saved, () => roll);
      await r.conn.start(null);
      FakeSocket.last.open();
      FakeSocket.last.drop();
      for (let ms = 0; ms <= 1000; ms += 50) {
        if (FakeSocket.instances.length > 1) {
          delays.add(ms);
          break;
        }
        r.advance(50);
      }
    }
    expect(delays.size).toBeGreaterThan(1);
  });

  it("three failed handshakes in a row look like a revoked token: report rejected, retry slowly", async () => {
    const r = setup();
    await r.conn.start(null);
    for (let i = 0; i < 3; i++) {
      FakeSocket.last.drop(1006); // closed before ever opening
      r.advance(30_000);
    }
    expect(connections(r)).toContain("rejected");
    const count = FakeSocket.instances.length;
    FakeSocket.last.drop(1006);
    r.advance(29_999);
    expect(FakeSocket.instances).toHaveLength(count);
    r.advance(1);
    expect(FakeSocket.instances).toHaveLength(count + 1); // still tries every 30 s
  });

  it("onVisible reconnects immediately when the socket died while the phone slept", async () => {
    const r = setup();
    await r.conn.start(null);
    FakeSocket.last.open();
    FakeSocket.last.drop();
    r.conn.onVisible();
    expect(FakeSocket.instances).toHaveLength(2); // no waiting for the backoff timer
  });

  it("onVisible on a healthy socket just resyncs; on a silent one it reconnects", async () => {
    const r = setup();
    await r.conn.start(null);
    FakeSocket.last.open();
    FakeSocket.last.receive(incoming("pong", {}));
    r.advance(2000);
    r.conn.onVisible();
    expect(FakeSocket.last.types()).toEqual(["state.sync", "state.sync"]);
    expect(FakeSocket.instances).toHaveLength(1);
    r.advance(11_000); // the browser froze the socket in the background; it still says OPEN
    r.conn.onVisible();
    expect(FakeSocket.instances).toHaveLength(2);
  });
});

describe("liveness", () => {
  it("sends a heartbeat ping every 25 s while open", async () => {
    const r = setup();
    await r.conn.start(null);
    FakeSocket.last.open();
    r.advance(25_000);
    FakeSocket.last.receive(incoming("pong", {}));
    r.advance(25_000);
    expect(FakeSocket.last.types()).toEqual(["state.sync", "ping", "ping"]);
  });

  it("a half-open socket (no traffic for 70 s) is torn down and replaced", async () => {
    const r = setup();
    await r.conn.start(null);
    const dead = FakeSocket.last;
    dead.open();
    r.advance(75_000);
    expect(dead.closedWith).toBe(4000);
    expect(FakeSocket.instances).toHaveLength(2);
    dead.drop(); // a late close event from the dead socket must not disturb the new one
    r.advance(60_000);
    expect(FakeSocket.instances).toHaveLength(2);
  });

  it("ignores garbage and unknown messages", async () => {
    const r = setup();
    await r.conn.start(null);
    FakeSocket.last.open();
    const before = r.actions.length;
    FakeSocket.last.receive("{not json");
    FakeSocket.last.receive({ v: 1, type: "room.key", id: "x", payload: {} });
    await flush();
    expect(r.actions).toHaveLength(before);
  });
});

describe("sending", () => {
  async function online(): Promise<Rig> {
    const r = setup();
    await r.conn.start(null);
    FakeSocket.last.open();
    return r;
  }

  it("button presses carry fresh ids and idempotency keys (a double tap is two commands)", async () => {
    const r = await online();
    expect(r.conn.sendButton("vol_up")).toBe(true);
    expect(r.conn.sendButton("vol_up")).toBe(true);
    const [a, b] = FakeSocket.last.sent.slice(1) as Array<{ payload: Record<string, string> }>;
    expect(a?.payload).toMatchObject({ action: "vol_up", args: {} });
    expect(a?.payload["idempotency_key"]).not.toBe(b?.payload["idempotency_key"]);
    expect(a?.payload["command_id"]).not.toBe(b?.payload["command_id"]);
    const sentActions = r.actions.filter((x) => x.type === "sent");
    expect(sentActions).toHaveLength(2);
  });

  it("text commands are trimmed and bounded; blanks are ignored", async () => {
    const r = await online();
    expect(r.conn.sendText("   ")).toBe(false);
    expect(r.conn.sendText("  pause  ")).toBe(true);
    expect(r.conn.sendText("x".repeat(900))).toBe(true);
    const texts = FakeSocket.last.sent.filter((m) => m["type"] === "cmd.text").map((m) => (m["payload"] as { text: string }).text);
    expect(texts[0]).toBe("pause");
    expect(texts[1]).toHaveLength(500);
  });

  it("voice is sent as begin, ordered chunks, end - all under one command id", async () => {
    const r = await online();
    expect(r.conn.sendAudio({ chunks: ["AAAA", "BBBB", "CC=="], mime: "audio/webm", durationMs: 1800 })).toBe(true);
    const sent = FakeSocket.last.sent.slice(1) as Array<{ type: string; payload: Record<string, unknown> }>;
    expect(sent.map((m) => m.type)).toEqual(["audio.begin", "audio.chunk", "audio.chunk", "audio.chunk", "audio.end"]);
    expect(sent.filter((m) => m.type === "audio.chunk").map((m) => m.payload["seq"])).toEqual([0, 1, 2]);
    expect(new Set(sent.map((m) => m.payload["command_id"])).size).toBe(1);
    expect(sent[0]?.payload).toMatchObject({ mime: "audio/webm" });
    expect(sent.at(-1)?.payload).toMatchObject({ total_chunks: 3, duration_ms: 1800 });
    expect(r.actions.some((a) => a.type === "sent" && a.label === "Listening...")).toBe(true);
  });

  it("when offline, commands fail loudly instead of queueing silently", async () => {
    const r = setup();
    await r.conn.start(null); // socket exists but never opened
    expect(r.conn.sendButton("next")).toBe(false);
    expect(r.conn.sendAudio({ chunks: ["AAAA"], mime: "audio/webm", durationMs: 500 })).toBe(false);
    const toasts = r.actions.filter((a) => a.type === "toast");
    expect(toasts).toHaveLength(2);
    expect(FakeSocket.last.sent).toEqual([]);
  });
});

describe("shutdown", () => {
  it("stop() cancels timers and detaches the socket", async () => {
    const r = setup();
    await r.conn.start(null);
    const sock = FakeSocket.last;
    sock.open();
    r.conn.stop();
    expect(sock.closedWith).toBe(1000);
    sock.drop();
    r.advance(120_000);
    expect(FakeSocket.instances).toHaveLength(1);
  });

  it("unpair() forgets the token, closes the socket and never reconnects", async () => {
    const r = setup();
    await r.conn.start(null);
    FakeSocket.last.open();
    await r.conn.unpair();
    expect(r.storage.clear).toHaveBeenCalled();
    expect(r.storage.saved).toBeNull();
    expect(r.actions.at(-1)).toEqual({ type: "reset" });
    expect(r.unpaired).toHaveLength(1);
    r.advance(120_000);
    expect(FakeSocket.instances).toHaveLength(1);
    expect(r.conn.sendButton("next")).toBe(false);
  });
});
