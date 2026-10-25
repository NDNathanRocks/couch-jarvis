import { useEffect, useRef } from "react";
import type { PointerAction } from "../protocol";
import { TouchpadGesture, type PointerEmit } from "../utils/gestures";

interface Props {
  onPointer: (e: PointerEmit) => void;
  disabled: boolean;
}

/** A relative-motion touchpad for the PC cursor. The page itself must never scroll under it. */
export function TouchPad({ onPointer, disabled }: Props) {
  const gesture = useRef<TouchpadGesture | null>(null);
  const emit = useRef(onPointer);
  useEffect(() => {
    emit.current = onPointer;
  });
  useEffect(() => {
    gesture.current = new TouchpadGesture({ emit: (e) => emit.current(e) });
    return () => gesture.current?.cancel();
  }, []);

  const click = (action: PointerAction) => () => onPointer({ action });
  const scroll = (notches: number) => () => onPointer({ scroll: notches });
  const button =
    "press glass-btn min-h-12 flex-1 !rounded-2xl text-sm font-medium";

  return (
    <section aria-label="Touchpad" className="space-y-2.5">
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
