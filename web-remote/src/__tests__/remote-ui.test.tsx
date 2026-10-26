import { act, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import App from "../App";
import { Favorites } from "../components/Favorites";
import { NowPlayingCard } from "../components/NowPlayingCard";
import { Slider } from "../components/Slider";
import { VolumeSlider } from "../components/VolumeSlider";
import { DEFAULT_FAVORITES, LIBRARY, SLOTS, assign, blockedReason, comboLabel, loadCustomKeys, loadFavorites, saveFavorites } from "../utils/favorites";
import { resetAppCache } from "../utils/resetCache";
import { createThrottle } from "../utils/throttle";
import { versionAtLeast } from "../utils/version";
import { FakeSocket, incoming } from "./helpers";

/** jsdom has no layout: give the slider track a 200 px box starting at x = 0. */
function sizeTrack(el: HTMLElement): void {
  el.getBoundingClientRect = () => ({ left: 0, top: 0, right: 200, bottom: 40, width: 200, height: 40, x: 0, y: 0, toJSON: () => ({}) });
  el.setPointerCapture = () => undefined;
}

describe("Slider", () => {
  it("follows the finger anywhere on the bar and commits on release", () => {
    const onChange = vi.fn();
    const onCommit = vi.fn();
    render(<Slider label="Volume" value={0} max={100} onChange={onChange} onCommit={onCommit} />);
    const bar = screen.getByRole("slider", { name: "Volume" });
    sizeTrack(bar);
    fireEvent.pointerDown(bar, { clientX: 50, pointerId: 1 });
    fireEvent.pointerMove(bar, { clientX: 150, pointerId: 1 });
    expect(onChange).toHaveBeenLastCalledWith(75);
    expect(onCommit).not.toHaveBeenCalled();
    fireEvent.pointerUp(bar, { clientX: 150, pointerId: 1 });
    expect(onCommit).toHaveBeenCalledExactlyOnceWith(75);
  });

  it("clamps to the ends of the bar", () => {
    const onChange = vi.fn();
    render(<Slider label="S" value={0} max={100} onChange={onChange} onCommit={() => undefined} />);
    const bar = screen.getByRole("slider");
    sizeTrack(bar);
    fireEvent.pointerDown(bar, { clientX: -80, pointerId: 1 });
    expect(onChange).toHaveBeenLastCalledWith(0);
    fireEvent.pointerMove(bar, { clientX: 900, pointerId: 1 });
    expect(onChange).toHaveBeenLastCalledWith(100);
  });

  it("works from the keyboard and exposes its value to screen readers", () => {
    const onCommit = vi.fn();
    render(<Slider label="Volume" value={40} max={100} step={5} valueText="40 percent" onChange={() => undefined} onCommit={onCommit} />);
    const bar = screen.getByRole("slider");
    expect(bar).toHaveAttribute("aria-valuenow", "40");
    expect(bar).toHaveAttribute("aria-valuetext", "40 percent");
    fireEvent.keyDown(bar, { key: "ArrowRight" });
    expect(onCommit).toHaveBeenLastCalledWith(45);
    fireEvent.keyDown(bar, { key: "End" });
    expect(onCommit).toHaveBeenLastCalledWith(100);
  });

  it("ignores touches while disabled", () => {
    const onChange = vi.fn();
    render(<Slider label="S" value={0} max={100} disabled onChange={onChange} onCommit={() => undefined} />);
    const bar = screen.getByRole("slider");
    sizeTrack(bar);
    fireEvent.pointerDown(bar, { clientX: 100, pointerId: 1 });
    expect(onChange).not.toHaveBeenCalled();
    expect(bar).toHaveAttribute("aria-disabled", "true");
  });
});

describe("VolumeSlider", () => {
  beforeEach(() => vi.useFakeTimers());
  afterEach(() => vi.useRealTimers());

  it("sends the level while dragging (throttled) and the final one on release", () => {
    const onLevel = vi.fn();
    render(<VolumeSlider level={20} muted={false} disabled={false} onLevel={onLevel} onToggleMute={() => undefined} />);
    const bar = screen.getByRole("slider", { name: "Volume" });
    sizeTrack(bar);
    fireEvent.pointerDown(bar, { clientX: 20, pointerId: 1 }); // 10 %
    for (let x = 30; x <= 160; x += 10) fireEvent.pointerMove(bar, { clientX: x, pointerId: 1 });
    expect(onLevel.mock.calls.length).toBeLessThan(4); // a fast drag is not one message per pixel
    fireEvent.pointerUp(bar, { clientX: 160, pointerId: 1 });
    expect(onLevel).toHaveBeenLastCalledWith(80);
  });

  it("keeps your value over the PC's older report for a moment, then follows the PC", () => {
    const { rerender } = render(<VolumeSlider level={20} muted={false} disabled={false} onLevel={() => undefined} onToggleMute={() => undefined} />);
    const bar = screen.getByRole("slider");
    sizeTrack(bar);
    fireEvent.pointerDown(bar, { clientX: 100, pointerId: 1 });
    fireEvent.pointerUp(bar, { clientX: 100, pointerId: 1 });
    rerender(<VolumeSlider level={20} muted={false} disabled={false} onLevel={() => undefined} onToggleMute={() => undefined} />);
    expect(screen.getByRole("slider")).toHaveAttribute("aria-valuenow", "50");
    act(() => void vi.advanceTimersByTime(2000));
    expect(screen.getByRole("slider")).toHaveAttribute("aria-valuenow", "20");
  });

  it("the speaker button mutes, and a muted PC shows zero", () => {
    const onToggleMute = vi.fn();
    render(<VolumeSlider level={60} muted disabled={false} onLevel={() => undefined} onToggleMute={onToggleMute} />);
    expect(screen.getByRole("slider")).toHaveAttribute("aria-valuetext", expect.stringContaining("muted"));
    fireEvent.click(screen.getByRole("button", { name: "Mute" }));
    expect(onToggleMute).toHaveBeenCalledOnce();
  });
});

describe("seek slider on the Now Playing card", () => {
  const playing = { title: "Long film", source: "youtube" as const, is_playing: true, position_s: 100, duration_s: 1000 };

  it("scrubs to a time and sends it on release", () => {
    const onSeek = vi.fn();
    render(<NowPlayingCard playing={playing} pending={null} onSeek={onSeek} />);
    const bar = screen.getByRole("slider", { name: "Seek" });
    sizeTrack(bar);
    fireEvent.pointerDown(bar, { clientX: 100, pointerId: 1 }); // halfway = 500 s
    fireEvent.pointerUp(bar, { clientX: 100, pointerId: 1 });
    expect(onSeek).toHaveBeenLastCalledWith(500);
    expect(bar).toHaveAttribute("aria-valuetext", "8:20 of 16:40");
  });

  it("a drag survives the PC reporting a new position mid-drag (each seek makes it report)", () => {
    const onSeek = vi.fn();
    const { rerender } = render(<NowPlayingCard playing={playing} pending={null} onSeek={onSeek} />);
    const bar = screen.getByRole("slider", { name: "Seek" });
    sizeTrack(bar);
    fireEvent.pointerDown(bar, { clientX: 40, pointerId: 1 });
    rerender(<NowPlayingCard playing={{ ...playing, position_s: 200 }} pending={null} onSeek={onSeek} />);
    const still = screen.getByRole("slider", { name: "Seek" });
    expect(still).toBe(bar); // the same element: the drag was not cut short
    fireEvent.pointerMove(still, { clientX: 150, pointerId: 1 });
    fireEvent.pointerUp(still, { clientX: 150, pointerId: 1 });
    expect(onSeek).toHaveBeenLastCalledWith(750);
  });

  it("is a plain progress bar when the PC cannot seek or the length is unknown", () => {
    const { rerender } = render(<NowPlayingCard playing={playing} pending={null} />);
    expect(screen.queryByRole("slider")).not.toBeInTheDocument();
    expect(screen.getByRole("progressbar")).toBeInTheDocument();
    rerender(<NowPlayingCard playing={{ ...playing, duration_s: 0 }} pending={null} onSeek={() => undefined} />);
    expect(screen.queryByRole("slider")).not.toBeInTheDocument();
  });
});

describe("favourites", () => {
  it("the library has unique ids and the defaults all exist", () => {
    const ids = LIBRARY.map((f) => f.id);
    expect(new Set(ids).size).toBe(ids.length);
    expect(DEFAULT_FAVORITES).toHaveLength(SLOTS);
    for (const id of DEFAULT_FAVORITES) expect(ids).toContain(id);
  });

  it("assign() never puts a button in two slots", () => {
    const next = assign(["a", "b", null], 2, "a");
    expect(next).toEqual([null, "b", "a"]);
  });

  it("saves and restores, and recovers from corrupt storage", () => {
    saveFavorites(["mute", null, "fullscreen"]);
    expect(loadFavorites().slice(0, 3)).toEqual(["mute", null, "fullscreen"]);
    expect(loadFavorites()).toHaveLength(SLOTS);
    localStorage.setItem("cj.favorites.v1", "{not json");
    expect(loadFavorites()).toEqual([...DEFAULT_FAVORITES]);
    localStorage.setItem("cj.favorites.v1", JSON.stringify(["nope", "mute"]));
    expect(loadFavorites().slice(0, 2)).toEqual([null, "mute"]); // unknown ids are dropped
  });

  it("shows 8 slots, sends the pressed one and edits through the picker", () => {
    const onSend = vi.fn();
    const onChange = vi.fn();
    render(<Favorites daemonVersion="0.4.0" slots={[...DEFAULT_FAVORITES]} connected keysAllowed onSend={onSend} onChange={onChange} />);
    fireEvent.click(screen.getByRole("button", { name: "Mute" }));
    expect(onSend).toHaveBeenCalledWith(expect.objectContaining({ id: "mute" }));

    fireEvent.click(screen.getByRole("button", { name: "Edit" }));
    fireEvent.click(screen.getByRole("button", { name: "Change Mute" }));
    expect(onSend).toHaveBeenCalledTimes(1); // editing does not press anything
    fireEvent.click(screen.getByRole("button", { name: "Reload" }));
    expect(onChange).toHaveBeenCalledWith(["fullscreen", "subtitles", "reload", "skip_ad", "back", "new_tab", "panic", "sleep_display"]);
  });

  it("an empty slot offers the picker, and Remove empties a slot", () => {
    const onChange = vi.fn();
    render(<Favorites daemonVersion="0.4.0" slots={["mute", null, null, null, null, null, null, null]} connected keysAllowed onSend={() => undefined} onChange={onChange} />);
    fireEvent.click(screen.getByRole("button", { name: "Add a favourite to slot 2" }));
    expect(screen.getByRole("dialog", { name: "Choose a favourite" })).toBeInTheDocument();
    fireEvent.click(screen.getByRole("button", { name: "F5" }));
    expect(onChange).toHaveBeenCalledWith(["mute", "key_f5", null, null, null, null, null, null]);

    fireEvent.click(screen.getByRole("button", { name: "Change Mute" }));
    fireEvent.click(screen.getByRole("button", { name: /Remove/ }));
    expect(onChange).toHaveBeenLastCalledWith([null, null, null, null, null, null, null, null]);
  });

  it("disables everything when disconnected, and key buttons when remote input is off", () => {
    const { rerender } = render(<Favorites daemonVersion="0.4.0" slots={["mute", "key_f11"]} connected={false} keysAllowed onSend={() => undefined} onChange={() => undefined} />);
    expect(screen.getByRole("button", { name: "Mute" })).toBeDisabled();
    rerender(<Favorites daemonVersion="0.4.0" slots={["mute", "key_f11"]} connected keysAllowed={false} onSend={() => undefined} onChange={() => undefined} />);
    expect(screen.getByRole("button", { name: "Mute" })).toBeEnabled();
    expect(screen.getByRole("button", { name: "Full screen (F11)" })).toBeDisabled();
  });

  it("builds a keyboard shortcut button, presses it, keeps it after a reload, and deletes it", () => {
    const onSend = vi.fn();
    const onChange = vi.fn();
    const slots = ["mute", null, null, null, null, null, null, null];
    const { unmount } = render(<Favorites daemonVersion="0.4.0" slots={slots} connected keysAllowed onSend={onSend} onChange={onChange} />);
    fireEvent.click(screen.getByRole("button", { name: "Add a favourite to slot 2" }));
    fireEvent.click(screen.getByRole("button", { name: /New shortcut/ }));
    fireEvent.click(screen.getByRole("button", { name: "Ctrl" }));
    fireEvent.click(screen.getByRole("button", { name: "Shift" }));
    fireEvent.change(screen.getByRole("combobox", { name: "Key" }), { target: { value: "t" } });
    expect(screen.getByText("Ctrl+Shift+T")).toBeInTheDocument();
    fireEvent.change(screen.getByRole("textbox", { name: /Name/ }), { target: { value: "Reopen tab" } });
    fireEvent.click(screen.getByRole("button", { name: "Add shortcut" }));

    const made = loadCustomKeys();
    expect(made).toEqual([expect.objectContaining({ label: "Reopen tab", key: "t", mods: ["ctrl", "shift"] })]);
    const id = made[0]!.id;
    expect(onChange).toHaveBeenCalledWith(["mute", id, null, null, null, null, null, null]);
    saveFavorites(["mute", id]);
    expect(loadFavorites().slice(0, 2)).toEqual(["mute", id]); // a custom id survives a reload
    unmount();

    render(<Favorites daemonVersion="0.4.0" slots={["mute", id]} connected keysAllowed onSend={onSend} onChange={onChange} />);
    fireEvent.click(screen.getByRole("button", { name: "Reopen tab" }));
    expect(onSend).toHaveBeenCalledWith(expect.objectContaining({ send: { kind: "key", key: "t", mods: ["ctrl", "shift"] } }));

    fireEvent.click(screen.getByRole("button", { name: "Edit" }));
    fireEvent.click(screen.getByRole("button", { name: "Change Reopen tab" }));
    fireEvent.click(screen.getByRole("button", { name: "Delete Reopen tab" }));
    expect(loadCustomKeys()).toEqual([]);
    expect(onChange).toHaveBeenLastCalledWith(["mute", null]);
  });

  it("refuses shortcuts the PC blocks", () => {
    expect(blockedReason("f4", ["alt"])).toMatch(/Alt\+F4/);
    expect(blockedReason("delete", ["ctrl", "alt"])).toMatch(/Ctrl\+Alt/);
    expect(blockedReason("esc", ["ctrl"])).not.toBeNull();
    expect(blockedReason("t", ["ctrl", "shift"])).toBeNull();
    expect(comboLabel("left", ["alt"])).toBe("Alt+←");
  });

  it("hides buttons an older PC cannot do, and greys them if already placed", () => {
    render(<Favorites daemonVersion="0.3.0" slots={["exit_fullscreen", null]} connected keysAllowed onSend={() => undefined} onChange={() => undefined} />);
    expect(screen.getByRole("button", { name: "Exit fullscreen" })).toBeDisabled();
    fireEvent.click(screen.getByRole("button", { name: "Add a favourite to slot 2" }));
    expect(screen.queryByRole("button", { name: "Duplicate tab" })).not.toBeInTheDocument();
    expect(screen.getByRole("button", { name: "New tab" })).toBeInTheDocument();
  });
});

describe("helpers", () => {
  it("versionAtLeast compares numbers, not strings", () => {
    expect(versionAtLeast("0.3.0", "0.3.0")).toBe(true);
    expect(versionAtLeast("0.10.1", "0.3.0")).toBe(true);
    expect(versionAtLeast("0.2.9", "0.3.0")).toBe(false);
    expect(versionAtLeast("1.0.0", "0.3.0")).toBe(true);
    expect(versionAtLeast("", "0.3.0")).toBe(false); // a PC too old to report a version
    expect(versionAtLeast("junk", "0.3.0")).toBe(false);
  });

  it("throttle sends the newest value at most once per interval, and flush sends at once", () => {
    vi.useFakeTimers();
    const send = vi.fn();
    const t = createThrottle(send, 100);
    t.push(1);
    t.push(2);
    t.push(3);
    expect(send).toHaveBeenCalledTimes(1);
    act(() => void vi.advanceTimersByTime(100));
    expect(send).toHaveBeenLastCalledWith(3);
    t.push(4);
    t.flush(9);
    expect(send).toHaveBeenLastCalledWith(9);
    act(() => void vi.advanceTimersByTime(500));
    expect(send).toHaveBeenCalledTimes(3);
    vi.useRealTimers();
  });
});

describe("reset app cache", () => {
  it("clears service workers and caches, keeps pairing and favourites, then reloads", async () => {
    localStorage.setItem("cj.pairing", "keep-me");
    saveFavorites(["mute"]);
    localStorage.setItem("cj.recentCommands", '["x"]');
    const unregister = vi.fn().mockResolvedValue(true);
    const del = vi.fn().mockResolvedValue(true);
    const reload = vi.fn();
    await resetAppCache({
      serviceWorker: { getRegistrations: async () => [{ unregister }, { unregister }] },
      caches: { keys: async () => ["a", "b", "c"], delete: del },
      reload,
    });
    expect(unregister).toHaveBeenCalledTimes(2);
    expect(del).toHaveBeenCalledTimes(3);
    expect(localStorage.getItem("cj.pairing")).toBe("keep-me");
    expect(loadFavorites()[0]).toBe("mute");
    expect(localStorage.getItem("cj.recentCommands")).toBeNull();
    expect(reload).toHaveBeenCalledOnce();
  });

  it("still reloads when the browser has no service worker or Cache Storage", async () => {
    const reload = vi.fn();
    await resetAppCache({ reload });
    expect(reload).toHaveBeenCalledOnce();
  });

  it("still reloads when a step throws", async () => {
    const reload = vi.fn();
    await resetAppCache({
      serviceWorker: { getRegistrations: async () => Promise.reject(new Error("blocked")) },
      caches: { keys: async () => Promise.reject(new Error("blocked")), delete: async () => true },
      reload,
    });
    expect(reload).toHaveBeenCalledOnce();
  });
});

describe("the remote page", () => {
  beforeEach(() => {
    FakeSocket.reset();
    vi.stubGlobal("WebSocket", FakeSocket);
    window.history.replaceState(null, "", "/?room=room-abcdef123&secret=qr-secret-value-123456");
  });
  afterEach(() => {
    vi.unstubAllGlobals();
    window.history.replaceState(null, "", "/");
  });

  async function pairedRemote(daemonVersion: string | undefined) {
    render(<App />);
    await waitFor(() => expect(FakeSocket.instances.length).toBe(1));
    const sock = FakeSocket.last;
    await act(async () => sock.open());
    await act(async () => {
      sock.receive(incoming("pair.granted", { device_id: "dev-7", device_token: "the.jwt.token", expires_at: 2_000_000_000 }));
    });
    await act(async () => {
      sock.receive(
        incoming("state.snapshot", {
          pc_online: true,
          mode: "lan",
          volume: 42,
          muted: false,
          rev: 3,
          ...(daemonVersion ? { daemon_version: daemonVersion } : {}),
          now_playing: { title: "Lofi Beats", source: "youtube", is_playing: true, position_s: 5, duration_s: 100 },
          devices: [{ device_id: "dev-7", name: "Pixel", online: true, last_seen: 1 }],
        }),
      );
    });
    await screen.findByRole("button", { name: "Play / Pause" });
    return sock;
  }

  it("lays out the page: seek, transport, volume, favourites, then the trackpad", async () => {
    await pairedRemote("0.3.0");
    const order = ["Now playing", "Transport", "Volume", "Favourites", "Touchpad"].map((name) => {
      const el = screen.getAllByLabelText(name, { selector: "section, div" })[0]!;
      return el;
    });
    for (let i = 1; i < order.length; i++) {
      expect(order[i - 1]!.compareDocumentPosition(order[i]!) & Node.DOCUMENT_POSITION_FOLLOWING).toBeTruthy();
    }
    expect(screen.getByRole("slider", { name: "Seek" })).toBeInTheDocument();
    expect(screen.getByRole("slider", { name: "Volume" })).toHaveAttribute("aria-valuenow", "42");
    expect(screen.getAllByRole("button", { name: /Add a favourite|Mute|Fullscreen/ }).length).toBeGreaterThan(0);
  });

  it("sends a slider value as a button command with a number, without tracking it as a task", async () => {
    const sock = await pairedRemote("0.3.0");
    const bar = screen.getByRole("slider", { name: "Volume" });
    sizeTrack(bar);
    sock.sent.length = 0;
    fireEvent.pointerDown(bar, { clientX: 150, pointerId: 1 });
    fireEvent.pointerUp(bar, { clientX: 150, pointerId: 1 });
    const volume = sock.sent.filter((m) => m["type"] === "cmd.button");
    expect(volume.at(-1)).toMatchObject({ payload: { action: "volume_set", args: { level: 75 } } });
  });

  it("falls back to buttons when the PC app is older than the sliders", async () => {
    await pairedRemote(undefined);
    expect(screen.queryByRole("slider", { name: "Volume" })).not.toBeInTheDocument();
    expect(screen.queryByRole("slider", { name: "Seek" })).not.toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Volume up" })).toBeInTheDocument();
    expect(screen.getByRole("progressbar")).toBeInTheDocument();
  });

  it("the Keys tab holds the keyboard, and the trackpad lives on the Remote page", async () => {
    await pairedRemote("0.3.0");
    expect(screen.getByTestId("touchpad")).toBeInTheDocument();
    fireEvent.click(screen.getByRole("tab", { name: "Touchpad & keys" }));
    expect(await screen.findByRole("group", { name: "Function keys" })).toBeInTheDocument();
    expect(screen.queryByTestId("touchpad")).not.toBeInTheDocument();
  });

  it("Reset app cache is in Settings and asks first", async () => {
    await pairedRemote("0.3.0");
    fireEvent.click(screen.getByRole("button", { name: "Settings" }));
    fireEvent.click(screen.getByRole("button", { name: "Reset app cache" }));
    expect(screen.getByRole("button", { name: /Yes, reset and reload/ })).toBeInTheDocument();
    fireEvent.click(screen.getByRole("button", { name: "Cancel" }));
    expect(screen.getByRole("button", { name: "Reset app cache" })).toBeInTheDocument();
    expect(screen.getByText(/PC app 0\.3\.0/)).toBeInTheDocument();
  });

  it("announces a newer release, and the notice can be dismissed", async () => {
    render(<App />);
    await waitFor(() => expect(FakeSocket.instances.length).toBe(1));
    const sock = FakeSocket.last;
    await act(async () => sock.open());
    await act(async () => {
      sock.receive(incoming("pair.granted", { device_id: "dev-7", device_token: "t", expires_at: 2_000_000_000 }));
    });
    await act(async () => {
      sock.receive(incoming("state.snapshot", { pc_online: true, rev: 1, daemon_version: "0.3.0", update_available: "0.4.0" }));
    });
    expect(await screen.findByText("0.4.0")).toBeInTheDocument();
    expect(screen.getByRole("link", { name: "Get it" })).toHaveAttribute("rel", expect.stringContaining("noopener"));
    fireEvent.click(screen.getByRole("button", { name: "Dismiss update notice" }));
    expect(screen.queryByText("0.4.0")).not.toBeInTheDocument();
  });

  it("shows trial days left, says when the trial has ended, and Buy asks the PC", async () => {
    render(<App />);
    await waitFor(() => expect(FakeSocket.instances.length).toBe(1));
    const sock = FakeSocket.last;
    await act(async () => sock.open());
    await act(async () => {
      sock.receive(incoming("pair.granted", { device_id: "dev-7", device_token: "t", expires_at: 2_000_000_000 }));
    });
    await act(async () => {
      sock.receive(incoming("state.snapshot", { pc_online: true, rev: 1, daemon_version: "1.0.0", license: { status: "trial", days_left: 3 } }));
    });
    expect(await screen.findByText(/Free trial:/)).toHaveTextContent("Free trial: 3 days left.");
    await act(async () => {
      sock.receive(incoming("state.patch", { rev: 2, license: { status: "expired", days_left: 0 } }));
    });
    expect(screen.getByRole("alert")).toHaveTextContent("trial has ended");
    fireEvent.click(screen.getByRole("button", { name: "Buy" }));
    expect(sock.sent.at(-1)).toMatchObject({ type: "cmd.button", payload: { action: "buy" } });
    await act(async () => {
      sock.receive(incoming("state.patch", { rev: 3, license: null })); // bought
    });
    expect(screen.queryByRole("button", { name: "Buy" })).not.toBeInTheDocument();
  });

  it("offers the Apps row to a 1.0.0 PC only, and a tap opens the site on the TV", async () => {
    render(<App />);
    await waitFor(() => expect(FakeSocket.instances.length).toBe(1));
    const sock = FakeSocket.last;
    await act(async () => sock.open());
    await act(async () => {
      sock.receive(incoming("pair.granted", { device_id: "dev-7", device_token: "t", expires_at: 2_000_000_000 }));
    });
    await act(async () => {
      sock.receive(incoming("state.snapshot", { pc_online: true, rev: 1, daemon_version: "0.3.0" }));
    });
    await screen.findByRole("tablist");
    expect(screen.queryByRole("button", { name: "Open Netflix" })).not.toBeInTheDocument();
    await act(async () => {
      sock.receive(incoming("state.patch", { rev: 2, daemon_version: "1.0.0" }));
    });
    fireEvent.click(screen.getByRole("button", { name: "Open Netflix" }));
    expect(sock.sent.at(-1)).toMatchObject({ type: "cmd.text", payload: { text: "open netflix" } });
  });

  it("a guest phone gets no touchpad or keyboard, and is told why", async () => {
    render(<App />);
    await waitFor(() => expect(FakeSocket.instances.length).toBe(1));
    const sock = FakeSocket.last;
    await act(async () => sock.open());
    await act(async () => {
      sock.receive(incoming("pair.granted", { device_id: "dev-7", device_token: "t", expires_at: 2_000_000_000 }));
    });
    const me = { device_id: "dev-7", name: "Me", online: true, last_seen: 1 };
    await act(async () => {
      sock.receive(incoming("state.snapshot", { pc_online: true, rev: 1, daemon_version: "1.0.0", devices: [me] }));
    });
    expect(await screen.findByRole("region", { name: "Touchpad" })).toBeInTheDocument();
    await act(async () => {
      sock.receive(incoming("state.patch", { rev: 2, devices: [{ ...me, role: "guest" }] }));
    });
    expect(screen.queryByRole("region", { name: "Touchpad" })).not.toBeInTheDocument();
    expect(screen.getByText(/This phone is a guest/)).toBeInTheDocument();
  });

  it("shows no licence banner for a PC that sends none (paid, or older than 1.0.0)", async () => {
    render(<App />);
    await waitFor(() => expect(FakeSocket.instances.length).toBe(1));
    const sock = FakeSocket.last;
    await act(async () => sock.open());
    await act(async () => {
      sock.receive(incoming("pair.granted", { device_id: "dev-7", device_token: "t", expires_at: 2_000_000_000 }));
    });
    await act(async () => {
      sock.receive(incoming("state.snapshot", { pc_online: true, rev: 1, daemon_version: "0.3.0" }));
    });
    await screen.findByRole("tablist");
    expect(screen.queryByRole("button", { name: "Buy" })).not.toBeInTheDocument();
  });
});
