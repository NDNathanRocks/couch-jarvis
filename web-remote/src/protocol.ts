/**
 * Strictly typed envelope layer over the generated payload types (contracts.gen.ts).
 * The generated envelopes mark `type`/`v` optional (they have schema defaults), which breaks
 * discriminated-union narrowing, so the envelope shell is declared here and only the payloads
 * come from the generated file. src/__tests__/contracts.test.ts keeps both in sync.
 */
import type * as C from "./contracts.gen";

export const PROTOCOL_VERSION = 1 as const;
export type QuickAction = C.Action;
export type { DevicePresence, NowPlaying, TimerInfo } from "./contracts.gen";
/** This TV's spoken-reply preferences (every field present once the PC has reported them). */
export type VoicePrefs = Required<C.VoiceState>;

export interface Envelope<K extends string, P> {
  v: typeof PROTOCOL_VERSION;
  type: K;
  id: string;
  ts: number;
  room_id: string;
  from: string;
  reply_to: string | null;
  payload: P;
}

/** Messages the PC/relay send to phones. */
export interface IncomingPayloads {
  "pair.granted": C.PairGrantedPayload;
  "pair.denied": C.PairDeniedPayload;
  "cmd.ack": C.CmdAckPayload;
  "agent.progress": C.AgentProgressPayload;
  "cmd.result": C.CmdResultPayload;
  "state.snapshot": C.RoomState;
  "state.patch": C.Payload;
  presence: C.PresencePayload;
  error: C.ErrorPayload;
  pong: C.Empty;
}
export type IncomingType = keyof IncomingPayloads;
export type Incoming = { [K in IncomingType]: Envelope<K, IncomingPayloads[K]> }[IncomingType];

/** Messages phones send. */
export interface OutgoingPayloads {
  "pair.request": C.PairRequestPayload;
  "cmd.button": C.CmdButtonPayload;
  "cmd.text": C.CmdTextPayload;
  "input.pointer": C.InputPointerPayload;
  "input.key": C.InputKeyPayload;
  "input.text": C.InputTextPayload;
  "audio.begin": C.AudioBeginPayload;
  "audio.chunk": C.AudioChunkPayload;
  "audio.end": C.AudioEndPayload;
  "state.sync": C.Empty;
  ping: C.Empty;
}
export type OutgoingType = keyof OutgoingPayloads;

export const INCOMING_TYPES: ReadonlySet<string> = new Set<IncomingType>([
  "pair.granted",
  "pair.denied",
  "cmd.ack",
  "agent.progress",
  "cmd.result",
  "state.snapshot",
  "state.patch",
  "presence",
  "error",
  "pong",
]);

/** Runtime list of the phone->server types; the compiler checks it against OutgoingPayloads. */
export const OUTGOING_TYPES = [
  "pair.request",
  "cmd.button",
  "cmd.text",
  "input.pointer",
  "input.key",
  "input.text",
  "audio.begin",
  "audio.chunk",
  "audio.end",
  "state.sync",
  "ping",
] as const satisfies readonly OutgoingType[];

export type PointerAction = NonNullable<C.InputPointerPayload["action"]>;
export type Modifier = NonNullable<C.InputKeyPayload["mods"]>[number];

export function uuid(): string {
  if (typeof crypto !== "undefined" && typeof crypto.randomUUID === "function") {
    return crypto.randomUUID();
  }
  return "xxxxxxxx-xxxx-4xxx-yxxx-xxxxxxxxxxxx".replace(/[xy]/g, (c) => {
    const r = Math.floor(Math.random() * 16);
    return (c === "x" ? r : (r & 0x3) | 0x8).toString(16);
  });
}

export function makeEnvelope<K extends OutgoingType>(
  type: K,
  roomId: string,
  from: string,
  payload: OutgoingPayloads[K],
  now: () => number = Date.now,
): Envelope<K, OutgoingPayloads[K]> {
  return {
    v: PROTOCOL_VERSION,
    type,
    id: uuid(),
    ts: now() / 1000,
    room_id: roomId,
    from,
    reply_to: null,
    payload,
  };
}

function isRecord(value: unknown): value is Record<string, unknown> {
  return typeof value === "object" && value !== null && !Array.isArray(value);
}

/** Parse a wire message. Returns null for anything that is not a well-formed known envelope. */
export function parseIncoming(raw: unknown): Incoming | null {
  if (typeof raw !== "string") return null;
  let data: unknown;
  try {
    data = JSON.parse(raw);
  } catch {
    return null;
  }
  if (!isRecord(data) || data["v"] !== PROTOCOL_VERSION) return null;
  const { type, id, payload } = data;
  if (typeof type !== "string" || !INCOMING_TYPES.has(type)) return null;
  if (typeof id !== "string" || !isRecord(payload)) return null;
  return {
    v: PROTOCOL_VERSION,
    type,
    id,
    ts: typeof data["ts"] === "number" ? data["ts"] : 0,
    room_id: typeof data["room_id"] === "string" ? data["room_id"] : "",
    from: typeof data["from"] === "string" ? data["from"] : "",
    reply_to: typeof data["reply_to"] === "string" ? data["reply_to"] : null,
    payload,
  } as Incoming;
}
