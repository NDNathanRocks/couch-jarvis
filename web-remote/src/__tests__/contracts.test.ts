import { describe, expect, it } from "vitest";
import schema from "../../../shared/contracts/envelope.schema.json";
import { INCOMING_TYPES, OUTGOING_TYPES, parseIncoming } from "../protocol";

interface Def {
  properties?: Record<string, { const?: string }>;
}
const defs = (schema as unknown as { $defs: Record<string, Def> }).$defs;
const schemaTypes = new Set(
  Object.values(defs)
    .map((d) => d.properties?.["type"]?.const)
    .filter((t): t is string => typeof t === "string"),
);

describe("protocol stays in sync with the Python contract", () => {
  it("knows only message types the contract defines", () => {
    for (const t of INCOMING_TYPES) expect(schemaTypes, `incoming ${t}`).toContain(t);
    for (const t of OUTGOING_TYPES) expect(schemaTypes, `outgoing ${t}`).toContain(t);
  });

  it("covers the phone-visible half of the contract", () => {
    // everything a phone may receive from PC/relay, except room.key which is PC-only
    const expected = [
      "pair.granted", "pair.denied", "cmd.ack", "agent.progress", "cmd.result",
      "state.snapshot", "state.patch", "presence", "error", "pong",
    ];
    expect([...INCOMING_TYPES].sort()).toEqual(expected.sort());
    expect(INCOMING_TYPES.has("room.key")).toBe(false); // the room key must never reach phones
  });
});

describe("parseIncoming", () => {
  const ok = { v: 1, type: "pong", id: "i", ts: 1, room_id: "r", from: "relay", reply_to: null, payload: {} };

  it("accepts a well-formed envelope", () => {
    expect(parseIncoming(JSON.stringify(ok))?.type).toBe("pong");
  });

  it.each([
    ["not json", "{nope"],
    ["array", "[]"],
    ["wrong version", JSON.stringify({ ...ok, v: 2 })],
    ["unknown type", JSON.stringify({ ...ok, type: "cmd.button" })],
    ["room.key", JSON.stringify({ ...ok, type: "room.key" })],
    ["missing id", JSON.stringify({ ...ok, id: undefined })],
    ["array payload", JSON.stringify({ ...ok, payload: [] })],
    ["non-string input", 42],
    ["null", "null"],
  ])("rejects %s", (_name, raw) => {
    expect(parseIncoming(raw)).toBeNull();
  });

  it("normalises optional fields", () => {
    const msg = parseIncoming(JSON.stringify({ v: 1, type: "pong", id: "i", payload: {} }));
    expect(msg).toMatchObject({ ts: 0, room_id: "", from: "", reply_to: null });
  });
});
