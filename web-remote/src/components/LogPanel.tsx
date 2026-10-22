import { useState } from "react";
import type { LogEntry } from "../state/roomReducer";

interface Props {
  entries: LogEntry[];
  onClear: () => void;
  onClose: () => void;
}

const DOT: Record<LogEntry["level"], string> = {
  ok: "bg-emerald-400",
  error: "bg-rose-500",
  info: "bg-zinc-500",
};

function clock(at: number): string {
  if (!at) return "--:--:--";
  return new Date(at).toLocaleTimeString([], { hour12: false });
}

/** Plain-text version of the log, for pasting into a chat or a bug report. */
export function logToText(entries: LogEntry[]): string {
  return entries
    .map((e) => `${clock(e.at)} ${e.level.toUpperCase()} ${e.text}${e.detail ? `\n    ${e.detail.replace(/\n/g, "\n    ")}` : ""}`)
    .join("\n");
}

export function LogPanel({ entries, onClear, onClose }: Props) {
  const [copied, setCopied] = useState(false);
  const newestFirst = [...entries].reverse();

  const copy = async () => {
    try {
      await navigator.clipboard.writeText(logToText(entries));
      setCopied(true);
      setTimeout(() => setCopied(false), 1500);
    } catch {
      /* clipboard blocked: the text is still on screen */
    }
  };

  return (
    <div
      role="dialog"
      aria-modal="true"
      aria-label="Log"
      className="fixed inset-0 z-50 flex flex-col bg-zinc-950/95 px-4 pb-[env(safe-area-inset-bottom)] pt-[calc(env(safe-area-inset-top)+0.75rem)] backdrop-blur"
    >
      <header className="mb-3 flex items-center gap-2">
        <h2 className="flex-1 text-lg font-semibold">What happened</h2>
        <button type="button" onClick={() => void copy()} className="min-h-11 rounded-xl px-3 text-sm text-zinc-200 ring-1 ring-zinc-700">
          {copied ? "Copied" : "Copy"}
        </button>
        <button type="button" onClick={onClear} className="min-h-11 rounded-xl px-3 text-sm text-zinc-200 ring-1 ring-zinc-700">
          Clear
        </button>
        <button type="button" onClick={onClose} aria-label="Close log" className="min-h-11 rounded-xl bg-zinc-800 px-4 text-sm text-zinc-100">
          Close
        </button>
      </header>
      {newestFirst.length === 0 ? (
        <p className="mt-10 text-center text-sm text-zinc-500">Nothing yet. Commands and problems will show up here.</p>
      ) : (
        <ul className="flex-1 space-y-2 overflow-y-auto pb-6">
          {newestFirst.map((e) => (
            <li key={e.id} className="rounded-xl bg-zinc-900 px-3 py-2 ring-1 ring-zinc-800">
              <div className="flex items-start gap-2">
                <span aria-hidden className={`mt-1.5 h-2.5 w-2.5 shrink-0 rounded-full ${DOT[e.level]}`} />
                <div className="min-w-0 flex-1">
                  <p className="break-words text-sm text-zinc-100">{e.text}</p>
                  {e.detail && <p className="mt-1 whitespace-pre-wrap break-words font-mono text-xs text-zinc-400">{e.detail}</p>}
                </div>
                <time className="shrink-0 text-[11px] tabular-nums text-zinc-500">{clock(e.at)}</time>
              </div>
            </li>
          ))}
        </ul>
      )}
    </div>
  );
}
