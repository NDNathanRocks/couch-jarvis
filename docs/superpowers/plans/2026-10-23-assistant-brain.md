# Assistant Brain Implementation Plan

> For agentic workers: execute task by task (superpowers:executing-plans). TDD: failing test, implement, pass, commit.

**Goal:** memory of recent turns, `say`/`ask` replies, follow-up questions with auto-mic, and web/general-knowledge questions routed to a new Edge search tab.
**Architecture:** extend the one-shot router (spec: `docs/superpowers/specs/2026-10-23-assistant-brain-design.md`). Memory + pending question live in `agent/memory.py`; graph splits `say`/`ask` steps out of the plan into `AgentState.reply/ask`; `web_search` is a new op backed by `Browser.open_search_tab`.
**Tech Stack:** Python 3.12 / pydantic / langgraph / pytest-asyncio; React + vitest.

## Global Constraints
- Spoken replies only for `kind == "voice"` (existing rule in `VoiceService.announce`).
- Page text is untrusted; `say`/`ask` never trigger actions; at most one `ask`, always the last step.
- Memory: last 6 turns, 30 min idle expiry; pending question 60 s; stored in `memory.json` (PC only).
- No relay redeploy unless the envelope schema rejects the new field (verify). Vercel redeploy required.

### Task 1: ConversationMemory
Files: create `pc-daemon/agent/memory.py`, `tests/daemon/test_memory.py`.
Produces: `ConversationMemory(path|None, clock)`, `.record(device_id, utterance, plan, ok, message, page_title)`, `.render() -> str` (`<recent_actions>` text), `.set_pending(device_id, question, choices)`, `.pending(device_id) -> Pending|None`, `.clear_pending(device_id)`, `.clear()`; trims to 6, expires after 1800 s idle, pending expires after 60 s; corrupt file -> empty.

### Task 2: Plan ops and state
Files: modify `agent/state.py` (StepOp += `say`, `ask`, `web_search`; AgentState += `reply: str | None`, `ask: dict | None`), `IntentPlan` validator (≤1 ask, ask last). Test in `tests/daemon/test_assistant_plans.py`.

### Task 3: Browser.open_search_tab + web_search op
Files: `executors/base.py` (protocol), `executors/edge_cdp.py` (new tab, goto `https://www.bing.com/search?q=…`, adopt), `executors/fakes.py`, `agent/graph.py` run_step + success_message ("Searching the web for …"). Test with fakes.

### Task 4: Fast path for web/general questions
Files: `agent/fastpath.py` (`plan_text` returns web_search plan for question shapes with no on-page referent: what/who/when/where/why/how …, weather, news, "capital of"), tests added to `tests/daemon/test_fastpath.py`. "what song is this", "what is playing", "what episode" are NOT web questions (left to LLM `say`).

### Task 5: Router prompt, memory in graph, say/ask handling
Files: `agent/prompts.py` (ops say/ask/web_search, rules, examples incl. retry-on-new-tab), `agent/graph.py` (`AgentDeps.memory`, context adds `<recent_actions>` and `<pending_question>`, intent_router moves say/ask into state, say-only plans finish ok without executor, runner records memory + pending, result carries `ask`), `contracts.py` (`AskPrompt`, `CmdResultPayload.ask`), regenerate contracts (`scripts/export_contracts.py`). Tests `tests/daemon/test_assistant_graph.py`.

### Task 6: Voice and wiring
Files: `voice/service.py` (`announce(..., spoken=...)`: speak reply/question text for voice commands), `main.py` (create memory, pass to deps), `service.py` (nothing if result passes through). Tests in `test_voice_replies.py`.

### Task 7: Phone UI
Files: `web-remote/src/protocol.ts`/`contracts.gen.ts`, `state/roomReducer.ts` (store `pendingAsk`), `components/QuestionCard.tsx`, `App.tsx`, `CommandBar.tsx` (auto-start tap-to-talk when `listen`), tests `web-remote/src/__tests__/question.test.tsx`.

### Task 8: Verify and ship
Full pytest + vitest + tsc + vite build; restart daemon; check relay accepts `ask` (e2e or schema); `vercel deploy --prod`; confirm bundle; commit.
