import { selectPcOnline, type RoomView } from "../state/roomReducer";
import type { Transport } from "../utils/config";

export interface BadgeInfo {
  label: string;
  tone: "good" | "warn" | "bad";
}

export function badgeInfo(view: RoomView, transport: Transport): BadgeInfo {
  switch (view.connection) {
    case "idle":
      return { label: "Not paired", tone: "warn" };
    case "connecting":
      return { label: "Connecting…", tone: "warn" };
    case "offline":
      return { label: "Reconnecting…", tone: "warn" };
    case "rejected":
      return { label: "Re-pair needed", tone: "bad" };
    case "online":
      if (!selectPcOnline(view)) return { label: "PC offline", tone: "bad" };
      return { label: transport === "lan" ? "Connected · Local" : "Connected", tone: "good" };
  }
}

const TONES = {
  good: "bg-emerald-500/15 text-emerald-300 ring-emerald-500/40",
  warn: "bg-amber-500/15 text-amber-300 ring-amber-500/40",
  bad: "bg-rose-500/15 text-rose-300 ring-rose-500/40",
} as const;

export function ConnectionBadge({ view, transport }: { view: RoomView; transport: Transport }) {
  const { label, tone } = badgeInfo(view, transport);
  return (
    <span
      role="status"
      aria-label={`Connection: ${label}`}
      className={`inline-flex items-center gap-2 rounded-full px-3 py-1 text-sm font-medium ring-1 ${TONES[tone]}`}
    >
      <span className={`h-2 w-2 rounded-full ${tone === "good" ? "bg-emerald-400" : tone === "warn" ? "bg-amber-400 animate-pulse" : "bg-rose-400"}`} />
      {label}
    </span>
  );
}
