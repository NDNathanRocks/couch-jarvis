import type { Bookmark } from "../contracts.gen";

interface Props {
  items: Bookmark[];
  disabled: boolean;
  /** Send a command to the TV (the same words you could say). */
  onCommand: (text: string) => void;
}

function host(url: string): string {
  try {
    return new URL(url).hostname.replace(/^www\./, "");
  } catch {
    return "";
  }
}

/** Pages saved with "Save this page": tap one and the TV opens it in the tab it is showing. */
export function BookmarkList({ items, disabled, onCommand }: Props) {
  return (
    <section aria-label="Saved pages" className="space-y-3">
      <button
        type="button"
        disabled={disabled}
        onClick={() => onCommand("save this page")}
        className="flex min-h-12 w-full items-center justify-center rounded-2xl bg-accent text-[15px] font-bold text-ink active:brightness-90 disabled:opacity-40"
      >
        Save the page on the TV
      </button>
      {items.length === 0 ? (
        <p className="rounded-2xl bg-surface p-4 text-sm text-muted ring-1 ring-line">
          Nothing saved yet. Open a page you like, then tap the button above (or say &ldquo;save this page&rdquo;).
        </p>
      ) : (
        <ul className="space-y-2">
          {items.map((item) => (
            <li key={item.id} className="flex items-stretch gap-2">
              <button
                type="button"
                disabled={disabled}
                onClick={() => onCommand(`open my bookmark ${item.id}`)}
                className="min-h-14 min-w-0 flex-1 rounded-2xl bg-surface px-4 text-left ring-1 ring-line active:bg-surface-2 disabled:opacity-40"
              >
                <span className="block truncate text-[15px] font-medium text-zinc-100">{item.title}</span>
                <span className="block truncate text-xs text-muted">{host(item.url)}</span>
              </button>
              <button
                type="button"
                disabled={disabled}
                aria-label={`Remove ${item.title}`}
                onClick={() => onCommand(`remove the bookmark ${item.id}`)}
                className="min-h-14 min-w-12 flex-none rounded-2xl text-muted ring-1 ring-line active:bg-surface disabled:opacity-40"
              >
                ✕
              </button>
            </li>
          ))}
        </ul>
      )}
    </section>
  );
}
