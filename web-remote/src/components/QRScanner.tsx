import type { PairParams } from "../connection/roomConnection";
import type { Connection } from "../state/roomReducer";

const ROOM_RE = /^[A-Za-z0-9_-]{8,64}$/;
const SECRET_RE = /^[A-Za-z0-9_-]{16,128}$/;

/** Read ?room=...&secret=... from a QR deep link. Returns null unless both look valid. */
export function readPairParams(search: string): PairParams | null {
  const q = new URLSearchParams(search);
  const roomId = q.get("room");
  const secret = q.get("secret");
  if (!roomId || !secret || !ROOM_RE.test(roomId) || !SECRET_RE.test(secret)) return null;
  return { roomId, secret };
}

/** Remove the room secret from the address bar/history once it has been exchanged for a token. */
export function stripPairParams(win: Pick<Window, "location" | "history"> = window): void {
  const url = new URL(win.location.href);
  if (!url.searchParams.has("secret") && !url.searchParams.has("room")) return;
  url.searchParams.delete("secret");
  url.searchParams.delete("room");
  win.history.replaceState(null, "", `${url.pathname}${url.search}${url.hash}`);
}

interface Props {
  connection: Connection;
  hasPairParams: boolean;
  paired: boolean;
}

/** Explains pairing: the QR itself is scanned by the phone's own camera app, which opens this page. */
export function QRScanner({ connection, hasPairParams, paired }: Props) {
  if (connection === "unreachable") {
    // Soft failure: the remote stays on screen (disabled) and we keep retrying in the background.
    return (
      <section className="rounded-2xl border border-amber-500/40 bg-amber-500/10 p-4 text-center" role="status">
        <h2 className="text-base font-semibold">Can't reach your TV</h2>
        <p className="mt-1 text-sm text-zinc-300">
          Check your connection - we'll keep trying. If this keeps happening, scan the QR code on the TV again.
        </p>
      </section>
    );
  }
  if (paired && connection !== "rejected") return null;
  if (connection === "rejected") {
    return (
      <section className="rounded-2xl border border-amber-500/40 bg-amber-500/10 p-5 text-center" role="alert">
        <h2 className="text-lg font-semibold">This phone is no longer paired</h2>
        <p className="mt-2 text-sm text-zinc-300">Scan the QR code on the TV again to reconnect.</p>
      </section>
    );
  }
  if (hasPairParams) {
    return (
      <section className="rounded-2xl border border-sky-500/40 bg-sky-500/10 p-5 text-center" role="status">
        <h2 className="text-lg font-semibold">Pairing with your TV…</h2>
        <p className="mt-2 text-sm text-zinc-300">This only takes a moment.</p>
      </section>
    );
  }
  return (
    <section className="rounded-2xl border border-zinc-700 bg-zinc-900 p-6 text-center">
      <h2 className="text-lg font-semibold">Pair this phone</h2>
      <p className="mt-2 text-sm text-zinc-300">
        Open your camera and point it at the QR code on the TV. It opens this page and pairs
        automatically. You only do this once.
      </p>
    </section>
  );
}
