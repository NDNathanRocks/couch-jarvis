import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { TouchpadGesture, type PointerEmit } from "../utils/gestures";

let clock = 0;
let events: PointerEmit[] = [];

function pad(opts: { speed?: number; naturalScroll?: boolean } = {}): TouchpadGesture {
  return new TouchpadGesture({ emit: (e) => events.push(e), now: () => clock, speed: 1, ...opts });
}

beforeEach(() => {
  vi.useFakeTimers();
  clock = 0;
  events = [];
});
afterEach(() => vi.useRealTimers());

function advance(ms: number): void {
  clock += ms;
  vi.advanceTimersByTime(ms);
}

describe("TouchpadGesture", () => {
  it("a quick tap is a left click", () => {
    const g = pad();
    g.down(1, 50, 50);
    advance(80);
    g.up(1);
    expect(events).toEqual([{ action: "click" }]);
  });

  it("a tap that wanders too far is a move, not a click", () => {
    const g = pad();
    g.down(1, 50, 50);
    g.move(1, 90, 50);
    advance(60);
    g.up(1);
    expect(events.some((e) => e.action === "click")).toBe(false);
    expect(events.reduce((n, e) => n + (e.dx ?? 0), 0)).toBe(40);
  });

  it("a slow press with no movement is not a click", () => {
    const g = pad();
    g.down(1, 5, 5);
    advance(300); // not long enough to drag (450 ms) but too long to be a tap
    g.up(1);
    expect(events).toEqual([]);
  });

  it("two-finger tap right-clicks", () => {
    const g = pad();
    g.down(1, 10, 10);
    g.down(2, 60, 10);
    advance(90);
    g.up(1);
    g.up(2);
    expect(events).toEqual([{ action: "right_click" }]);
  });

  it("one-finger drag moves the cursor, batched, keeping the remainder", () => {
    const g = pad({ speed: 1.5 });
    g.down(1, 0, 0);
    for (let i = 1; i <= 10; i++) g.move(1, i * 4, 0); // 40 px * 1.5 = 60
    advance(50);
    g.up(1);
    const total = events.reduce((n, e) => n + (e.dx ?? 0), 0);
    expect(total).toBe(60);
    expect(events.length).toBeLessThanOrEqual(2); // coalesced, not one event per touch sample
  });

  it("two-finger drag up scrolls the page down (natural) in whole notches", () => {
    const g = pad();
    g.down(1, 10, 200);
    g.down(2, 60, 200);
    for (let y = 190; y >= 100; y -= 10) {
      g.move(1, 10, y);
      g.move(2, 60, y);
    }
    const scroll = events.reduce((n, e) => n + (e.scroll ?? 0), 0);
    expect(scroll).toBeGreaterThan(0);
    expect(Number.isInteger(scroll)).toBe(true);
    expect(events.every((e) => e.dx === undefined)).toBe(true); // scrolling never moves the cursor
  });

  it("natural scroll can be turned off", () => {
    const g = pad({ naturalScroll: false });
    g.down(1, 10, 200);
    g.down(2, 60, 200);
    for (let y = 190; y >= 100; y -= 10) {
      g.move(1, 10, y);
      g.move(2, 60, y);
    }
    expect(events.reduce((n, e) => n + (e.scroll ?? 0), 0)).toBeLessThan(0);
  });

  it("press and hold then drag is a click-drag", () => {
    const g = pad();
    g.down(1, 0, 0);
    advance(500);
    expect(events).toEqual([{ action: "drag_start" }]);
    g.move(1, 30, 0);
    advance(50);
    g.up(1);
    expect(events.map((e) => e.action ?? "move")).toEqual(["drag_start", "move", "drag_end"]);
  });

  it("moving before the hold time cancels the drag", () => {
    const g = pad();
    g.down(1, 0, 0);
    g.move(1, 40, 0);
    advance(600);
    g.up(1);
    expect(events.some((e) => e.action === "drag_start")).toBe(false);
  });

  it("cancel releases a drag in progress", () => {
    const g = pad();
    g.down(1, 0, 0);
    advance(500);
    g.cancel();
    expect(events.at(-1)).toEqual({ action: "drag_end" });
  });
});

describe("pointer acceleration", () => {
  const drag = (accel: number, stepPx: number, stepMs: number): number => {
    events = [];
    const g = new TouchpadGesture({ emit: (e) => events.push(e), now: () => clock, speed: 1, accel });
    g.down(1, 0, 0);
    g.move(1, 20, 0); // past the tap dead zone
    advance(50);
    events = [];
    let x = 20;
    for (let i = 0; i < 10; i++) {
      advance(stepMs);
      x += stepPx;
      g.move(1, x, 0);
    }
    advance(50);
    g.up(1);
    return events.reduce((n, e) => n + (e.dx ?? 0), 0);
  };

  it("is off unless asked for: the cursor moves 1:1", () => {
    expect(drag(0, 5, 100)).toBe(50);
  });

  it("a slow finger moves the cursor less, a quick flick moves it further", () => {
    const slow = drag(1, 2, 100); // 0.02 px/ms
    const fast = drag(1, 30, 16); // ~1.9 px/ms
    expect(slow).toBeLessThan(20); // under 1:1 (20 px moved)
    expect(fast).toBeGreaterThan(300 * 2); // well over 1:1 (300 px moved)
  });
});
