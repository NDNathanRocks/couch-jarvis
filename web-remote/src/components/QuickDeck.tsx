import type { QuickAction } from "../protocol";

interface DeckButton {
  action: QuickAction;
  label: string;
  icon: string;
}

export const DECK: readonly DeckButton[] = [
  { action: "prev", label: "Previous", icon: "⏮" },
  { action: "play_pause", label: "Play / Pause", icon: "⏯" },
  { action: "next", label: "Next", icon: "⏭" },
  { action: "seek_back_10", label: "Back 10s", icon: "⏪" },
  { action: "fullscreen", label: "Fullscreen", icon: "⛶" },
  { action: "seek_fwd_10", label: "Forward 10s", icon: "⏩" },
  { action: "vol_down", label: "Volume down", icon: "🔉" },
  { action: "mute", label: "Mute", icon: "🔇" },
  { action: "vol_up", label: "Volume up", icon: "🔊" },
  { action: "skip_ad", label: "Skip ad", icon: "🚫" },
  { action: "close_tab", label: "Close tab", icon: "✖" },
  { action: "sleep_display", label: "Screen off", icon: "🌙" },
];

interface Props {
  onPress: (action: QuickAction) => void;
  disabled: boolean;
}

export function QuickDeck({ onPress, disabled }: Props) {
  return (
    <section aria-label="Quick deck" className="space-y-3">
      <button
        type="button"
        disabled={disabled}
        onClick={() => onPress("panic")}
        className="flex min-h-14 w-full items-center justify-center gap-2 rounded-2xl bg-rose-600 text-lg font-semibold text-white active:scale-[0.98] disabled:opacity-40"
        aria-label="Stop everything"
      >
        <span aria-hidden>⏹</span> Stop
      </button>
      <div className="grid grid-cols-3 gap-3">
        {DECK.map((b) => (
          <button
            key={b.action}
            type="button"
            disabled={disabled}
            onClick={() => onPress(b.action)}
            aria-label={b.label}
            className="flex min-h-[4.5rem] flex-col items-center justify-center gap-1 rounded-2xl bg-zinc-800 px-1 py-2 text-zinc-100 ring-1 ring-zinc-700 active:scale-95 active:bg-zinc-700 disabled:opacity-40"
          >
            <span className="text-2xl" aria-hidden>
              {b.icon}
            </span>
            <span className="text-[11px] leading-tight text-zinc-300">{b.label}</span>
          </button>
        ))}
      </div>
      <button
        type="button"
        disabled={disabled}
        onClick={() => onPress("show_qr")}
        className="flex min-h-12 w-full items-center justify-center gap-2 rounded-2xl bg-transparent text-sm text-zinc-300 ring-1 ring-zinc-700 active:bg-zinc-800 disabled:opacity-40"
      >
        <span aria-hidden>📱</span> Pair a friend (show QR on TV)
      </button>
    </section>
  );
}
