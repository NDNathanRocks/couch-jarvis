import { useEffect, useRef, useState } from "react";
import {
  HoldRecorder,
  MAX_RECORDING_MS,
  RecorderFailure,
  voiceSupported,
  type Recording,
} from "../utils/audioRecorder";

interface Props {
  disabled: boolean;
  onRecording: (recording: Recording) => void;
  onProblem: (message: string) => void;
}

const MESSAGES = {
  unsupported: "Voice isn't available here. Use the text box instead.",
  denied: "Microphone permission was denied.",
  too_short: "Hold the button while you speak.",
} as const;

export function HoldToTalk({ disabled, onRecording, onProblem }: Props) {
  const [active, setActive] = useState(false);
  const [supported] = useState(voiceSupported);
  const recorder = useRef<HoldRecorder | null>(null);
  const releasedEarly = useRef(false);

  const finish = async (pending: Promise<Recording>) => {
    setActive(false);
    try {
      onRecording(await pending);
    } catch (err) {
      if (err instanceof RecorderFailure) onProblem(MESSAGES[err.reason]);
      else onProblem("Recording failed.");
    }
  };

  useEffect(() => () => recorder.current?.cancel(), []);

  const begin = async (e: React.PointerEvent<HTMLButtonElement>) => {
    if (disabled || !supported || active) return;
    e.currentTarget.setPointerCapture?.(e.pointerId);
    releasedEarly.current = false;
    const rec = new HoldRecorder((autoStopped) => {
      recorder.current = null; // so releasing the button afterwards cannot send the clip twice
      void finish(autoStopped);
    });
    recorder.current = rec;
    try {
      await rec.start();
    } catch (err) {
      recorder.current = null;
      onProblem(err instanceof RecorderFailure ? MESSAGES[err.reason] : "Could not start the microphone.");
      return;
    }
    navigator.vibrate?.(15);
    if (releasedEarly.current) void finish(rec.stop()); // finger lifted while permission was pending
    else setActive(true);
  };

  const end = () => {
    releasedEarly.current = true;
    const rec = recorder.current;
    if (rec && active) {
      recorder.current = null;
      navigator.vibrate?.(10);
      void finish(rec.stop());
    }
  };

  return (
    <div className="flex flex-col items-center gap-2">
      <button
        type="button"
        disabled={disabled || !supported}
        onPointerDown={(e) => void begin(e)}
        onPointerUp={end}
        onPointerCancel={end}
        onContextMenu={(e) => e.preventDefault()}
        aria-label="Hold to talk"
        aria-pressed={active}
        style={{ touchAction: "none", WebkitUserSelect: "none", userSelect: "none" }}
        className={`relative flex h-32 w-32 items-center justify-center rounded-full text-5xl shadow-lg ring-4 transition disabled:opacity-40 ${
          active ? "scale-110 bg-sky-500 ring-sky-300" : "bg-sky-600 ring-sky-800"
        }`}
      >
        <span aria-hidden>🎙</span>
        {active && (
          <span
            aria-hidden
            className="absolute inset-0 animate-ping rounded-full bg-sky-400/40"
            style={{ animationDuration: `${MAX_RECORDING_MS}ms` }}
          />
        )}
      </button>
      <p className="text-sm text-zinc-400">
        {supported ? (active ? "Listening… release to send" : "Hold to talk") : "Voice needs a secure (https) connection"}
      </p>
    </div>
  );
}
