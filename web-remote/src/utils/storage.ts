/**
 * Durable pairing storage. localStorage is the fast path; an IndexedDB mirror survives the
 * cases where iOS/Android evict localStorage for a home-screen PWA. Every access is guarded:
 * private windows and blocked storage must degrade to "not paired", never throw.
 */
export interface Pairing {
  roomId: string;
  deviceId: string;
  deviceToken: string;
  /** Unix seconds. */
  expiresAt: number;
  deviceName: string;
}

export interface PairingStorage {
  load(): Promise<Pairing | null>;
  save(pairing: Pairing): Promise<void>;
  clear(): Promise<void>;
}

const LS_KEY = "cj.pairing";
const DB_NAME = "couch-jarvis";
const STORE = "kv";

export function isPairing(value: unknown): value is Pairing {
  if (typeof value !== "object" || value === null) return false;
  const v = value as Record<string, unknown>;
  return (
    typeof v["roomId"] === "string" &&
    typeof v["deviceId"] === "string" &&
    typeof v["deviceToken"] === "string" &&
    typeof v["expiresAt"] === "number" &&
    typeof v["deviceName"] === "string" &&
    v["deviceToken"] !== ""
  );
}

function openDb(): Promise<IDBDatabase> {
  return new Promise((resolve, reject) => {
    const req = indexedDB.open(DB_NAME, 1);
    req.onupgradeneeded = () => req.result.createObjectStore(STORE);
    req.onsuccess = () => resolve(req.result);
    req.onerror = () => reject(req.error ?? new Error("indexedDB open failed"));
  });
}

async function idbRun<T>(mode: IDBTransactionMode, fn: (store: IDBObjectStore) => IDBRequest<T>): Promise<T> {
  const db = await openDb();
  try {
    return await new Promise<T>((resolve, reject) => {
      const req = fn(db.transaction(STORE, mode).objectStore(STORE));
      req.onsuccess = () => resolve(req.result);
      req.onerror = () => reject(req.error ?? new Error("indexedDB request failed"));
    });
  } finally {
    db.close();
  }
}

function lsGet(): Pairing | null {
  try {
    const raw = localStorage.getItem(LS_KEY);
    if (!raw) return null;
    const parsed: unknown = JSON.parse(raw);
    return isPairing(parsed) ? parsed : null;
  } catch {
    return null;
  }
}

export const browserPairingStorage: PairingStorage = {
  async load() {
    const fast = lsGet();
    if (fast) return fast;
    try {
      const mirrored = await idbRun<unknown>("readonly", (s) => s.get(LS_KEY));
      if (isPairing(mirrored)) {
        try {
          localStorage.setItem(LS_KEY, JSON.stringify(mirrored)); // heal the fast path
        } catch {
          /* storage blocked: the mirror still works */
        }
        return mirrored;
      }
    } catch {
      /* IndexedDB unavailable */
    }
    return null;
  },

  async save(pairing) {
    try {
      localStorage.setItem(LS_KEY, JSON.stringify(pairing));
    } catch {
      /* continue: the IndexedDB mirror may still work */
    }
    try {
      await idbRun("readwrite", (s) => s.put(pairing, LS_KEY));
    } catch {
      /* best effort */
    }
  },

  async clear() {
    try {
      localStorage.removeItem(LS_KEY);
    } catch {
      /* ignore */
    }
    try {
      await idbRun("readwrite", (s) => s.delete(LS_KEY));
    } catch {
      /* ignore */
    }
  },
};

/** A friendly default name for the Friends bar, e.g. "iPhone" / "Pixel 9" / "Android phone". */
export function guessDeviceName(userAgent: string = navigator.userAgent): string {
  if (/iPhone/.test(userAgent)) return "iPhone";
  if (/iPad/.test(userAgent)) return "iPad";
  const pixel = /\b(Pixel [0-9a-zA-Z ]{1,8}?)(?:\)|;| Build)/.exec(userAgent);
  if (pixel?.[1]) return pixel[1].trim();
  if (/Android/.test(userAgent)) return "Android phone";
  return "Phone";
}
