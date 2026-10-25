import { APP_NAME, GET_URL, PRIVACY_URL, TAGLINE, TERMS_URL } from "../brand";
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
  return <Landing />;
}

/** A visitor with no pairing and no QR link: say what this is and how to start. */
function Landing() {
  const steps = [
    { title: `Get ${APP_NAME} on the PC`, text: "The Windows PC that is plugged into your TV." },
    { title: "Open it", text: "A QR code appears on the TV." },
    { title: "Scan the code", text: "With this phone's camera. You only do this once." },
  ];
  return (
    <section className="glass rise rounded-3xl p-6" aria-labelledby="landing-title">
      <h2 id="landing-title" className="text-2xl font-bold tracking-tight">
        {TAGLINE}.
      </h2>
      <p className="mt-2 text-sm text-zinc-300">
        Your phone becomes the remote for the PC on your TV: voice, buttons, a touchpad and a keyboard. No accounts and no
        Wi-Fi setup. Friends can scan the same code and get their own remote.
      </p>
      <ol className="mt-5 space-y-3">
        {steps.map((s, i) => (
          <li key={s.title} className="flex gap-3">
            <span aria-hidden className="grid size-8 shrink-0 place-items-center rounded-full bg-accent font-semibold text-zinc-950">
              {i + 1}
            </span>
            <span>
              <span className="block font-semibold">{s.title}</span>
              <span className="block text-sm text-zinc-400">{s.text}</span>
            </span>
          </li>
        ))}
      </ol>
      <a
        href={GET_URL}
        className="press mt-6 flex min-h-12 items-center justify-center rounded-full bg-accent font-semibold text-zinc-950"
      >
        Get {APP_NAME} for Windows
      </a>
      <p className="mt-4 text-center text-xs text-zinc-500">
        <a href={PRIVACY_URL} className="underline underline-offset-2">Privacy</a>
        {" · "}
        <a href={TERMS_URL} className="underline underline-offset-2">Terms</a>
      </p>
    </section>
  );
}
