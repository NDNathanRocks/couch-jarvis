import { useEffect, useState } from "react";
import type { TimerInfo } from "../protocol";

export function remainingLabel(endsAtSeconds: number, nowMs: number): string {
  const left = Math.max(0, Math.ceil(endsAtSeconds - nowMs / 1000));
  const h = Math.floor(left / 3600);
  const m = Math.floor((left % 3600) / 60);
  const s = left % 60;
  const mm = String(m).padStart(h > 0 ? 2 : 1, "0");
  return `${h > 0 ? `${h}:` : ""}${mm}:${String(s).padStart(2, "0")}`;
}

export function TimerList({ timers, now = Date.now }: { timers: TimerInfo[]; now?: () => number }) {
  const [tick, setTick] = useState(now());
  useEffect(() => {
    if (timers.length === 0) return;
    const id = setInterval(() => setTick(now()), 1000);
    return () => clearInterval(id);
  }, [timers.length, now]);

  if (timers.length === 0) return null;
  return (
    <section aria-label="Active timers" className="space-y-2">
      <h2 className="text-xs font-semibold uppercase tracking-wide text-zinc-500">Timers</h2>
      <ul className="space-y-2">
        {timers.map((t) => (
          <li key={t.timer_id} className="flex items-center justify-between rounded-xl bg-zinc-900 px-4 py-3 ring-1 ring-zinc-800">
            <span className="text-sm text-zinc-200">
              {t.label}
              {t.action === "pause" && <span className="ml-2 text-xs text-zinc-500">then pause</span>}
            </span>
            <span className="font-mono text-lg tabular-nums text-sky-300">{remainingLabel(t.ends_at, tick)}</span>
          </li>
        ))}
      </ul>
    </section>
  );
}
