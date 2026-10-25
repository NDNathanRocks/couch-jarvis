import { useEffect, useRef, useState } from "react";
import { FAV_GROUPS, LIBRARY, SLOTS, assign, findFav, type Fav } from "../utils/favorites";
import { tap } from "../utils/haptics";
import { Icon } from "./Icon";

interface Props {
  slots: readonly (string | null)[];
  /** False: nothing can be sent (not connected). */
  connected: boolean;
  /** Keys need the PC's remote-input setting on. */
  keysAllowed: boolean;
  onSend: (fav: Fav) => void;
  onChange: (slots: (string | null)[]) => void;
}

const LONG_PRESS_MS = 550;

/** Two rows of four buttons the user fills from the library. Long-press or Edit rearranges them. */
export function Favorites({ slots, connected, keysAllowed, onSend, onChange }: Props) {
  const [editing, setEditing] = useState(false);
  const [picking, setPicking] = useState<number | null>(null);
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

  const usable = (fav: Fav) => connected && (fav.send.kind !== "key" || keysAllowed);

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
          const fav = findFav(slots[i] ?? null);
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
          onPick={(id) => {
            onChange(assign(slots, picking, id));
            setPicking(null);
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
  onPick,
  onClose,
}: {
  current: string | null;
  used: readonly (string | null)[];
  onPick: (id: string | null) => void;
  onClose: () => void;
}) {
  const closeRef = useRef<HTMLButtonElement>(null);
  useEffect(() => {
    closeRef.current?.focus();
    const onKey = (e: KeyboardEvent) => e.key === "Escape" && onClose();
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [onClose]);

  return (
    <div role="dialog" aria-modal="true" aria-label="Choose a favourite" className="fade fixed inset-0 z-50 flex flex-col justify-end bg-black/60">
      <button type="button" aria-label="Close" tabIndex={-1} className="flex-1 cursor-default" onClick={onClose} />
      <div className="sheet mx-auto max-h-[80%] w-full max-w-md overflow-y-auto rounded-t-[32px] bg-canvas px-4 pb-[calc(env(safe-area-inset-bottom)+1.25rem)] pt-3 ring-1 ring-white/10">
        <div className="mx-auto mb-3 h-1 w-10 rounded-full bg-white/20" aria-hidden />
        <header className="mb-3 flex items-center gap-2">
          <h2 className="flex-1 text-lg font-semibold">Choose a button</h2>
          {current && (
            <button type="button" onClick={() => onPick(null)} className="press flex min-h-11 items-center gap-1.5 rounded-full px-3 text-sm text-rose-300">
              <Icon name="trash" size={16} /> Remove
            </button>
          )}
          <button ref={closeRef} type="button" onClick={onClose} className="press min-h-11 rounded-full px-3 text-sm text-zinc-300">
            Close
          </button>
        </header>
        {FAV_GROUPS.map((group) => (
          <div key={group} className="mb-4">
            <h3 className="mb-2 px-1 text-xs font-semibold uppercase tracking-widest text-muted">{group}</h3>
            <div className="grid grid-cols-4 gap-2">
              {LIBRARY.filter((f) => f.group === group).map((fav) => {
                const inUse = used.includes(fav.id);
                return (
                  <button
                    key={fav.id}
                    type="button"
                    onClick={() => onPick(fav.id)}
                    aria-label={fav.label}
                    aria-current={fav.id === current || undefined}
                    className={`glass-btn press flex min-h-[4.5rem] flex-col items-center justify-center gap-1 rounded-2xl px-1 text-center text-[11px] leading-tight ${fav.id === current ? "!ring-2 !ring-accent" : ""} ${inUse && fav.id !== current ? "opacity-50" : ""}`}
                  >
                    {fav.icon ? <Icon name={fav.icon} size={20} /> : <span className="text-sm font-bold">{fav.label}</span>}
                    {fav.icon && <span className="line-clamp-2">{fav.label}</span>}
                  </button>
                );
              })}
            </div>
          </div>
        ))}
      </div>
    </div>
  );
}
