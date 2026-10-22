import { useCallback, useEffect, useRef, useState } from "react";
import {
  HoldRecorder,
  RecorderFailure,
  voiceSupported,
  type Recording,
} from "../utils/audioRecorder";

export type MicMode = "tap" | "hold";

const MODE_KEY = "cj.micMode";

/** Tap to start, tap to send is the default; hold-to-talk is kept as a saved preference. */
export function loadMicMode(): MicMode {
  try {
    return localStorage.getItem(MODE_KEY) === "hold" ? "hold" : "tap";
  } catch {
    return "tap";
  }
}

export function saveMicMode(mode: MicMode): void {
  try {
    localStorage.setItem(MODE_KEY, mode);
  } catch {
    /* private mode: the choice just lasts for this visit */
  }
}

const PROBLEMS: Record<MicMode, Record<"unsupported" | "denied" | "too_short", string>> = {
  tap: {
    unsupported: "Voice isn't available here. Use the text box instead.",
    denied: "Microphone permission was denied.",
    too_short: "That was too short. Tap, speak, then tap again to send.",
  },
  hold: {
    unsupported: "Voice isn't available here. Use the text box instead.",
    denied: "Microphone permission was denied.",
    too_short: "Hold the button while you speak.",
  },
};

interface Options {
  mode: MicMode;
  disabled: boolean;
  onRecording: (recording: Recording) => void;
  onProblem: (message: string) => void;
}

export interface Voice {
  supported: boolean;
  active: boolean;
  elapsedMs: number;
  /** Tap mode: start, or send if already recording. */
  toggle(): void;
  /** Throw the recording away without sending it. */
  cancel(): void;
  /** Hold mode: press / release. */
  holdStart(): void;
  holdEnd(): void;
}

export function useVoice({ mode, disabled, onRecording, onProblem }: Options): Voice {
  const [active, setActive] = useState(false);
  const [elapsedMs, setElapsedMs] = useState(0);
  const [supported] = useState(voiceSupported);
  const recorder = useRef<HoldRecorder | null>(null);
  const startedAt = useRef(0);
  const releasedEarly = useRef(false);
  const latest = useRef({ mode, onRecording, onProblem });
  useEffect(() => {
    latest.current = { mode, onRecording, onProblem };
  });

  useEffect(() => {
    if (!active) return;
    const id = setInterval(() => setElapsedMs(Date.now() - startedAt.current), 200);
    return () => clearInterval(id);
  }, [active]);

  useEffect(() => () => recorder.current?.cancel(), []);

  const finish = useCallback(async (pending: Promise<Recording>) => {
    setActive(false);
    setElapsedMs(0);
    const { mode: m, onRecording: send, onProblem: problem } = latest.current;
    try {
      send(await pending);
    } catch (err) {
      if (err instanceof RecorderFailure) problem(PROBLEMS[m][err.reason]);
      else problem("Recording failed.");
    }
  }, []);

  const start = useCallback(async (): Promise<boolean> => {
    if (disabled || !supported || recorder.current) return false;
    const rec = new HoldRecorder((autoStopped) => {
      recorder.current = null; // the 15 s limit sent it: a later tap must not send it twice
      void finish(autoStopped);
    });
    recorder.current = rec;
    try {
      await rec.start();
    } catch (err) {
      recorder.current = null;
      const { mode: m, onProblem: problem } = latest.current;
      problem(err instanceof RecorderFailure ? PROBLEMS[m][err.reason] : "Could not start the microphone.");
      return false;
    }
    navigator.vibrate?.(15);
    startedAt.current = Date.now();
    setActive(true);
    return true;
  }, [disabled, supported, finish]);

  const stop = useCallback(() => {
    const rec = recorder.current;
    if (!rec) return;
    recorder.current = null;
    navigator.vibrate?.(10);
    void finish(rec.stop());
  }, [finish]);

  const cancel = useCallback(() => {
    recorder.current?.cancel();
    recorder.current = null;
    setActive(false);
    setElapsedMs(0);
  }, []);

  const toggle = useCallback(() => {
    if (recorder.current) stop();
    else void start();
  }, [start, stop]);

  const holdStart = useCallback(() => {
    releasedEarly.current = false;
    void start().then((ok) => {
      if (ok && releasedEarly.current) stop(); // finger lifted while permission was pending
    });
  }, [start, stop]);

  const holdEnd = useCallback(() => {
    releasedEarly.current = true;
    if (recorder.current) stop();
  }, [stop]);

  return { supported, active, elapsedMs, toggle, cancel, holdStart, holdEnd };
}

export function formatElapsed(ms: number): string {
  const s = Math.floor(ms / 1000);
  return `${Math.floor(s / 60)}:${String(s % 60).padStart(2, "0")}`;
}
