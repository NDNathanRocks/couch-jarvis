import type { Modifier, QuickAction } from "../protocol";
import type { IconName } from "../components/Icon";

export type FavGroup = "Playback" | "Volume" | "Display" | "Browser" | "Keys" | "Say";

/** What pressing a favourite does: a PC button, a keyboard key, or a spoken-style command. */
export type FavSend =
  | { kind: "action"; action: QuickAction }
  | { kind: "key"; key: string; mods: Modifier[] }
  | { kind: "text"; text: string };

export interface Fav {
  id: string;
  label: string;
  group: FavGroup;
  /** Drawn above the label; keys without an icon show their own name large instead. */
  icon?: IconName;
  send: FavSend;
  /** First PC app version that understands it (older PCs show it disabled). */
  since?: string;
}

const action = (id: string, label: string, group: FavGroup, icon: IconName, a: QuickAction): Fav => ({
  id,
  label,
  group,
  icon,
  send: { kind: "action", action: a },
});
const key = (id: string, label: string, k: string, icon?: IconName): Fav => ({
  id,
  label,
  group: "Keys",
  icon,
  send: { kind: "key", key: k, mods: [] },
});

/** Every button a favourite slot can hold. Ids are stored on the phone: never rename one. */
export const LIBRARY: readonly Fav[] = [
  action("play_pause", "Play / Pause", "Playback", "play", "play_pause"),
  action("prev", "Previous", "Playback", "prev", "prev"),
  action("next", "Next", "Playback", "next", "next"),
  action("seek_back_10", "Back 10s", "Playback", "back10", "seek_back_10"),
  action("seek_fwd_10", "Forward 10s", "Playback", "fwd10", "seek_fwd_10"),
  action("panic", "Stop", "Playback", "stop", "panic"),
  action("vol_down", "Volume down", "Volume", "speakerLow", "vol_down"),
  action("vol_up", "Volume up", "Volume", "speakerHigh", "vol_up"),
  action("mute", "Mute", "Volume", "speakerMute", "mute"),
  action("fullscreen", "Fullscreen", "Display", "fullscreen", "fullscreen"),
  action("subtitles", "Subtitles", "Display", "cc", "subtitles"),
  action("skip_ad", "Skip ad", "Display", "skipAd", "skip_ad"),
  action("sleep_display", "Screen off", "Display", "moon", "sleep_display"),
  action("show_qr", "Pair (QR)", "Display", "plus", "show_qr"),
  action("back", "Back page", "Browser", "back", "back"),
  action("forward", "Forward page", "Browser", "forward", "forward"),
  action("reload", "Reload", "Browser", "reload", "reload"),
  action("new_tab", "New tab", "Browser", "plus", "new_tab"),
  action("close_tab", "Close tab", "Browser", "close", "close_tab"),
  action("prev_tab", "Tab left", "Browser", "tabLeft", "prev_tab"),
  action("next_tab", "Tab right", "Browser", "tabRight", "next_tab"),
  action("scroll_up", "Scroll up", "Browser", "up", "scroll_up"),
  action("scroll_down", "Scroll down", "Browser", "down", "scroll_down"),
  key("key_f11", "Full screen (F11)", "f11", "fullscreen"),
  key("key_esc", "Escape", "esc"),
  key("key_space", "Space", "space"),
  key("key_enter", "Enter", "enter"),
  ...Array.from({ length: 12 }, (_, i) => key(`key_f${i + 1}`, `F${i + 1}`, `f${i + 1}`)).filter((f) => f.id !== "key_f11"),
  { id: "say_subs_on", label: "Subtitles on", group: "Say", icon: "cc", send: { kind: "text", text: "subtitles on" } },
  { id: "say_subs_off", label: "Subtitles off", group: "Say", icon: "cc", send: { kind: "text", text: "subtitles off" } },
  { id: "say_fullscreen", label: "Go fullscreen", group: "Say", icon: "fullscreen", send: { kind: "text", text: "fullscreen" } },
];

export const FAV_GROUPS: readonly FavGroup[] = ["Playback", "Volume", "Display", "Browser", "Keys", "Say"];
export const SLOTS = 8;
export const DEFAULT_FAVORITES: readonly (string | null)[] = [
  "fullscreen",
  "subtitles",
  "mute",
  "skip_ad",
  "back",
  "new_tab",
  "panic",
  "sleep_display",
];

const byId = new Map(LIBRARY.map((f) => [f.id, f]));
export const findFav = (id: string | null): Fav | null => (id ? (byId.get(id) ?? null) : null);

const STORAGE_KEY = "cj.favorites.v1";

/** The saved slots, always exactly SLOTS long. Anything unreadable falls back to the defaults. */
export function loadFavorites(): (string | null)[] {
  try {
    const raw = JSON.parse(localStorage.getItem(STORAGE_KEY) ?? "null");
    if (Array.isArray(raw)) {
      const slots = raw.slice(0, SLOTS).map((v) => (typeof v === "string" && byId.has(v) ? v : null));
      while (slots.length < SLOTS) slots.push(null);
      return slots;
    }
  } catch {
    /* blocked or corrupt storage: use the defaults */
  }
  return [...DEFAULT_FAVORITES];
}

export function saveFavorites(slots: readonly (string | null)[]): void {
  try {
    localStorage.setItem(STORAGE_KEY, JSON.stringify(slots.slice(0, SLOTS)));
  } catch {
    /* private mode: the choice lasts until the page closes */
  }
}

/** Slot list with `id` put in slot `index` (an id never sits in two slots). */
export function assign(slots: readonly (string | null)[], index: number, id: string | null): (string | null)[] {
  const next = slots.map((s) => (id !== null && s === id ? null : s));
  next[index] = id;
  return next;
}
