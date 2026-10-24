import { fireEvent, render, screen } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";
import { QuestionCard } from "../components/QuestionCard";
import { initialView, roomReducer, type RoomAction, type RoomView } from "../state/roomReducer";
import { incoming } from "./helpers";

const run = (view: RoomView, ...actions: RoomAction[]): RoomView => actions.reduce(roomReducer, view);
const msg = (...args: Parameters<typeof incoming>): RoomAction => ({ type: "msg", msg: incoming(...args) });
const sent = (id: string): RoomAction => ({ type: "sent", messageId: `m-${id}`, commandId: id, label: "x" });

const asked = (listen: boolean) =>
  msg("cmd.result", {
    command_id: "c1",
    ok: true,
    message: "Which song?",
    ask: { question: "Which song?", choices: ["Thank U, Next", "7 rings"], listen, expires_s: 60 },
  } as never);

describe("the assistant's question", () => {
  it("my result with a question opens a card instead of a toast", () => {
    const v = run(initialView, sent("c1"), asked(true));
    expect(v.ask).toMatchObject({ question: "Which song?", choices: ["Thank U, Next", "7 rings"], listen: true });
    expect(v.toasts).toEqual([]);
  });

  it("a friend's question never opens a card on my phone", () => {
    const v = run(initialView, asked(true));
    expect(v.ask).toBeNull();
  });

  it("my next result, or dismissing, closes the card", () => {
    let v = run(initialView, sent("c1"), asked(false));
    v = run(v, sent("c2"), msg("cmd.result", { command_id: "c2", ok: true, message: "Playing" } as never));
    expect(v.ask).toBeNull();
    v = run(initialView, sent("c1"), asked(false), { type: "dismiss_ask" });
    expect(v.ask).toBeNull();
  });
});

describe("QuestionCard", () => {
  const card = { id: "a", question: "Which song?", choices: ["Thank U, Next", "7 rings"], listen: true, expiresAt: Date.now() + 60_000 };

  it("shows the question and sends the tapped choice", () => {
    const onAnswer = vi.fn();
    render(<QuestionCard ask={card} disabled={false} listening onAnswer={onAnswer} onDismiss={() => {}} />);
    expect(screen.getByText("Which song?")).toBeTruthy();
    expect(screen.getByText(/Listening for your answer/)).toBeTruthy();
    fireEvent.click(screen.getByRole("button", { name: "7 rings" }));
    expect(onAnswer).toHaveBeenCalledWith("7 rings");
  });

  it("can be dismissed and is inert when disconnected", () => {
    const onAnswer = vi.fn();
    const onDismiss = vi.fn();
    render(<QuestionCard ask={card} disabled listening={false} onAnswer={onAnswer} onDismiss={onDismiss} />);
    fireEvent.click(screen.getByRole("button", { name: "7 rings" }));
    expect(onAnswer).not.toHaveBeenCalled();
    fireEvent.click(screen.getByRole("button", { name: "Dismiss question" }));
    expect(onDismiss).toHaveBeenCalled();
  });
});
