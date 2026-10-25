import {
  makeEnvelope,
  parseIncoming,
  uuid,
  type Modifier,
  type OutgoingPayloads,
  type OutgoingType,
  type PointerAction,
  type QuickAction,
} from "../protocol";
import type { RoomAction } from "../state/roomReducer";
import { backoffMs, type Endpoint } from "../utils/config";
import type { Recording } from "../utils/audioRecorder";
import type { Pairing, PairingStorage } from "../utils/storage";

export const WS_CONNECTING = 0;
export const WS_OPEN = 1;

/** The slice of WebSocket this class uses (lets tests inject a fake). */
export interface SocketLike {
  readyState: number;
  send(data: string): void;
  close(code?: number, reason?: string): void;
  onopen: ((ev: unknown) => void) | null;
  onmessage: ((ev: { data: unknown }) => void) | null;
  onclose: ((ev: { code: number }) => void) | null;
  onerror: ((ev: unknown) => void) | null;
}

export const browserSocketFactory = (url: string): SocketLike => new WebSocket(url) as unknown as SocketLike;

export interface PairParams {
  roomId: string;
  secret: string;
}

export interface ConnectionOptions {
  endpoint: Endpoint;
  storage: PairingStorage;
  dispatch: (action: RoomAction) => void;
  createSocket: (url: string) => SocketLike;
  deviceName: string;
  onPaired?: (pairing: Pairing) => void;
  /** A saved pairing is being used (the app was reopened): who this phone is. */
  onRestored?: (pairing: Pairing) => void;
  onUnpaired?: () => void;
  rng?: () => number;
  now?: () => number;
  heartbeatMs?: number;
  /** No message for this long means the socket is dead even if it still says OPEN. */
  staleMs?: number;
}

type Mode =
  | { kind: "none" }
  | { kind: "token"; pairing: Pairing }
  | { kind: "pair"; roomId: string; secret: string };

const TOKEN_FAILURES_BEFORE_REPAIR_HINT = 3;
// After repeated handshake failures keep trying, but gently: 15 s is quick enough to feel
// recovered once an outage ends, cheap enough that a revoked token costs almost nothing.
const SLOW_RETRY_MS = 15_000;

export class RoomConnection {
  private mode: Mode = { kind: "none" };
  private socket: SocketLike | null = null;
  private generation = 0;
  private attempts = 0;
  private handshakeFailures = 0;
  private opened = false;
  private lastMessageAt = 0;
  private reconnectTimer: ReturnType<typeof setTimeout> | null = null;
  private heartbeatTimer: ReturnType<typeof setInterval> | null = null;
  private readonly rng: () => number;
  private readonly now: () => number;

  constructor(private readonly opts: ConnectionOptions) {
    this.rng = opts.rng ?? Math.random;
    this.now = opts.now ?? Date.now;
  }

  // ------------------------------------------------------------------ lifecycle

  /** Scanning the QR (params) always pairs; otherwise a saved pairing reconnects silently. */
  async start(params: PairParams | null): Promise<void> {
    const gen = ++this.generation;
    this.clearTimers();
    const saved = params ? null : await this.opts.storage.load();
    if (gen !== this.generation) return; // stopped (or restarted by StrictMode) while loading
    if (params) this.mode = { kind: "pair", roomId: params.roomId, secret: params.secret };
    else if (saved) {
      this.mode = { kind: "token", pairing: saved };
      this.opts.onRestored?.(saved);
    }
    else {
      this.mode = { kind: "none" };
      this.opts.dispatch({ type: "connection", connection: "idle", at: this.now() });
      return;
    }
    this.connect();
  }

  stop(): void {
    this.generation++;
    this.clearTimers();
    this.detachSocket(1000);
  }

  async unpair(): Promise<void> {
    this.stop();
    this.mode = { kind: "none" };
    await this.opts.storage.clear();
    this.opts.dispatch({ type: "reset" });
    this.opts.onUnpaired?.();
  }

  // ------------------------------------------------------------------ connecting

  private connect(): void {
    if (this.mode.kind === "none") return;
    this.clearTimers();
    const { endpoint, deviceName } = this.opts;
    const url =
      this.mode.kind === "token"
        ? endpoint.tokenUrl({ roomId: this.mode.pairing.roomId, token: this.mode.pairing.deviceToken, name: deviceName })
        : endpoint.pairUrl({ roomId: this.mode.roomId, name: deviceName });
    this.opts.dispatch({ type: "connection", connection: "connecting", at: this.now() });
    let sock: SocketLike;
    try {
      sock = this.opts.createSocket(url);
    } catch {
      this.scheduleReconnect();
      return;
    }
    this.socket = sock;
    this.opened = false;
    const gen = this.generation;
    const live = () => this.socket === sock && gen === this.generation;
    sock.onopen = () => {
      if (!live()) return;
      this.opened = true;
      this.lastMessageAt = this.now();
      this.opts.dispatch({ type: "connection", connection: "online", at: this.now() });
      if (this.mode.kind === "pair") {
        this.send("pair.request", { room_secret: this.mode.secret, device_name: this.opts.deviceName });
      } else {
        this.send("state.sync", {});
      }
      this.startHeartbeat();
    };
    sock.onmessage = (ev) => {
      if (live()) void this.handleMessage(ev.data);
    };
    sock.onclose = () => {
      if (live()) this.handleClosed();
    };
    sock.onerror = () => undefined; // onclose always follows and drives the retry
  }

  private async handleMessage(data: unknown): Promise<void> {
    this.lastMessageAt = this.now();
    this.attempts = 0;
    this.handshakeFailures = 0;
    const msg = parseIncoming(data);
    if (!msg) return;
    if (msg.type === "pair.granted") {
      const roomId = this.mode.kind === "pair" ? this.mode.roomId : this.mode.kind === "token" ? this.mode.pairing.roomId : "";
      if (!roomId) return;
      const pairing: Pairing = {
        roomId,
        deviceId: msg.payload.device_id,
        deviceToken: msg.payload.device_token,
        expiresAt: msg.payload.expires_at,
        deviceName: this.opts.deviceName,
      };
      await this.opts.storage.save(pairing);
      const firstPairing = this.mode.kind === "pair";
      this.mode = { kind: "token", pairing };
      this.opts.onPaired?.(pairing);
      this.opts.dispatch({ type: "msg", msg });
      if (firstPairing) this.send("state.sync", {}); // ask for the current TV state
      return;
    }
    if (msg.type === "pair.denied") {
      this.mode = { kind: "none" };
      this.clearTimers();
      this.detachSocket(1000);
      this.opts.dispatch({ type: "msg", msg });
      this.opts.onUnpaired?.();
      return;
    }
    this.opts.dispatch({ type: "msg", msg });
  }

  private handleClosed(): void {
    this.stopHeartbeat();
    this.socket = null;
    if (this.mode.kind === "none") return;
    if (!this.opened && this.mode.kind === "token") {
      // The handshake itself failed: a revoked/expired token looks exactly like this.
      this.handshakeFailures++;
    }
    const unreachable = this.handshakeFailures >= TOKEN_FAILURES_BEFORE_REPAIR_HINT;
    this.opts.dispatch({ type: "connection", connection: unreachable ? "unreachable" : "offline", at: this.now() });
    this.scheduleReconnect(unreachable ? SLOW_RETRY_MS : undefined);
  }

  private scheduleReconnect(fixedDelayMs?: number): void {
    if (this.reconnectTimer !== null) clearTimeout(this.reconnectTimer);
    const delay = fixedDelayMs ?? backoffMs(this.attempts, this.rng);
    this.attempts++;
    this.reconnectTimer = setTimeout(() => {
      this.reconnectTimer = null;
      this.connect();
    }, delay);
  }

  // ------------------------------------------------------------------ liveness

  private startHeartbeat(): void {
    this.stopHeartbeat();
    const every = this.opts.heartbeatMs ?? 25_000;
    const stale = this.opts.staleMs ?? 70_000;
    this.heartbeatTimer = setInterval(() => {
      if (this.now() - this.lastMessageAt > stale) {
        this.forceReconnect(); // half-open socket: the network died without a close frame
        return;
      }
      this.send("ping", {});
    }, every);
  }

  private stopHeartbeat(): void {
    if (this.heartbeatTimer !== null) clearInterval(this.heartbeatTimer);
    this.heartbeatTimer = null;
  }

  private forceReconnect(): void {
    this.attempts = 0;
    this.detachSocket(4000);
    this.stopHeartbeat();
    this.clearReconnect();
    this.connect();
  }

  /** Phone woke up / app foregrounded. Mobile browsers freeze sockets in the background. */
  onVisible(): void {
    if (this.mode.kind === "none") return;
    const sock = this.socket;
    if (!sock || sock.readyState > WS_OPEN) {
      this.attempts = 0;
      this.clearReconnect();
      this.connect();
    } else if (sock.readyState === WS_OPEN) {
      if (this.now() - this.lastMessageAt > 10_000) this.forceReconnect();
      else this.send("state.sync", {});
    }
  }

  onOnline(): void {
    this.onVisible();
  }

  requestSync(): void {
    this.send("state.sync", {});
  }

  // ------------------------------------------------------------------ sending

  private from(): string {
    return this.mode.kind === "token" ? this.mode.pairing.deviceId : "phone";
  }

  private roomId(): string {
    return this.mode.kind === "token" ? this.mode.pairing.roomId : this.mode.kind === "pair" ? this.mode.roomId : "";
  }

  private send<K extends OutgoingType>(type: K, payload: OutgoingPayloads[K]): string | null {
    const sock = this.socket;
    if (!sock || sock.readyState !== WS_OPEN) return null;
    const env = makeEnvelope(type, this.roomId(), this.from(), payload, this.now);
    sock.send(JSON.stringify(env));
    return env.id;
  }

  private notConnected(): false {
    this.opts.dispatch({
      type: "toast",
      toast: { id: uuid(), kind: "error", text: "Not connected yet - try again in a moment", mine: true },
    });
    return false;
  }

  sendButton(action: QuickAction): boolean {
    const commandId = uuid();
    const id = this.send("cmd.button", { command_id: commandId, idempotency_key: uuid(), action, args: {} });
    if (!id) return this.notConnected();
    this.opts.dispatch({ type: "sent", messageId: id, commandId, label: "Sending...", at: this.now() });
    return true;
  }

  /**
   * A slider value (volume level, seek position). Applied by the PC at once with no reply, so it
   * is not tracked as a command; only a refusal (rate limit, nothing to seek in) comes back.
   */
  sendSlider(action: "volume_set" | "seek_to", value: number): boolean {
    const args: Record<string, number> =
      action === "volume_set" ? { level: Math.round(Math.max(0, Math.min(100, value))) } : { position_s: Math.max(0, Math.round(value * 10) / 10) };
    const id = this.send("cmd.button", { command_id: uuid(), idempotency_key: uuid(), action, args });
    if (!id) return this.notConnected();
    this.opts.dispatch({ type: "sent", messageId: id });
    return true;
  }

  sendText(text: string): boolean {
    const trimmed = text.trim().slice(0, 500);
    if (!trimmed) return false;
    const commandId = uuid();
    const id = this.send("cmd.text", { command_id: commandId, idempotency_key: uuid(), text: trimmed });
    if (!id) return this.notConnected();
    this.opts.dispatch({ type: "sent", messageId: id, commandId, label: "Sending...", at: this.now() });
    return true;
  }

  /** Touchpad movement/clicks. Fire-and-forget: never tracked, never toasts. */
  sendPointer(p: { dx?: number; dy?: number; scroll?: number; action?: PointerAction }): boolean {
    const payload: OutgoingPayloads["input.pointer"] = {};
    if (p.dx) payload.dx = Math.max(-400, Math.min(400, Math.round(p.dx)));
    if (p.dy) payload.dy = Math.max(-400, Math.min(400, Math.round(p.dy)));
    if (p.scroll) payload.scroll = Math.max(-30, Math.min(30, Math.round(p.scroll)));
    if (p.action) payload.action = p.action;
    if (Object.keys(payload).length === 0) return true;
    return this.send("input.pointer", payload) !== null;
  }

  /** A key or chord (alt+left ...). Tracked so a refusal from the PC reaches this phone. */
  sendKey(key: string, mods: Modifier[] = []): boolean {
    // the generated schema type is a tuple union (0-3 items); the contract caps it at three
    const limited = mods.slice(0, 3) as NonNullable<OutgoingPayloads["input.key"]["mods"]>;
    const id = this.send("input.key", { key, mods: limited });
    if (!id) return this.notConnected();
    this.opts.dispatch({ type: "sent", messageId: id });
    return true;
  }

  /** Literal typed text from the phone keyboard. */
  sendTyped(text: string): boolean {
    const clipped = text.slice(0, 200);
    if (!clipped) return false;
    const id = this.send("input.text", { text: clipped });
    if (!id) return this.notConnected();
    this.opts.dispatch({ type: "sent", messageId: id });
    return true;
  }

  sendAudio(rec: Recording): boolean {
    const commandId = uuid();
    const begin = this.send("audio.begin", { command_id: commandId, idempotency_key: uuid(), mime: rec.mime });
    if (!begin) return this.notConnected();
    rec.chunks.forEach((b64, seq) => this.send("audio.chunk", { command_id: commandId, seq, b64 }));
    const end = this.send("audio.end", {
      command_id: commandId,
      total_chunks: rec.chunks.length,
      duration_ms: rec.durationMs,
    });
    this.opts.dispatch({ type: "sent", messageId: end ?? begin, commandId, label: "Listening...", at: this.now() });
    return true;
  }

  // ------------------------------------------------------------------ helpers

  private detachSocket(code: number): void {
    const sock = this.socket;
    this.socket = null;
    if (sock) {
      sock.onopen = sock.onmessage = sock.onclose = sock.onerror = null;
      try {
        sock.close(code);
      } catch {
        /* already closed */
      }
    }
  }

  private clearReconnect(): void {
    if (this.reconnectTimer !== null) clearTimeout(this.reconnectTimer);
    this.reconnectTimer = null;
  }

  private clearTimers(): void {
    this.clearReconnect();
    this.stopHeartbeat();
  }
}
