/**
 * Several TVs on one phone ("Living room", "Bedroom"). The active TV is still the one pairing in
 * storage.ts (the connection uses only that); this list remembers every TV this phone paired
 * with, so switching is: make another one the active pairing, then reload. Scanning another TV's
 * QR adds it here instead of replacing the first one.
 */
import { isPairing, type Pairing } from "./storage";

export interface Tv {
  label: string;
  pairing: Pairing;
}

const KEY = "cj.tvs";
const MAX_TVS = 8;

export function loadTvs(): Tv[] {
  try {
    const raw: unknown = JSON.parse(localStorage.getItem(KEY) ?? "[]");
    if (!Array.isArray(raw)) return [];
    return raw
      .filter((t): t is Tv => typeof t === "object" && t !== null && typeof t.label === "string" && isPairing(t.pairing))
      .slice(0, MAX_TVS);
  } catch {
    return [];
  }
}

function save(tvs: Tv[]): Tv[] {
  try {
    localStorage.setItem(KEY, JSON.stringify(tvs.slice(0, MAX_TVS)));
  } catch {
    /* storage blocked: the active pairing still works, only the list is lost */
  }
  return tvs;
}

/** Remember (or refresh the token of) the TV this phone just paired with or reconnected to. */
export function rememberTv(pairing: Pairing): Tv[] {
  const tvs = loadTvs();
  const i = tvs.findIndex((t) => t.pairing.roomId === pairing.roomId);
  if (i >= 0) {
    const current = tvs[i];
    if (current) tvs[i] = { ...current, pairing };
    return save(tvs);
  }
  return save([...tvs, { label: nextLabel(tvs), pairing }]);
}

function nextLabel(tvs: Tv[]): string {
  if (tvs.length === 0) return "TV";
  for (let n = tvs.length + 1; ; n++) {
    const label = `TV ${n}`;
    if (!tvs.some((t) => t.label === label)) return label;
  }
}

export function renameTv(roomId: string, label: string): Tv[] {
  const clean = label.trim().slice(0, 32);
  if (!clean) return loadTvs();
  return save(loadTvs().map((t) => (t.pairing.roomId === roomId ? { ...t, label: clean } : t)));
}

export function forgetTv(roomId: string): Tv[] {
  return save(loadTvs().filter((t) => t.pairing.roomId !== roomId));
}

export function labelOf(roomId: string | undefined, tvs: Tv[] = loadTvs()): string | null {
  return tvs.find((t) => t.pairing.roomId === roomId)?.label ?? null;
}
