import { act, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import App from "../App";
import { FakeSocket, incoming } from "./helpers";

const SECRET = "qr-secret-value-123456";

function openQrLink(): void {
  window.history.replaceState(null, "", `/?room=room-abcdef123&secret=${SECRET}`);
}

beforeEach(() => {
  FakeSocket.reset();
  // jsdom serves http://localhost, which the app treats as the daemon-hosted (LAN) PWA. Pairing
  // uses the same flow there; the hosted/cloud URL building is covered in config.test.ts.
  vi.stubGlobal("WebSocket", FakeSocket);
});

afterEach(() => {
  vi.unstubAllGlobals();
  window.history.replaceState(null, "", "/");
});

describe("first-time pairing from a QR link", () => {
  it("pairs, removes the secret from the URL, stores the token and shows the remote", async () => {
    openQrLink();
    render(<App />);
    expect(await screen.findByText(/Pairing with your TV/)).toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "Play / Pause" })).not.toBeInTheDocument();

    await waitFor(() => expect(FakeSocket.instances.length).toBe(1));
    const sock = FakeSocket.last;
    expect(new URL(sock.url).searchParams.get("pair")).toBe("1");
    await act(async () => sock.open());
    expect(sock.sent[0]).toMatchObject({ type: "pair.request", payload: { room_secret: SECRET } });

    await act(async () => {
      sock.receive(incoming("pair.granted", { device_id: "dev-7", device_token: "the.jwt.token", expires_at: 2_000_000_000 }));
    });
    await act(async () => {
      sock.receive(
        incoming("state.snapshot", {
          pc_online: true, mode: "lan", volume: 42, muted: false, rev: 3,
          now_playing: { title: "Lofi Beats", source: "youtube", is_playing: true, position_s: 5, duration_s: 100 },
          timers: [{ timer_id: "t1", label: "Sleep timer", ends_at: Date.now() / 1000 + 600, action: "pause" }],
          devices: [{ device_id: "dev-7", name: "Pixel", online: true, last_seen: 1 }],
        }),
      );
    });

    expect(await screen.findByRole("button", { name: "Play / Pause" })).toBeEnabled();
    expect(screen.getByText("Lofi Beats")).toBeInTheDocument();
    expect(screen.getByText("Sleep timer")).toBeInTheDocument();
    expect(screen.getByText("(you)")).toBeInTheDocument();
    expect(screen.queryByText(/Pairing with your TV/)).not.toBeInTheDocument();

    // the secret is gone from the address bar and history, the token is persisted
    expect(window.location.href).not.toContain(SECRET);
    expect(window.location.search).toBe("");
    expect(JSON.parse(localStorage.getItem("cj.pairing") ?? "{}")).toMatchObject({
      roomId: "room-abcdef123", deviceId: "dev-7", deviceToken: "the.jwt.token",
    });
  });

  it("a button press goes out as a cmd.button and its result shows as a toast", async () => {
    openQrLink();
    render(<App />);
    await waitFor(() => expect(FakeSocket.instances.length).toBe(1));
    const sock = FakeSocket.last;
    await act(async () => sock.open());
    await act(async () => {
      sock.receive(incoming("pair.granted", { device_id: "dev-7", device_token: "tok", expires_at: 2_000_000_000 }));
      sock.receive(incoming("state.snapshot", { pc_online: true, rev: 1 }));
    });

    fireEvent.click(await screen.findByRole("button", { name: "Next" }));
    const press = sock.sent.find((m) => m["type"] === "cmd.button");
    expect(press).toMatchObject({ from: "dev-7", payload: { action: "next" } });
    const commandId = (press?.["payload"] as { command_id: string }).command_id;

    await act(async () => {
      sock.receive(incoming("cmd.result", { command_id: commandId, ok: true, message: "Done", tier_used: "dom", latency_ms: 9 }));
    });
    expect(await screen.findByText("Done")).toBeInTheDocument();
  });

  it("a wrong QR shows the re-scan guidance and hides the remote", async () => {
    openQrLink();
    render(<App />);
    await waitFor(() => expect(FakeSocket.instances.length).toBe(1));
    await act(async () => FakeSocket.last.open());
    await act(async () => FakeSocket.last.receive(incoming("pair.denied", { reason: "invalid_secret" })));
    expect(await screen.findByRole("alert")).toHaveTextContent(/scan the QR code on the TV again/i);
    expect(screen.queryByRole("button", { name: "Play / Pause" })).not.toBeInTheDocument();
  });
});

describe("returning phone", () => {
  it("reconnects silently from the stored token and can forget itself", async () => {
    localStorage.setItem(
      "cj.pairing",
      JSON.stringify({ roomId: "room-abcdef123", deviceId: "dev-1", deviceToken: "stored.tok", expiresAt: 2_000_000_000, deviceName: "Pixel" }),
    );
    render(<App />);
    await waitFor(() => expect(FakeSocket.instances.length).toBe(1));
    expect(new URL(FakeSocket.last.url).searchParams.get("token")).toBe("stored.tok");
    await act(async () => FakeSocket.last.open());
    await act(async () => FakeSocket.last.receive(incoming("state.snapshot", { pc_online: true, rev: 1 })));
    expect(await screen.findByRole("button", { name: "Play / Pause" })).toBeEnabled();

    fireEvent.click(screen.getByRole("button", { name: "Forget this phone" }));
    expect(await screen.findByText(/Pair this phone/)).toBeInTheDocument();
    expect(localStorage.getItem("cj.pairing")).toBeNull();
  });

  it("with no saved pairing and no QR it explains how to pair", async () => {
    render(<App />);
    expect(await screen.findByText(/Pair this phone/)).toBeInTheDocument();
    expect(FakeSocket.instances).toHaveLength(0);
  });

  it("controls are disabled until the socket is online", async () => {
    localStorage.setItem(
      "cj.pairing",
      JSON.stringify({ roomId: "room-abcdef123", deviceId: "dev-1", deviceToken: "t", expiresAt: 2_000_000_000, deviceName: "P" }),
    );
    render(<App />);
    await waitFor(() => expect(FakeSocket.instances.length).toBe(1));
    expect(await screen.findByRole("button", { name: "Play / Pause" })).toBeDisabled();
    expect(screen.getByRole("button", { name: "Tap to talk" })).toBeDisabled();
  });
});

describe("transient outages", () => {
  it("keeps the remote on screen (disabled) and never claims the pairing is gone", async () => {
    localStorage.setItem(
      "cj.pairing",
      JSON.stringify({ roomId: "room-abcdef123", deviceId: "dev-1", deviceToken: "t", expiresAt: 2_000_000_000, deviceName: "P" }),
    );
    vi.useFakeTimers();
    render(<App />);
    for (let i = 0; i < 3; i++) {
      await act(async () => {
        await vi.advanceTimersByTimeAsync(31_000);
      });
      await act(async () => FakeSocket.last.drop(1006)); // handshake never completed (e.g. throttled)
    }
    vi.useRealTimers();
    expect(await screen.findByText(/Can't reach your TV/)).toBeInTheDocument();
    expect(screen.queryByText(/no longer paired/i)).not.toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Play / Pause" })).toBeDisabled(); // visible, not hidden
    expect(screen.getByRole("button", { name: "Forget this phone" })).toBeEnabled(); // escape hatch stays
    expect(localStorage.getItem("cj.pairing")).not.toBeNull(); // the pairing is kept
  });
});
