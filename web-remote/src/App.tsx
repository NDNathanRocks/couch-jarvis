import { useEffect, useMemo, useRef, useState, type CSSProperties } from "react";
import { ConnectionBadge } from "./components/ConnectionBadge";
import { FriendsBar } from "./components/FriendsBar";
import { CommandBar } from "./components/CommandBar";
import { Favorites } from "./components/Favorites";
import { Icon } from "./components/Icon";
import { KeyPad } from "./components/KeyPad";
import { LogPanel } from "./components/LogPanel";
import { NowPlayingCard } from "./components/NowPlayingCard";
import { QRScanner, readPairParams, stripPairParams } from "./components/QRScanner";
import { SettingsSheet } from "./components/SettingsSheet";
import { BookmarkList } from "./components/BookmarkList";
import { QuestionCard } from "./components/QuestionCard";
import { Suggestions } from "./components/Suggestions";
import { TimerList } from "./components/TimerList";
import { Toasts } from "./components/Toasts";
import { TouchPad } from "./components/TouchPad";
import { Transport } from "./components/Transport";
import { VolumeSlider } from "./components/VolumeSlider";
import { useRoomSocket } from "./hooks/useRoomSocket";
import { loadMicMode, saveMicMode, useVoice, type MicMode } from "./hooks/useVoice";
import { selectDevices } from "./state/roomReducer";
import { resolveEndpoint } from "./utils/config";
import { loadFavorites, saveFavorites, type Fav } from "./utils/favorites";
import { tap } from "./utils/haptics";
import { loadRecents, rememberCommand } from "./utils/recents";
import type { Pairing } from "./utils/storage";
import { SLIDERS_SINCE, versionAtLeast } from "./utils/version";

type Tab = "remote" | "saved" | "keys";
const TABS: readonly { id: Tab; label: string; aria?: string }[] = [
  { id: "remote", label: "Remote" },
  { id: "saved", label: "Saved" },
  { id: "keys", label: "Keys", aria: "Touchpad & keys" },
];

const RELEASES_URL = "https://github.com/NDNathanRocks/couch-jarvis/releases/latest";

/** How long the mic stays open for the answer to a spoken question. */
const ASK_LISTEN_MS = 8000;

export default function App() {
  const endpoint = useMemo(
    () => resolveEndpoint(import.meta.env.VITE_RELAY_WS_URL as string | undefined, window.location),
    [],
  );
  const pairParams = useMemo(() => readPairParams(window.location.search), []);
  const [self, setSelf] = useState<Pairing | null>(null);
  const [tab, setTab] = useState<Tab>("remote");
  const [logOpen, setLogOpen] = useState(false);
  const [settingsOpen, setSettingsOpen] = useState(false);
  const [recents, setRecents] = useState<string[]>(loadRecents);
  const [micMode, setMicMode] = useState<MicMode>(loadMicMode);
  const [favorites, setFavorites] = useState<(string | null)[]>(loadFavorites);
  const [dismissedUpdate, setDismissedUpdate] = useState<string | null>(null);

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
  // The phone app and the PC app update separately: only show sliders a PC this old can answer.
  const canSlide = versionAtLeast(view.room.daemon_version, SLIDERS_SINCE);
  const voice = useVoice({
    mode: micMode,
    disabled: !connected,
    onRecording: (recording) => actions.sendAudio(recording),
    onProblem: actions.notify,
  });
  const chooseMic = (next: MicMode) => {
    setMicMode(next);
    saveMicMode(next);
  };
  const sendCommand = (text: string) => {
    tap();
    return actions.sendText(text);
  };
  const sendTyped = (text: string) => {
    const sent = sendCommand(text);
    if (sent) setRecents(rememberCommand(text));
    return sent;
  };
  const sendFavorite = (fav: Fav) => {
    const { send } = fav;
    if (send.kind === "action") actions.sendButton(send.action);
    else if (send.kind === "key") actions.sendKey(send.key, send.mods);
    else actions.sendText(send.text);
  };
  const changeFavorites = (next: (string | null)[]) => {
    setFavorites(next);
    saveFavorites(next);
  };

  // Slide the new tab in from the side it sits on.
  const tabIndex = TABS.findIndex((t) => t.id === tab);
  const [lastIndex, setLastIndex] = useState(tabIndex);
  const from = tabIndex >= lastIndex ? "18px" : "-18px";
  const chooseTab = (next: Tab) => {
    setLastIndex(tabIndex);
    setTab(next);
  };

  // A spoken command that ended in a question: let the answer be spoken too. Wait for the TV to
  // finish asking, or the mic would hear the question, and stop listening if nothing is said.
  const askId = view.ask?.listen ? view.ask.id : null;
  const askLength = view.ask?.question.length ?? 0;
  const voiceRef = useRef(voice);
  useEffect(() => {
    voiceRef.current = voice; // always the latest, without reading a ref while rendering
  });
  useEffect(() => {
    if (askId === null) return;
    const open = setTimeout(() => {
      if (voiceRef.current.supported && !voiceRef.current.active) voiceRef.current.toggle();
    }, 1500 + askLength * 70);
    const close = setTimeout(() => {
      if (voiceRef.current.active) voiceRef.current.toggle(); // sends what was said
    }, 1500 + askLength * 70 + ASK_LISTEN_MS);
    return () => {
      clearTimeout(open);
      clearTimeout(close);
    };
  }, [askId, askLength]);

  const update = view.room.update_available;
  const inputOff = (
    <p className="glass rounded-2xl p-4 text-sm text-zinc-400">Touchpad and keyboard are turned off on the PC (CJ_REMOTE_INPUT=false).</p>
  );

  return (
    <main className="mx-auto flex min-h-full w-full max-w-md flex-col gap-4 px-4 pb-[calc(env(safe-area-inset-bottom)+6rem)] pt-[calc(env(safe-area-inset-top)+1rem)]">
      <header className="flex flex-wrap items-center justify-between gap-x-2 gap-y-1">
        <h1 className="whitespace-nowrap bg-gradient-to-r from-white to-accent bg-clip-text text-xl font-bold tracking-tight text-transparent">Couch Jarvis</h1>
        <div className="flex items-center gap-2">
          {paired && (
            <button type="button" onClick={() => setSettingsOpen(true)} aria-label="Settings" className="press glass-btn min-h-11 min-w-11">
              <Icon name="gear" size={18} />
            </button>
          )}
          <button
            type="button"
            onClick={() => setLogOpen(true)}
            aria-label={lastIsError ? "Log (there was a problem)" : "Log"}
            className="press glass-btn relative min-h-11 min-w-11 px-3 text-xs"
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
          {update && update !== dismissedUpdate && (
            <aside className="glass rise flex items-center gap-3 rounded-2xl p-3 text-sm" aria-label="Update available">
              <Icon name="download" size={18} />
              <span className="flex-1">
                Couch Jarvis <b>{update}</b> is out.{" "}
                <a href={RELEASES_URL} target="_blank" rel="noopener noreferrer" className="font-semibold text-accent underline underline-offset-2">
                  Get it
                </a>
              </span>
              <button type="button" aria-label="Dismiss update notice" onClick={() => setDismissedUpdate(update)} className="press min-h-11 min-w-11 text-zinc-400">
                ✕
              </button>
            </aside>
          )}

          <nav role="tablist" aria-label="Views" className="glass relative grid grid-cols-3 rounded-full p-1">
            <span
              aria-hidden
              className="tab-pill absolute inset-y-1 left-1 w-[calc((100%-0.5rem)/3)] rounded-full bg-white/15 shadow-inner"
              style={{ transform: `translateX(${tabIndex * 100}%)` }}
            />
            {TABS.map((t) => (
              <button
                key={t.id}
                type="button"
                role="tab"
                aria-label={t.aria}
                aria-selected={tab === t.id}
                onClick={() => chooseTab(t.id)}
                className={`relative min-h-11 rounded-full text-sm font-semibold transition-colors ${tab === t.id ? "text-white" : "text-muted"}`}
              >
                {t.label}
              </button>
            ))}
          </nav>

          <div key={tab} className="view flex flex-col gap-4" style={{ "--from": from } as CSSProperties}>
            {tab === "remote" && (
              <>
                {view.ask && (
                  <QuestionCard
                    ask={view.ask}
                    disabled={!connected}
                    listening={voice.active}
                    onAnswer={(choice) => {
                      actions.dismissAsk();
                      sendCommand(choice);
                    }}
                    onDismiss={actions.dismissAsk}
                  />
                )}
                <NowPlayingCard
                  playing={view.room.now_playing}
                  pending={progress}
                  onSeek={canSlide && connected ? (s) => actions.sendSlider("seek_to", s) : undefined}
                />
                {!view.room.now_playing && <Suggestions disabled={!connected} recents={recents} onSend={sendCommand} />}
                <Transport
                  playing={view.room.now_playing?.is_playing === true}
                  disabled={!connected}
                  onPress={(action) => {
                    tap();
                    actions.sendButton(action);
                  }}
                />
                {canSlide ? (
                  <VolumeSlider
                    level={view.room.volume}
                    muted={view.room.muted}
                    disabled={!connected}
                    onLevel={(level) => actions.sendSlider("volume_set", level)}
                    onToggleMute={() => actions.sendButton("mute")}
                  />
                ) : (
                  <div className="glass rise grid grid-cols-3 gap-2 rounded-[28px] p-2" role="group" aria-label="Volume">
                    {(
                      [
                        ["vol_down", "Volume down", "speakerLow"],
                        ["mute", "Mute", "speakerMute"],
                        ["vol_up", "Volume up", "speakerHigh"],
                      ] as const
                    ).map(([action, label, icon]) => (
                      <button key={action} type="button" disabled={!connected} aria-label={label} onClick={() => actions.sendButton(action)} className="press glass-btn min-h-12">
                        <Icon name={icon} />
                      </button>
                    ))}
                  </div>
                )}
                <Favorites slots={favorites} connected={connected} keysAllowed={inputAllowed} onSend={sendFavorite} onChange={changeFavorites} />
                {inputAllowed ? <TouchPad disabled={!connected} onPointer={(e) => actions.sendPointer(e)} /> : inputOff}
                <TimerList timers={view.room.timers} />
                <FriendsBar devices={selectDevices(view)} selfId={self?.deviceId ?? null} />
              </>
            )}

            {tab === "saved" && <BookmarkList items={view.room.bookmarks} disabled={!connected} onCommand={sendCommand} />}

            {tab === "keys" &&
              (inputAllowed ? <KeyPad disabled={!connected} onKey={actions.sendKey} onText={actions.sendTyped} /> : inputOff)}
          </div>
        </>
      )}
      <Toasts toasts={view.toasts} onDismiss={actions.dismiss} onSuggest={sendCommand} />
      {paired && view.connection !== "rejected" && tab === "remote" && (
        <CommandBar disabled={!connected} voice={voice} micMode={micMode} onSend={sendTyped} />
      )}
      {settingsOpen && (
        <SettingsSheet
          voice={view.room.voice}
          adblock={view.room.browser.adblock}
          micMode={micMode}
          version={view.room.daemon_version}
          disabled={!connected}
          onCommand={sendCommand}
          onMicMode={chooseMic}
          onUnpair={() => {
            setSettingsOpen(false);
            void actions.unpair();
          }}
          onClose={() => setSettingsOpen(false)}
        />
      )}
      {logOpen && <LogPanel entries={view.log} onClear={actions.clearLog} onClose={() => setLogOpen(false)} />}
    </main>
  );
}
