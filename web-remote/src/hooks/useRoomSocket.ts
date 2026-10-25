import { useEffect, useMemo, useReducer, useRef } from "react";
import {
  RoomConnection,
  browserSocketFactory,
  type PairParams,
  type SocketLike,
} from "../connection/roomConnection";
import { uuid, type Modifier, type PointerAction, type QuickAction } from "../protocol";
import { initialView, roomReducer, type RoomView } from "../state/roomReducer";
import type { Recording } from "../utils/audioRecorder";
import type { Endpoint } from "../utils/config";
import { browserPairingStorage, guessDeviceName, type Pairing, type PairingStorage } from "../utils/storage";

/** The PC's own hard ceiling per command is 25 s; allow slack for slow networks. */
export const PENDING_MAX_AGE_MS = 45_000;

export interface RoomSocketOptions {
  endpoint: Endpoint;
  pairParams: PairParams | null;
  onPaired?: (pairing: Pairing) => void;
  onRestored?: (pairing: Pairing) => void;
  onUnpaired?: () => void;
  storage?: PairingStorage;
  createSocket?: (url: string) => SocketLike;
  deviceName?: string;
}

export interface RoomActions {
  sendButton(action: QuickAction): boolean;
  sendSlider(action: "volume_set" | "seek_to", value: number): boolean;
  sendText(text: string): boolean;
  sendAudio(recording: Recording): boolean;
  sendPointer(p: { dx?: number; dy?: number; scroll?: number; action?: PointerAction }): boolean;
  sendKey(key: string, mods?: Modifier[]): boolean;
  sendTyped(text: string): boolean;
  unpair(): Promise<void>;
  dismiss(toastId: string): void;
  dismissAsk(): void;
  notify(text: string): void;
  clearLog(): void;
}

export function useRoomSocket(options: RoomSocketOptions): { view: RoomView; actions: RoomActions } {
  const [view, dispatch] = useReducer(roomReducer, initialView);
  const connection = useRef<RoomConnection | null>(null);
  const latest = useRef(options);
  useEffect(() => {
    latest.current = options; // read by the long-lived connection's callbacks
  });

  useEffect(() => {
    const conn = new RoomConnection({
      endpoint: latest.current.endpoint,
      storage: latest.current.storage ?? browserPairingStorage,
      dispatch,
      createSocket: latest.current.createSocket ?? browserSocketFactory,
      deviceName: latest.current.deviceName ?? guessDeviceName(),
      onPaired: (p) => latest.current.onPaired?.(p),
      onRestored: (p) => latest.current.onRestored?.(p),
      onUnpaired: () => latest.current.onUnpaired?.(),
    });
    connection.current = conn;
    void conn.start(latest.current.pairParams);
    const wake = () => {
      if (document.visibilityState === "visible") conn.onVisible();
    };
    const online = () => conn.onOnline();
    document.addEventListener("visibilitychange", wake);
    window.addEventListener("online", online);
    window.addEventListener("pageshow", wake);
    return () => {
      document.removeEventListener("visibilitychange", wake);
      window.removeEventListener("online", online);
      window.removeEventListener("pageshow", wake);
      conn.stop();
      connection.current = null;
    };
  }, []);

  const hasPending = Object.keys(view.pending).length > 0;
  useEffect(() => {
    if (!hasPending) return;
    const id = setInterval(() => dispatch({ type: "expire", now: Date.now(), maxAgeMs: PENDING_MAX_AGE_MS }), 5000);
    return () => clearInterval(id);
  }, [hasPending]);

  useEffect(() => {
    if (view.needsSync) {
      connection.current?.requestSync(); // a revision gap means we missed a patch
      dispatch({ type: "synced" });
    }
  }, [view.needsSync]);

  const actions = useMemo<RoomActions>(
    () => ({
      sendButton: (a) => connection.current?.sendButton(a) ?? false,
      sendSlider: (a, v) => connection.current?.sendSlider(a, v) ?? false,
      sendText: (t) => connection.current?.sendText(t) ?? false,
      sendAudio: (r) => connection.current?.sendAudio(r) ?? false,
      sendPointer: (p) => connection.current?.sendPointer(p) ?? false,
      sendKey: (k, m) => connection.current?.sendKey(k, m) ?? false,
      sendTyped: (t) => connection.current?.sendTyped(t) ?? false,
      unpair: async () => {
        await connection.current?.unpair();
      },
      dismiss: (id) => dispatch({ type: "dismiss", id }),
      dismissAsk: () => dispatch({ type: "dismiss_ask" }),
      notify: (text) => dispatch({ type: "toast", toast: { id: uuid(), kind: "info", text, mine: true } }),
      clearLog: () => dispatch({ type: "clear_log" }),
    }),
    [],
  );
  return { view, actions };
}
