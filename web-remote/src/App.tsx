import { useMemo, useState } from "react";
import { ConnectionBadge } from "./components/ConnectionBadge";
import { FriendsBar } from "./components/FriendsBar";
import { CommandBar } from "./components/CommandBar";
import { KeyPad } from "./components/KeyPad";
import { LogPanel } from "./components/LogPanel";
import { NowPlayingCard } from "./components/NowPlayingCard";
import { QRScanner, readPairParams, stripPairParams } from "./components/QRScanner";
import { QuickDeck } from "./components/QuickDeck";
import { TimerList } from "./components/TimerList";
import { Toasts } from "./components/Toasts";
import { TouchPad } from "./components/TouchPad";
import { useRoomSocket } from "./hooks/useRoomSocket";
import { loadMicMode, saveMicMode, useVoice, type MicMode } from "./hooks/useVoice";
import { selectDevices } from "./state/roomReducer";
import { resolveEndpoint } from "./utils/config";
import type { Pairing } from "./utils/storage";

type Tab = "remote" | "touchpad";

export default function App() {
  const endpoint = useMemo(
    () => resolveEndpoint(import.meta.env.VITE_RELAY_WS_URL as string | undefined, window.location),
    [],
  );
  const pairParams = useMemo(() => readPairParams(window.location.search), []);
  const [self, setSelf] = useState<Pairing | null>(null);
  const [tab, setTab] = useState<Tab>("remote");
  const [logOpen, setLogOpen] = useState(false);
  const [micMode, setMicMode] = useState<MicMode>(loadMicMode);

  const { view, actions } = useRoomSocket({
    endpoint,
    pairParams,
    onPaired: (pairing) => {
      setSelf(pairing);
      stripPairParams(); // the room secret has been exchanged for a token: remove it from the URL
    },
    onUnpaired: () => setSelf(null),
  });

  const connected = view.connection === "online";
  // Paired = we hold a token, i.e. either this session paired or a saved pairing is reconnecting.
  const paired = self !== null || (pairParams === null && view.connection !== "idle");
  const progress = Object.values(view.pending).at(-1) ?? null;
  const lastIsError = view.log.at(-1)?.level === "error";
  const inputAllowed = view.room.remote_input;
  const voice = useVoice({
    mode: micMode,
    disabled: !connected,
    onRecording: (recording) => actions.sendAudio(recording),
    onProblem: actions.notify,
  });
  const switchMic = () => {
    const next: MicMode = micMode === "tap" ? "hold" : "tap";
    setMicMode(next);
    saveMicMode(next);
  };

  return (
    <main className="mx-auto flex min-h-full w-full max-w-md flex-col gap-5 px-4 pb-[calc(env(safe-area-inset-bottom)+6rem)] pt-[calc(env(safe-area-inset-top)+1rem)]">
      <header className="flex items-center justify-between gap-2">
        <h1 className="text-xl font-semibold tracking-tight">Couch Jarvis</h1>
        <div className="flex items-center gap-2">
          <button
            type="button"
            onClick={() => setLogOpen(true)}
            aria-label={lastIsError ? "Log (there was a problem)" : "Log"}
            className="relative min-h-11 min-w-11 rounded-full px-3 text-xs text-zinc-300 ring-1 ring-line active:bg-surface"
          >
            Log
            {lastIsError && <span aria-hidden className="absolute right-1.5 top-1.5 h-2 w-2 rounded-full bg-rose-500" />}
          </button>
          <ConnectionBadge view={view} transport={endpoint.transport} />
        </div>
      </header>

      <QRScanner connection={view.connection} hasPairParams={pairParams !== null} paired={paired} />

      {paired && view.connection !== "rejected" && (
        <>
          <nav role="tablist" aria-label="Views" className="grid grid-cols-2 gap-2 rounded-2xl bg-surface p-1 ring-1 ring-line">
            {(
              [
                ["remote", "Remote"],
                ["touchpad", "Touchpad & keys"],
              ] as const
            ).map(([id, label]) => (
              <button
                key={id}
                type="button"
                role="tab"
                aria-selected={tab === id}
                onClick={() => setTab(id)}
                className={`min-h-11 rounded-xl text-sm font-medium ${tab === id ? "bg-line text-white" : "text-muted"}`}
              >
                {label}
              </button>
            ))}
          </nav>

          {tab === "remote" && (
            <>
              <NowPlayingCard playing={view.room.now_playing} pending={progress} />
              <QuickDeck disabled={!connected} onPress={(action) => actions.sendButton(action)} />
              <TimerList timers={view.room.timers} />
              <FriendsBar devices={selectDevices(view)} selfId={self?.deviceId ?? null} />
              <button
                type="button"
                onClick={switchMic}
                className="min-h-11 self-center px-3 text-xs text-muted underline underline-offset-2"
              >
                {micMode === "tap" ? "Mic mode: tap on, tap off (switch to press and hold)" : "Mic mode: press and hold (switch to tap on, tap off)"}
              </button>
            </>
          )}

          {tab === "touchpad" &&
            (inputAllowed ? (
              <>
                <TouchPad disabled={!connected} onPointer={(e) => actions.sendPointer(e)} />
                <KeyPad disabled={!connected} onKey={actions.sendKey} onText={actions.sendTyped} />
              </>
            ) : (
              <p className="rounded-2xl bg-zinc-900 p-4 text-sm text-zinc-400 ring-1 ring-zinc-800">
                Touchpad and keyboard are turned off on the PC (CJ_REMOTE_INPUT=false).
              </p>
            ))}

          <button
            type="button"
            onClick={() => void actions.unpair()}
            className="mt-2 min-h-11 self-center px-3 text-xs text-zinc-600 underline underline-offset-2"
          >
            Forget this phone
          </button>
        </>
      )}
      <Toasts toasts={view.toasts} onDismiss={actions.dismiss} onSuggest={actions.sendText} />
      {paired && view.connection !== "rejected" && tab === "remote" && (
        <CommandBar disabled={!connected} voice={voice} micMode={micMode} onSend={actions.sendText} />
      )}
      {logOpen && <LogPanel entries={view.log} onClear={actions.clearLog} onClose={() => setLogOpen(false)} />}
    </main>
  );
}
