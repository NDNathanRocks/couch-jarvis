import { act, render, screen, waitFor } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import App from "../App";
import type { Pairing } from "../utils/storage";
import { forgetTv, labelOf, loadTvs, rememberTv, renameTv } from "../utils/tvs";
import { FakeSocket, incoming } from "./helpers";

const pairing = (roomId: string, token = "tok"): Pairing => ({
  roomId,
  deviceId: `dev-${roomId}`,
  deviceToken: token,
  expiresAt: 2_000_000_000,
  deviceName: "Phone",
});

describe("several TVs on one phone", () => {
  beforeEach(() => {
    localStorage.clear();
    FakeSocket.reset();
    vi.stubGlobal("WebSocket", FakeSocket);
  });
  afterEach(() => vi.unstubAllGlobals());

  it("remembers each TV once, refreshes its token, and names them", () => {
    rememberTv(pairing("living-room1"));
    rememberTv(pairing("bedroom-tv1"));
    rememberTv(pairing("living-room1", "new-token"));
    const tvs = loadTvs();
    expect(tvs.map((t) => t.label)).toEqual(["TV", "TV 2"]);
    expect(tvs[0]?.pairing.deviceToken).toBe("new-token");
  });

  it("renames and forgets", () => {
    rememberTv(pairing("living-room1"));
    rememberTv(pairing("bedroom-tv1"));
    renameTv("living-room1", "  Living room  ");
    expect(labelOf("living-room1")).toBe("Living room");
    renameTv("living-room1", "   "); // an empty name is ignored
    expect(labelOf("living-room1")).toBe("Living room");
    expect(forgetTv("living-room1").map((t) => t.pairing.roomId)).toEqual(["bedroom-tv1"]);
  });

  it("survives a damaged list", () => {
    localStorage.setItem("cj.tvs", '[{"label": 3}, "junk", {"label": "ok", "pairing": {"roomId": "x"}}]');
    expect(loadTvs()).toEqual([]);
    localStorage.setItem("cj.tvs", "not json");
    expect(loadTvs()).toEqual([]);
  });

  it("shows a TV switcher once the phone knows two TVs", async () => {
    localStorage.setItem("cj.pairing", JSON.stringify(pairing("living-room1")));
    rememberTv(pairing("bedroom-tv1"));
    render(<App />);
    await waitFor(() => expect(FakeSocket.instances.length).toBe(1));
    await act(async () => FakeSocket.last.open());
    await act(async () => FakeSocket.last.receive(incoming("state.snapshot", { pc_online: true, rev: 1 })));
    const picker = await screen.findByRole("combobox", { name: "TV" });
    expect(picker).toHaveValue("living-room1");
    expect(screen.getAllByRole("option").map((o) => o.textContent)).toEqual(["TV", "TV 2"]);
  });
});
