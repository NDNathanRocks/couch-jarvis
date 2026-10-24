# Assistant brain: memory, replies, follow-up questions (design)

Phase 1 of the "user ready" push. Later phases: browsing behaviour (search without auto-click, popup rules + ad-block toggle, "click on X"), bookmarks, QR control.

## Goal
Commands no longer have to be actions. The agent remembers the last few turns, can answer or ask a question back, and sends web/general-knowledge questions to an Edge search.

## Decisions (user-approved)
- Extend the existing one-shot router (no second agent, no tool loop). Fast paths still skip the LLM.
- Follow-ups: question spoken (voice commands only), phone shows a card with choices and reopens the mic for ~8 s.
- Chat scope: questions about the screen / what the agent just did are answered by the assistant (`say`). Web search and general-knowledge questions ("weather today", "capital of Greece") are NOT answered by the LLM: open a new tab and search them on Edge (Bing/Copilot summarises, user can click results).

## Components
1. `agent/memory.py` `ConversationMemory`: last 6 turns (utterance, step ops/args, ok, message, page title/url), 30 min idle expiry, persisted to `memory.json`, `clear()` for "forget that". Renders `<recent_actions>` for the router prompt.
2. Plan types (`IntentPlan`): new ops `say {text}`, `ask {question, choices[]}`, and `web_search {query}` (new tab + Edge/Bing search, stays on results). `ask` max one per plan, always last step.
3. Pending question: stored per device for 60 s; the next utterance from that device is routed with `<pending_question>` in the prompt. Cleared by answer, "never mind", expiry.
4. Router prompt: memory, pending question, URL/playing state, rules for say/ask/web_search, examples ("retry that on a new tab", "what is the capital of Greece").
5. Fast path: obvious question shapes (what/who/when/where/how ... , weather, news) with no on-page referent go straight to `web_search` without the LLM.
6. Contracts: `CmdResultPayload.ask {question, choices, expires_s, listen}` and `reply` text; regenerated TS contracts.
7. Voice: ask/say spoken only when `kind=="voice"`.
8. Phone: question card with choice buttons; auto-opens the mic (tap-to-talk) ~8 s when `listen` is set; answers go as ordinary `cmd.text`/audio.

## Error handling
LLM down / bad JSON -> existing "didn't understand" result. Page text remains untrusted. `say`/`ask` never trigger actions by themselves. Memory file unreadable -> start empty.

## Testing
Unit: memory trim/expiry/persist; prompt rendering; plan validation (one ask, last); fast-path question routing; "retry on new tab" via fake LLM; pending-question follow-up; speech only for voice; web search executor on fakes. Web: question card, choices, auto-mic. Real-stack check on the PC, then Vercel deploy (relay redeploy only if envelope validation needs it).
