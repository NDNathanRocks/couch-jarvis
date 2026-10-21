/**
 * Hold-to-talk recording: MediaRecorder (Opus/WebM, or MP4/AAC on iOS Safari) cut into base64
 * chunks that each fit inside one API Gateway WebSocket frame.
 */

/** 18432 raw bytes -> exactly 24576 base64 chars (the contract's per-chunk maximum). A multiple
 * of 3, so no chunk but the last ever carries base64 padding. */
export const CHUNK_RAW_BYTES = 18432;
export const MAX_RECORDING_MS = 15000;
export const MIN_RECORDING_MS = 350;

const CANDIDATES = ["audio/webm;codecs=opus", "audio/webm", "audio/mp4", "audio/ogg;codecs=opus"];

export function pickMimeType(isSupported: (mime: string) => boolean): string | null {
  return CANDIDATES.find((m) => isSupported(m)) ?? null;
}

export function bytesToBase64(bytes: Uint8Array): string {
  let binary = "";
  const step = 0x8000;
  for (let i = 0; i < bytes.length; i += step) {
    binary += String.fromCharCode(...bytes.subarray(i, i + step));
  }
  return btoa(binary);
}

export async function blobToChunks(blob: Blob): Promise<string[]> {
  const bytes = new Uint8Array(await blob.arrayBuffer());
  const chunks: string[] = [];
  for (let offset = 0; offset < bytes.length; offset += CHUNK_RAW_BYTES) {
    chunks.push(bytesToBase64(bytes.subarray(offset, offset + CHUNK_RAW_BYTES)));
  }
  return chunks;
}

export interface Recording {
  chunks: string[];
  mime: string;
  durationMs: number;
}

export type RecorderError = "unsupported" | "denied" | "too_short";

export class RecorderFailure extends Error {
  constructor(readonly reason: RecorderError) {
    super(reason);
  }
}

export function voiceSupported(): boolean {
  return (
    typeof window !== "undefined" &&
    window.isSecureContext &&
    typeof navigator.mediaDevices?.getUserMedia === "function" &&
    typeof MediaRecorder !== "undefined" &&
    pickMimeType((m) => MediaRecorder.isTypeSupported(m)) !== null
  );
}

export class HoldRecorder {
  private recorder: MediaRecorder | null = null;
  private stream: MediaStream | null = null;
  private parts: Blob[] = [];
  private startedAt = 0;
  private autoStop: ReturnType<typeof setTimeout> | null = null;
  private stopped: Promise<Recording> | null = null;

  constructor(private readonly onAutoStop?: (recording: Promise<Recording>) => void) {}

  async start(): Promise<void> {
    const mime = pickMimeType((m) => typeof MediaRecorder !== "undefined" && MediaRecorder.isTypeSupported(m));
    if (!mime || !navigator.mediaDevices?.getUserMedia) throw new RecorderFailure("unsupported");
    try {
      this.stream = await navigator.mediaDevices.getUserMedia({ audio: { echoCancellation: true, noiseSuppression: true } });
    } catch {
      throw new RecorderFailure("denied");
    }
    this.parts = [];
    this.recorder = new MediaRecorder(this.stream, { mimeType: mime });
    this.recorder.ondataavailable = (e) => {
      if (e.data.size > 0) this.parts.push(e.data);
    };
    this.startedAt = performance.now();
    this.recorder.start(250);
    this.autoStop = setTimeout(() => this.onAutoStop?.(this.stop()), MAX_RECORDING_MS);
  }

  /** Finish and return the chunks. Safe to call twice (the second call gets the same result). */
  stop(): Promise<Recording> {
    if (this.stopped) return this.stopped;
    const recorder = this.recorder;
    if (!recorder) return Promise.reject(new RecorderFailure("unsupported"));
    if (this.autoStop) clearTimeout(this.autoStop);
    this.stopped = new Promise<Recording>((resolve, reject) => {
      recorder.onstop = () => {
        const durationMs = Math.round(performance.now() - this.startedAt);
        this.release();
        if (durationMs < MIN_RECORDING_MS) return reject(new RecorderFailure("too_short"));
        const mime = recorder.mimeType || "audio/webm";
        blobToChunks(new Blob(this.parts, { type: mime })).then(
          (chunks) => resolve({ chunks, mime, durationMs }),
          reject,
        );
      };
      if (recorder.state !== "inactive") recorder.stop();
      else recorder.onstop?.(new Event("stop"));
    });
    return this.stopped;
  }

  cancel(): void {
    if (this.autoStop) clearTimeout(this.autoStop);
    if (this.recorder && this.recorder.state !== "inactive") this.recorder.stop();
    this.release();
    this.stopped = Promise.reject(new RecorderFailure("too_short"));
    this.stopped.catch(() => undefined);
  }

  private release(): void {
    this.stream?.getTracks().forEach((t) => t.stop());
    this.stream = null;
  }
}
