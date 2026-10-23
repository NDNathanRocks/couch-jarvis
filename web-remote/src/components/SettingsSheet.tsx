import { useEffect, useRef } from "react";
import type { VoicePrefs } from "../protocol";
import type { MicMode } from "../hooks/useVoice";

type Style = VoicePrefs["style"];
type Voice = VoicePrefs["voice"];

/**
 * Spoken-reply choices. Each one is sent to the TV as the same words you could say out loud
 * ("turn voice replies off", "use the british female voice"), so the sheet and your voice are
 * one feature and the TV (not this phone) remembers the answer.
 */
export const STYLES: readonly { id: Style; label: string; sample: string }[] = [
  { id: "jarvis", label: "Jarvis", sample: "“Now playing The Weeknd on YouTube Music.”" },
  { id: "friendly", label: "Friendly", sample: "“You got it! Playing The Weeknd.”" },
  { id: "brief", label: "Brief", sample: "“Playing The Weeknd.”" },
];

export const VOICES: readonly { id: Voice; label: string; hint: string; say: string }[] = [
  { id: "british_male", label: "Jarvis", hint: "British, male", say: "use the british male voice" },
  { id: "british_female", label: "Friday", hint: "British, female", say: "use the british female voice" },
  { id: "us_male", label: "Chill", hint: "American, male", say: "use the american male voice" },
  { id: "us_female", label: "Aria", hint: "American, female", say: "use the american female voice" },
];

const ENGINES: Record<VoicePrefs["engine"], string> = {
  elevenlabs: "ElevenLabs",
  edge: "Microsoft neural voices",
  windows: "Windows voices",
  none: "",
};

interface Props {
  voice: VoicePrefs;
  micMode: MicMode;
  disabled: boolean;
  /** Send a command to the TV (the same words you could say). */
  onCommand: (text: string) => void;
  onMicMode: (mode: MicMode) => void;
  onUnpair: () => void;
  onClose: () => void;
}

export function SettingsSheet({ voice, micMode, disabled, onCommand, onMicMode, onUnpair, onClose }: Props) {
  const closeRef = useRef<HTMLButtonElement>(null);
  useEffect(() => {
    closeRef.current?.focus();
    const onKey = (e: KeyboardEvent) => e.key === "Escape" && onClose();
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [onClose]);

  return (
    <div role="dialog" aria-modal="true" aria-label="Settings" className="fixed inset-0 z-50 flex flex-col justify-end bg-black/60">
      <button type="button" aria-label="Close settings" tabIndex={-1} className="flex-1 cursor-default" onClick={onClose} />
      <div className="mx-auto max-h-[88%] w-full max-w-md overflow-y-auto rounded-t-3xl bg-canvas px-4 pb-[calc(env(safe-area-inset-bottom)+1.25rem)] pt-3 ring-1 ring-line">
        <div className="mx-auto mb-3 h-1 w-10 rounded-full bg-line" aria-hidden />
        <header className="mb-4 flex items-center gap-2">
          <h2 className="flex-1 text-lg font-semibold">Settings</h2>
          <button ref={closeRef} type="button" onClick={onClose} className="min-h-11 rounded-full bg-line px-5 text-sm font-medium text-zinc-100">
            Done
          </button>
        </header>

        <section aria-labelledby="voice-h" className="space-y-3">
          <div className="flex items-center gap-3 rounded-2xl bg-surface p-4 ring-1 ring-line">
            <div className="min-w-0 flex-1">
              <h3 id="voice-h" className="text-[15px] font-semibold">
                Voice replies
              </h3>
              <p className="mt-0.5 text-xs text-muted">The TV tells you what it just did. Saved on this TV for everyone.</p>
            </div>
            <Switch
              label="Voice replies"
              checked={voice.enabled}
              disabled={disabled}
              onChange={(on) => onCommand(on ? "turn voice replies on" : "turn voice replies off")}
            />
          </div>

          {voice.enabled && (
            <>
              <fieldset className="space-y-2 rounded-2xl bg-surface p-4 ring-1 ring-line">
                <legend className="sr-only">Reply style</legend>
                <p className="text-xs font-semibold uppercase tracking-wide text-zinc-500">Style</p>
                <div className="grid grid-cols-3 gap-1 rounded-xl bg-canvas p-1 ring-1 ring-line">
                  {STYLES.map((s) => (
                    <button
                      key={s.id}
                      type="button"
                      aria-pressed={voice.style === s.id}
                      disabled={disabled}
                      onClick={() => onCommand(`set the style to ${s.id}`)}
                      className={`min-h-11 rounded-lg text-sm ${voice.style === s.id ? "bg-line font-semibold text-white" : "text-muted"} disabled:opacity-40`}
                    >
                      {s.label}
                    </button>
                  ))}
                </div>
                <p className="text-xs text-muted">{STYLES.find((s) => s.id === voice.style)?.sample}</p>
              </fieldset>

              <fieldset className="space-y-2 rounded-2xl bg-surface p-4 ring-1 ring-line">
                <legend className="sr-only">Voice</legend>
                <p className="text-xs font-semibold uppercase tracking-wide text-zinc-500">Voice</p>
                <div className="grid grid-cols-2 gap-2">
                  {VOICES.map((v) => (
                    <button
                      key={v.id}
                      type="button"
                      aria-pressed={voice.voice === v.id}
                      disabled={disabled}
                      onClick={() => onCommand(v.say)}
                      className={`min-h-14 rounded-xl px-3 text-left ring-1 disabled:opacity-40 ${
                        voice.voice === v.id ? "bg-accent/15 ring-accent" : "bg-canvas ring-line"
                      }`}
                    >
                      <span className="block text-sm font-semibold">{v.label}</span>
                      <span className="block text-xs text-muted">{v.hint}</span>
                    </button>
                  ))}
                </div>
              </fieldset>

              <div className="flex items-center gap-3 rounded-2xl bg-surface p-4 ring-1 ring-line">
                <div className="min-w-0 flex-1">
                  <p className="text-[15px] font-medium">Also after button taps</p>
                  <p className="mt-0.5 text-xs text-muted">Off: it only speaks after voice and typed commands.</p>
                </div>
                <Switch
                  label="Speak after button taps"
                  checked={voice.buttons}
                  disabled={disabled}
                  onChange={(on) => onCommand(on ? "speak for button taps" : "don't speak for buttons")}
                />
              </div>

              <div className="flex items-center gap-3">
                <button
                  type="button"
                  disabled={disabled}
                  onClick={() => onCommand("test the voice")}
                  className="min-h-12 rounded-full bg-accent px-6 text-sm font-bold text-ink active:brightness-90 disabled:opacity-40"
                >
                  Hear it
                </button>
                {voice.engine !== "none" && <p className="text-xs text-muted">Speaking with {ENGINES[voice.engine]}</p>}
              </div>
            </>
          )}
        </section>

        <section aria-labelledby="mic-h" className="mt-6 space-y-2">
          <h3 id="mic-h" className="text-xs font-semibold uppercase tracking-wide text-zinc-500">
            Microphone
          </h3>
          <div className="grid grid-cols-2 gap-1 rounded-xl bg-surface p-1 ring-1 ring-line">
            {(
              [
                ["tap", "Tap to talk"],
                ["hold", "Hold to talk"],
              ] as const
            ).map(([id, label]) => (
              <button
                key={id}
                type="button"
                aria-pressed={micMode === id}
                onClick={() => onMicMode(id)}
                className={`min-h-11 rounded-lg text-sm ${micMode === id ? "bg-line font-semibold text-white" : "text-muted"}`}
              >
                {label}
              </button>
            ))}
          </div>
        </section>

        <button
          type="button"
          onClick={onUnpair}
          className="mt-6 min-h-11 w-full rounded-full text-sm text-zinc-500 underline underline-offset-2"
        >
          Forget this phone
        </button>
      </div>
    </div>
  );
}

function Switch({
  label,
  checked,
  disabled,
  onChange,
}: {
  label: string;
  checked: boolean;
  disabled: boolean;
  onChange: (on: boolean) => void;
}) {
  return (
    <button
      type="button"
      role="switch"
      aria-checked={checked}
      aria-label={label}
      disabled={disabled}
      onClick={() => onChange(!checked)}
      className={`relative h-8 w-14 flex-none rounded-full transition-colors disabled:opacity-40 ${checked ? "bg-accent" : "bg-line"}`}
    >
      <span className={`absolute top-1 h-6 w-6 rounded-full bg-white shadow transition-all ${checked ? "left-7" : "left-1"}`} />
    </button>
  );
}
