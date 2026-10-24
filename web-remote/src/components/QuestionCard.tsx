import { useEffect } from "react";
import type { AskCard } from "../state/roomReducer";

interface Props {
  ask: AskCard;
  disabled: boolean;
  /** The mic is open for the answer. */
  listening: boolean;
  onAnswer: (text: string) => void;
  onDismiss: () => void;
}

/** The assistant needs one more detail: answer by voice, by tapping a choice, or by typing. */
export function QuestionCard({ ask, disabled, listening, onAnswer, onDismiss }: Props) {
  useEffect(() => {
    const wait = Math.max(0, ask.expiresAt - Date.now());
    const id = setTimeout(onDismiss, wait);
    return () => clearTimeout(id);
  }, [ask.id, ask.expiresAt, onDismiss]);

  return (
    <section aria-label="Question" className="space-y-3 rounded-3xl bg-surface p-4 ring-1 ring-accent/40">
      <div className="flex items-start justify-between gap-3">
        <p className="text-[15px] font-medium text-zinc-100">{ask.question}</p>
        <button
          type="button"
          onClick={onDismiss}
          aria-label="Dismiss question"
          className="min-h-11 min-w-11 flex-none rounded-full text-sm text-muted active:bg-surface-2"
        >
          ✕
        </button>
      </div>
      {listening && <p className="text-xs text-rose-300">Listening for your answer…</p>}
      {ask.choices.length > 0 && (
        <ul className="flex flex-wrap gap-2">
          {ask.choices.map((choice) => (
            <li key={choice}>
              <button
                type="button"
                disabled={disabled}
                onClick={() => onAnswer(choice)}
                className="min-h-11 rounded-full bg-surface-2 px-4 text-[13px] text-zinc-100 ring-1 ring-line active:brightness-90 disabled:opacity-40"
              >
                {choice}
              </button>
            </li>
          ))}
        </ul>
      )}
    </section>
  );
}
