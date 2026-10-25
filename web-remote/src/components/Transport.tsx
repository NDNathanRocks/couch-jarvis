import type { QuickAction } from "../protocol";
import { Icon, type IconName } from "./Icon";

interface Props {
  playing: boolean;
  disabled: boolean;
  onPress: (action: QuickAction) => void;
}

interface Side {
  action: QuickAction;
  label: string;
  icon: IconName;
  size: string;
}

const LEFT: readonly Side[] = [
  { action: "seek_back_10", label: "Back 10s", icon: "back10", size: "h-12 w-12" },
  { action: "prev", label: "Previous", icon: "prev", size: "h-[52px] w-[52px]" },
];
const RIGHT: readonly Side[] = [
  { action: "next", label: "Next", icon: "next", size: "h-[52px] w-[52px]" },
  { action: "seek_fwd_10", label: "Forward 10s", icon: "fwd10", size: "h-12 w-12" },
];

/** Skip back, previous, play/pause (the big one), next, skip forward. */
export function Transport({ playing, disabled, onPress }: Props) {
  const side = (b: Side) => (
    <button key={b.action} type="button" disabled={disabled} onClick={() => onPress(b.action)} aria-label={b.label} className={`press glass-btn ${b.size}`}>
      <Icon name={b.icon} size={22} />
    </button>
  );
  return (
    <div className="rise flex items-center justify-between px-1" role="group" aria-label="Transport">
      {LEFT.map(side)}
      <button
        type="button"
        disabled={disabled}
        onClick={() => onPress("play_pause")}
        aria-label="Play / Pause"
        className="press play-glow flex h-[72px] w-[72px] items-center justify-center rounded-full bg-gradient-to-b from-white to-zinc-300 text-ink disabled:opacity-40"
      >
        <span key={playing ? "pause" : "play"} className="pop flex">
          <Icon name={playing ? "pause" : "play"} size={30} />
        </span>
      </button>
      {RIGHT.map(side)}
    </div>
  );
}
