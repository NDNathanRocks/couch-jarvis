import type { DevicePresence, Incoming, NowPlaying, TimerInfo, VoicePrefs } from "../protocol";

/**
 * "rejected" is definitive (the relay or PC said pair.denied). "unreachable" is what repeated
 * handshake failures look like: a revoked token, an API throttle and a dead Wi-Fi are
 * indistinguishable from a browser, so it must never claim the pairing is gone.
 */
export type Connection = "idle" | "connecting" | "online" | "offline" | "unreachable" | "rejected";

export interface RoomSnapshot {
  pc_online: boolean;
  mode: "cloud" | "lan";
  now_playing: NowPlaying | null;
  volume: number;
  muted: boolean;
  timers: TimerInfo[];
  queue_depth: number;
  driver: string | null;
  devices: DevicePresence[];
  /** The PC allows the touchpad/keyboard (CJ_REMOTE_INPUT). */
  remote_input: boolean;
  /** Spoken replies, saved on the PC so every phone shows the same choice. */
  voice: VoicePrefs;
  /** Browsing preferences, saved on the PC. */
  browser: { adblock: boolean };
  rev: number;
}

/** One line of the on-phone debug log. `at` is ms since the epoch (0 = unknown). */
export interface LogEntry {
  id: string;
  at: number;
  level: "ok" | "error" | "info";
  text: string;
  /** Extra lines: what the PC heard, which step failed and why. */
  detail?: string;
}

export interface Toast {
  id: string;
  kind: "ok" | "error" | "info";
  text: string;
  mine: boolean;
  /** A one-tap follow-up the PC offers ("open a new tab and search?"): sent as a text command. */
  suggest?: { label: string; text: string };
}

/** A question from the assistant, waiting for the next thing this phone says or taps. */
export interface AskCard {
  id: string;
  question: string;
  choices: string[];
  /** Open the mic by itself: only after a spoken command. */
  listen: boolean;
  expiresAt: number;
}

export interface RoomView {
  connection: Connection;
  /** The assistant's open question for this phone, if any. */
  ask: AskCard | null;
  /** The last snapshot/patches from the PC. */
  room: RoomSnapshot;
  hasSnapshot: boolean;
  /** Relay presence (authoritative for who is connected) - null when there is no relay (LAN). */
  relayPcOnline: boolean | null;
  relayDevices: DevicePresence[] | null;
  /** command_id -> latest human-readable progress, for commands this phone issued. */
  pending: Record<string, string>;
  /** command_id -> when it was sent (ms), so a command the PC never answers can time out. */
  pendingAt: Record<string, number>;
  toasts: Toast[];
  /** A patch arrived with a revision gap: ask the PC for a fresh snapshot. */
  needsSync: boolean;
  /** Recent ids of messages this phone sent, to recognise errors that answer them. */
  sent: string[];
  /** Newest last. Bounded; shown by the Log button so problems can be debugged from the couch. */
  log: LogEntry[];
}

export type RoomAction =
  | { type: "connection"; connection: Connection; at?: number }
  | { type: "clear_log" }
  | { type: "sent"; messageId: string; commandId?: string; label?: string; at?: number }
  | { type: "expire"; now: number; maxAgeMs: number }
  | { type: "msg"; msg: Incoming }
  | { type: "toast"; toast: Toast }
  | { type: "dismiss"; id: string }
  | { type: "dismiss_ask" }
  | { type: "synced" }
  | { type: "reset" };

const MAX_TOASTS = 4;
const MAX_SENT = 40;
const MAX_LOG = 80;

export const emptyRoom: RoomSnapshot = {
  pc_online: false,
  mode: "cloud",
  now_playing: null,
  volume: 50,
  muted: false,
  timers: [],
  queue_depth: 0,
  driver: null,
  devices: [],
  remote_input: true,
  voice: { enabled: true, style: "jarvis", voice: "british_male", buttons: false, engine: "none" },
  browser: { adblock: true },
  rev: 0,
};

export const initialView: RoomView = {
  connection: "idle",
  ask: null,
  room: emptyRoom,
  hasSnapshot: false,
  relayPcOnline: null,
  relayDevices: null,
  pending: {},
  pendingAt: {},
  toasts: [],
  needsSync: false,
  sent: [],
  log: [],
};

/** Message time in ms (the sender stamps seconds; 0 when missing). */
function msgTime(msg: { ts: number }): number {
  return msg.ts > 0 ? Math.round(msg.ts * 1000) : 0;
}

function withLog(view: RoomView, entry: LogEntry): RoomView {
  return { ...view, log: [...view.log, entry].slice(-MAX_LOG) };
}

const CONNECTION_TEXT: Record<Connection, string> = {
  idle: "Not paired",
  connecting: "Connecting to the relay...",
  online: "Connected",
  offline: "Connection lost - retrying",
  unreachable: "Cannot reach the relay (retrying slowly)",
  rejected: "Pairing was refused",
};

function isPlain(value: unknown): value is Record<string, unknown> {
  return typeof value === "object" && value !== null && !Array.isArray(value);
}

/** RFC 7386 JSON merge patch. null sets null (our nullable fields), arrays replace. */
export function mergePatch(target: unknown, patch: unknown): unknown {
  if (!isPlain(patch)) return patch;
  const base: Record<string, unknown> = isPlain(target) ? { ...target } : {};
  for (const [key, value] of Object.entries(patch)) {
    base[key] = isPlain(value) && isPlain(base[key]) ? mergePatch(base[key], value) : value;
  }
  return base;
}

export function normalizeRoom(partial: Partial<RoomSnapshot>): RoomSnapshot {
  // `voice` is nested: an older PC sends none, a newer one may send only the fields that changed
  return {
    ...emptyRoom,
    ...partial,
    voice: { ...emptyRoom.voice, ...partial.voice },
    browser: { ...emptyRoom.browser, ...partial.browser },
  };
}

function omit<T>(record: Record<string, T>, key: string): Record<string, T> {
  const copy = { ...record };
  delete copy[key];
  return copy;
}

function pushToast(toasts: Toast[], toast: Toast): Toast[] {
  return [...toasts.filter((t) => t.id !== toast.id), toast].slice(-MAX_TOASTS);
}

const NODE_LABELS: Record<string, string> = {
  stt: "Listening",
  intent_router: "Understanding",
  dom_executor: "Doing it",
  vision_fallback: "Looking at the screen",
  post_action_verifier: "Checking",
  recovery_node: "Retrying",
};

export function progressText(node: string, status: string, detail: string): string {
  if (node === "stt" && status === "done" && detail) return `Heard: "${detail}"`;
  return `${NODE_LABELS[node] ?? node}...`;
}

export function roomReducer(view: RoomView, action: RoomAction): RoomView {
  switch (action.type) {
    case "reset":
      return { ...initialView, log: view.log }; // keep the log: it explains how we got here
    case "clear_log":
      return { ...view, log: [] };
    case "connection": {
      if (action.connection === view.connection) return view;
      const next = { ...view, connection: action.connection };
      return withLog(next, {
        id: `conn-${action.at ?? 0}-${action.connection}`,
        at: action.at ?? 0,
        level: action.connection === "online" ? "ok" : "info",
        text: CONNECTION_TEXT[action.connection],
      });
    }
    case "toast":
      return { ...view, toasts: pushToast(view.toasts, action.toast) };
    case "dismiss_ask":
      return { ...view, ask: null };
    case "dismiss":
      return { ...view, toasts: view.toasts.filter((t) => t.id !== action.id) };
    case "synced":
      return { ...view, needsSync: false };
    case "sent": {
      const sent = [...view.sent, action.messageId].slice(-MAX_SENT);
      const pending = action.commandId
        ? { ...view.pending, [action.commandId]: action.label ?? "Sending..." }
        : view.pending;
      const pendingAt =
        action.commandId && action.at !== undefined
          ? { ...view.pendingAt, [action.commandId]: action.at }
          : view.pendingAt;
      return { ...view, sent, pending, pendingAt };
    }
    case "expire": {
      // The PC answers within ~25 s (its hard ceiling). A command still pending long after that
      // lost its result (dropped event, PC restart): stop showing a spinner forever.
      const stale = Object.keys(view.pending).filter(
        (id) => (view.pendingAt[id] ?? action.now) < action.now - action.maxAgeMs,
      );
      if (stale.length === 0) return view;
      let pending = view.pending;
      let pendingAt = view.pendingAt;
      for (const id of stale) {
        pending = omit(pending, id);
        pendingAt = omit(pendingAt, id);
      }
      return withLog(
        {
          ...view,
          pending,
          pendingAt,
          toasts: pushToast(view.toasts, {
            id: `expired-${action.now}`,
            kind: "error",
            text: "No answer from the PC - check the TV and try again",
            mine: true,
          }),
        },
        {
          id: `expired-${action.now}`,
          at: action.now,
          level: "error",
          text: "No answer from the PC",
          detail: "The command was sent but no result came back. The PC may be asleep, the daemon stopped, or the relay dropped it.",
        },
      );
    }
    case "msg":
      return reduceMessage(view, action.msg);
  }
}

function reduceMessage(view: RoomView, msg: Incoming): RoomView {
  switch (msg.type) {
    case "state.snapshot":
      // A snapshot always wins, even with a lower rev: the PC may have restarted.
      return { ...view, room: normalizeRoom(msg.payload as Partial<RoomSnapshot>), hasSnapshot: true, needsSync: false };
    case "state.patch": {
      const rev = msg.payload["rev"];
      if (typeof rev !== "number" || rev <= view.room.rev) return view; // stale or malformed
      const merged = mergePatch(view.room, msg.payload) as RoomSnapshot;
      return { ...view, room: normalizeRoom(merged), needsSync: view.needsSync || rev > view.room.rev + 1 };
    }
    case "presence":
      return { ...view, relayDevices: msg.payload.devices ?? [], relayPcOnline: msg.payload.pc_online ?? false };
    case "agent.progress": {
      const { command_id, node, status, detail } = msg.payload;
      if (!(command_id in view.pending)) return view; // someone else's command
      return { ...view, pending: { ...view.pending, [command_id]: progressText(node, status, detail ?? "") } };
    }
    case "cmd.ack": {
      if (msg.payload.status !== "rejected") return view;
      const mine = msg.payload.command_id in view.pending;
      const pending = omit(view.pending, msg.payload.command_id);
      const pendingAt = omit(view.pendingAt, msg.payload.command_id);
      // Say why: a phone that is too fast, or a TV that is swamped, needs different advice.
      const text =
        msg.payload.reason === "rate_limited"
          ? "Easy there - too many taps. Wait a moment."
          : msg.payload.reason === "queue_full"
            ? "The TV is busy. Try again in a few seconds."
            : "Command rejected";
      const next = { ...view, pending, pendingAt, toasts: pushToast(view.toasts, { id: msg.id, kind: "error", text, mine }) };
      return mine ? withLog(next, { id: msg.id, at: msgTime(msg), level: "error", text, detail: `rejected: ${msg.payload.reason ?? "unknown"}` }) : next;
    }
    case "cmd.result": {
      const { command_id, ok, message, heard, detail, tier_used, latency_ms, suggest, ask } = msg.payload;
      const mine = command_id in view.pending;
      const pending = omit(view.pending, command_id);
      const pendingAt = omit(view.pendingAt, command_id);
      const card: AskCard | null =
        mine && ask
          ? {
              id: msg.id,
              question: ask.question,
              choices: ask.choices ?? [],
              listen: ask.listen ?? false,
              expiresAt: msgTime(msg) + (ask.expires_s ?? 60) * 1000,
            }
          : null;
      const next = {
        ...view,
        pending,
        pendingAt,
        ask: mine ? card : view.ask, // my next result closes my open question
        toasts: card ? view.toasts : pushToast(view.toasts, {
          id: msg.id,
          kind: ok ? "ok" : "error",
          text: message,
          mine,
          // only the phone that asked is offered the follow-up, not its friends
          ...(mine && suggest ? { suggest: { label: suggest.label, text: suggest.text } } : {}),
        }),
      };
      if (!mine) return next;
      const lines = [
        heard ? `heard: "${heard}"` : "",
        detail ?? "",
        `${tier_used ?? "none"} tier, ${Math.round(latency_ms ?? 0)} ms`,
      ].filter(Boolean);
      return withLog(next, { id: msg.id, at: msgTime(msg), level: ok ? "ok" : "error", text: message, detail: lines.join("\n") });
    }
    case "error": {
      // Errors broadcast by the PC for a friend's message carry that message's id: ignore those.
      if (msg.reply_to !== null && !view.sent.includes(msg.reply_to)) return view;
      if (msg.payload.code === "rate_limited") return view; // touchpad bursts: noise, not news
      const text = msg.payload.code === "pc_offline" ? "The PC is offline" : msg.payload.message;
      return withLog(
        { ...view, toasts: pushToast(view.toasts, { id: msg.id, kind: "error", text, mine: true }) },
        { id: msg.id, at: msgTime(msg), level: "error", text, detail: `code: ${msg.payload.code}` },
      );
    }
    case "pair.denied":
      return {
        ...view,
        connection: "rejected",
        toasts: pushToast(view.toasts, { id: msg.id, kind: "error", text: "Pairing failed: scan the QR code again", mine: true }),
      };
    case "pair.granted":
    case "pong":
      return view; // handled by the connection layer
  }
}

/** Devices to show: relay presence wins in cloud mode, the PC's own list in LAN mode. */
export function selectDevices(view: RoomView): DevicePresence[] {
  return view.relayDevices ?? view.room.devices;
}

/** The PC is "online" if the relay says so, or (LAN) if we hold a recent snapshot from it. */
export function selectPcOnline(view: RoomView): boolean {
  return view.relayPcOnline ?? (view.hasSnapshot && view.room.pc_online);
}
