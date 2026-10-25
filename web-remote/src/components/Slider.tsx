import { useRef, useState, type KeyboardEvent, type PointerEvent, type ReactNode } from "react";

export interface SliderState {
  /** 0..1 position of the thumb. */
  fraction: number;
  dragging: boolean;
}

interface Props {
  label: string;
  value: number;
  max: number;
  /** Keyboard / screen-reader step. */
  step?: number;
  disabled?: boolean;
  /** Text read out for the value, e.g. "45 percent" or "1:20 of 3:45". */
  valueText?: string;
  /** While dragging (and for each key press). */
  onChange: (value: number) => void;
  /** Once, when the finger lifts or a key press settles. */
  onCommit: (value: number) => void;
  className?: string;
  children?: (state: SliderState) => ReactNode;
}

/**
 * A touch slider that works anywhere on the bar (like iOS Control Centre): press, drag, release.
 * Pointer capture keeps the drag alive when the finger leaves the bar, and `touch-none` stops the
 * page scrolling underneath. It is also a real ARIA slider for keyboards and screen readers.
 */
export function Slider({ label, value, max, step = 1, disabled, valueText, onChange, onCommit, className = "", children }: Props) {
  const track = useRef<HTMLDivElement>(null);
  const [drag, setDrag] = useState<number | null>(null);
  const shown = drag ?? value;
  const fraction = max > 0 ? Math.max(0, Math.min(1, shown / max)) : 0;

  const fromPointer = (e: PointerEvent): number => {
    const rect = track.current?.getBoundingClientRect();
    if (!rect || rect.width === 0) return shown;
    return Math.max(0, Math.min(max, ((e.clientX - rect.left) / rect.width) * max));
  };
  const move = (e: PointerEvent) => {
    const v = fromPointer(e);
    setDrag(v);
    onChange(v);
  };
  const release = (e: PointerEvent) => {
    if (drag === null) return;
    const v = fromPointer(e);
    setDrag(null);
    onCommit(v);
  };
  const nudge = (e: KeyboardEvent) => {
    const big = Math.max(step, max / 10);
    const delta = { ArrowRight: step, ArrowUp: step, ArrowLeft: -step, ArrowDown: -step, PageUp: big, PageDown: -big }[e.key];
    let next: number | null = null;
    if (delta !== undefined) next = shown + delta;
    else if (e.key === "Home") next = 0;
    else if (e.key === "End") next = max;
    if (next === null) return;
    e.preventDefault();
    const v = Math.max(0, Math.min(max, next));
    onChange(v);
    onCommit(v);
  };

  return (
    <div
      ref={track}
      role="slider"
      tabIndex={disabled ? -1 : 0}
      aria-label={label}
      aria-valuemin={0}
      aria-valuemax={Math.round(max)}
      aria-valuenow={Math.round(shown)}
      aria-valuetext={valueText}
      aria-disabled={disabled || undefined}
      data-dragging={drag !== null || undefined}
      className={`touch-none select-none outline-none focus-visible:ring-2 focus-visible:ring-accent ${disabled ? "pointer-events-none opacity-40" : ""} ${className}`}
      onPointerDown={(e) => {
        if (disabled) return;
        e.currentTarget.setPointerCapture?.(e.pointerId);
        move(e);
      }}
      onPointerMove={(e) => drag !== null && move(e)}
      onPointerUp={release}
      onPointerCancel={() => setDrag(null)}
      onKeyDown={nudge}
    >
      {children?.({ fraction, dragging: drag !== null })}
    </div>
  );
}
