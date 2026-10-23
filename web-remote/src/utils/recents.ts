const KEY = "cj.recentCommands";
const MAX = 5;

/** The commands you typed lately, newest first, so a repeat is one tap. */
export function loadRecents(): string[] {
  try {
    const raw = JSON.parse(localStorage.getItem(KEY) ?? "[]") as unknown;
    return Array.isArray(raw) ? raw.filter((x): x is string => typeof x === "string").slice(0, MAX) : [];
  } catch {
    return [];
  }
}

export function rememberCommand(text: string): string[] {
  const clean = text.trim();
  const next = [clean, ...loadRecents().filter((r) => r.toLowerCase() !== clean.toLowerCase())].filter(Boolean).slice(0, MAX);
  try {
    localStorage.setItem(KEY, JSON.stringify(next));
  } catch {
    /* private mode: the list just lasts for this visit */
  }
  return next;
}
