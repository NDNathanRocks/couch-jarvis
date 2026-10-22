import { act, fireEvent, render, screen } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { ConnectionBadge, badgeInfo } from "../components/ConnectionBadge";
import { FriendsBar, initials } from "../components/FriendsBar";
import { NowPlayingCard, formatClock } from "../components/NowPlayingCard";
import { QRScanner, readPairParams, stripPairParams } from "../components/QRScanner";
import { DECK, QuickDeck } from "../components/QuickDeck";
import { TimerList, remainingLabel } from "../components/TimerList";
import { TOAST_MS, Toasts } from "../components/Toasts";
import { initialView, roomReducer, type RoomView } from "../state/roomReducer";
import type { Voice } from "../hooks/useVoice";
import { incoming } from "./helpers";

const online = (patch: Partial<RoomView> = {}): RoomView => ({
  ...roomReducer(initialView, { type: "msg", msg: incoming("state.snapshot", { pc_online: true, rev: 1 }) }),
  connection: "online",
  ...patch,
});

describe("QuickDeck", () => {
  it("renders every action with an accessible name and sends the right one", () => {
    const onPress = vi.fn();
    render(<QuickDeck onPress={onPress} disabled={false} />);
    for (const button of DECK) fireEvent.click(screen.getByRole("button", { name: button.label }));
    fireEvent.click(screen.getByRole("button", { name: "Stop everything" }));
    fireEvent.click(screen.getByRole("button", { name: /Pair a friend/ }));
    expect(onPress.mock.calls.map((c) => c[0])).toEqual([...DECK.map((b) => b.action), "panic", "show_qr"]);
  });

  it("is inert while disconnected", () => {
    const onPress = vi.fn();
    render(<QuickDeck onPress={onPress} disabled />);
    fireEvent.click(screen.getByRole("button", { name: "Play / Pause" }));
    expect(onPress).not.toHaveBeenCalled();
    expect(screen.getByRole("button", { name: "Stop everything" })).toBeDisabled();
  });
});

describe("QRScanner", () => {
  afterEach(() => window.history.replaceState(null, "", "/"));

  it("reads valid pairing params and rejects malformed ones", () => {
    expect(readPairParams("?room=room-abcdef123&secret=abcdefghijklmnop")).toEqual({ roomId: "room-abcdef123", secret: "abcdefghijklmnop" });
    expect(readPairParams("")).toBeNull();
    expect(readPairParams("?room=room-abcdef123")).toBeNull();
    expect(readPairParams("?room=short&secret=abcdefghijklmnop")).toBeNull();
    expect(readPairParams("?room=room-abcdef123&secret=has%20spaces%20in%20it!!")).toBeNull();
    expect(readPairParams("?room=%3Cscript%3E&secret=abcdefghijklmnop")).toBeNull();
  });

  it("strips the secret (and room) from the address bar but keeps unrelated params", () => {
    window.history.replaceState(null, "", "/?room=room-abcdef123&secret=abcdefghijklmnop&utm=keep#frag");
    stripPairParams();
    expect(window.location.href).not.toContain("secret");
    expect(window.location.href).not.toContain("abcdefghijklmnop");
    expect(window.location.search).toBe("?utm=keep");
    expect(window.location.hash).toBe("#frag");
  });

  it("strip is a no-op without params", () => {
    const spy = vi.spyOn(window.history, "replaceState");
    stripPairParams();
    expect(spy).not.toHaveBeenCalled();
    spy.mockRestore();
  });

  it("shows the right guidance for each pairing state", () => {
    const { rerender, container } = render(<QRScanner connection="idle" hasPairParams={false} paired={false} />);
    expect(screen.getByText(/point it at the QR code/i)).toBeInTheDocument();
    rerender(<QRScanner connection="connecting" hasPairParams paired={false} />);
    expect(screen.getByText(/Pairing with your TV/)).toBeInTheDocument();
    rerender(<QRScanner connection="unreachable" hasPairParams={false} paired />);
    expect(screen.getByRole("status")).toHaveTextContent(/Can't reach your TV/);
    expect(screen.getByRole("status")).toHaveTextContent(/keep trying/i);
    rerender(<QRScanner connection="rejected" hasPairParams={false} paired />);
    expect(screen.getByRole("alert")).toHaveTextContent(/scan the QR code on the TV again/i);
    rerender(<QRScanner connection="online" hasPairParams={false} paired />);
    expect(container).toBeEmptyDOMElement();
  });
});

describe("ConnectionBadge", () => {
  it.each([
    [{ connection: "idle" as const }, "Not paired"],
    [{ connection: "connecting" as const }, "Connecting…"],
    [{ connection: "offline" as const }, "Reconnecting…"],
    [{ connection: "unreachable" as const }, "Can't connect"],
    [{ connection: "rejected" as const }, "Re-pair needed"],
  ])("%j -> %s", (patch, label) => {
    expect(badgeInfo({ ...initialView, ...patch }, "cloud").label).toBe(label);
  });

  it("distinguishes phone-online from PC-online, and cloud from local", () => {
    expect(badgeInfo(online({ relayPcOnline: false }), "cloud")).toEqual({ label: "PC offline", tone: "bad" });
    expect(badgeInfo(online({ relayPcOnline: true }), "cloud")).toEqual({ label: "Connected", tone: "good" });
    expect(badgeInfo(online(), "lan").label).toBe("Connected · Local");
  });

  it("is announced to assistive tech", () => {
    render(<ConnectionBadge view={online()} transport="cloud" />);
    expect(screen.getByRole("status", { name: "Connection: Connected" })).toBeInTheDocument();
  });
});

describe("TimerList", () => {
  it("formats remaining time and never goes negative", () => {
    expect(remainingLabel(1000 + 65, 1000 * 1000)).toBe("1:05");
    expect(remainingLabel(1000 + 3725, 1000 * 1000)).toBe("1:02:05");
    expect(remainingLabel(900, 1000 * 1000)).toBe("0:00");
  });

  it("counts down every second", () => {
    vi.useFakeTimers();
    let now = 1_000_000;
    const timers = [{ timer_id: "t", label: "Sleep timer", ends_at: now / 1000 + 90, action: "pause" }];
    render(<TimerList timers={timers} now={() => now} />);
    expect(screen.getByText("1:30")).toBeInTheDocument();
    expect(screen.getByText("then pause")).toBeInTheDocument();
    act(() => {
      now += 10_000;
      vi.advanceTimersByTime(1000);
    });
    expect(screen.getByText("1:20")).toBeInTheDocument();
    vi.useRealTimers();
  });

  it("renders nothing without timers", () => {
    const { container } = render(<TimerList timers={[]} />);
    expect(container).toBeEmptyDOMElement();
  });
});

describe("FriendsBar", () => {
  const devices = [
    { device_id: "a", name: "Ann Lee", online: true, last_seen: 1 },
    { device_id: "b", name: "Bob", online: false, last_seen: 1 },
  ];

  it("marks yourself and counts only online friends", () => {
    render(<FriendsBar devices={devices} selfId="a" />);
    expect(screen.getByText("Connected · 1")).toBeInTheDocument();
    expect(screen.getByText("(you)")).toBeInTheDocument();
    expect(screen.getByText("offline", { selector: ".sr-only" })).toBeInTheDocument();
  });

  it("initials", () => {
    expect(initials("Ann Lee")).toBe("AL");
    expect(initials("bob")).toBe("B");
    expect(initials("  ")).toBe("?");
  });
});

describe("NowPlayingCard", () => {
  beforeEach(() => vi.useFakeTimers());
  afterEach(() => vi.useRealTimers());

  it("shows progress and interpolates between PC reports while playing", () => {
    const playing = { title: "Lofi Girl", source: "youtube" as const, is_playing: true, position_s: 60, duration_s: 600 };
    render(<NowPlayingCard playing={playing} pending={null} />);
    expect(screen.getByText("1:00 / 10:00")).toBeInTheDocument();
    act(() => void vi.advanceTimersByTime(5000));
    expect(screen.getByText("1:05 / 10:00")).toBeInTheDocument();
    expect(screen.getByRole("progressbar")).toHaveAttribute("aria-valuenow", "11");
  });

  it("does not advance while paused, and shows the thinking status", () => {
    const paused = { title: "Song", source: "ytmusic" as const, is_playing: false, position_s: 10, duration_s: 100 };
    render(<NowPlayingCard playing={paused} pending="Checking..." />);
    act(() => void vi.advanceTimersByTime(5000));
    expect(screen.getByText("0:10 / 1:40")).toBeInTheDocument();
    expect(screen.getByText("Checking...")).toBeInTheDocument();
    expect(screen.getByText(/YouTube Music · Paused/)).toBeInTheDocument();
  });

  it("empty state", () => {
    render(<NowPlayingCard playing={null} pending={null} />);
    expect(screen.getByText("Nothing playing")).toBeInTheDocument();
  });

  it("formatClock", () => {
    expect(formatClock(0)).toBe("0:00");
    expect(formatClock(3599)).toBe("59:59");
    expect(formatClock(-4)).toBe("0:00");
  });
});

describe("Toasts", () => {
  beforeEach(() => vi.useFakeTimers());
  afterEach(() => vi.useRealTimers());

  it("auto-dismisses after a few seconds and on tap", () => {
    const onDismiss = vi.fn();
    const toasts = [
      { id: "a", kind: "ok" as const, text: "Playing lofi", mine: true },
      { id: "b", kind: "error" as const, text: "Failed", mine: false },
    ];
    render(<Toasts toasts={toasts} onDismiss={onDismiss} />);
    fireEvent.click(screen.getByText("Failed"));
    expect(onDismiss).toHaveBeenCalledWith("b");
    act(() => void vi.advanceTimersByTime(TOAST_MS));
    expect(onDismiss).toHaveBeenCalledWith("a");
  });
});

describe("CommandBar", () => {
  const voice = (over: Partial<Voice> = {}): Voice => ({
    supported: true,
    active: false,
    elapsedMs: 0,
    toggle: vi.fn(),
    cancel: vi.fn(),
    holdStart: vi.fn(),
    holdEnd: vi.fn(),
    ...over,
  });

  it("stays editable while offline; only sending is blocked, and the draft is kept", async () => {
    const { CommandBar } = await import("../components/CommandBar");
    const onSend = vi.fn(() => true);
    render(<CommandBar disabled voice={voice()} micMode="tap" onSend={onSend} />);
    const input = screen.getByLabelText("Type a command");
    expect(input).toBeEnabled();
    fireEvent.change(input, { target: { value: "play lofi" } });
    expect(input).toHaveValue("play lofi");
    expect(screen.getByRole("button", { name: "Send" })).toBeDisabled();
    fireEvent.submit(input.closest("form") as HTMLFormElement); // enter key while offline still routes to onSend
    expect(onSend).toHaveBeenCalledWith("play lofi");
  });

  it("shows the mic when there is nothing typed, and tapping it toggles recording", async () => {
    const { CommandBar } = await import("../components/CommandBar");
    const v = voice();
    render(<CommandBar disabled={false} voice={v} micMode="tap" onSend={vi.fn()} />);
    expect(screen.queryByRole("button", { name: "Send" })).toBeNull();
    fireEvent.click(screen.getByRole("button", { name: "Tap to talk" }));
    expect(v.toggle).toHaveBeenCalledTimes(1);
  });

  it("while listening it is a compact panel with a timer, Cancel and Tap to send", async () => {
    const { CommandBar } = await import("../components/CommandBar");
    const v = voice({ active: true, elapsedMs: 4200 });
    render(<CommandBar disabled={false} voice={v} micMode="tap" onSend={vi.fn()} />);
    const panel = screen.getByRole("group", { name: "Listening" });
    expect(panel).toHaveTextContent("0:04");
    fireEvent.click(screen.getByRole("button", { name: "Cancel" }));
    fireEvent.click(screen.getByRole("button", { name: "Send recording" }));
    expect(v.cancel).toHaveBeenCalledTimes(1);
    expect(v.toggle).toHaveBeenCalledTimes(1);
  });

  it("hold mode keeps the old press-and-release behaviour", async () => {
    const { CommandBar } = await import("../components/CommandBar");
    const v = voice();
    render(<CommandBar disabled={false} voice={v} micMode="hold" onSend={vi.fn()} />);
    const mic = screen.getByRole("button", { name: "Hold to talk" });
    fireEvent.pointerDown(mic);
    fireEvent.pointerUp(mic);
    expect(v.holdStart).toHaveBeenCalledTimes(1);
    expect(v.holdEnd).toHaveBeenCalledTimes(1);
    expect(v.toggle).not.toHaveBeenCalled();
  });

  it("is inert when voice is unsupported (plain http) but typing works", async () => {
    const { CommandBar } = await import("../components/CommandBar");
    render(<CommandBar disabled={false} voice={voice({ supported: false })} micMode="tap" onSend={vi.fn()} />);
    expect(screen.getByRole("button", { name: "Tap to talk" })).toBeDisabled();
    expect(screen.getByLabelText("Type a command")).toBeEnabled();
  });
});

describe("suggestion toast", () => {
  it("offers a one-tap follow-up that sends the suggested command", async () => {
    const { SUGGEST_TOAST_MS } = await import("../components/Toasts");
    const onSuggest = vi.fn();
    const onDismiss = vi.fn();
    const toast = {
      id: "s1",
      kind: "error" as const,
      text: "I couldn't find a search bar on this page.",
      mine: true,
      suggest: { label: "Open a new tab and search YouTube", text: "search for big bang theory on youtube in a new tab" },
    };
    render(<Toasts toasts={[toast]} onDismiss={onDismiss} onSuggest={onSuggest} />);
    expect(screen.getByText(/couldn't find a search bar/)).toBeInTheDocument();
    fireEvent.click(screen.getByRole("button", { name: "Open a new tab and search YouTube" }));
    expect(onSuggest).toHaveBeenCalledWith("search for big bang theory on youtube in a new tab");
    expect(onDismiss).toHaveBeenCalledWith("s1");
    expect(SUGGEST_TOAST_MS).toBeGreaterThan(TOAST_MS);
  });

  it("Not now just dismisses it", () => {
    const onSuggest = vi.fn();
    const onDismiss = vi.fn();
    const toast = { id: "s2", kind: "error" as const, text: "No bar", mine: true, suggest: { label: "Do it", text: "x" } };
    render(<Toasts toasts={[toast]} onDismiss={onDismiss} onSuggest={onSuggest} />);
    fireEvent.click(screen.getByRole("button", { name: "Not now" }));
    expect(onDismiss).toHaveBeenCalledWith("s2");
    expect(onSuggest).not.toHaveBeenCalled();
  });
});
