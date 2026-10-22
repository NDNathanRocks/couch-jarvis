import { useState } from "react";
import { formatElapsed, type MicMode, type Voice } from "../hooks/useVoice";
import { MAX_RECORDING_MS } from "../utils/audioRecorder";
import { Icon } from "./Icon";

interface Props {
  /** The connection is not up: typing still works, sending and the mic do not. */
  disabled: boolean;
  voice: Voice;
  micMode: MicMode;
  onSend: (text: string) => boolean;
}

const BARS = [10, 22, 34, 18, 40, 26, 12, 30, 20, 36, 14, 24, 8];

/**
 * The one place to give a command: type it or say it. It lives in a dock at the bottom so the
 * thumb reaches it, stays one row tall until you talk, and only then grows into a short
 * "listening" panel instead of taking over the screen.
 */
export function CommandBar({ disabled, voice, micMode, onSend }: Props) {
  const [text, setText] = useState("");
  const hold = micMode === "hold";
  const micLabel = hold ? "Hold to talk" : "Tap to talk";

  if (voice.active) {
    return (
      <Dock>
        <div className="rounded-3xl bg-surface p-4 ring-1 ring-rose-400/30" role="group" aria-label="Listening">
          <div className="flex items-center justify-between">
            <p className="flex items-center gap-2 text-sm font-semibold text-rose-300">
              <span className="h-2.5 w-2.5 animate-pulse rounded-full bg-rose-400" aria-hidden />
              Listening
            </p>
            <p className="text-xs tabular-nums text-muted">
              {formatElapsed(voice.elapsedMs)} of {formatElapsed(MAX_RECORDING_MS)}
            </p>
          </div>
          <div className="mt-3 flex h-11 items-center justify-center gap-1" aria-hidden>
            {BARS.map((h, i) => (
              <span
                key={i}
                className="w-1 animate-pulse rounded-full bg-rose-400"
                style={{ height: h, animationDelay: `${i * 90}ms` }}
              />
            ))}
          </div>
          {hold ? (
            <p className="mt-3 text-center text-sm text-muted">Release to send</p>
          ) : (
            <div className="mt-3 flex gap-2">
              <button
                type="button"
                onClick={voice.cancel}
                className="min-h-12 rounded-full px-5 text-sm font-medium text-zinc-200 ring-1 ring-line active:bg-surface-2"
              >
                Cancel
              </button>
              <button
                type="button"
                onClick={voice.toggle}
                aria-label="Send recording"
                className="flex min-h-12 flex-1 items-center justify-center gap-2 rounded-full bg-rose-400 text-[15px] font-bold text-ink active:bg-rose-300"
              >
                <Icon name="stop" size={18} />
                Tap to send
              </button>
            </div>
          )}
        </div>
      </Dock>
    );
  }

  const hasText = text.trim() !== "";
  return (
    <Dock>
      {!voice.supported && (
        <p className="mb-2 px-2 text-center text-xs text-muted">Voice needs a secure (https) connection. Type instead.</p>
      )}
      <form
        className="flex items-center gap-2.5"
        onSubmit={(e) => {
          e.preventDefault();
          if (onSend(text)) setText("");
        }}
      >
        <input
          value={text}
          onChange={(e) => setText(e.target.value)}
          maxLength={500}
          placeholder="Say or type a command"
          aria-label="Type a command"
          enterKeyHint="send"
          autoCapitalize="none"
          className="h-[52px] min-w-0 flex-1 rounded-full bg-surface px-5 text-[15px] text-zinc-100 ring-1 ring-line placeholder:text-muted focus:outline-none focus:ring-2 focus:ring-accent"
        />
        {hasText ? (
          <button
            type="submit"
            disabled={disabled}
            aria-label="Send"
            className="flex h-14 w-14 flex-none items-center justify-center rounded-full bg-accent text-ink active:brightness-90 disabled:opacity-40"
          >
            <Icon name="send" size={24} />
          </button>
        ) : (
          <button
            type="button"
            disabled={disabled || !voice.supported}
            aria-label={micLabel}
            title={voice.supported ? micLabel : "Voice needs a secure (https) connection"}
            onClick={hold ? undefined : voice.toggle}
            onPointerDown={hold ? voice.holdStart : undefined}
            onPointerUp={hold ? voice.holdEnd : undefined}
            onPointerCancel={hold ? voice.holdEnd : undefined}
            onContextMenu={(e) => e.preventDefault()}
            style={{ touchAction: "none", WebkitUserSelect: "none", userSelect: "none" }}
            className="flex h-14 w-14 flex-none items-center justify-center rounded-full bg-accent text-ink active:brightness-90 disabled:opacity-40"
          >
            <Icon name="mic" size={24} />
          </button>
        )}
      </form>
    </Dock>
  );
}

function Dock({ children }: { children: React.ReactNode }) {
  return (
    <div className="fixed inset-x-0 bottom-0 z-40 border-t border-line/60 bg-canvas/95 backdrop-blur">
      <div className="mx-auto max-w-md px-3 pb-[calc(env(safe-area-inset-bottom)+0.75rem)] pt-2.5">{children}</div>
    </div>
  );
}
