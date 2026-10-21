import { useEffect, useMemo, useReducer, useRef } from "react";
import {
  RoomConnection,
  browserSocketFactory,
  type PairParams,
  type SocketLike,
} from "../connection/roomConnection";
import { uuid, type QuickAction } from "../protocol";
import { initialView, roomReducer, type RoomView } from "../state/roomReducer";
import type { Recording } from "../utils/audioRecorder";
import type { Endpoint } from "../utils/config";
import { browserPairingStorage, guessDeviceName, type Pairing, type PairingStorage } from "../utils/storage";

export interface RoomSocketOptions {
  endpoint: Endpoint;
  pairParams: PairParams | null;
  onPaired?: (pairing: Pairing) => void;
  onUnpaired?: () => void;
  storage?: PairingStorage;
  createSocket?: (url: string) => SocketLike;
  deviceName?: string;
}

export interface RoomActions {
  sendButton(action: QuickAction): boolean;
  sendText(text: string): boolean;
  sendAudio(recording: Recording): boolean;
  unpair(): Promise<void>;
  dismiss(toastId: string): void;
  notify(text: string): void;
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

  useEffect(() => {
    if (view.needsSync) {
      connection.current?.requestSync(); // a revision gap means we missed a patch
      dispatch({ type: "synced" });
    }
  }, [view.needsSync]);

  const actions = useMemo<RoomActions>(
    () => ({
      sendButton: (a) => connection.current?.sendButton(a) ?? false,
      sendText: (t) => connection.current?.sendText(t) ?? false,
      sendAudio: (r) => connection.current?.sendAudio(r) ?? false,
      unpair: async () => {
        await connection.current?.unpair();
      },
      dismiss: (id) => dispatch({ type: "dismiss", id }),
      notify: (text) => dispatch({ type: "toast", toast: { id: uuid(), kind: "info", text, mine: true } }),
    }),
    [],
  );
  return { view, actions };
}
