import { useEffect, useMemo, useState } from "react";
import type { NowPlaying } from "../protocol";
import { createThrottle } from "../utils/throttle";
import { Slider } from "./Slider";

export function formatClock(totalSeconds: number): string {
  const s = Math.max(0, Math.floor(totalSeconds));
  const h = Math.floor(s / 3600);
  const m = Math.floor((s % 3600) / 60);
  const sec = String(s % 60).padStart(2, "0");
  return h > 0 ? `${h}:${String(m).padStart(2, "0")}:${sec}` : `${m}:${sec}`;
}

const SOURCES: Record<NowPlaying["source"] & string, string> = {
  youtube: "YouTube",
  ytmusic: "YouTube Music",
  other: "Browser",
};

interface Props {
  playing: NowPlaying | null;
  pending: string | null;
  /** Jump to this many seconds. Undefined: the PC cannot seek, so the bar only shows progress. */
  onSeek?: (seconds: number) => void;
}

export function NowPlayingCard({ playing, pending, onSeek }: Props) {
  if (!playing) {
    return (
      <section className="glass rise rounded-[28px] p-6 text-center" aria-label="Now playing">
        <div className="mx-auto mb-3 flex h-12 w-12 items-center justify-center rounded-full bg-white/5 text-xl" aria-hidden>
          🛋️
        </div>
        <p className="text-sm text-muted">Nothing playing</p>
        {pending && <p className="mt-1 text-xs text-accent">{pending}</p>}
      </section>
    );
  }
  // A new title starts afresh. Position reports must NOT remount the body: that would cut short
  // a finger still dragging the seek bar (each seek makes the PC report a new position).
  return <PlayingBody key={playing.title} playing={playing} pending={pending} onSeek={onSeek} />;
}

function PlayingBody({ playing, pending, onSeek }: { playing: NowPlaying; pending: string | null; onSeek?: (s: number) => void }) {
  const [elapsed, setElapsed] = useState(0);
  const [landed, setLanded] = useState<number | null>(null); // where you let go, until the PC reports back
  const isPlaying = playing.is_playing;
  // Each report from the PC restarts the local clock that moves the bar smoothly in between.
  const report = `${playing.position_s}|${isPlaying}`;
  const [lastReport, setLastReport] = useState(report);
  if (report !== lastReport) {
    setLastReport(report);
    setElapsed(0);
  }
  useEffect(() => {
    if (!isPlaying) return;
    const id = setInterval(() => setElapsed((e) => e + 1), 1000);
    return () => clearInterval(id);
  }, [isPlaying]);
  useEffect(() => {
    if (landed === null) return;
    const id = setTimeout(() => setLanded(null), 2500);
    return () => clearTimeout(id);
  }, [landed]);

  // A drag can pass many positions: send a few while moving, and the final one on release.
  const throttle = useMemo(() => createThrottle(() => undefined, 250), []);
  useEffect(() => {
    throttle.setSend((v) => onSeek?.(v));
  });
  useEffect(() => () => throttle.cancel(), [throttle]);

  const duration = playing.duration_s ?? 0;
  const known = Number.isFinite(duration) && duration > 0;
  const clock = Math.min(known ? duration : Infinity, (playing.position_s ?? 0) + elapsed);
  const now = landed ?? clock;
  const pct = known ? Math.min(100, (now / duration) * 100) : 0;

  return (
    <section className="glass rise relative overflow-hidden rounded-[28px] p-4" aria-label="Now playing">
      {playing.thumbnail_url && (
        <img src={playing.thumbnail_url} alt="" aria-hidden className="pointer-events-none absolute inset-0 h-full w-full scale-150 object-cover opacity-30 blur-3xl" referrerPolicy="no-referrer" />
      )}
      <div className="relative flex gap-3.5">
        {playing.thumbnail_url ? (
          <img src={playing.thumbnail_url} alt="" className="h-[72px] w-[72px] flex-none rounded-2xl object-cover shadow-lg shadow-black/40" referrerPolicy="no-referrer" />
        ) : (
          <div className="flex h-[72px] w-[72px] flex-none items-center justify-center rounded-2xl bg-white/10 text-2xl" aria-hidden>
            🎵
          </div>
        )}
        <div className="min-w-0 flex-1">
          <p className="line-clamp-2 text-[15px] font-semibold leading-snug text-white">{playing.title}</p>
          <p className="mt-1 flex items-center gap-2 text-xs text-zinc-300">
            <span className={`eq ${isPlaying ? "eq-on" : ""}`} aria-hidden>
              <i />
              <i />
              <i />
            </span>
            {SOURCES[playing.source ?? "other"]} · {isPlaying ? "Playing" : "Paused"}
          </p>
          {pending && <p className="mt-1 text-xs text-accent">{pending}</p>}
        </div>
      </div>

      <div className="relative mt-4">
        {known && onSeek ? (
          <Slider
            label="Seek"
            value={now}
            max={duration}
            step={10}
            valueText={`${formatClock(now)} of ${formatClock(duration)}`}
            onChange={(v) => {
              setLanded(v);
              throttle.push(v);
            }}
            onCommit={(v) => {
              setLanded(v);
              throttle.flush(v);
            }}
            className="relative h-8 cursor-pointer"
          >
            {({ fraction, dragging }) => (
              <>
                <div className={`absolute inset-x-0 top-1/2 -translate-y-1/2 rounded-full bg-white/15 transition-[height] duration-150 ${dragging ? "h-2.5" : "h-1.5"}`}>
                  <div className="absolute inset-y-0 left-0 w-full origin-left rounded-full bg-white" style={{ transform: `scaleX(${fraction})`, transition: dragging ? "none" : "transform 1s linear" }} />
                </div>
                <div
                  aria-hidden
                  className={`absolute top-1/2 h-4 w-4 -translate-x-1/2 -translate-y-1/2 rounded-full bg-white shadow-md shadow-black/50 transition-transform duration-150 ${dragging ? "scale-150" : ""}`}
                  style={{ left: `${fraction * 100}%` }}
                />
                {dragging && (
                  <div aria-hidden className="pop absolute -top-7 -translate-x-1/2 rounded-lg bg-black/80 px-2 py-0.5 text-xs font-semibold tabular-nums text-white" style={{ left: `${Math.max(8, Math.min(92, fraction * 100))}%` }}>
                    {formatClock(now)}
                  </div>
                )}
              </>
            )}
          </Slider>
        ) : (
          <div className="h-1.5 overflow-hidden rounded-full bg-white/15" role="progressbar" aria-label="Progress" aria-valuenow={Math.round(pct)} aria-valuemin={0} aria-valuemax={100}>
            <div className="h-full bg-white transition-[width] duration-1000 ease-linear" style={{ width: `${pct}%` }} />
          </div>
        )}
        <p className="mt-1 flex justify-between text-[11px] tabular-nums text-zinc-300">
          <span>{formatClock(now)}</span>
          <span>{known ? `-${formatClock(Math.max(0, duration - now))}` : "--:--"}</span>
        </p>
      </div>
    </section>
  );
}
