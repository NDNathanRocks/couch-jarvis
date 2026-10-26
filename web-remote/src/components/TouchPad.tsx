import { useEffect, useRef, useState } from "react";
import type { PointerAction } from "../protocol";
import { TouchpadGesture, type PointerEmit } from "../utils/gestures";
import { tap } from "../utils/haptics";

interface Props {
  onPointer: (e: PointerEmit) => void;
  disabled: boolean;
}

/** Cursor speed choices; acceleration is on for all of them (slow finger = precise). */
export const PAD_SPEEDS = { slow: 1.1, normal: 1.6, fast: 2.4 } as const;
export type PadSpeed = keyof typeof PAD_SPEEDS;
const SPEED_KEY = "cj.touchpad.speed";

export function loadPadSpeed(): PadSpeed {
  try {
    const saved = localStorage.getItem(SPEED_KEY);
    if (saved && saved in PAD_SPEEDS) return saved as PadSpeed;
  } catch {
    /* blocked storage */
  }
  return "normal";
}

function savePadSpeed(speed: PadSpeed): void {
  try {
    localStorage.setItem(SPEED_KEY, speed);
  } catch {
    /* private mode: lasts until the page closes */
  }
}

/** A relative-motion touchpad for the PC cursor. The page itself must never scroll under it. */
export function TouchPad({ onPointer, disabled }: Props) {
  const gesture = useRef<TouchpadGesture | null>(null);
  const emit = useRef(onPointer);
  const [speed, setSpeed] = useState<PadSpeed>(loadPadSpeed);
  useEffect(() => {
    emit.current = onPointer;
  });
  useEffect(() => {
    gesture.current = new TouchpadGesture({
      speed: PAD_SPEEDS[speed],
      accel: 1,
      emit: (e) => {
        if (e.action === "click" || e.action === "right_click" || e.action === "drag_start") tap(8);
        emit.current(e);
      },
    });
    return () => gesture.current?.cancel();
  }, [speed]);

  const click = (action: PointerAction) => () => {
    tap(8);
    onPointer({ action });
  };
  const scroll = (notches: number) => () => onPointer({ scroll: notches });
  const button =
    "press glass-btn min-h-12 flex-1 !rounded-2xl text-sm font-medium";

  return (
    <section aria-label="Touchpad" className="space-y-2.5">
      <div className="flex items-center justify-between px-1">
        <h2 className="text-xs font-semibold uppercase tracking-widest text-muted">Touchpad</h2>
        <div role="radiogroup" aria-label="Cursor speed" className="glass flex rounded-full p-0.5 text-xs">
          {(Object.keys(PAD_SPEEDS) as PadSpeed[]).map((s) => (
            <button
              key={s}
              type="button"
              role="radio"
              aria-checked={speed === s}
              onClick={() => {
                setSpeed(s);
                savePadSpeed(s);
              }}
              className={`min-h-9 rounded-full px-3 font-semibold capitalize ${speed === s ? "bg-white/15 text-white" : "text-muted"}`}
            >
              {s}
            </button>
          ))}
        </div>
      </div>
      <div
        role="application"
        aria-label="Touchpad area: drag to move the cursor, tap to click, two fingers to scroll"
        data-testid="touchpad"
        className={`glass relative h-56 touch-none select-none rounded-[28px] ${disabled ? "opacity-40" : ""}`}
        onPointerDown={(e) => {
          if (disabled) return;
          e.currentTarget.setPointerCapture?.(e.pointerId);
          gesture.current?.down(e.pointerId, e.clientX, e.clientY);
        }}
        onPointerMove={(e) => gesture.current?.move(e.pointerId, e.clientX, e.clientY)}
        onPointerUp={(e) => gesture.current?.up(e.pointerId)}
        onPointerCancel={() => gesture.current?.cancel()}
        onContextMenu={(e) => e.preventDefault()}
      >
        <p className="pointer-events-none absolute inset-0 flex items-center justify-center px-6 text-center text-xs text-zinc-600">
          Drag to move · tap to click · two fingers to scroll or right-click · hold then drag to select
        </p>
      </div>
      <div className="flex gap-3">
        <button type="button" disabled={disabled} onClick={click("click")} className={button}>
          Left click
        </button>
        <button type="button" disabled={disabled} onClick={click("double_click")} className={button}>
          Double click
        </button>
        <button type="button" disabled={disabled} onClick={click("right_click")} className={button}>
          Right click
        </button>
      </div>
      <div className="flex gap-3">
        <button type="button" disabled={disabled} onClick={scroll(-3)} className={button} aria-label="Scroll up">
          ▲ Scroll up
        </button>
        <button type="button" disabled={disabled} onClick={scroll(3)} className={button} aria-label="Scroll down">
          ▼ Scroll down
        </button>
      </div>
    </section>
  );
}
