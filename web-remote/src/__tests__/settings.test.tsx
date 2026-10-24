import { act, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import App from "../App";
import { emptyRoom, normalizeRoom } from "../state/roomReducer";
import { FakeSocket, incoming } from "./helpers";

beforeEach(() => {
  FakeSocket.reset();
  localStorage.clear();
  vi.stubGlobal("WebSocket", FakeSocket);
  localStorage.setItem(
    "cj.pairing",
    JSON.stringify({ roomId: "room-abcdef123", deviceId: "dev-1", deviceToken: "t", expiresAt: 2_000_000_000, deviceName: "Pixel" }),
  );
});

afterEach(() => {
  vi.unstubAllGlobals();
});

async function openSettings(voice?: Record<string, unknown>) {
  render(<App />);
  await waitFor(() => expect(FakeSocket.instances.length).toBe(1));
  await act(async () => FakeSocket.last.open());
  await act(async () => FakeSocket.last.receive(incoming("state.snapshot", { pc_online: true, rev: 1, ...(voice ? { voice } : {}) })));
  fireEvent.click(await screen.findByRole("button", { name: "Settings" }));
  return FakeSocket.last;
}

const texts = (sock: FakeSocket) =>
  sock.sent.filter((m) => m["type"] === "cmd.text").map((m) => (m["payload"] as { text: string }).text);

describe("voice reply settings", () => {
  it("shows the choices the TV reports and sends them as ordinary commands", async () => {
    const sock = await openSettings({ enabled: true, style: "friendly", voice: "us_female", buttons: false, engine: "edge" });
    expect(screen.getByRole("switch", { name: "Voice replies" })).toBeChecked();
    expect(screen.getByRole("button", { name: "Friendly" })).toHaveAttribute("aria-pressed", "true");
    expect(screen.getByRole("button", { name: /Aria/ })).toHaveAttribute("aria-pressed", "true");
    expect(screen.getByText(/Speaking with Microsoft neural voices/)).toBeInTheDocument();

    fireEvent.click(screen.getByRole("button", { name: "Brief" }));
    fireEvent.click(screen.getByRole("button", { name: /Friday/ }));
    fireEvent.click(screen.getByRole("switch", { name: "Speak after button taps" }));
    fireEvent.click(screen.getByRole("button", { name: "Hear it" }));
    fireEvent.click(screen.getByRole("switch", { name: "Voice replies" }));
    expect(texts(sock)).toEqual([
      "set the style to brief",
      "use the british female voice",
      "speak for button taps",
      "test the voice",
      "turn voice replies off",
    ]);
  });

  it("is on by default before the TV has said anything, and hides the details when off", async () => {
    await openSettings();
    expect(screen.getByRole("switch", { name: "Voice replies" })).toBeChecked();
    expect(screen.getByRole("button", { name: "Jarvis" })).toHaveAttribute("aria-pressed", "true");
    // the TV turns it off (e.g. someone said "stop talking"): every phone follows
    await act(async () =>
      FakeSocket.last.receive(incoming("state.patch", { rev: 2, voice: { enabled: false, style: "jarvis", voice: "british_male", buttons: false, engine: "none" } })),
    );
    expect(screen.getByRole("switch", { name: "Voice replies" })).not.toBeChecked();
    expect(screen.queryByRole("button", { name: "Hear it" })).not.toBeInTheDocument();
  });

  it("changes the mic mode and closes with Escape", async () => {
    await openSettings();
    fireEvent.click(screen.getByRole("button", { name: "Hold to talk" }));
    expect(localStorage.getItem("cj.micMode")).toBe("hold");
    fireEvent.keyDown(window, { key: "Escape" });
    expect(screen.queryByRole("dialog", { name: "Settings" })).not.toBeInTheDocument();
  });

  it("is not available before pairing", () => {
    localStorage.clear();
    render(<App />);
    expect(screen.queryByRole("button", { name: "Settings" })).not.toBeInTheDocument();
  });
});

describe("room state from an older PC", () => {
  it("fills in voice defaults when the PC does not send them", () => {
    expect(normalizeRoom({ volume: 10 }).voice).toEqual(emptyRoom.voice);
    expect(normalizeRoom({ voice: { enabled: false } as never }).voice).toMatchObject({ enabled: false, style: "jarvis" });
  });
});

describe("suggestions", () => {
  it("offers ideas while nothing plays and remembers what you typed", async () => {
    render(<App />);
    await waitFor(() => expect(FakeSocket.instances.length).toBe(1));
    await act(async () => FakeSocket.last.open());
    await act(async () => FakeSocket.last.receive(incoming("state.snapshot", { pc_online: true, rev: 1 })));
    fireEvent.click(await screen.findByRole("button", { name: "Turn on subtitles" }));
    expect(texts(FakeSocket.last)).toEqual(["Turn on subtitles"]);

    fireEvent.change(screen.getByLabelText("Type a command"), { target: { value: "go fullscreen" } });
    fireEvent.submit(screen.getByLabelText("Type a command").closest("form")!);
    expect(JSON.parse(localStorage.getItem("cj.recentCommands") ?? "[]")).toEqual(["go fullscreen"]);
  });
});

describe("ad blocking setting", () => {
  it("shows the PC's choice and flips it with an ordinary command", async () => {
    const sock = await openSettings();
    const toggle = screen.getByRole("switch", { name: "Block ads and popups" });
    expect(toggle).toBeChecked(); // on until the PC says otherwise
    fireEvent.click(toggle);
    expect(texts(sock)).toEqual(["turn off ad blocking"]);
    await act(async () => sock.receive(incoming("state.patch", { browser: { adblock: false }, rev: 2 })));
    expect(screen.getByRole("switch", { name: "Block ads and popups" })).not.toBeChecked();
    fireEvent.click(screen.getByRole("switch", { name: "Block ads and popups" }));
    expect(texts(sock)).toEqual(["turn off ad blocking", "turn on ad blocking"]);
  });
});
