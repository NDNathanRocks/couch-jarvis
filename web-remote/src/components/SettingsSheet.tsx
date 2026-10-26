import { LICENCES_URL, PRIVACY_URL, SUPPORT_URL, TERMS_URL } from "../brand";
import { useEffect, useRef, useState } from "react";
import type { Tv } from "../utils/tvs";
import type { VoicePrefs } from "../protocol";
import type { MicMode } from "../hooks/useVoice";
import { resetAppCache } from "../utils/resetCache";
import { Icon } from "./Icon";

type Style = VoicePrefs["style"];
type Voice = VoicePrefs["voice"];

/**
 * Spoken-reply choices. Each one is sent to the TV as the same words you could say out loud
 * ("turn voice replies off", "use the british female voice"), so the sheet and your voice are
 * one feature and the TV (not this phone) remembers the answer.
 */
export const STYLES: readonly { id: Style; label: string; sample: string }[] = [
  { id: "jarvis", label: "Butler", sample: "“Now playing The Weeknd on YouTube Music.”" },
  { id: "friendly", label: "Friendly", sample: "“You got it! Playing The Weeknd.”" },
  { id: "brief", label: "Brief", sample: "“Playing The Weeknd.”" },
];

export const VOICES: readonly { id: Voice; label: string; hint: string; say: string }[] = [
  { id: "british_male", label: "Sterling", hint: "British, male", say: "use the british male voice" },
  { id: "british_female", label: "Aurora", hint: "British, female", say: "use the british female voice" },
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
  adblock: boolean;
  micMode: MicMode;
  /** The connected PC app's version, shown for support. */
  version?: string;
  disabled: boolean;
  /** Send a command to the TV (the same words you could say). */
  onCommand: (text: string) => void;
  onMicMode: (mode: MicMode) => void;
  onUnpair: () => void;
  onClose: () => void;
  /** Every TV this phone paired with (rename here; scan another TV's QR to add one). */
  tvs?: Tv[];
  currentRoom?: string | null;
  /** A guest phone: the PC refuses its settings changes, so they are not offered. */
  guest?: boolean;
  onRenameTv?: (roomId: string, label: string) => void;
}

export function SettingsSheet({
  voice,
  adblock,
  micMode,
  version,
  disabled,
  onCommand,
  onMicMode,
  onUnpair,
  onClose,
  tvs = [],
  currentRoom = null,
  onRenameTv,
  guest = false,
}: Props) {
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

        {guest ? (
          <p className="rounded-2xl bg-surface p-4 text-sm text-zinc-400 ring-1 ring-line">
            This phone is a guest: the TV&apos;s voice, browsing and QR settings are for the owner&apos;s phones.
          </p>
        ) : (
          <>
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

        <section aria-labelledby="web-h" className="mt-6 space-y-3">
          <div className="flex items-center gap-3 rounded-2xl bg-surface p-4 ring-1 ring-line">
            <div className="min-w-0 flex-1">
              <h3 id="web-h" className="text-[15px] font-semibold">
                Pop-up protection
              </h3>
              <p className="mt-0.5 text-xs text-muted">
                Closes tabs that other websites open on their own. Links you pick from search results always open.
              </p>
            </div>
            <Switch
              label="Pop-up protection"
              checked={adblock}
              disabled={disabled}
              // the older wording: every PC version understands it
              onChange={(on) => onCommand(on ? "turn on ad blocking" : "turn off ad blocking")}
            />
          </div>
        </section>

        <section aria-labelledby="qr-h" className="mt-6 space-y-2">
          <h3 id="qr-h" className="text-xs font-semibold uppercase tracking-wide text-zinc-500">
            QR code on the TV
          </h3>
          <p className="text-xs text-muted">
            It hides itself once a phone is connected and stays hidden, even if your phone locks.
          </p>
          <div className="grid grid-cols-2 gap-2">
            <button
              type="button"
              disabled={disabled}
              onClick={() => onCommand("show the qr code")}
              className="min-h-11 rounded-xl bg-surface text-sm font-medium ring-1 ring-line active:bg-surface-2 disabled:opacity-40"
            >
              Show QR
            </button>
            <button
              type="button"
              disabled={disabled}
              onClick={() => onCommand("hide the qr code")}
              className="min-h-11 rounded-xl bg-surface text-sm font-medium ring-1 ring-line active:bg-surface-2 disabled:opacity-40"
            >
              Hide QR
            </button>
          </div>
          <button
            type="button"
            disabled={disabled}
            onClick={() => {
              if (window.confirm("Reset the QR code? Old QR codes stop working. Phones already paired keep working.")) {
                onCommand("reset the qr code");
              }
            }}
            className="min-h-11 w-full rounded-xl text-sm text-muted ring-1 ring-line active:bg-surface disabled:opacity-40"
          >
            Reset QR code
          </button>
        </section>
          </>
        )}

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

        <section className="mt-6" aria-label="App">
          <h3 className="mb-2 text-sm font-medium text-zinc-300">App</h3>
          <ResetCache />
          <p className="mt-1.5 px-1 text-xs text-muted">
            {version ? `PC app ${version}. ` : ""}Reloads the newest version of this page. You stay paired and keep your favourites.
          </p>
        </section>

        <section aria-label="Your TVs" className="mt-6">
          <h3 className="text-sm font-semibold text-zinc-300">Your TVs</h3>
          <ul className="mt-2 space-y-2">
            {tvs.map((t) => (
              <li key={t.pairing.roomId} className="flex items-center gap-2">
                <input
                  aria-label={`Name of ${t.label}`}
                  defaultValue={t.label}
                  maxLength={32}
                  onBlur={(e) => onRenameTv?.(t.pairing.roomId, e.target.value)}
                  className="glass min-h-11 flex-1 rounded-xl px-3 text-sm"
                />
                {t.pairing.roomId === currentRoom && <span className="text-xs text-accent">This one</span>}
              </li>
            ))}
          </ul>
          <p className="mt-2 text-xs text-zinc-500">To add another TV, scan the QR code on it with this phone's camera.</p>
        </section>
        <button
          type="button"
          onClick={onUnpair}
          className="mt-6 min-h-11 w-full rounded-full text-sm text-zinc-500 underline underline-offset-2"
        >
          {tvs.length > 1 ? "Forget this TV" : "Forget this phone"}
        </button>
        <nav aria-label="About" className="mt-4 flex flex-wrap justify-center gap-x-4 gap-y-2 text-xs text-zinc-500">
          {(
            [
              ["Privacy", PRIVACY_URL],
              ["Terms", TERMS_URL],
              ["Licences", LICENCES_URL],
              ["Support", SUPPORT_URL],
            ] as const
          ).map(([label, href]) => (
            <a key={label} href={href} target="_blank" rel="noopener noreferrer" className="inline-flex min-h-11 items-center underline underline-offset-2">
              {label}
            </a>
          ))}
        </nav>
      </div>
    </div>
  );
}

function ResetCache() {
  const [state, setState] = useState<"idle" | "confirm" | "working">("idle");
  if (state === "confirm") {
    return (
      <div className="flex gap-2" role="group" aria-label="Confirm reset">
        <button
          type="button"
          onClick={() => {
            setState("working");
            void resetAppCache();
          }}
          className="press min-h-11 flex-1 rounded-xl bg-accent text-sm font-semibold text-ink"
        >
          Yes, reset and reload
        </button>
        <button type="button" onClick={() => setState("idle")} className="press min-h-11 flex-1 rounded-xl bg-surface text-sm text-zinc-300 ring-1 ring-line">
          Cancel
        </button>
      </div>
    );
  }
  return (
    <button
      type="button"
      disabled={state === "working"}
      onClick={() => setState("confirm")}
      className="press flex min-h-11 w-full items-center justify-center gap-2 rounded-xl bg-surface text-sm text-zinc-100 ring-1 ring-line disabled:opacity-50"
    >
      <Icon name="refresh" size={16} /> {state === "working" ? "Reloading…" : "Reset app cache"}
    </button>
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
