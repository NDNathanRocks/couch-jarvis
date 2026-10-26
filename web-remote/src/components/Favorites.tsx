import { useEffect, useRef, useState, type ReactNode } from "react";
import type { Modifier } from "../protocol";
import {
  FAV_GROUPS,
  LIBRARY,
  MAX_CUSTOM,
  MOD_LABEL,
  NAMED_KEYS,
  SLOTS,
  assign,
  blockedReason,
  comboLabel,
  favSupported,
  findFav,
  loadCustomKeys,
  makeCustomKey,
  saveCustomKeys,
  type CustomKey,
  type Fav,
} from "../utils/favorites";
import { tap } from "../utils/haptics";
import { Icon } from "./Icon";

interface Props {
  slots: readonly (string | null)[];
  /** False: nothing can be sent (not connected). */
  connected: boolean;
  /** Keys need the PC's remote-input setting on. */
  keysAllowed: boolean;
  /** The PC app version: buttons it is too old for are hidden or greyed. */
  daemonVersion: string;
  onSend: (fav: Fav) => void;
  onChange: (slots: (string | null)[]) => void;
}

const LONG_PRESS_MS = 550;

/** Two rows of four buttons the user fills from the library. Long-press or Edit rearranges them. */
export function Favorites({ slots, connected, keysAllowed, daemonVersion, onSend, onChange }: Props) {
  const [editing, setEditing] = useState(false);
  const [picking, setPicking] = useState<number | null>(null);
  const [custom, setCustom] = useState<CustomKey[]>(loadCustomKeys);
  const hold = useRef<ReturnType<typeof setTimeout> | null>(null);
  const held = useRef(false);

  const startHold = () => {
    held.current = false;
    hold.current = setTimeout(() => {
      held.current = true;
      tap(25);
      setEditing(true);
    }, LONG_PRESS_MS);
  };
  const stopHold = () => {
    if (hold.current) clearTimeout(hold.current);
    hold.current = null;
  };
  useEffect(() => stopHold, []);

  const usable = (fav: Fav) => connected && favSupported(fav, daemonVersion) && (fav.send.kind !== "key" || keysAllowed);
  const changeCustom = (next: CustomKey[]) => {
    setCustom(next);
    saveCustomKeys(next);
  };

  return (
    <section aria-label="Favourites" className="space-y-2.5">
      <div className="flex items-center justify-between px-1">
        <h2 className="text-xs font-semibold uppercase tracking-widest text-muted">Favourites</h2>
        <button
          type="button"
          onClick={() => {
            setEditing((e) => !e);
            setPicking(null);
          }}
          aria-pressed={editing}
          className="press flex min-h-11 min-w-11 items-center justify-center gap-1.5 rounded-full px-3 text-xs font-semibold text-accent"
        >
          <Icon name={editing ? "check" : "pencil"} size={14} />
          {editing ? "Done" : "Edit"}
        </button>
      </div>

      <div className="grid grid-cols-4 gap-2.5">
        {Array.from({ length: SLOTS }, (_, i) => {
          const fav = findFav(slots[i] ?? null, custom);
          const tile = "glass-btn press relative flex aspect-square flex-col items-center justify-center gap-1.5 rounded-[22px] px-1 text-center text-[11px] font-medium leading-tight";
          if (!fav) {
            return (
              <button
                key={i}
                type="button"
                onClick={() => {
                  setEditing(true);
                  setPicking(i);
                }}
                aria-label={`Add a favourite to slot ${i + 1}`}
                className={`${tile} !bg-transparent text-zinc-500 ring-1 !ring-dashed ring-white/15 ${editing ? "" : "opacity-50"}`}
              >
                <Icon name="plus" size={20} />
              </button>
            );
          }
          return (
            <button
              key={i}
              type="button"
              disabled={!editing && !usable(fav)}
              aria-label={editing ? `Change ${fav.label}` : fav.label}
              onPointerDown={editing ? undefined : startHold}
              onPointerUp={stopHold}
              onPointerLeave={stopHold}
              onPointerCancel={stopHold}
              onContextMenu={(e) => e.preventDefault()}
              onClick={() => {
                if (held.current) {
                  held.current = false;
                  return;
                }
                if (editing) setPicking(i);
                else {
                  tap();
                  onSend(fav);
                }
              }}
              style={{ animationDelay: `${(i % 4) * 40 + Math.floor(i / 4) * 80}ms` }}
              className={`${tile} rise ${editing ? "jiggle" : ""}`}
            >
              {fav.icon ? <Icon name={fav.icon} size={22} /> : <span className="text-base font-bold">{fav.label}</span>}
              {fav.icon && <span className="line-clamp-2 px-0.5">{fav.label}</span>}
            </button>
          );
        })}
      </div>

      {picking !== null && (
        <Picker
          current={slots[picking] ?? null}
          used={slots}
          custom={custom}
          daemonVersion={daemonVersion}
          keysAllowed={keysAllowed}
          onPick={(id) => {
            onChange(assign(slots, picking, id));
            setPicking(null);
          }}
          onCreate={(made) => {
            changeCustom([...custom, made]);
            onChange(assign(slots, picking, made.id));
            setPicking(null);
          }}
          onDelete={(id) => {
            changeCustom(custom.filter((c) => c.id !== id));
            if (slots.includes(id)) onChange(slots.map((s) => (s === id ? null : s)));
          }}
          onClose={() => setPicking(null)}
        />
      )}
    </section>
  );
}

function Picker({
  current,
  used,
  custom,
  daemonVersion,
  keysAllowed,
  onPick,
  onCreate,
  onDelete,
  onClose,
}: {
  current: string | null;
  used: readonly (string | null)[];
  custom: readonly CustomKey[];
  daemonVersion: string;
  keysAllowed: boolean;
  onPick: (id: string | null) => void;
  onCreate: (made: CustomKey) => void;
  onDelete: (id: string) => void;
  onClose: () => void;
}) {
  const [creating, setCreating] = useState(false);
  const closeRef = useRef<HTMLButtonElement>(null);
  useEffect(() => {
    closeRef.current?.focus();
    const onKey = (e: KeyboardEvent) => e.key === "Escape" && onClose();
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [onClose]);

  const choice = (fav: Fav, extra?: ReactNode) => {
    const inUse = used.includes(fav.id);
    return (
      <div key={fav.id} className="relative">
        <button
          type="button"
          onClick={() => onPick(fav.id)}
          aria-label={fav.label}
          aria-current={fav.id === current || undefined}
          className={`glass-btn press flex min-h-[4.5rem] w-full flex-col items-center justify-center gap-1 rounded-2xl px-1 text-center text-[11px] leading-tight ${fav.id === current ? "!ring-2 !ring-accent" : ""} ${inUse && fav.id !== current ? "opacity-50" : ""}`}
        >
          {fav.icon ? <Icon name={fav.icon} size={20} /> : <span className="text-sm font-bold">{fav.label}</span>}
          {fav.icon && <span className="line-clamp-2">{fav.label}</span>}
        </button>
        {extra}
      </div>
    );
  };

  return (
    <div role="dialog" aria-modal="true" aria-label="Choose a favourite" className="fade fixed inset-0 z-50 flex flex-col justify-end bg-black/60">
      <button type="button" aria-label="Close" tabIndex={-1} className="flex-1 cursor-default" onClick={onClose} />
      <div className="sheet mx-auto max-h-[85%] w-full max-w-md overflow-y-auto rounded-t-[32px] bg-canvas px-4 pb-[calc(env(safe-area-inset-bottom)+1.25rem)] pt-3 ring-1 ring-white/10">
        <div className="mx-auto mb-3 h-1 w-10 rounded-full bg-white/20" aria-hidden />
        <header className="mb-3 flex items-center gap-2">
          <h2 className="flex-1 text-lg font-semibold">{creating ? "New shortcut" : "Choose a button"}</h2>
          {current && !creating && (
            <button type="button" onClick={() => onPick(null)} className="press flex min-h-11 items-center gap-1.5 rounded-full px-3 text-sm text-rose-300">
              <Icon name="trash" size={16} /> Remove
            </button>
          )}
          <button ref={closeRef} type="button" onClick={creating ? () => setCreating(false) : onClose} className="press min-h-11 rounded-full px-3 text-sm text-zinc-300">
            {creating ? "Back" : "Close"}
          </button>
        </header>
        {creating ? (
          <ShortcutEditor onSave={onCreate} />
        ) : (
          FAV_GROUPS.map((group) => {
            if (group === "Custom") {
              return (
                <div key={group} className="mb-4">
                  <h3 className="mb-2 px-1 text-xs font-semibold uppercase tracking-widest text-muted">Your shortcuts</h3>
                  {!keysAllowed && <p className="mb-2 px-1 text-xs text-zinc-400">Shortcuts press keys on the PC, so they need the touchpad and keys turned on for this phone.</p>}
                  <div className="grid grid-cols-4 gap-2">
                    {custom.map((c) => {
                      const fav = findFav(c.id, custom);
                      if (!fav) return null;
                      return choice(
                        fav,
                        <button
                          type="button"
                          aria-label={`Delete ${fav.label}`}
                          onClick={() => onDelete(c.id)}
                          className="press absolute -right-1 -top-1 flex h-7 w-7 items-center justify-center rounded-full bg-zinc-800 text-rose-300 ring-1 ring-white/15"
                        >
                          <Icon name="close" size={12} />
                        </button>,
                      );
                    })}
                    {custom.length < MAX_CUSTOM && (
                      <button
                        type="button"
                        onClick={() => setCreating(true)}
                        className="press flex min-h-[4.5rem] flex-col items-center justify-center gap-1 rounded-2xl px-1 text-center text-[11px] leading-tight text-accent ring-1 ring-dashed ring-accent/50"
                      >
                        <Icon name="plus" size={20} />
                        New shortcut
                      </button>
                    )}
                  </div>
                </div>
              );
            }
            const shown = LIBRARY.filter((f) => f.group === group && favSupported(f, daemonVersion));
            if (shown.length === 0) return null;
            return (
              <div key={group} className="mb-4">
                <h3 className="mb-2 px-1 text-xs font-semibold uppercase tracking-widest text-muted">{group}</h3>
                <div className="grid grid-cols-4 gap-2">{shown.map((fav) => choice(fav))}</div>
              </div>
            );
          })
        )}
      </div>
    </div>
  );
}

const LETTERS = [..."abcdefghijklmnopqrstuvwxyz0123456789"].map((k) => [k, k.toUpperCase()] as const);

/** Build a keyboard shortcut: hold-keys (Ctrl, Alt, Shift) plus one key, and an optional name. */
function ShortcutEditor({ onSave }: { onSave: (made: CustomKey) => void }) {
  const [mods, setMods] = useState<Modifier[]>([]);
  const [key, setKey] = useState("");
  const [label, setLabel] = useState("");
  const blocked = key ? blockedReason(key, mods) : null;
  const toggle = (m: Modifier) => setMods((now) => (now.includes(m) ? now.filter((x) => x !== m) : [...now, m]));

  return (
    <form
      className="space-y-4"
      onSubmit={(e) => {
        e.preventDefault();
        if (!key || blocked) return;
        onSave(makeCustomKey(label, key, mods));
      }}
    >
      <p className="text-sm text-zinc-400">Pick the keys to press together on the PC, e.g. Ctrl + Shift + T to reopen a closed tab.</p>
      <fieldset>
        <legend className="mb-2 px-1 text-xs font-semibold uppercase tracking-widest text-muted">Hold</legend>
        <div className="flex gap-2">
          {(["ctrl", "alt", "shift"] as const).map((m) => (
            <button
              key={m}
              type="button"
              aria-pressed={mods.includes(m)}
              onClick={() => toggle(m)}
              className={`press min-h-11 flex-1 rounded-2xl text-sm font-semibold ring-1 ${mods.includes(m) ? "bg-accent text-zinc-950 ring-accent" : "glass-btn ring-white/10"}`}
            >
              {MOD_LABEL[m]}
            </button>
          ))}
        </div>
      </fieldset>
      <label className="block">
        <span className="mb-2 block px-1 text-xs font-semibold uppercase tracking-widest text-muted">Key</span>
        <select value={key} onChange={(e) => setKey(e.target.value)} className="glass-btn min-h-12 w-full rounded-2xl px-3 text-base outline-none">
          <option value="">Choose a key…</option>
          <optgroup label="Letters and numbers">
            {LETTERS.map(([k, name]) => (
              <option key={k} value={k}>
                {name}
              </option>
            ))}
          </optgroup>
          <optgroup label="Special keys">
            {NAMED_KEYS.map(([k, name]) => (
              <option key={k} value={k}>
                {name}
              </option>
            ))}
          </optgroup>
        </select>
      </label>
      <label className="block">
        <span className="mb-2 block px-1 text-xs font-semibold uppercase tracking-widest text-muted">Name (optional)</span>
        <input
          value={label}
          onChange={(e) => setLabel(e.target.value)}
          maxLength={40}
          placeholder={key ? comboLabel(key, mods) : "e.g. Reopen tab"}
          className="glass-btn min-h-12 w-full rounded-2xl px-3 text-base outline-none placeholder:text-zinc-500"
        />
      </label>
      <div className="glass flex items-center justify-between rounded-2xl p-3" aria-live="polite">
        <span className="text-sm text-zinc-400">Presses</span>
        <span className="font-mono text-base font-semibold">{key ? comboLabel(key, mods) : "—"}</span>
      </div>
      {blocked && (
        <p role="alert" className="text-sm text-rose-300">
          {blocked}
        </p>
      )}
      <button type="submit" disabled={!key || blocked !== null} className="press min-h-12 w-full rounded-full bg-accent font-semibold text-zinc-950 disabled:opacity-40">
        Add shortcut
      </button>
    </form>
  );
}
