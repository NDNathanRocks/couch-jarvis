import { useEffect } from "react";
import type { Toast } from "../state/roomReducer";

const KIND = {
  ok: "bg-emerald-600/95 text-white",
  error: "bg-rose-600/95 text-white",
  info: "bg-zinc-700/95 text-white",
} as const;

export const TOAST_MS = 4000;
/** A toast that asks a question stays long enough to read and answer. */
export const SUGGEST_TOAST_MS = 15000;

interface ItemProps {
  toast: Toast;
  onDismiss: (id: string) => void;
  onSuggest?: (text: string) => void;
}

function ToastItem({ toast, onDismiss, onSuggest }: ItemProps) {
  const ms = toast.suggest ? SUGGEST_TOAST_MS : TOAST_MS;
  useEffect(() => {
    const id = setTimeout(() => onDismiss(toast.id), ms);
    return () => clearTimeout(id);
  }, [toast.id, ms, onDismiss]);

  if (toast.suggest) {
    const { label, text } = toast.suggest;
    return (
      <li className="pointer-events-auto rounded-2xl bg-surface p-4 text-sm shadow-lg ring-1 ring-accent/60">
        <p className="font-medium text-zinc-100">{toast.text}</p>
        <div className="mt-3 flex gap-2">
          <button
            type="button"
            onClick={() => {
              onSuggest?.(text);
              onDismiss(toast.id);
            }}
            className="min-h-11 flex-1 rounded-full bg-accent px-4 text-sm font-bold text-ink active:brightness-90"
          >
            {label}
          </button>
          <button
            type="button"
            onClick={() => onDismiss(toast.id)}
            className="min-h-11 rounded-full px-4 text-sm text-zinc-200 ring-1 ring-line active:bg-surface-2"
          >
            Not now
          </button>
        </div>
      </li>
    );
  }
  return (
    <li
      className={`pointer-events-auto rounded-xl px-4 py-3 text-sm shadow-lg ${KIND[toast.kind]} ${toast.mine ? "" : "opacity-80"}`}
      onClick={() => onDismiss(toast.id)}
    >
      {toast.text}
    </li>
  );
}

export function Toasts({
  toasts,
  onDismiss,
  onSuggest,
}: {
  toasts: Toast[];
  onDismiss: (id: string) => void;
  onSuggest?: (text: string) => void;
}) {
  return (
    <ul
      role="status"
      aria-live="polite"
      className="pointer-events-none fixed inset-x-0 bottom-0 z-50 mx-auto mb-[calc(env(safe-area-inset-bottom)+5.5rem)] flex max-w-md flex-col gap-2 px-4"
    >
      {toasts.map((t) => (
        <ToastItem key={t.id} toast={t} onDismiss={onDismiss} onSuggest={onSuggest} />
      ))}
    </ul>
  );
}
