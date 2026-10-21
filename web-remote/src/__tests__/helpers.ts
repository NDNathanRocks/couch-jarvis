import type { SocketLike } from "../connection/roomConnection";
import type { Incoming, IncomingPayloads, IncomingType } from "../protocol";

/** Controllable stand-in for WebSocket. */
export class FakeSocket implements SocketLike {
  static instances: FakeSocket[] = [];
  static reset(): void {
    FakeSocket.instances = [];
  }
  static get last(): FakeSocket {
    const s = FakeSocket.instances.at(-1);
    if (!s) throw new Error("no socket was created");
    return s;
  }

  readyState = 0;
  sent: Array<Record<string, unknown>> = [];
  closedWith: number | null = null;
  onopen: ((ev: unknown) => void) | null = null;
  onmessage: ((ev: { data: unknown }) => void) | null = null;
  onclose: ((ev: { code: number }) => void) | null = null;
  onerror: ((ev: unknown) => void) | null = null;

  constructor(readonly url: string) {
    FakeSocket.instances.push(this);
  }

  send(data: string): void {
    this.sent.push(JSON.parse(data) as Record<string, unknown>);
  }
  close(code = 1000): void {
    this.closedWith = code;
    this.readyState = 3;
  }

  // ---- test controls
  open(): void {
    this.readyState = 1;
    this.onopen?.({});
  }
  receive(msg: unknown): void {
    this.onmessage?.({ data: typeof msg === "string" ? msg : JSON.stringify(msg) });
  }
  drop(code = 1006): void {
    this.readyState = 3;
    this.onclose?.({ code });
  }
  types(): string[] {
    return this.sent.map((m) => String(m["type"]));
  }
}

let counter = 0;
export function incoming<K extends IncomingType>(
  type: K,
  payload: IncomingPayloads[K],
  extra: Partial<Pick<Incoming, "id" | "reply_to" | "from">> = {},
): Incoming {
  counter++;
  return {
    v: 1,
    type,
    id: extra.id ?? `msg-${counter}`,
    ts: 1,
    room_id: "room-abcdef123",
    from: extra.from ?? "pc",
    reply_to: extra.reply_to ?? null,
    payload,
  } as Incoming;
}

export const flush = async (): Promise<void> => {
  for (let i = 0; i < 5; i++) await Promise.resolve();
};
