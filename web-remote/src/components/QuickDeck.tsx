import type { QuickAction } from "../protocol";
import { Icon, type IconName } from "./Icon";

interface DeckButton {
  action: QuickAction;
  label: string;
  icon: IconName;
}

/** The transport row: skip back, previous, play/pause (the big one), next, skip forward. */
const TRANSPORT: readonly DeckButton[] = [
  { action: "seek_back_10", label: "Back 10s", icon: "back10" },
  { action: "prev", label: "Previous", icon: "prev" },
  { action: "play_pause", label: "Play / Pause", icon: "play" },
  { action: "next", label: "Next", icon: "next" },
  { action: "seek_fwd_10", label: "Forward 10s", icon: "fwd10" },
];

const TILES: readonly DeckButton[] = [
  { action: "fullscreen", label: "Fullscreen", icon: "fullscreen" },
  { action: "subtitles", label: "Subtitles", icon: "cc" },
  { action: "vol_down", label: "Volume down", icon: "volDown" },
  { action: "vol_up", label: "Volume up", icon: "volUp" },
  { action: "mute", label: "Mute", icon: "mute" },
  { action: "skip_ad", label: "Skip ad", icon: "skipAd" },
  { action: "close_tab", label: "Close tab", icon: "close" },
  { action: "sleep_display", label: "Screen off", icon: "moon" },
];

export const DECK: readonly DeckButton[] = [...TRANSPORT, ...TILES];

/** Browser controls: the buttons for "go back", "new tab" and friends. */
export const BROWSER_DECK: readonly DeckButton[] = [
  { action: "back", label: "Back page", icon: "back" },
  { action: "forward", label: "Forward page", icon: "forward" },
  { action: "reload", label: "Reload", icon: "reload" },
  { action: "new_tab", label: "New tab", icon: "plus" },
  { action: "prev_tab", label: "Tab left", icon: "tabLeft" },
  { action: "next_tab", label: "Tab right", icon: "tabRight" },
  { action: "scroll_up", label: "Scroll up", icon: "up" },
  { action: "scroll_down", label: "Scroll down", icon: "down" },
];

interface Props {
  onPress: (action: QuickAction) => void;
  disabled: boolean;
}

const ROUND =
  "flex items-center justify-center rounded-full bg-surface text-zinc-100 ring-1 ring-line active:scale-95 active:bg-surface-2 disabled:opacity-40";
const TILE =
  "flex min-h-[4.25rem] flex-col items-center justify-center gap-1.5 rounded-2xl bg-surface px-1 text-[11px] font-medium leading-tight text-zinc-100 ring-1 ring-line active:scale-95 active:bg-surface-2 disabled:opacity-40";

export function QuickDeck({ onPress, disabled }: Props) {
  const press = (action: QuickAction) => () => onPress(action);
  return (
    <section aria-label="Quick deck" className="space-y-3.5">
      <div className="flex items-center justify-between" role="group" aria-label="Transport">
        {TRANSPORT.map((b) => {
          const main = b.action === "play_pause";
          return (
            <button
              key={b.action}
              type="button"
              disabled={disabled}
              onClick={press(b.action)}
              aria-label={b.label}
              className={`${ROUND} ${main ? "h-[68px] w-[68px] !bg-accent text-ink !ring-accent" : b.action === "prev" || b.action === "next" ? "h-[52px] w-[52px]" : "h-12 w-12"}`}
            >
              <Icon name={main ? "play" : b.icon} size={main ? 28 : 22} />
            </button>
          );
        })}
      </div>

      <div className="grid grid-cols-4 gap-2.5" role="group" aria-label="Controls">
        {TILES.map((b) => (
          <button
            key={b.action}
            type="button"
            disabled={disabled}
            onClick={press(b.action)}
            aria-label={b.label}
            className={TILE}
          >
            <Icon name={b.icon} />
            {b.label}
          </button>
        ))}
      </div>

      <div className="grid grid-cols-4 gap-2" role="group" aria-label="Browser">
        {BROWSER_DECK.map((b) => (
          <button
            key={b.action}
            type="button"
            disabled={disabled}
            onClick={press(b.action)}
            aria-label={b.label}
            className="flex min-h-16 flex-col items-center justify-center gap-1 rounded-2xl bg-surface px-1 py-2 text-[11px] leading-tight text-zinc-200 ring-1 ring-line active:scale-95 active:bg-surface-2 disabled:opacity-40"
          >
            <Icon name={b.icon} size={20} />
            {b.label}
          </button>
        ))}
      </div>

      <div className="grid grid-cols-2 gap-2.5">
        <button
          type="button"
          disabled={disabled}
          onClick={press("panic")}
          aria-label="Stop everything"
          className="flex min-h-12 items-center justify-center gap-2 rounded-2xl bg-rose-500/15 text-sm font-semibold text-rose-300 ring-1 ring-rose-400/40 active:bg-rose-500/25 disabled:opacity-40"
        >
          <Icon name="stop" size={16} /> Stop
        </button>
        <button
          type="button"
          disabled={disabled}
          onClick={press("show_qr")}
          className="flex min-h-12 items-center justify-center gap-2 rounded-2xl bg-transparent text-sm text-zinc-300 ring-1 ring-line active:bg-surface disabled:opacity-40"
        >
          Pair a friend (show QR on TV)
        </button>
      </div>
    </section>
  );
}
