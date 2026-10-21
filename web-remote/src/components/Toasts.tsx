import { useEffect } from "react";
import type { Toast } from "../state/roomReducer";

const KIND = {
  ok: "bg-emerald-600/95 text-white",
  error: "bg-rose-600/95 text-white",
  info: "bg-zinc-700/95 text-white",
} as const;

export const TOAST_MS = 4000;

function ToastItem({ toast, onDismiss }: { toast: Toast; onDismiss: (id: string) => void }) {
  useEffect(() => {
    const id = setTimeout(() => onDismiss(toast.id), TOAST_MS);
    return () => clearTimeout(id);
  }, [toast.id, onDismiss]);
  return (
    <li
      className={`pointer-events-auto rounded-xl px-4 py-3 text-sm shadow-lg ${KIND[toast.kind]} ${toast.mine ? "" : "opacity-80"}`}
      onClick={() => onDismiss(toast.id)}
    >
      {toast.text}
    </li>
  );
}

export function Toasts({ toasts, onDismiss }: { toasts: Toast[]; onDismiss: (id: string) => void }) {
  return (
    <ul
      role="status"
      aria-live="polite"
      className="pointer-events-none fixed inset-x-0 bottom-0 z-50 mx-auto flex max-w-md flex-col gap-2 px-4 pb-[calc(env(safe-area-inset-bottom)+1rem)]"
    >
      {toasts.map((t) => (
        <ToastItem key={t.id} toast={t} onDismiss={onDismiss} />
      ))}
    </ul>
  );
}
