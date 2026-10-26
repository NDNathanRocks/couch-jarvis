import { beforeEach, describe, expect, it, vi } from "vitest";
import { forwardToNewAddress, importFromOldAddress, validTarget } from "../utils/handoff";

const pairing = (roomId: string) => ({ roomId, deviceId: "dev-1", deviceToken: "tok", expiresAt: 2_000_000_000, deviceName: "Pixel" });

function fakeWindow(url: string) {
  const u = new URL(url);
  const replaced: string[] = [];
  const state: string[] = [];
  return {
    win: {
      location: {
        get origin() {
          return u.origin;
        },
        get search() {
          return u.search;
        },
        get hash() {
          return u.hash;
        },
        get pathname() {
          return u.pathname;
        },
        replace: (to: string) => replaced.push(to),
      },
      history: { replaceState: (_s: unknown, _t: string, to: string) => state.push(to) },
    } as unknown as Window,
    replaced,
    state,
  };
}

beforeEach(() => localStorage.clear());

describe("validTarget", () => {
  it("accepts only another https origin", () => {
    expect(validTarget("https://armrest-app.pages.dev", "https://old.vercel.app")?.origin).toBe("https://armrest-app.pages.dev");
    expect(validTarget("http://armrest-app.pages.dev", "https://old.vercel.app")).toBeNull();
    expect(validTarget("https://old.vercel.app", "https://old.vercel.app")).toBeNull(); // no loop
    expect(validTarget("javascript:alert(1)", "https://old.vercel.app")).toBeNull();
    expect(validTarget(undefined, "https://old.vercel.app")).toBeNull();
  });
});

describe("old address -> new address", () => {
  it("does nothing when no new address is configured", () => {
    const { win, replaced } = fakeWindow("https://old.vercel.app/");
    expect(forwardToNewAddress(undefined, win)).toBe(false);
    expect(replaced).toEqual([]);
  });

  it("carries pairing and TVs in the fragment, never in the query string", () => {
    localStorage.setItem("cj.pairing", JSON.stringify(pairing("room-aaaaaa")));
    localStorage.setItem("cj.tvs", JSON.stringify([{ label: "Living room", pairing: pairing("room-aaaaaa") }]));
    const { win, replaced } = fakeWindow("https://old.vercel.app/");
    expect(forwardToNewAddress("https://armrest-app.pages.dev", win)).toBe(true);
    const to = new URL(replaced[0]!);
    expect(to.origin).toBe("https://armrest-app.pages.dev");
    expect(to.search).toBe("");
    expect(to.hash.startsWith("#cj-handoff=")).toBe(true);
    expect(to.hash).not.toContain("tok"); // encoded, not plain text
  });

  it("forwards an old QR link too", () => {
    const { win, replaced } = fakeWindow("https://old.vercel.app/?room=room-bbbbbb&secret=secretsecret1");
    expect(forwardToNewAddress("https://armrest-app.pages.dev", win)).toBe(true);
    expect(new URL(replaced[0]!).search).toBe("");
  });
});

describe("new address takes it in", () => {
  function roundTrip(setup: () => void, newUrl = "https://armrest-app.pages.dev/") {
    setup();
    const from = fakeWindow("https://old.vercel.app/?room=room-bbbbbb&secret=secretsecret1");
    forwardToNewAddress(newUrl, from.win);
    const hash = new URL(from.replaced[0]!).hash;
    localStorage.clear();
    const to = fakeWindow(`${newUrl}${hash}`);
    return { imported: importFromOldAddress(to.win), state: to.state };
  }

  it("restores the pairing and TVs, restores a pending QR, and clears the fragment", () => {
    const { imported, state } = roundTrip(() => {
      localStorage.setItem("cj.pairing", JSON.stringify(pairing("room-aaaaaa")));
      localStorage.setItem("cj.tvs", JSON.stringify([{ label: "Bedroom", pairing: pairing("room-aaaaaa") }]));
    });
    expect(imported).toBe(true);
    expect(JSON.parse(localStorage.getItem("cj.pairing")!).roomId).toBe("room-aaaaaa");
    expect(JSON.parse(localStorage.getItem("cj.tvs")!)[0].label).toBe("Bedroom");
    expect(state[0]).toBe("/?room=room-bbbbbb&secret=secretsecret1");
    expect(state[0]).not.toContain("#");
  });

  it("never overwrites a pairing the new address already has", () => {
    const from = fakeWindow("https://old.vercel.app/");
    localStorage.setItem("cj.pairing", JSON.stringify(pairing("room-old111")));
    forwardToNewAddress("https://armrest-app.pages.dev", from.win);
    const hash = new URL(from.replaced[0]!).hash;
    localStorage.clear();
    localStorage.setItem("cj.pairing", JSON.stringify(pairing("room-new222")));
    importFromOldAddress(fakeWindow(`https://armrest-app.pages.dev/${hash}`).win);
    expect(JSON.parse(localStorage.getItem("cj.pairing")!).roomId).toBe("room-new222");
  });

  it("ignores junk and unrelated fragments", () => {
    const junk = fakeWindow("https://armrest-app.pages.dev/#cj-handoff=%%%not-base64");
    expect(importFromOldAddress(junk.win)).toBe(true); // recognised, cleared, nothing imported
    expect(localStorage.getItem("cj.pairing")).toBeNull();
    const other = fakeWindow("https://armrest-app.pages.dev/#something-else");
    expect(importFromOldAddress(other.win)).toBe(false);
    vi.restoreAllMocks();
  });

  it("rejects a forged QR with a bad room id", () => {
    const payload = btoa(JSON.stringify({ v: 1, pair: { room: "../../x", secret: "secretsecret1" } }));
    const to = fakeWindow(`https://armrest-app.pages.dev/#cj-handoff=${payload}`);
    importFromOldAddress(to.win);
    expect(to.state[0]).toBe("/");
  });
});
