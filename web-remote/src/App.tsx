import { useMemo, useState } from "react";
import { ConnectionBadge } from "./components/ConnectionBadge";
import { FriendsBar } from "./components/FriendsBar";
import { HoldToTalk } from "./components/HoldToTalk";
import { NowPlayingCard } from "./components/NowPlayingCard";
import { QRScanner, readPairParams, stripPairParams } from "./components/QRScanner";
import { QuickDeck } from "./components/QuickDeck";
import { TextCommand } from "./components/TextCommand";
import { TimerList } from "./components/TimerList";
import { Toasts } from "./components/Toasts";
import { useRoomSocket } from "./hooks/useRoomSocket";
import { selectDevices } from "./state/roomReducer";
import { resolveEndpoint } from "./utils/config";
import type { Pairing } from "./utils/storage";

export default function App() {
  const endpoint = useMemo(
    () => resolveEndpoint(import.meta.env.VITE_RELAY_WS_URL as string | undefined, window.location),
    [],
  );
  const pairParams = useMemo(() => readPairParams(window.location.search), []);
  const [self, setSelf] = useState<Pairing | null>(null);

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

  return (
    <main className="mx-auto flex min-h-full w-full max-w-md flex-col gap-5 px-4 pb-[calc(env(safe-area-inset-bottom)+6rem)] pt-[calc(env(safe-area-inset-top)+1rem)]">
      <header className="flex items-center justify-between">
        <h1 className="text-xl font-semibold tracking-tight">Couch Jarvis</h1>
        <ConnectionBadge view={view} transport={endpoint.transport} />
      </header>

      <QRScanner connection={view.connection} hasPairParams={pairParams !== null} paired={paired} />

      {paired && view.connection !== "rejected" && (
        <>
          <NowPlayingCard playing={view.room.now_playing} pending={progress} />
          <div className="flex justify-center py-2">
            <HoldToTalk
              disabled={!connected}
              onRecording={(recording) => actions.sendAudio(recording)}
              onProblem={actions.notify}
            />
          </div>
          <TextCommand disabled={!connected} onSend={actions.sendText} />
          <QuickDeck disabled={!connected} onPress={(action) => actions.sendButton(action)} />
          <TimerList timers={view.room.timers} />
          <FriendsBar devices={selectDevices(view)} selfId={self?.deviceId ?? null} />
          <button
            type="button"
            onClick={() => void actions.unpair()}
            className="mt-2 min-h-11 self-center px-3 text-xs text-zinc-600 underline underline-offset-2"
          >
            Forget this phone
          </button>
        </>
      )}
      <Toasts toasts={view.toasts} onDismiss={actions.dismiss} />
    </main>
  );
}
