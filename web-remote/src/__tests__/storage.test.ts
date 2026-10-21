import { afterEach, describe, expect, it, vi } from "vitest";
import { browserPairingStorage, guessDeviceName, isPairing, type Pairing } from "../utils/storage";

const pairing: Pairing = {
  roomId: "room-abcdef123",
  deviceId: "dev-1",
  deviceToken: "jwt.token.value",
  expiresAt: 2_000_000_000,
  deviceName: "Pixel 9",
};

afterEach(async () => {
  vi.restoreAllMocks();
  await browserPairingStorage.clear();
});

describe("pairing storage", () => {
  it("round-trips through localStorage", async () => {
    await browserPairingStorage.save(pairing);
    expect(await browserPairingStorage.load()).toEqual(pairing);
    expect(JSON.parse(localStorage.getItem("cj.pairing") ?? "null")).toEqual(pairing);
  });

  it("falls back to the IndexedDB mirror when localStorage is wiped, and heals localStorage", async () => {
    await browserPairingStorage.save(pairing);
    localStorage.clear(); // e.g. the OS evicted site data but the PWA's IndexedDB survived
    expect(await browserPairingStorage.load()).toEqual(pairing);
    expect(localStorage.getItem("cj.pairing")).not.toBeNull();
  });

  it("the pairing survives with localStorage completely blocked", async () => {
    vi.spyOn(Storage.prototype, "setItem").mockImplementation(() => {
      throw new DOMException("blocked", "SecurityError");
    });
    vi.spyOn(Storage.prototype, "getItem").mockImplementation(() => {
      throw new DOMException("blocked", "SecurityError");
    });
    await browserPairingStorage.save(pairing);
    expect(await browserPairingStorage.load()).toEqual(pairing);
  });

  it("returns null (never throws) for corrupt or foreign data", async () => {
    localStorage.setItem("cj.pairing", "{broken");
    expect(await browserPairingStorage.load()).toBeNull();
    localStorage.setItem("cj.pairing", JSON.stringify({ roomId: 1 }));
    expect(await browserPairingStorage.load()).toBeNull();
    localStorage.setItem("cj.pairing", JSON.stringify({ ...pairing, deviceToken: "" }));
    expect(await browserPairingStorage.load()).toBeNull();
  });

  it("clear removes both copies", async () => {
    await browserPairingStorage.save(pairing);
    await browserPairingStorage.clear();
    expect(localStorage.getItem("cj.pairing")).toBeNull();
    expect(await browserPairingStorage.load()).toBeNull();
  });

  it("validates shape strictly", () => {
    expect(isPairing(pairing)).toBe(true);
    expect(isPairing(null)).toBe(false);
    expect(isPairing({ ...pairing, expiresAt: "soon" })).toBe(false);
  });
});

describe("guessDeviceName", () => {
  it.each([
    ["Mozilla/5.0 (iPhone; CPU iPhone OS 17_0 like Mac OS X)", "iPhone"],
    ["Mozilla/5.0 (iPad; CPU OS 17_0 like Mac OS X)", "iPad"],
    ["Mozilla/5.0 (Linux; Android 14; Pixel 9) AppleWebKit/537.36", "Pixel 9"],
    ["Mozilla/5.0 (Linux; Android 13; SM-S918B) AppleWebKit/537.36", "Android phone"],
    ["Mozilla/5.0 (Windows NT 10.0)", "Phone"],
  ])("%s -> %s", (ua, expected) => {
    expect(guessDeviceName(ua)).toBe(expected);
  });
});
