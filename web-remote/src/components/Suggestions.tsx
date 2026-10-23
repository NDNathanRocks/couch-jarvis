interface Props {
  disabled: boolean;
  recents: string[];
  onSend: (text: string) => void;
}

/** Things worth trying, shown while nothing is playing. A tap sends it like any typed command. */
const IDEAS = [
  "Open a new tab and play lofi beats on YouTube Music",
  "Search for The Big Bang Theory",
  "Turn on subtitles",
  "Go fullscreen",
  "Set a sleep timer for 30 minutes",
] as const;

export function Suggestions({ disabled, recents, onSend }: Props) {
  const shown = recents.length > 0 ? recents : IDEAS;
  return (
    <section aria-label={recents.length > 0 ? "Recent commands" : "Try saying"} className="space-y-2">
      <h2 className="text-xs font-semibold uppercase tracking-wide text-zinc-500">{recents.length > 0 ? "Recent" : "Try saying"}</h2>
      <ul className="no-scrollbar -mx-4 flex gap-2 overflow-x-auto px-4 pb-1">
        {shown.map((text) => (
          <li key={text} className="flex-none">
            <button
              type="button"
              disabled={disabled}
              onClick={() => onSend(text)}
              className="min-h-11 max-w-[17rem] truncate rounded-full bg-surface px-4 text-[13px] text-zinc-200 ring-1 ring-line active:bg-surface-2 disabled:opacity-40"
            >
              {text}
            </button>
          </li>
        ))}
      </ul>
    </section>
  );
}
