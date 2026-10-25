/** Local-storage keys the reset clears. Pairing, favourites and the mic choice are kept on purpose. */
const DISPOSABLE_KEYS = ["cj.recentCommands"];

export interface ResetEnv {
  serviceWorker?: { getRegistrations(): Promise<readonly { unregister(): Promise<boolean> }[]> };
  caches?: { keys(): Promise<string[]>; delete(name: string): Promise<boolean> };
  reload(): void;
}

/**
 * "Reset app cache": forget the downloaded copy of the app so the newest version loads, without
 * un-pairing this phone. Each step is independent: a browser that lacks one still does the rest.
 */
export async function resetAppCache(env: ResetEnv = browserEnv()): Promise<void> {
  try {
    const registrations = (await env.serviceWorker?.getRegistrations()) ?? [];
    await Promise.all(registrations.map((r) => r.unregister()));
  } catch {
    /* no service worker support */
  }
  try {
    const names = (await env.caches?.keys()) ?? [];
    await Promise.all(names.map((n) => env.caches?.delete(n)));
  } catch {
    /* no Cache Storage */
  }
  try {
    sessionStorage.clear();
    for (const key of DISPOSABLE_KEYS) localStorage.removeItem(key);
  } catch {
    /* storage blocked */
  }
  env.reload();
}

function browserEnv(): ResetEnv {
  return {
    serviceWorker: "serviceWorker" in navigator ? navigator.serviceWorker : undefined,
    caches: "caches" in window ? window.caches : undefined,
    reload: () => window.location.reload(),
  };
}
