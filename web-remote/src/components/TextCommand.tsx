import { useState } from "react";

/** The input stays editable while offline (a blip must not dismiss the keyboard); only Send is gated. */
export function TextCommand({ disabled, onSend }: { disabled: boolean; onSend: (text: string) => boolean }) {
  const [text, setText] = useState("");
  return (
    <form
      className="flex gap-2"
      onSubmit={(e) => {
        e.preventDefault();
        if (onSend(text)) setText("");
      }}
    >
      <input
        value={text}
        onChange={(e) => setText(e.target.value)}
        maxLength={500}
        placeholder="Or type a command…"
        aria-label="Type a command"
        enterKeyHint="send"
        autoCapitalize="none"
        className="min-h-12 min-w-0 flex-1 rounded-xl bg-zinc-900 px-4 text-base text-zinc-100 ring-1 ring-zinc-700 placeholder:text-zinc-500 focus:outline-none focus:ring-sky-500"
      />
      <button
        type="submit"
        disabled={disabled || text.trim() === ""}
        className="min-h-12 rounded-xl bg-sky-600 px-5 font-medium text-white active:bg-sky-700 disabled:opacity-40"
      >
        Send
      </button>
    </form>
  );
}
