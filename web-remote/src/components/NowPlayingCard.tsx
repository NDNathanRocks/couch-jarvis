import { useEffect, useState } from "react";
import type { NowPlaying } from "../protocol";

export function formatClock(totalSeconds: number): string {
  const s = Math.max(0, Math.floor(totalSeconds));
  const m = Math.floor(s / 60);
  return `${m}:${String(s % 60).padStart(2, "0")}`;
}

const SOURCES: Record<NowPlaying["source"] & string, string> = {
  youtube: "YouTube",
  ytmusic: "YouTube Music",
  other: "Browser",
};

export function NowPlayingCard({ playing, pending }: { playing: NowPlaying | null; pending: string | null }) {
  if (!playing) {
    return (
      <section className="rounded-2xl bg-surface p-4 text-center text-muted ring-1 ring-line">
        <p className="text-sm">Nothing playing</p>
        {pending && <p className="mt-1 text-xs text-accent">{pending}</p>}
      </section>
    );
  }
  // The PC reports position every few seconds. Remounting on each report (key) restarts the local
  // clock that interpolates in between, so the bar moves smoothly without drifting.
  return <PlayingBody key={`${playing.title}|${playing.position_s}|${playing.is_playing}`} playing={playing} pending={pending} />;
}

function PlayingBody({ playing, pending }: { playing: NowPlaying; pending: string | null }) {
  const [elapsed, setElapsed] = useState(0);
  const isPlaying = playing.is_playing;
  useEffect(() => {
    if (!isPlaying) return;
    const id = setInterval(() => setElapsed((e) => e + 1), 1000);
    return () => clearInterval(id);
  }, [isPlaying]);

  const duration = playing.duration_s ?? 0;
  const now = Math.min(duration || Infinity, (playing.position_s ?? 0) + elapsed);
  const pct = duration > 0 ? Math.min(100, (now / duration) * 100) : 0;
  return (
    <section className="flex gap-3 rounded-2xl bg-surface p-3 ring-1 ring-line" aria-label="Now playing">
      {playing.thumbnail_url ? (
        <img src={playing.thumbnail_url} alt="" className="h-16 w-16 flex-none rounded-lg object-cover" referrerPolicy="no-referrer" />
      ) : (
        <div className="flex h-16 w-16 flex-none items-center justify-center rounded-lg bg-zinc-800 text-2xl" aria-hidden>
          🎵
        </div>
      )}
      <div className="min-w-0 flex-1">
        <p className="truncate font-medium text-zinc-100">{playing.title}</p>
        <p className="text-xs text-zinc-400">
          {SOURCES[playing.source ?? "other"]} · {playing.is_playing ? "Playing" : "Paused"}
        </p>
        <div className="mt-2 h-1.5 overflow-hidden rounded-full bg-zinc-800" role="progressbar" aria-valuenow={Math.round(pct)} aria-valuemin={0} aria-valuemax={100}>
          <div className="h-full bg-accent transition-[width] duration-1000 ease-linear" style={{ width: `${pct}%` }} />
        </div>
        <p className="mt-1 text-[11px] tabular-nums text-zinc-500">
          {formatClock(now)} / {duration > 0 ? formatClock(duration) : "--:--"}
        </p>
        {pending && <p className="mt-1 text-xs text-accent">{pending}</p>}
      </div>
    </section>
  );
}
