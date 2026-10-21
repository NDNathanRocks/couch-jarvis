import { describe, expect, it } from "vitest";
import {
  emptyRoom,
  initialView,
  mergePatch,
  progressText,
  roomReducer,
  selectDevices,
  selectPcOnline,
  type RoomAction,
  type RoomView,
} from "../state/roomReducer";
import { incoming } from "./helpers";

const run = (view: RoomView, ...actions: RoomAction[]): RoomView => actions.reduce(roomReducer, view);
const msg = (...args: Parameters<typeof incoming>): RoomAction => ({ type: "msg", msg: incoming(...args) });

const snapshot = (rev: number, extra: Record<string, unknown> = {}) =>
  msg("state.snapshot", { ...emptyRoom, pc_online: true, rev, ...extra } as never);

describe("snapshots and patches", () => {
  it("a snapshot replaces state and fills defaults", () => {
    const v = run(initialView, msg("state.snapshot", { volume: 30, rev: 4 }));
    expect(v.hasSnapshot).toBe(true);
    expect(v.room).toMatchObject({ volume: 30, rev: 4, muted: false, timers: [], now_playing: null });
  });

  it("applies newer patches and ignores stale or duplicate revisions", () => {
    let v = run(initialView, snapshot(5));
    v = run(v, msg("state.patch", { volume: 70, rev: 6 }));
    expect(v.room.volume).toBe(70);
    const stale = run(v, msg("state.patch", { volume: 5, rev: 6 })); // same rev replayed
    expect(stale).toBe(v);
    expect(run(v, msg("state.patch", { volume: 5, rev: 2 }))).toBe(v);
    expect(run(v, msg("state.patch", { volume: 5 }))).toBe(v); // no rev: untrusted
  });

  it("a revision gap applies the patch but asks for a fresh snapshot", () => {
    let v = run(initialView, snapshot(5));
    v = run(v, msg("state.patch", { muted: true, rev: 9 }));
    expect(v.room.muted).toBe(true);
    expect(v.needsSync).toBe(true);
    expect(run(v, { type: "synced" }).needsSync).toBe(false);
  });

  it("a lower-rev snapshot still wins (the PC restarted)", () => {
    let v = run(initialView, snapshot(500));
    v = run(v, snapshot(1, { volume: 10 }));
    expect(v.room).toMatchObject({ rev: 1, volume: 10 });
    v = run(v, msg("state.patch", { volume: 11, rev: 2 })); // patches work again afterwards
    expect(v.room.volume).toBe(11);
  });

  it("merge patch replaces arrays, merges objects and can clear now_playing", () => {
    expect(mergePatch({ a: { b: 1, c: 2 }, l: [1, 2] }, { a: { b: 9 }, l: [3] })).toEqual({ a: { b: 9, c: 2 }, l: [3] });
    let v = run(initialView, snapshot(1, { now_playing: { title: "x", source: "youtube", is_playing: true } }));
    v = run(v, msg("state.patch", { now_playing: null, rev: 2 }));
    expect(v.room.now_playing).toBeNull();
  });
});

describe("presence", () => {
  const friends = [{ device_id: "d1", name: "Ann", online: true, last_seen: 1 }];

  it("relay presence overrides the PC's own device list and PC status", () => {
    let v = run(initialView, snapshot(1, { devices: [{ device_id: "x", name: "Old", online: true, last_seen: 0 }] }));
    expect(selectDevices(v)[0]?.name).toBe("Old"); // LAN: no relay presence yet
    v = run(v, msg("presence", { devices: friends, pc_online: false }));
    expect(selectDevices(v)).toEqual(friends);
    expect(selectPcOnline(v)).toBe(false);
  });

  it("without a relay, PC online comes from the snapshot", () => {
    expect(selectPcOnline(initialView)).toBe(false);
    expect(selectPcOnline(run(initialView, snapshot(1)))).toBe(true);
  });
});

describe("commands, progress and toasts", () => {
  const sent = (commandId: string, messageId = `m-${commandId}`): RoomAction => ({
    type: "sent", messageId, commandId, label: "Sending...",
  });

  it("tracks progress only for this phone's commands", () => {
    let v = run(initialView, sent("c1"));
    v = run(v, msg("agent.progress", { command_id: "c1", node: "vision_fallback", status: "start", detail: "" }));
    v = run(v, msg("agent.progress", { command_id: "friends-cmd", node: "stt", status: "start", detail: "" }));
    expect(v.pending).toEqual({ c1: "Looking at the screen..." });
  });

  it("a result clears pending and toasts; friends' results are marked not-mine", () => {
    let v = run(initialView, sent("c1"));
    v = run(v, msg("cmd.result", { command_id: "c1", ok: true, message: "Playing lofi", tier_used: "dom", latency_ms: 5 }));
    v = run(v, msg("cmd.result", { command_id: "other", ok: false, message: "Nope", tier_used: "none", latency_ms: 5 }));
    expect(v.pending).toEqual({});
    expect(v.toasts).toMatchObject([
      { kind: "ok", text: "Playing lofi", mine: true },
      { kind: "error", text: "Nope", mine: false },
    ]);
  });

  it("rejected acks surface; accepted and duplicate acks stay quiet", () => {
    const quiet = run(initialView, msg("cmd.ack", { command_id: "c", status: "accepted" }), msg("cmd.ack", { command_id: "c", status: "duplicate" }));
    expect(quiet.toasts).toEqual([]);
    const loud = run(initialView, sent("c"), msg("cmd.ack", { command_id: "c", status: "rejected" }));
    expect(loud.toasts[0]).toMatchObject({ kind: "error", mine: true });
    expect(loud.pending).toEqual({});
  });

  it("shows errors that answer our messages (or have no target) and hides friends' errors", () => {
    let v = run(initialView, sent("c1", "msg-mine"));
    v = run(v, msg("error", { code: "x", message: "friend problem", retryable: false }, { reply_to: "msg-theirs" }));
    expect(v.toasts).toEqual([]);
    v = run(v, msg("error", { code: "unauthorized", message: "mine", retryable: false }, { reply_to: "msg-mine" }));
    v = run(v, msg("error", { code: "pc_offline", message: "raw", retryable: true }));
    expect(v.toasts.map((t) => t.text)).toEqual(["mine", "The PC is offline"]);
  });

  it("keeps only the newest four toasts and supports dismiss", () => {
    let v = initialView;
    for (let i = 0; i < 6; i++) v = run(v, { type: "toast", toast: { id: `t${i}`, kind: "info", text: `${i}`, mine: true } });
    expect(v.toasts.map((t) => t.id)).toEqual(["t2", "t3", "t4", "t5"]);
    expect(run(v, { type: "dismiss", id: "t3" }).toasts.map((t) => t.id)).toEqual(["t2", "t4", "t5"]);
  });

  it("bounds the remembered sent ids", () => {
    let v = initialView;
    for (let i = 0; i < 100; i++) v = run(v, { type: "sent", messageId: `m${i}` });
    expect(v.sent).toHaveLength(40);
    expect(v.sent.at(-1)).toBe("m99");
  });

  it("pair.denied marks the connection rejected", () => {
    const v = run(initialView, msg("pair.denied", { reason: "invalid_secret" }));
    expect(v.connection).toBe("rejected");
    expect(v.toasts[0]?.kind).toBe("error");
  });

  it("reset returns to the initial view", () => {
    expect(run(run(initialView, snapshot(3)), { type: "reset" })).toBe(initialView);
  });

  it("progress text is friendly and includes what was heard", () => {
    expect(progressText("stt", "done", "pause")).toBe('Heard: "pause"');
    expect(progressText("dom_executor", "start", "")).toBe("Doing it...");
    expect(progressText("mystery", "start", "")).toBe("mystery...");
  });
});
