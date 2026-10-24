import { useRef, useState, type FormEvent, type KeyboardEvent } from "react";
import type { Modifier } from "../protocol";

interface Props {
  onKey: (key: string, mods?: Modifier[]) => void;
  onText: (text: string) => void;
  disabled: boolean;
}

interface Key {
  key: string;
  label: string;
}

const MODS: readonly { mod: Modifier; label: string }[] = [
  { mod: "ctrl", label: "Ctrl" },
  { mod: "alt", label: "Alt" },
  { mod: "shift", label: "Shift" },
];

const KEYS: readonly Key[] = [
  { key: "esc", label: "Esc" },
  { key: "tab", label: "Tab" },
  { key: "enter", label: "Enter" },
  { key: "backspace", label: "⌫" },
  { key: "delete", label: "Del" },
  { key: "space", label: "Space" },
  { key: "home", label: "Home" },
  { key: "end", label: "End" },
  { key: "pageup", label: "PgUp" },
  { key: "pagedown", label: "PgDn" },
];

/** F1-F12. F11 is the browser's own full screen: it hides the tab strip that fullscreen can leave. */
const FUNCTION_KEYS: readonly Key[] = Array.from({ length: 12 }, (_, i) => ({
  key: `f${i + 1}`,
  label: `F${i + 1}`,
}));

const ARROWS: readonly Key[] = [
  { key: "left", label: "←" },
  { key: "up", label: "↑" },
  { key: "down", label: "↓" },
  { key: "right", label: "→" },
];

interface Shortcut {
  label: string;
  key: string;
  mods: Modifier[];
}

/** One-tap chords for the browser on the TV. alt+left is "Back". */
export const SHORTCUTS: readonly Shortcut[] = [
  { label: "Back", key: "left", mods: ["alt"] },
  { label: "Forward", key: "right", mods: ["alt"] },
  { label: "Reload", key: "r", mods: ["ctrl"] },
  { label: "New tab", key: "t", mods: ["ctrl"] },
  { label: "Close tab", key: "w", mods: ["ctrl"] },
  { label: "Next tab", key: "tab", mods: ["ctrl"] },
  { label: "Prev tab", key: "tab", mods: ["ctrl", "shift"] },
  { label: "Address bar", key: "l", mods: ["ctrl"] },
  { label: "Zoom in", key: "=", mods: ["ctrl"] },
  { label: "Zoom out", key: "-", mods: ["ctrl"] },
  { label: "Reset zoom", key: "0", mods: ["ctrl"] },
  { label: "Switch window", key: "tab", mods: ["alt"] },
  { label: "Full screen (F11)", key: "f11", mods: [] },
];

const SENTINEL = "​"; // so Backspace always has something to delete on a phone keyboard
const NAMED_FROM_KEYBOARD: Record<string, string> = {
  Enter: "enter",
  Backspace: "backspace",
  Delete: "delete",
  Escape: "esc",
  Tab: "tab",
  ArrowLeft: "left",
  ArrowRight: "right",
  ArrowUp: "up",
  ArrowDown: "down",
};

export function KeyPad({ onKey, onText, disabled }: Props) {
  const [mods, setMods] = useState<Modifier[]>([]);
  const box = useRef<HTMLTextAreaElement>(null);

  const toggle = (mod: Modifier) =>
    setMods((m) => (m.includes(mod) ? m.filter((x) => x !== mod) : [...m, mod]));

  /** Latched modifiers apply to the next key only, like a sticky key. */
  const press = (key: string, extra: Modifier[] = []) => {
    onKey(key, [...new Set([...mods, ...extra])]);
    setMods([]);
  };

  const reset = (el: HTMLTextAreaElement) => {
    el.value = SENTINEL;
    el.setSelectionRange?.(SENTINEL.length, SENTINEL.length);
  };

  const onInput = (e: FormEvent<HTMLTextAreaElement>) => {
    const ev = e.nativeEvent as InputEvent;
    const el = e.currentTarget;
    if (ev.isComposing) return; // predictive keyboards: wait for the finished word
    switch (ev.inputType) {
      case "insertText":
      case "insertFromPaste":
        if (ev.data) onText(ev.data);
        else if (el.value.replace(SENTINEL, "")) onText(el.value.replace(SENTINEL, ""));
        break;
      case "insertLineBreak":
      case "insertParagraph":
        onKey("enter", []);
        break;
      case "deleteContentBackward":
        onKey("backspace", []);
        break;
      case "deleteContentForward":
        onKey("delete", []);
        break;
    }
    reset(el);
  };

  const onKeyDown = (e: KeyboardEvent<HTMLTextAreaElement>) => {
    if (e.nativeEvent.isComposing) return;
    const named = NAMED_FROM_KEYBOARD[e.key];
    // Backspace/Enter arrive as input events (handled in onInput); the rest never edit the box,
    // so a hardware keyboard's Esc/Tab/arrows are forwarded here and kept out of the field.
    if (named && e.key !== "Backspace" && e.key !== "Enter") {
      e.preventDefault();
      onKey(named, []);
    }
  };

  const keyButton =
    "flex min-h-11 items-center justify-center rounded-xl bg-zinc-800 px-1 text-sm text-zinc-100 ring-1 ring-zinc-700 active:bg-zinc-700 disabled:opacity-40";

  return (
    <section aria-label="Keyboard" className="space-y-3">
      <textarea
        ref={box}
        aria-label="Type on the TV"
        placeholder="Tap here to type on the TV"
        disabled={disabled}
        defaultValue={SENTINEL}
        rows={1}
        autoCapitalize="off"
        autoCorrect="off"
        spellCheck={false}
        onInput={onInput}
        onKeyDown={onKeyDown}
        onCompositionEnd={(e) => {
          if (e.data) onText(e.data);
          reset(e.currentTarget);
        }}
        className="min-h-12 w-full resize-none rounded-2xl bg-zinc-900 px-4 py-3 text-base text-zinc-100 ring-1 ring-zinc-700 placeholder:text-zinc-500 focus:ring-2 focus:ring-indigo-500 disabled:opacity-40"
      />

      <div role="group" aria-label="Modifiers" className="flex gap-2">
        {MODS.map(({ mod, label }) => (
          <button
            key={mod}
            type="button"
            disabled={disabled}
            aria-pressed={mods.includes(mod)}
            onClick={() => toggle(mod)}
            className={`flex min-h-11 flex-1 items-center justify-center rounded-xl text-sm ring-1 disabled:opacity-40 ${
              mods.includes(mod) ? "bg-indigo-600 text-white ring-indigo-400" : "bg-zinc-800 text-zinc-100 ring-zinc-700"
            }`}
          >
            {label}
          </button>
        ))}
      </div>

      <div role="group" aria-label="Keys" className="grid grid-cols-4 gap-2">
        {ARROWS.map((k) => (
          <button key={k.key} type="button" disabled={disabled} aria-label={`Key ${k.key}`} onClick={() => press(k.key)} className={keyButton}>
            {k.label}
          </button>
        ))}
        {KEYS.map((k) => (
          <button key={k.key} type="button" disabled={disabled} aria-label={`Key ${k.key}`} onClick={() => press(k.key)} className={keyButton}>
            {k.label}
          </button>
        ))}
      </div>

      <div role="group" aria-label="Function keys" className="grid grid-cols-6 gap-2">
        {FUNCTION_KEYS.map((k) => (
          <button key={k.key} type="button" disabled={disabled} aria-label={`Key ${k.key}`} onClick={() => press(k.key)} className={keyButton}>
            {k.label}
          </button>
        ))}
      </div>

      <div role="group" aria-label="Browser shortcuts" className="grid grid-cols-3 gap-2">
        {SHORTCUTS.map((s) => (
          <button
            key={s.label}
            type="button"
            disabled={disabled}
            onClick={() => press(s.key, s.mods)}
            className="flex min-h-11 items-center justify-center rounded-xl bg-zinc-800/60 px-1 text-xs text-zinc-200 ring-1 ring-zinc-700 active:bg-zinc-700 disabled:opacity-40"
          >
            {s.label}
          </button>
        ))}
      </div>
    </section>
  );
}
