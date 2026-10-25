/** One tap opens a streaming site on the TV ("open netflix" is understood by the PC itself, no AI). */
export interface StreamingApp {
  name: string;
  /** What the PC is told; the PC's fast path knows every one of these (pc-daemon/agent/fastpath.py). */
  command: string;
  color: string;
}

export const APPS: readonly StreamingApp[] = [
  { name: "YouTube", command: "open youtube", color: "#ff3b30" },
  { name: "Netflix", command: "open netflix", color: "#e50914" },
  { name: "Prime Video", command: "open prime video", color: "#00a8e1" },
  { name: "Disney+", command: "open disney plus", color: "#2f6bff" },
  { name: "YT Music", command: "open youtube music", color: "#ff0033" },
  { name: "Spotify", command: "open spotify", color: "#1ed760" },
  { name: "Twitch", command: "open twitch", color: "#9146ff" },
  { name: "Plex", command: "open plex", color: "#e5a00d" },
  { name: "Crunchyroll", command: "open crunchyroll", color: "#f47521" },
];

interface Props {
  disabled: boolean;
  onOpen: (command: string) => void;
}

export function AppsRow({ disabled, onOpen }: Props) {
  return (
    <section aria-label="Apps" className="rise -mx-4">
      <ul className="no-scrollbar flex gap-2 overflow-x-auto px-4 pb-1">
        {APPS.map((app) => (
          <li key={app.name} className="shrink-0">
            <button
              type="button"
              disabled={disabled}
              onClick={() => onOpen(app.command)}
              aria-label={`Open ${app.name}`}
              className="press glass-btn flex min-h-11 items-center gap-2 rounded-full px-4 text-sm font-semibold"
            >
              <span aria-hidden className="size-2.5 rounded-full" style={{ background: app.color }} />
              {app.name}
            </button>
          </li>
        ))}
      </ul>
    </section>
  );
}
