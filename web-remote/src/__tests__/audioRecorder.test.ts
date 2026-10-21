import { describe, expect, it } from "vitest";
import { CHUNK_RAW_BYTES, blobToChunks, bytesToBase64, pickMimeType } from "../utils/audioRecorder";

const MAX_B64 = 24 * 1024; // the contract's per-chunk limit (AudioChunkPayload.b64 maxLength)

function fromBase64(b64: string): Uint8Array {
  return Uint8Array.from(atob(b64), (c) => c.charCodeAt(0));
}

describe("audio chunking", () => {
  it("a raw chunk encodes to exactly the 24 KB limit, never above", () => {
    expect(bytesToBase64(new Uint8Array(CHUNK_RAW_BYTES)).length).toBe(MAX_B64);
    expect(CHUNK_RAW_BYTES % 3).toBe(0); // no padding mid-stream
  });

  it.each([0, 1, 100, CHUNK_RAW_BYTES - 1, CHUNK_RAW_BYTES, CHUNK_RAW_BYTES + 1, CHUNK_RAW_BYTES * 3 + 7, 200_000])(
    "round-trips %i bytes with every chunk within the limit",
    async (size) => {
      const bytes = Uint8Array.from({ length: size }, (_v, i) => (i * 31 + 7) % 256);
      const chunks = await blobToChunks(new Blob([bytes]));
      expect(chunks.length).toBe(Math.ceil(size / CHUNK_RAW_BYTES));
      for (const c of chunks) expect(c.length).toBeLessThanOrEqual(MAX_B64);
      const joined = new Uint8Array(chunks.reduce((n, c) => n + fromBase64(c).length, 0));
      let offset = 0;
      for (const c of chunks) {
        const part = fromBase64(c);
        joined.set(part, offset);
        offset += part.length;
      }
      expect(Array.from(joined)).toEqual(Array.from(bytes));
    },
  );

  it("a 15 s Opus clip (~60 KB) fits in a handful of frames", async () => {
    const chunks = await blobToChunks(new Blob([new Uint8Array(60_000)]));
    expect(chunks.length).toBe(4);
  });

  it("only the final chunk may carry base64 padding", async () => {
    const chunks = await blobToChunks(new Blob([new Uint8Array(CHUNK_RAW_BYTES * 2 + 1)]));
    expect(chunks.slice(0, -1).every((c) => !c.endsWith("="))).toBe(true);
  });
});

describe("pickMimeType", () => {
  it("prefers webm/opus, then webm, then mp4 (iOS Safari)", () => {
    expect(pickMimeType(() => true)).toBe("audio/webm;codecs=opus");
    expect(pickMimeType((m) => m === "audio/mp4")).toBe("audio/mp4");
    expect(pickMimeType((m) => m.startsWith("audio/webm") && !m.includes("opus"))).toBe("audio/webm");
    expect(pickMimeType(() => false)).toBeNull();
  });
});
