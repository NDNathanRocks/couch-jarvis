/**
 * Moving the phone app to a new address without un-pairing anyone.
 *
 * A browser keeps pairings per origin, so a new address starts empty. The OLD site (built with
 * VITE_HANDOFF_URL) forwards what it holds to the new address inside the URL *fragment*, which
 * browsers never send to any server. The NEW site imports it and clears the fragment. Old QR codes
 * (?room=...&secret=...) are forwarded the same way, so they keep pairing.
 */
import { isPairing, type Pairing } from "./storage";
import type { Tv } from "./tvs";

const PAIRING_KEY = "cj.pairing";
const TVS_KEY = "cj.tvs";
const FRAGMENT = "cj-handoff=";
const ROOM_RE = /^[A-Za-z0-9_-]{6,64}$/;
const SECRET_RE = /^[A-Za-z0-9_-]{8,128}$/;

interface Payload {
  v: 1;
  pairing?: Pairing;
  tvs?: Tv[];
  /** A QR link that was opened on the old address and not yet used. */
  pair?: { room: string; secret: string };
}

type Win = Pick<Window, "location" | "history">;

function encode(payload: Payload): string {
  const bytes = new TextEncoder().encode(JSON.stringify(payload));
  let binary = "";
  for (const b of bytes) binary += String.fromCharCode(b);
  return btoa(binary).replace(/\+/g, "-").replace(/\//g, "_").replace(/=+$/, "");
}

function decode(text: string): Payload | null {
  try {
    const padded = text.replace(/-/g, "+").replace(/_/g, "/");
    const binary = atob(padded + "=".repeat((4 - (padded.length % 4)) % 4));
    const bytes = Uint8Array.from(binary, (c) => c.charCodeAt(0));
    const raw: unknown = JSON.parse(new TextDecoder().decode(bytes));
    if (typeof raw !== "object" || raw === null || (raw as { v?: unknown }).v !== 1) return null;
    return raw as Payload;
  } catch {
    return null;
  }
}

function readJson(key: string): unknown {
  try {
    return JSON.parse(localStorage.getItem(key) ?? "null");
  } catch {
    return null;
  }
}

/** Only an https address that is not this one can receive the handoff. */
export function validTarget(target: string | undefined, here: string): URL | null {
  if (!target) return null;
  try {
    const url = new URL(target);
    return url.protocol === "https:" && url.origin !== here ? url : null;
  } catch {
    return null;
  }
}

/** OLD site: forward everything this phone holds. Returns true when the page is being replaced. */
export function forwardToNewAddress(target: string | undefined, win: Win = window): boolean {
  const url = validTarget(target, win.location.origin);
  if (url === null) return false;
  const payload: Payload = { v: 1 };
  const pairing = readJson(PAIRING_KEY);
  if (isPairing(pairing)) payload.pairing = pairing;
  const tvs = readJson(TVS_KEY);
  if (Array.isArray(tvs)) payload.tvs = tvs.filter((t): t is Tv => typeof t === "object" && t !== null && isPairing((t as Tv).pairing));
  const q = new URLSearchParams(win.location.search);
  const room = q.get("room");
  const secret = q.get("secret");
  if (room && secret && ROOM_RE.test(room) && SECRET_RE.test(secret)) payload.pair = { room, secret };
  url.hash = FRAGMENT + encode(payload);
  win.location.replace(url.toString());
  return true;
}

/** NEW site: take in what the old address sent, then clear it from the address bar. */
export function importFromOldAddress(win: Win = window): boolean {
  const hash = win.location.hash.replace(/^#/, "");
  if (!hash.startsWith(FRAGMENT)) return false;
  const payload = decode(hash.slice(FRAGMENT.length));
  let search = win.location.search;
  if (payload !== null) {
    try {
      if (payload.pairing && isPairing(payload.pairing) && readJson(PAIRING_KEY) === null) {
        localStorage.setItem(PAIRING_KEY, JSON.stringify(payload.pairing));
      }
      const have = readJson(TVS_KEY);
      const known = new Set((Array.isArray(have) ? (have as Tv[]) : []).map((t) => t.pairing?.roomId));
      const merged = [...(Array.isArray(have) ? (have as Tv[]) : [])];
      for (const tv of payload.tvs ?? []) {
        if (typeof tv.label === "string" && isPairing(tv.pairing) && !known.has(tv.pairing.roomId)) merged.push(tv);
      }
      if (merged.length > 0) localStorage.setItem(TVS_KEY, JSON.stringify(merged.slice(0, 8)));
    } catch {
      /* storage blocked: nothing to import into */
    }
    const pair = payload.pair;
    if (pair && ROOM_RE.test(pair.room) && SECRET_RE.test(pair.secret)) {
      search = `?${new URLSearchParams({ room: pair.room, secret: pair.secret }).toString()}`;
    }
  }
  win.history.replaceState(null, "", `${win.location.pathname}${search}`);
  return true;
}
