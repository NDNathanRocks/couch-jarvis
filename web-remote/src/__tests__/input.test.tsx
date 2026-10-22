import { fireEvent, render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it, vi } from "vitest";
import { KeyPad, SHORTCUTS } from "../components/KeyPad";
import { LogPanel, logToText } from "../components/LogPanel";
import { TouchPad } from "../components/TouchPad";
import { initialView, roomReducer, type RoomAction, type RoomView } from "../state/roomReducer";
import { incoming } from "./helpers";

const run = (view: RoomView, ...actions: RoomAction[]): RoomView => actions.reduce(roomReducer, view);
const msg = (...args: Parameters<typeof incoming>): RoomAction => ({ type: "msg", msg: incoming(...args) });
const pendingView = (id: string): RoomView => run(initialView, { type: "sent", messageId: "m", commandId: id, at: 1 });

describe("debug log (reducer)", () => {
  it("records connection changes once, not on every retry of the same state", () => {
    const v = run(
      initialView,
      { type: "connection", connection: "connecting", at: 1000 },
      { type: "connection", connection: "connecting", at: 2000 },
      { type: "connection", connection: "online", at: 3000 },
    );
    expect(v.log.map((e) => e.text)).toEqual(["Connecting to the relay...", "Connected"]);
  });

  it("a failed command logs what was heard and why it failed", () => {
    const v = run(
      pendingView("c1"),
      msg("cmd.result", {
        command_id: "c1",
        ok: false,
        message: "Sorry, that did not work. Try again.",
        heard: "play episode five",
        detail: "browser.navigate via llm [navigate]; step0/dom: url is not on the media allowlist",
        tier_used: "none",
        latency_ms: 812.4,
      } as never),
    );
    const entry = v.log.at(-1);
    expect(entry).toMatchObject({ level: "error", text: "Sorry, that did not work. Try again." });
    expect(entry?.detail).toContain('heard: "play episode five"');
    expect(entry?.detail).toContain("url is not on the media allowlist");
    expect(entry?.detail).toContain("812 ms");
  });

  it("a friend's command result is not logged here", () => {
    const v = run(initialView, msg("cmd.result", { command_id: "someone-else", ok: false, message: "x" } as never));
    expect(v.log).toEqual([]);
  });

  it("timeouts and PC errors are logged; touchpad rate limiting is not", () => {
    let v = run(pendingView("c1"), { type: "expire", now: 99_000_000, maxAgeMs: 1 });
    expect(v.log.at(-1)?.text).toBe("No answer from the PC");
    v = run(
      { ...initialView, sent: ["orig"] },
      msg("error", { code: "key_not_allowed", message: "ctrl+esc is blocked", retryable: false } as never, { reply_to: "orig" } as never),
    );
    expect(v.log.at(-1)).toMatchObject({ level: "error", text: "ctrl+esc is blocked", detail: "code: key_not_allowed" });
    const quiet = run(
      { ...initialView, sent: ["orig"] },
      msg("error", { code: "rate_limited", message: "Slow down", retryable: true } as never, { reply_to: "orig" } as never),
    );
    expect(quiet.log).toEqual([]);
  });

  it("the log is bounded and survives unpairing", () => {
    let v = initialView;
    for (let i = 0; i < 200; i++) v = run(v, { type: "connection", connection: i % 2 ? "online" : "offline", at: i });
    expect(v.log.length).toBeLessThanOrEqual(80);
    expect(run(v, { type: "reset" }).log).toEqual(v.log);
    expect(run(v, { type: "clear_log" }).log).toEqual([]);
  });
});

describe("LogPanel", () => {
  const entries = [
    { id: "1", at: 0, level: "ok" as const, text: "Connected" },
    { id: "2", at: 0, level: "error" as const, text: "No answer", detail: "line a\nline b" },
  ];

  it("shows newest first with details, and can clear and close", () => {
    const onClear = vi.fn();
    const onClose = vi.fn();
    render(<LogPanel entries={entries} onClear={onClear} onClose={onClose} />);
    const items = screen.getAllByRole("listitem");
    expect(items[0]).toHaveTextContent("No answer");
    expect(items[0]).toHaveTextContent("line b");
    fireEvent.click(screen.getByRole("button", { name: "Clear" }));
    fireEvent.click(screen.getByRole("button", { name: "Close log" }));
    expect(onClear).toHaveBeenCalled();
    expect(onClose).toHaveBeenCalled();
  });

  it("explains an empty log and renders text for copying", () => {
    render(<LogPanel entries={[]} onClear={() => undefined} onClose={() => undefined} />);
    expect(screen.getByText(/Nothing yet/)).toBeInTheDocument();
    expect(logToText(entries)).toContain("ERROR No answer\n    line a\n    line b");
  });
});

describe("KeyPad", () => {
  it("sends named keys and browser shortcuts (alt+left is Back)", () => {
    const onKey = vi.fn();
    render(<KeyPad onKey={onKey} onText={vi.fn()} disabled={false} />);
    fireEvent.click(screen.getByRole("button", { name: "Key esc" }));
    fireEvent.click(screen.getByRole("button", { name: "Back" }));
    expect(onKey.mock.calls).toEqual([
      ["esc", []],
      ["left", ["alt"]],
    ]);
    expect(SHORTCUTS.find((s) => s.label === "Prev tab")).toMatchObject({ key: "tab", mods: ["ctrl", "shift"] });
  });

  it("modifiers latch for one key, then release", () => {
    const onKey = vi.fn();
    render(<KeyPad onKey={onKey} onText={vi.fn()} disabled={false} />);
    fireEvent.click(screen.getByRole("button", { name: "Alt" }));
    expect(screen.getByRole("button", { name: "Alt" })).toHaveAttribute("aria-pressed", "true");
    fireEvent.click(screen.getByRole("button", { name: "Key left" }));
    fireEvent.click(screen.getByRole("button", { name: "Key left" }));
    expect(onKey.mock.calls).toEqual([
      ["left", ["alt"]],
      ["left", []],
    ]);
    expect(screen.getByRole("button", { name: "Alt" })).toHaveAttribute("aria-pressed", "false");
  });

  it("typing in the box sends text; Enter and Backspace become keys", async () => {
    const onKey = vi.fn();
    const onText = vi.fn();
    render(<KeyPad onKey={onKey} onText={onText} disabled={false} />);
    const box = screen.getByRole("textbox", { name: "Type on the TV" });
    await userEvent.click(box);
    await userEvent.keyboard("hi");
    expect(onText.mock.calls.map((c) => c[0]).join("")).toBe("hi");
    await userEvent.keyboard("{Backspace}");
    expect(onKey).toHaveBeenCalledWith("backspace", []);
    await userEvent.keyboard("{Enter}");
    expect(onKey).toHaveBeenCalledWith("enter", []);
  });

  it("is inert while disconnected", () => {
    const onKey = vi.fn();
    render(<KeyPad onKey={onKey} onText={vi.fn()} disabled />);
    fireEvent.click(screen.getByRole("button", { name: "Key esc" }));
    expect(onKey).not.toHaveBeenCalled();
  });
});

describe("TouchPad", () => {
  it("tapping the pad clicks; the buttons send explicit clicks and scrolls", () => {
    const onPointer = vi.fn();
    render(<TouchPad onPointer={onPointer} disabled={false} />);
    const pad = screen.getByTestId("touchpad");
    fireEvent.pointerDown(pad, { pointerId: 1, clientX: 10, clientY: 10 });
    fireEvent.pointerUp(pad, { pointerId: 1, clientX: 10, clientY: 10 });
    expect(onPointer).toHaveBeenCalledWith({ action: "click" });
    fireEvent.click(screen.getByRole("button", { name: "Right click" }));
    fireEvent.click(screen.getByRole("button", { name: "Scroll down" }));
    expect(onPointer).toHaveBeenCalledWith({ action: "right_click" });
    expect(onPointer).toHaveBeenCalledWith({ scroll: 3 });
  });

  it("ignores touches while disconnected", () => {
    const onPointer = vi.fn();
    render(<TouchPad onPointer={onPointer} disabled />);
    const pad = screen.getByTestId("touchpad");
    fireEvent.pointerDown(pad, { pointerId: 1, clientX: 10, clientY: 10 });
    fireEvent.pointerUp(pad, { pointerId: 1, clientX: 10, clientY: 10 });
    expect(onPointer).not.toHaveBeenCalled();
  });
});
