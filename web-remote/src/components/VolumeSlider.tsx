import { useEffect, useMemo, useRef, useState } from "react";
import { createThrottle } from "../utils/throttle";
import { tap } from "../utils/haptics";
import { Icon, type IconName } from "./Icon";
import { Slider } from "./Slider";

interface Props {
  level: number;
  muted: boolean;
  disabled: boolean;
  /** Send a level (already throttled). */
  onLevel: (level: number) => void;
  onToggleMute: () => void;
}

/** How long after letting go the bar keeps your value instead of the PC's older report. */
const HOLD_MS = 1500;

export function VolumeSlider({ level, muted, disabled, onLevel, onToggleMute }: Props) {
  const throttle = useMemo(() => createThrottle(onLevel), []); // eslint-disable-line react-hooks/exhaustive-deps
  useEffect(() => {
    throttle.setSend(onLevel);
  });
  useEffect(() => () => throttle.cancel(), [throttle]);

  // The value you just chose wins over the PC's report for a moment (the report lags behind).
  const [held, setHeld] = useState<{ value: number; until: number } | null>(null);
  useEffect(() => {
    if (!held) return;
    const id = setTimeout(() => setHeld(null), Math.max(0, held.until - Date.now()));
    return () => clearTimeout(id);
  }, [held]);
  const shown = held ? held.value : level;
  const effective = muted && !held ? 0 : shown;

  const lastTick = useRef(-1);
  const icon: IconName = effective <= 0 ? "speakerMute" : effective < 50 ? "speakerLow" : "speakerHigh";

  return (
    <section aria-label="Volume" className="glass flex items-center gap-3 rounded-[28px] p-2.5 pr-4">
      <button
        type="button"
        disabled={disabled}
        onClick={() => {
          tap();
          onToggleMute();
        }}
        aria-label="Mute"
        className="press flex h-11 w-11 flex-none items-center justify-center rounded-full bg-white/10 text-zinc-100 disabled:opacity-40"
      >
        <Icon name={icon} />
      </button>
      <Slider
        label="Volume"
        value={shown}
        max={100}
        step={1}
        disabled={disabled}
        valueText={`${Math.round(effective)} percent${muted ? ", muted" : ""}`}
        onChange={(v) => {
          const r = Math.round(v);
          setHeld({ value: r, until: Date.now() + HOLD_MS });
          if (Math.floor(r / 10) !== lastTick.current) {
            lastTick.current = Math.floor(r / 10);
            tap(6);
          }
          throttle.push(r);
        }}
        onCommit={(v) => {
          const r = Math.round(v);
          setHeld({ value: r, until: Date.now() + HOLD_MS });
          throttle.flush(r);
        }}
        className="relative h-11 flex-1 cursor-pointer"
      >
        {({ fraction, dragging }) => (
          <div className={`absolute inset-x-0 top-1/2 -translate-y-1/2 overflow-hidden rounded-full bg-white/10 transition-[height] duration-150 ${dragging ? "h-6" : "h-3"}`}>
            <div className="absolute inset-y-0 left-0 w-full origin-left rounded-full bg-gradient-to-r from-accent/80 to-accent" style={{ transform: `scaleX(${muted && !held ? 0 : fraction})`, transition: dragging ? "none" : "transform 180ms ease-out" }} />
          </div>
        )}
      </Slider>
      <output aria-live="off" className="w-12 flex-none text-right text-sm font-semibold tabular-nums text-zinc-200">
        {Math.round(effective)}%
      </output>
    </section>
  );
}
