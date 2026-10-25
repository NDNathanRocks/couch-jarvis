# plan.md — CouchJarvis / AegisRemote Implementation Plan

> Originally the Phase 1 (Architect) plan. Phases 2-3 are done; sections 1-7 are the original design, kept as the
> architecture reference. **Section 8 lists deviations; section 9 is the current state and roadmap (v0.2.0).**

## 0. Context

The repo is empty apart from `CLAUDE.md` (the CouchJarvis spec) and `AGENT_PROTOCOL.md` (the 4-phase build workflow).
The milestones in `AGENT_PROTOCOL.md` (RAG, semantic cache, X-Ray dashboard, pgvector) came from a different project template. **Decision (user-approved):** keep the protocol's
*structure* (4 sub-milestones, hard test gates, Critic phase) and fill it with CouchJarvis scope. Transport decision (user-approved):
**typed WebSocket envelopes** for phone↔AWS↔PC, plus a **local FastAPI SSE stream** (`/events`) on the daemon for the HUD, debugging, `curl -N` gates,
and the LAN fallback state feed.

Goal: phones anywhere control a Windows/TV PC by voice and buttons. Pairing is zero-account via QR. The cloud relay runs on AWS serverless. A LangGraph
Plan-Act-Verify agent drives Edge via CDP and falls back to vision. The system is hardened to "never break on the sofa".

### Key design decisions & assumptions
| Area | Decision | Why |
|---|---|---|
| IaC | **AWS SAM** (`infra/template.yaml`) | Native for API GW WS + Lambda + DynamoDB; deploys in one command |
| PWA | **Vite + React 18 + TS (strict) + Tailwind** | Static SPA, ideal for Vercel; no SSR needed |
| Python | 3.12, `pyproject.toml` in `pc-daemon/`, `ruff` + `mypy --strict` + `pytest` + `pytest-asyncio` | Production-grade gates |
| Auth | Separate secrets: `pc_key` (never leaves PC) and `room_secret` (in QR, rotatable). Lambda stores only **SHA-256 hashes**. Device tokens are **HS256 JWTs** signed with a deployment key in **SSM Parameter Store (Standard, free)** | Lambda can verify without holding raw PC secrets |
| Testability | Every hardware/network edge (Edge CDP, MSS, PyAutoGUI, Whisper, LLM/VLM, AWS) sits behind a `Protocol` with an in-repo **Fake**. Real-device tests use markers `edge`, `gpu`, `aws`, `llm` and are skipped by default | Gates stay deterministic and runnable in CI |
| Containers | **No Dockerfiles.** The daemon needs an interactive Windows desktop session, SAM packages the Lambdas, and Vercel hosts the PWA | The protocol's Docker step doesn't fit this system |

### Known risks surfaced up front
1. **API Gateway WebSocket is 12-month free tier, not always-free.** Lambda and DynamoDB (on-demand, 25 GB) are always-free. After 12 months, hobby volume costs roughly cents per month
   ($1/M messages + $0.25/M connection-minutes). The plan sets CloudWatch log retention to 3 days and adds an AWS Budget alarm at $1.
2. **Mixed content / secure context:** an HTTPS PWA on Vercel cannot open `ws://192.168.x.x`, and `getUserMedia` (mic) needs HTTPS. **LAN fallback design:** the daemon
   serves the same PWA build at `http://<lan-ip>:8765`. The HUD QR switches to that URL when the cloud is unreachable. Quick-deck and text commands work there.
   **Voice is disabled in LAN mode** unless an optional `LAN_TLS_CERT`/`LAN_TLS_KEY` (e.g. from mkcert) is configured.
3. API GW WS frame limit is **32 KB** and message limit is **128 KB**. Audio is sent as `audio.chunk` frames of ≤ 24 KB base64 each, reassembled on the PC.
   Hold-to-talk is capped at 15 s.
4. `$connect` cannot return a body. Pairing is therefore: connect with `?room&pair` → send a `pair.request` message → receive `pair.granted {device_token}`.

---

## 1. Directory Tree (target end state)

```text
couch-jarvis/
├── plan.md
├── CLAUDE.md  AGENT_PROTOCOL.md  README.md
├── .env.example                      # all daemon settings, documented
├── requirements.txt                  # pins daemon runtime deps (mirrors pyproject)
├── requirements-dev.txt              # pytest, pytest-asyncio, moto[dynamodb,ssm,apigatewaymanagementapi], ruff, mypy, httpx
├── pyproject.toml                    # root tool config: ruff, mypy, pytest (pythonpath = pc-daemon, infra/lambdas; markers)
├── shared/
│   └── contracts/
│       ├── envelope.schema.json      # GENERATED from Pydantic (single source of truth) -> consumed by PWA codegen
│       └── README.md
├── infra/
│   ├── template.yaml                 # WS API, 3 Lambdas, DynamoDB tables (TTL), SSM param ref, log retention, Budget alarm
│   ├── samconfig.toml
│   └── lambdas/
│       ├── common/                   # packaged as a SAM Layer
│       │   ├── models.py             # Lambda-side Pydantic copies of envelope types (generated/kept in sync by test)
│       │   ├── auth.py               # sha256 compare, JWT mint/verify (PyJWT), constant-time checks
│       │   ├── ddb.py                # table accessors (Rooms, Connections, Devices, Idempotency)
│       │   └── apigw.py              # post_to_connection with GoneException cleanup
│       ├── connect_handler.py        # $connect: role=pc|phone, validate pc_key / room_secret / device_token
│       ├── disconnect_handler.py     # $disconnect: delete connection, broadcast presence
│       └── relay_router.py           # $default: route by envelope.type (pair, cmd -> PC, state -> fan-out, audio relay)
├── pc-daemon/
│   ├── pyproject.toml
│   ├── main.py                       # asyncio.TaskGroup: relay client, local API, queue worker, watchdog, HUD bridge
│   ├── config.py                     # pydantic-settings; SetProcessDpiAwarenessContext(PER_MONITOR_AWARE_V2)
│   ├── contracts.py                  # ★ canonical Pydantic envelopes/events (Sec. 3) + JSON-schema export CLI
│   ├── room.py                       # persistent room_id / room_secret / pc_key in %APPDATA%\CouchJarvis\room.json (DPAPI-protected)
│   ├── event_bus.py                  # in-proc pub/sub -> cloud fan-out, LAN WS, SSE, HUD
│   ├── cloud_relay_client.py         # websockets client, full-jitter exponential backoff (1s..60s), heartbeat, cloud_up/down events
│   ├── local_api.py                  # FastAPI: /ws (LAN), /events (SSE), /api/command, /healthz, static PWA mount
│   ├── command_queue.py              # PriorityQueue + idempotency LRU/TTL + single-worker serialization + "who's driving" lock
│   ├── stt_engine.py                 # faster-whisper (CUDA, int8_float16) -> Groq whisper-large-v3-turbo fallback
│   ├── telemetry.py                  # JSONL spans (OTel-compatible field names), rotating file, metrics rollup
│   ├── llm/
│   │   ├── base.py                   # TextLLM / VisionLLM Protocols
│   │   ├── ollama.py  gemini.py      # real providers
│   │   └── fake.py                   # scripted responses for tests
│   ├── agent/
│   │   ├── state.py                  # AgentState + plan/step models (Sec. 2)
│   │   ├── prompts.py                # strict JSON prompts: router, VLM grounding, verifier
│   │   ├── fastpath.py               # deterministic regex intents (pause, volume, next) -> skip LLM
│   │   └── graph.py                  # LangGraph: router -> dom_executor -> vision_fallback -> verifier -> recovery
│   ├── executors/
│   │   ├── base.py                   # Executor Protocol, ActionResult
│   │   ├── edge_cdp.py               # Playwright connect_over_cdp, tab mgmt, YouTube/YT Music actions
│   │   ├── video_fullscreener.py     # Tier1 DOM selectors -> Tier2 JS inject -> Tier3 VLM+click
│   │   ├── vision_grounding.py       # MSS capture, Set-of-Mark grid, VLM bbox -> physical px mapping
│   │   ├── os_controller.py          # SendInput/PyAutoGUI DPI-safe, media keys, pycaw volume, SetThreadExecutionState
│   │   └── fakes.py                  # FakeBrowser, FakeScreen, FakeOS
│   ├── watchdog.py                   # CDP :9222 probe, Edge relaunch w/ --user-data-dir, crash-loop breaker
│   └── hud/
│       ├── tv_overlay.py             # PySide6 frameless, translucent, click-through; QR, toasts, timers, presence
│       └── qr.py                     # segno QR generation (cloud URL vs LAN URL)
├── web-remote/
│   ├── package.json  tsconfig.json (strict)  vite.config.ts (vite-plugin-pwa)  tailwind.config.ts
│   ├── vercel.json                   # SPA rewrites + security headers
│   ├── public/manifest.json  public/icons/*
│   ├── scripts/gen-contracts.ts      # json-schema-to-typescript from shared/contracts
│   └── src/
│       ├── main.tsx  App.tsx         # Hold-to-Talk, Quick Deck, Active Timers, Connected Friends, connection badge
│       ├── contracts.gen.ts          # GENERATED types
│       ├── hooks/useRoomSocket.ts    # reconnect w/ jitter, token persistence, heartbeat, visibilitychange resync
│       ├── components/QRScanner.tsx  # ?room=&secret= handler -> pair flow -> strip URL params
│       ├── components/{HoldToTalk,QuickDeck,TimerList,FriendsBar,Toasts}.tsx
│       ├── utils/audioRecorder.ts    # MediaRecorder opus/webm (mp4 fallback on iOS) -> 24 KB chunks
│       ├── utils/storage.ts          # localStorage + IndexedDB mirror for device_token
│       └── __tests__/*.test.ts(x)    # vitest + @testing-library/react
├── scripts/
│   ├── setup_edge_profile.ps1        # create TV profile dir, shortcut w/ --remote-debugging-port=9222
│   ├── start_daemon.ps1              # restart loop w/ backoff, logs to %LOCALAPPDATA%
│   ├── install_startup_task.ps1      # Task Scheduler "at logon" (interactive session, needed for GUI)
│   └── export_contracts.py           # writes shared/contracts/envelope.schema.json
├── tests/
│   ├── conftest.py                   # fakes, tmp telemetry dir, frozen clock
│   ├── daemon/  test_config.py test_room.py test_command_queue.py test_telemetry.py test_stt_engine.py
│   │            test_fastpath.py test_graph.py test_coord_scaling.py test_fullscreener.py
│   │            test_local_api_sse.py test_cloud_relay_client.py test_watchdog.py test_contracts.py
│   ├── infra/   test_connect.py test_relay_router.py test_disconnect.py test_auth.py   (moto)
│   └── integration/ (marked edge|gpu|aws|llm; skipped by default)
└── .github/workflows/ci.yml          # windows-latest: daemon; ubuntu: lambdas + sam validate + web
```

---

## 2. Core Pydantic Schemas — Agent State Machine (`pc-daemon/agent/state.py`)

```python
class Intent(StrEnum):
    MEDIA_PLAY = "media.play"            # "play lofi on youtube"
    MEDIA_CONTROL = "media.control"      # pause/resume/next/prev/seek
    VOLUME = "volume"                    # set/up/down/mute
    FULLSCREEN = "video.fullscreen"
    TAB = "browser.tab"                  # open/close/switch
    NAVIGATE = "browser.navigate"
    TIMER = "timer"                      # "sleep timer 30 min"
    SYSTEM = "system"                    # sleep display, etc. (allow-listed)
    UNKNOWN = "unknown"

class ActionStep(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")
    op: Literal["navigate","click_selector","type","key","media_key","volume_set","js_eval_allowlisted",
                "fullscreen","close_ad","vision_click","wait_for","timer_start"]
    args: dict[str, str | int | float | bool] = {}
    expect: str | None = None            # verifier predicate id, e.g. "video_playing", "is_fullscreen"
    timeout_ms: int = Field(5000, ge=100, le=30000)

class IntentPlan(BaseModel):            # Router LLM output (strict JSON)
    model_config = ConfigDict(extra="forbid")
    intent: Intent
    confidence: float = Field(ge=0, le=1)
    slots: dict[str, str | int | float] = {}
    steps: list[ActionStep] = Field(max_length=8)
    source: Literal["fastpath","llm"]

class GroundingResult(BaseModel):       # VLM output
    found: bool
    mark_id: int | None = None           # Set-of-Mark cell id
    bbox_norm: tuple[float,float,float,float] | None = None   # 0..1, x0,y0,x1,y1 of screenshot
    rationale: str = Field("", max_length=200)

class StepOutcome(BaseModel):
    step_index: int
    tier: Literal["dom","js","vision","os"]
    ok: bool
    error: str | None = None
    latency_ms: float

class VerifyResult(BaseModel):
    passed: bool
    predicate: str
    observed: dict[str, Any] = {}

class AgentState(BaseModel):            # LangGraph state (Pydantic state schema)
    trace_id: str
    command_id: str
    utterance: str
    issued_by: str                       # device_id
    plan: IntentPlan | None = None
    cursor: int = 0                      # current step index
    outcomes: list[StepOutcome] = []
    verify: VerifyResult | None = None
    attempts: int = 0
    max_attempts: int = 2
    status: Literal["routing","executing","vision","verifying","recovering","done","failed"] = "routing"
    final_message: str | None = None     # toast text for HUD/phones
```

**Graph edges** (`graph.py`):
`intent_router` → (UNKNOWN → END with failure toast) → `dom_executor`. When a DOM step fails and is vision-eligible, go to `vision_fallback`. Both lead to `post_action_verifier`.
If the verifier passes → END. If it fails and `attempts < max_attempts` → `recovery_node` (refocus Edge, dismiss overlays, reload tab) → `dom_executor`. Otherwise END(failed).
Every node emits `agent.node` telemetry and `agent.progress` events. Hard ceiling: 25 s per command (`asyncio.timeout`).

### Command queue models (`command_queue.py`)
```python
class Priority(IntEnum): EMERGENCY = 0; CONTROL = 10; AGENT = 50; BACKGROUND = 90
# EMERGENCY = pause/mute ("panic"), CONTROL = deterministic buttons, AGENT = voice/LLM
class QueuedCommand(BaseModel):
    priority: Priority; enqueued_at: float; seq: int      # tuple ordering (priority, seq)
    command: CommandPayload; idempotency_key: str; device_id: str
```
- Idempotency: TTL LRU (10 min, 2k keys). A duplicate gets `cmd.ack{status:"duplicate"}`, and the cached result is re-sent.
- Single worker ⇒ strict serialization. EMERGENCY commands may **cancel** the in-flight AGENT task (cooperative cancellation).
- "Driver lock": while an AGENT command runs, others queue and receive `cmd.ack{status:"queued", position}`.

### Config (`config.py`, pydantic-settings, prefix `CJ_`)
`relay_ws_url`, `pwa_base_url`, `lan_host=0.0.0.0`, `lan_port=8765`, `lan_tls_cert|key`, `edge_path`, `edge_profile_dir`, `cdp_port=9222`,
`stt_model=large-v3-turbo`, `stt_device=cuda`, `groq_api_key`, `router_provider=ollama|gemini`, `ollama_url`, `ollama_text_model`, `ollama_vlm_model=qwen2.5vl:7b`,
`gemini_api_key`, `telemetry_dir`, `hud_screen_index`, `log_level`. Secrets use `SecretStr`.

---

## 3. Data Contracts

### 3.1 WebSocket envelope (phone ↔ AWS ↔ PC, and LAN `/ws`), `pc-daemon/contracts.py`
```python
class Envelope(BaseModel, Generic[T]):
    v: Literal[1] = 1
    type: str                     # discriminator (see table)
    id: str                       # uuid7, sender-generated
    ts: float                     # unix seconds
    room_id: str
    from_: str = Field(alias="from")   # "pc" | device_id | "relay"
    reply_to: str | None = None
    payload: T
```
`type` is a discriminated union. JSON Schema is exported to `shared/contracts/` and TS types are generated for the PWA.

| type | direction | payload |
|---|---|---|
| `pair.request` | phone→relay | `{room_secret, device_name}` |
| `pair.granted` | relay→phone | `{device_id, device_token, expires_at}` (token 180 d, sliding refresh) |
| `pair.denied` | relay→phone | `{reason}` |
| `cmd.button` | phone→PC | `{command_id, idempotency_key, action: QuickAction, args}` |
| `cmd.text` | phone→PC | `{command_id, idempotency_key, text}` |
| `audio.begin` | phone→PC | `{command_id, idempotency_key, mime, sample_rate?}` |
| `audio.chunk` | phone→PC | `{command_id, seq, b64}` (≤ 24 KB) |
| `audio.end` | phone→PC | `{command_id, total_chunks, duration_ms}` |
| `cmd.ack` | PC→phone(s) | `{command_id, status: accepted|queued|duplicate|rejected, position?}` |
| `agent.progress` | PC→all | `{command_id, node, status, detail}` |
| `cmd.result` | PC→all | `{command_id, ok, message, tier_used, latency_ms}` |
| `state.snapshot` | PC→all / on request | `RoomState` |
| `state.patch` | PC→all | JSON-merge-patch of `RoomState` |
| `state.sync` | phone→PC | `{}` (sent on reconnect / visibilitychange) |
| `presence` | relay→all | `{devices:[{device_id,name,online,last_seen}]}` |
| `ping`/`pong` | both | `{}` (phones 25 s; API GW idle timeout is 10 min) |
| `error` | any | `{code, message, retryable}` |

```python
class RoomState(BaseModel):
    pc_online: bool; mode: Literal["cloud","lan"]
    now_playing: NowPlaying | None        # {title, source: youtube|ytmusic|other, is_playing, position_s, duration_s, thumbnail_url}
    volume: int = Field(ge=0, le=100); muted: bool
    timers: list[TimerInfo]               # {timer_id, label, ends_at, action}
    queue_depth: int; driver: str | None  # device_id currently executing
    devices: list[DevicePresence]
    rev: int                              # monotonic; phones drop stale patches
QuickAction = Literal["play_pause","next","prev","vol_up","vol_down","mute","fullscreen","skip_ad",
                      "close_tab","seek_fwd_10","seek_back_10","sleep_display","panic"]
```

### 3.2 DynamoDB tables (all on-demand, TTL attr `ttl`)
| Table | PK / SK | Attributes | TTL |
|---|---|---|---|
| `Rooms` | `room_id` | `pc_key_hash`, `room_secret_hash`, `pc_connection_id`, `created_at` | none |
| `Connections` | `connection_id`; GSI `room_id` | `role`, `device_id`, `connected_at` | 3 h |
| `Devices` | `room_id` / `device_id` | `name`, `revoked`, `last_seen` | 200 d |
| `Idempotency` | `room_id#key` | `first_seen` | 15 min |

Room registration is trust-on-first-use (TOFU): the first PC `$connect` for an unseen `room_id` stores the hashes. After that, the PC must present a matching `pc_key`.

### 3.3 SSE contract (daemon `GET /events`, `text/event-stream`)
- Each frame looks like `id: <rev or seq>\nevent: <type>\ndata: <Envelope JSON>\n\n`. Same envelope types as WS (`state.*`, `agent.progress`, `cmd.*`, `presence`, `telemetry.span`).
- The first frame is always `event: state.snapshot`. Supports `Last-Event-ID` replay from a 256-event ring buffer.
- `: keepalive` comment every 15 s. Headers: `Cache-Control: no-cache`, `X-Accel-Buffering: no`, `Connection: keep-alive`.
- Auth: `?token=<device_token>` (verified locally against the same signing key, which is cached from SSM on first cloud connect), or loopback-only without a token.
- `POST /api/command` (JSON `cmd.text`/`cmd.button` payload) is used by the curl gates and the LAN PWA.

### 3.4 Telemetry JSONL (`telemetry.py`; `%LOCALAPPDATA%\CouchJarvis\telemetry\YYYY-MM-DD.jsonl`, 50 MB rotation)
OTel-compatible span shape, one line per span:
```json
{"ts":"2026-10-20T19:02:11.412Z","trace_id":"32hex","span_id":"16hex","parent_span_id":"16hex|null",
 "name":"agent.node.dom_executor","kind":"INTERNAL","start_ns":0,"end_ns":0,"duration_ms":182.4,
 "status":"OK|ERROR","attributes":{
   "cj.command_id":"...","cj.device_id":"...","cj.intent":"media.play","cj.intent_source":"fastpath|llm",
   "cj.tier":"dom|js|vision|os","cj.attempt":1,"cj.fallback_reason":"iframe_cross_origin|overlay|selector_timeout|null",
   "cj.verify_passed":true,"gen_ai.system":"ollama|gemini|groq","gen_ai.request.model":"qwen2.5vl:7b",
   "gen_ai.usage.input_tokens":812,"gen_ai.usage.output_tokens":64,"cj.stt_engine":"faster-whisper|groq",
   "cj.stt_audio_ms":3100,"error.type":null}}
```
Span names: `cmd.lifecycle` (root, from enqueue to result), `stt.transcribe`, `agent.node.<name>`, `llm.call`, `vlm.ground`, `executor.step`, `watchdog.relaunch`, `relay.reconnect`.
`telemetry.rollup()` computes p50/p95 latency, DOM-vs-vision fallback rate, verify pass rate, and tokens per command. It is exposed at `GET /api/metrics`.

---

## 4. Milestones & Test Gates

Each gate must be **fully green** before the next milestone starts. Failures are fixed in place (per protocol). Commands run from the repo root on Windows PowerShell inside `.venv`.

### Milestone 2A — Daemon Core: Config, Contracts, Queue, Telemetry, STT
Build: root `pyproject.toml`, `requirements*.txt`, `.env.example`, `pc-daemon/{config,contracts,room,event_bus,command_queue,telemetry,stt_engine}.py`, `scripts/export_contracts.py`, and tests.
Key tests:
- `test_command_queue`: priority ordering; FIFO within the same priority; duplicate idempotency key → `duplicate`; 50 concurrent `put`s from 5 "devices" stay serialized (no overlap, asserted via an in-flight counter); EMERGENCY cancels a running AGENT task.
- `test_telemetry`: span nesting, valid JSONL, rollup math, rotation.
- `test_stt_engine`: local fake raises CUDA OOM → falls back to Groq (httpx mock), records `cj.stt_engine=groq`.
- `test_contracts`: round-trip every envelope type; the exported JSON schema matches the committed file (drift check).
- `test_room`: persistence across restarts; secret rotation keeps `room_id`.

**Gate 2A**
```powershell
ruff check . ; ruff format --check .
mypy --strict pc-daemon
pytest tests/daemon -q -m "not (edge or gpu or aws or llm)"
python scripts/export_contracts.py --check
```

### Milestone 2B — LangGraph Agent, Executors, Local API (SSE)
Build: `llm/*`, `agent/*`, `executors/*` (with fakes), `local_api.py`, `main.py` (with `--dry-run` that wires fakes), `watchdog.py` core logic.
Key tests:
- `test_fastpath`: "pause", "volume to 30", "next song" → plans with no LLM call.
- `test_graph` (FakeBrowser/FakeScreen/FakeLLM):
  1. happy DOM path;
  2. DOM selector times out inside a cross-origin iframe → vision fallback → verified;
  3. verifier fails → recovery → success on attempt 2;
  4. exhaust attempts → `failed` with toast;
  5. router returns malformed JSON → one repair retry → UNKNOWN.
- `test_coord_scaling`: VLM normalized bbox → physical pixels at 100/125/150/175 % DPI, multi-monitor offset, and negative-origin secondary monitors.
- `test_fullscreener`: tier escalation order and short-circuit on first success.
- `test_local_api_sse` (httpx ASGI): first frame is `state.snapshot`; a command produces `cmd.ack` → `agent.progress`* → `cmd.result`; `Last-Event-ID` replay; keepalive.
- `test_watchdog`: CDP probe fails → relaunch called with `--remote-debugging-port=9222 --user-data-dir=<profile>`; crash-loop breaker after 5 relaunches in 2 min.

**Gate 2B**
```powershell
ruff check . ; mypy --strict pc-daemon
pytest tests/daemon -q -m "not (edge or gpu or aws or llm)"
# live SSE smoke (dry-run fakes):
Start-Process python "pc-daemon/main.py --dry-run"
curl.exe -N http://127.0.0.1:8765/events            # expect state.snapshot, then keepalives
curl.exe -X POST http://127.0.0.1:8765/api/command -H "Content-Type: application/json" -d '{\"type\":\"cmd.text\",\"text\":\"pause\",\"idempotency_key\":\"k1\"}'
#   -> SSE shows cmd.ack, agent.progress, cmd.result(ok=true)
# optional, with Edge running: pytest -m edge tests/integration
```

### Milestone 2C — AWS Serverless Relay + Mobile PWA
Build: `infra/` (template, layer, 3 handlers, SSM param, 3-day log retention, Budget alarm), `cloud_relay_client.py`, and all of `web-remote/`.
Key tests:
- `tests/infra` (moto for DynamoDB/SSM, stubbed `apigatewaymanagementapi`):
  - PC TOFU registration;
  - wrong `pc_key` rejected;
  - phone pairs with `room_secret` → JWT;
  - revoked device rejected;
  - `cmd.*` routed only to the PC connection;
  - `state.*` fanned out to phones only;
  - `GoneException` deletes the stale connection;
  - oversize frame → `error{code:"too_large"}`;
  - relay-level idempotency drop.
- `test_cloud_relay_client`: backoff schedule (full jitter, capped), resubscribe + snapshot push after reconnect, emits `cloud_down` → HUD switches to LAN QR.
- Web (vitest):
  - `useRoomSocket` reconnects and re-sends `state.sync`;
  - token persists across reload (localStorage + IndexedDB);
  - `QRScanner` strips the secret from the URL after pairing;
  - stale `rev` patches are ignored;
  - `audioRecorder` chunks stay ≤ 24 KB.

**Gate 2C**
```powershell
cd infra ; sam validate --lint ; sam build ; cd ..
pytest tests/infra -q
cd web-remote ; npm ci ; npx tsc --noEmit ; npm run lint ; npx vitest run ; npm run build ; cd ..
```
`sam deploy --guided` and `vercel deploy` are **outward-facing** and run only after explicit user confirmation. Once deployed, run the manual E2E: scan the QR on 2 phones → both see the same `state.snapshot` → a button press on phone A updates phone B.

### Milestone 2D — HUD, Durability Packaging, Scripts, CI
Build: `hud/tv_overlay.py` + `hud/qr.py` (Qt thread ↔ asyncio bridge via `qasync`), the full watchdog loop, `os_controller` sleep prevention while media plays, all `scripts/*.ps1`, `.github/workflows/ci.yml`, and `README.md` (setup, pairing, deploy, troubleshooting).
Key tests: HUD view-model logic (toast queue, timer countdown, QR URL choice cloud vs LAN) is tested without a display. `pytest-qt` smoke tests are marked `gui`.
CI matrix:
- `windows-latest`: ruff, mypy, daemon pytest (no markers);
- `ubuntu-latest`: infra pytest, `sam validate --lint`, web tsc/lint/vitest/build;
- contracts drift check.

**Gate 2D**
```powershell
pytest -q -m "not (edge or gpu or aws or llm or gui)"     # full suite
Invoke-ScriptAnalyzer -Path scripts -Recurse -Severity Error   # PSScriptAnalyzer
python pc-daemon/main.py --dry-run --hud                    # HUD shows QR + toast on a test command
# CI: push branch -> all ci.yml jobs green (push only with user approval)
```

---

## 5. Phase 3 (Critic) checklist, adapted
Start the daemon (`--dry-run`, then real) and the PWA (`npm run dev` plus the LAN-served build). Run the full pytest suite. Run the `curl -N` SSE checks. Inspect the PWA with Playwright MCP at
mobile viewports (390×844, 360×740). Audit:
- SSE buffering (GZip middleware must exclude `/events`; frames must flush per event);
- CORS (`/api/*` allows only the PWA origin and LAN origin);
- API GW 32 KB frame handling;
- Groq/Gemini 429 handling (backoff + local fallback);
- reconnect storms (jitter);
- TS strictness;
- touch-target sizes ≥ 44 px;
- double-tap idempotency;
- iOS Safari MediaRecorder mime;
- watchdog crash-loop behavior.

## 6. Verification summary
- Unit/integration: `pytest` with fakes (deterministic, CI-safe). Real-device suites use opt-in markers.
- Live: Gate 2B `curl -N` SSE flow; Gate 2C two-phone sync after deploy; Gate 2D HUD QR pair → voice "play lofi hip hop on YouTube" → fullscreen verified → telemetry line shows `cj.tier` and `cj.verify_passed=true`.

## 7. Immediate next action after approval
Copy this plan to `couch-jarvis/plan.md`, then **stop** and wait for an explicit "start Phase 2" before writing any application code.

---

## 8. Implementation notes: deviations from this plan (recorded after Phase 2)

All four milestone gates passed. The build differs from the plan above in these deliberate ways:

| Plan said | Built | Why |
|---|---|---|
| Device-token key in **SSM Parameter Store** | Master secret created race-safely on first use in a `Config` DynamoDB table; **per-room key** = `HMAC(master, room_id)`; the relay hands the PC its room key in a new `room.key` message | One-command deploy with no secret parameter; lets the PC verify tokens **offline** for LAN mode without ever learning the master secret |
| Lambda `common/models.py` = pydantic copies, shipped as a **SAM Layer** | Dependency-free dict validation bundled with each function (no layer); `tests/infra/test_models_in_sync.py` pins it to the contract | Smaller/faster cold starts, no pydantic wheels in Lambda |
| `presence` = devices only | `presence` also carries `pc_online` | The relay is the authority on who is connected |
| 13 `QuickAction`s | + `show_qr` ("Pair a friend"), also a voice fastpath | Friends can pair after the first phone without restarting anything |
| 12 agent step ops | + `seek`, `close_tab`, `sleep_display`, `show_qr` | One explicit step per quick action |
| Optional LAN note | LAN `/ws` supports **pairing** (`?pair=1`) with the QR secret, offline | The LAN fallback works with the internet fully down |
| `tests/integration` real-device suites | Markers exist (`edge`, `gpu`, `aws`, `llm`) but no real-device tests were written or run | Needs a deployed stack / Edge session / GPU / models |
| `type` op (typing text) | Declared in the schema, **not implemented** (returns "unsupported") | Not needed by any intent yet |
| TypeScript 7 / newest toolchain | TypeScript **6.0.3** | `typescript-eslint` does not yet support TS 7 |

Bugs found by tests/live runs during the build and fixed: duplicate-event replay on fresh SSE connections, a FastAPI
dependency alias that 422'd every authenticated route, `$connect` pruning its own not-yet-established socket,
vision-fallback reason attribution, `Start-Process` mangling repo paths containing spaces, and a QR that lingered
over the TV when the HUD started with phones already connected.

---

## 9. Current state (v0.2.0, 2026-10-24) and roadmap

Production: AWS stack `couch-jarvis` in **us-east-2** (relay `wss://ltqv0kbaoc.execute-api.us-east-2.amazonaws.com/prod`),
phone app at https://couch-jarvis-remote.vercel.app, installer built with `installer\build.ps1`
(`installer\dist\CouchJarvis-Setup.exe`). Deploy order after changes: daemon restart; `vercel deploy --prod` from `web-remote`
after any UI change; relay redeploy only if envelope **types** change (new payload fields pass through).

Shipped since the original plan (all in the router/fast-path agent, see README "What you can do"):

| Phase | Feature |
|---|---|
| Touchpad/keys, Log, subtitles, page-aware commands | Browser control beyond the original quick deck |
| Spoken replies | TTS chain ElevenLabs -> Edge neural voices -> Windows speech; prefs in `voice.json`; spoken only after voice commands |
| Assistant brain | Conversation memory (`memory.json`), `say` / `ask` / `web_search` ops, follow-up question card with auto-mic |
| Browsing behaviour | Search never auto-clicks; popup policy by page type (`executors/site_policy.py`); ad-blocking switch (`browser.json`); named engines; `click_text` |
| Saved pages | `bookmark` op + phone list ("save this page", "open my bookmark X") |
| QR control | Hide after first pairing, toggle / reset by voice, tray icon, second launch re-shows it |
| Complex commands | `choose` op: search stops on results, then the model picks the best match from the page's own list ("play a song by X featuring Y") |

Roadmap / not yet done:
- Try the installer on a clean machine (only verified on the development PC).
- Real-device tests (`edge`, `gpu`, `aws`, `llm` markers) are still mostly manual.
- `type` step op is declared but unimplemented (the phone keyboard covers typing).
- Longer multi-step chains (more than search then choose) and a bigger conversational surface are open ideas.

---

## 10. Field-test fixes (v0.2.1), from the first clean-PC install

Findings: "My brain is offline" on most free-form commands, slow answers, fullscreen sometimes leaves the Edge tab strip
visible, F-keys and spoken key presses missing, subtitle language menu stays open after choosing.

Root causes found in code:
- The installer never installs Ollama or a model, and the default router is Ollama, so a clean PC has no brain. Any
  `LLMError` (not installed, still loading, 20 s timeout on CPU) was reported as the same "offline".
- Defaults assume a GPU: Whisper `large-v3-turbo` and a 7B router model are far too slow on a CPU.
- Fullscreen is verified by `document.fullscreenElement` only; the browser window itself may not be fullscreen.
- Subtitle menus are opened and clicked but never closed.

| Milestone | Change | Gate |
|---|---|---|
| 3A Brain | `LLMError.kind` (unavailable / model_missing / timeout / bad_reply) -> a specific spoken/toast message; `brain.py` probes Ollama, starts it if installed but not running, preloads the model (`keep_alive` 30 m); `router_provider=auto` (Gemini if a key is set, else Ollama, other one as fallback); model default by hardware (GPU 7B, CPU 3B); 90 s local timeout | pytest `test_brain.py`, `test_graph` |
| 3B Installer | `scripts/setup_brain.ps1` (installs Ollama via its installer, pulls models, reports progress), optional install task + Start-menu shortcut, optional free Gemini key page written to `config.env` | `build.ps1` builds; script dry-run |
| 3C Speed | `stt_model=auto` (GPU large-v3-turbo, CPU small), `stt_device=auto` default, CPU threads; telemetry unchanged | `test_stt_engine` |
| 3D Keys | F1-F12 row on the phone; contract allows f1-f12; Alt+F4 refused; spoken "press F11 / escape / control L" -> `press_key` op (fast path only, remote-input policy) | vitest, `test_fastpath`, `test_remote_input` |
| 3E Fullscreen | after fullscreen succeeds: make the browser *window* fullscreen via CDP (`Browser.setWindowBounds`) if it is not, and park the pointer away from the top edge | `test_fullscreener`, real-Edge integration |
| 3F Subtitles | after choosing a language/Off, close the menu (Escape, else click the player) and restore play state | `test_subtitles` |
| 3G Docs | README commands regrouped, INSTALL.md brain section, release v0.2.1 | all suites green |

Out of scope: shipping an API key inside the installer (still refused by `test_installer_secrets`).

**Status (2026-10-24): milestones 3A-3G built; 860 Python and 169 web tests pass, 23 real-Edge integration tests pass.**
Phone UI deployed to Vercel; installer v0.2.1 built (`installer\dist\CouchJarvis-Setup.exe`). Not yet verified on a
clean machine: the installer's Gemini-key page, the "Set up AI brain" download, and the fullscreen window fix on the TV's
headed Edge (headless tests cover the logic; `Browser.setWindowBounds` needs a real window).


## 11. v0.3.0 plan: modern remote UI, sliders, favourites, in-place updates (Phase 1, awaiting approval)

Requests: (1) the installer detects an existing install (v0.2.0 onwards) and updates it, now and for every future release;
(2) Remote page: after the transport buttons add a user-editable favourites grid (2 rows x 4 = 8 buttons, picked from a
library) with the trackpad directly below; (3) volume becomes a drag slider (Control-Centre style); (4) a draggable seek
slider on the timeline for videos and songs; (5) a drastic, modern, animated redesign; (6) every install must get its own
room keys; (7) the "Waiting for a phone" badge on the TV gets smaller and fades out after 10 s of no activity;
(8) a "Reset cache" button in Settings; (9) release/update rules recorded here for all future agents.

### 11.1 Findings in the current code
- Room identity is already generated per install by `RoomStore.load_or_create` (`secrets.token_urlsafe`: 72-bit room id,
  192-bit secret, 256-bit PC key) and stored in `%APPDATA%\CouchJarvis\room.json`, never in the installer. Gap: nothing
  *tests* that two installs differ or that no build ever contains a `room.json`; the relay's room-registration collision
  path is untested.
- Installer: the `AppId` is stable, so Inno Setup already installs over an older copy, but it shows no "updating" message,
  re-asks the Gemini-key page, and has no downgrade guard. The version is passed by hand to `build.ps1 -Version`.
- Volume is only `vol_up`/`vol_down` buttons plus voice `volume_set`; seek is relative only (`seek_fwd_10`, `delta_s`).
  `NowPlaying` already carries `position_s` / `duration_s` (polled every 10 s).
- The HUD status pill is always drawn ("Waiting for a phone" / "Online - N connected").
- The PWA is deployed independently of the PC app, so a new PWA can talk to an *older* daemon. Today the PWA cannot tell.

### 11.2 Milestones and gates
| Milestone | Change | Gate |
|---|---|---|
| 4A Contracts + daemon | `QuickAction` gains `volume_set` (args `level` 0-100) and `seek_to` (args `position_s` >= 0), validated by the schema. Agent ops reuse `os_ctl.set_volume` and a new `Browser.seek_to` (absolute `currentTime`, searched in frames like `seek`). Slider traffic gets its own limiter (client trailing throttle ~120 ms plus a final commit; server bucket sized for a drag) so drags never hit the 4/s command limit. Room state gains `daemon_version`; the PWA hides new controls on an older daemon | pytest contracts/graph/limiter; regenerate `envelope.schema.json` + `contracts.gen.ts`; vitest |
| 4B Unique keys | Tests: two fresh `RoomStore`s never share id/secret/key; build and portable zip contain no `room.json`; relay refuses to register an existing room id with a different PC key (add the test if missing); a copied install folder cannot carry identity because identity lives in `%APPDATA%` | `test_room`, `test_installer_secrets`, `tests/infra` |
| 4C TV badge | Pill about 60% of today's size; fades out after `CJ_HUD_BADGE_HIDE_S` (default 10, 0 = always on) of no phone/command activity; fades back in on any activity or when the status changes (phone leaves, error). QR card and toasts unchanged | `test_hud_viewmodel` (clock-driven), a manual look on the TV |
| 4D Phone redesign | See 11.3 | vitest, tsc, eslint, Playwright screenshots at 390 px and 768 px, reduced-motion check |
| 4E Reset cache | Settings > "Reset app cache": confirm, unregister service workers, delete Cache Storage, clear session data and non-essential local storage, reload. **Keeps** the pairing token and favourites (a separate "Unpair" already exists) | vitest with mocked `caches` / `serviceWorker` |
| 4F Installer upgrade | See 11.4 | scripted upgrade test (v0.2.0 -> new), `build.ps1` secret scan |
| 4G Release process | Single version source (`pc-daemon/version.py`, read by `build.ps1`, `package.json` and the release notes); section 12 rules; short pointer in `CLAUDE.md`; remove `plan.md` from `.gitignore` so clones (and future agents) get it | build reads the version; docs reviewed |

### 11.3 Phone UI (4D)
- **Remote page order**: header (small connection dot) -> Now Playing (artwork, title, seek slider, elapsed / remaining) ->
  transport row (prev, play/pause, next, back 10 s, forward 10 s) -> volume slider -> favourites (4 x 2) -> trackpad.
  The keyboard and F1-F12 keys move to a **Keys** tab (tabs: Remote, Saved, Keys). If the PC disables remote input the
  trackpad slot shows a short note, as today.
- **Volume slider**: full-width rounded pill, fill animates with the thumb, speaker icon changes with level (muted / low /
  high), tap the icon to mute, drag anywhere on the bar, haptic tick every 10 %, shows the % while dragging, follows the
  PC's real volume when not dragging. `role="slider"`, arrow keys, 44 px minimum touch target.
- **Seek slider**: drag to scrub with a live time bubble, commit on release; the position advances locally each second
  while playing and re-syncs on each poll; hidden for live streams (no finite duration); works for YouTube, YouTube Music
  and any page with a `<video>`/`<audio>`.
- **Favourites**: 8 slots; long-press or "Edit" enters edit mode (slots jiggle, empty slots show +); a bottom sheet shows
  the library grouped by Playback, Volume, Browser, Display, Keys (every `QuickAction`, F1-F12, F11 full screen, Esc, Space,
  common voice commands such as "subtitles on"). Stored per phone in local storage (not shared between phones; a shared
  version would need relay state and is out of scope). Defaults on first run: Fullscreen, Subtitles, Mute, Skip ad, Back,
  New tab, F11, Sleep display. A button the connected daemon does not support is shown disabled.
- **Look and motion**: keep the dark theme tokens, add depth (layered glass surfaces, soft shadows, blurred artwork behind
  Now Playing), spring press feedback, staggered entrance, animated tab transitions, toast slide-ins, slider thumb growth,
  play/pause icon morph, edit-mode jiggle. Only `transform`/`opacity` animate; everything honours `prefers-reduced-motion`
  (already enforced globally in `index.css`). No new animation dependency (CSS + small hooks) to keep the bundle small and
  the CSP strict.

### 11.4 Installer upgrade (4F)
- Never change `AppId {7B1E3C52-5A9D-4B5E-9C1A-2F4D8E6A0C31}`: it is how every future installer recognises an install.
- `InitializeSetup` reads `DisplayVersion` under `HKCU/HKLM Software\Microsoft\Windows\CurrentVersion\Uninstall\{AppId}_is1`,
  shows "Couch Jarvis X is installed; this updates it to Y. Your settings and paired phones are kept.", warns on a downgrade
  and on an identical version, stops the running app (`CloseApplications` plus a `taskkill` fallback) and restarts it after.
- On an upgrade skip the Gemini-key page and reuse the previous folder and tasks; keep `config.env` as it is (new settings
  are documented in `defaults.env`, never overwritten into the user's file). Never touch `%APPDATA%\CouchJarvis\room.json`
  or the Edge TV profile. Uninstall still asks before deleting user data; an upgrade never does.
- **App-side update notice** (proposed, opt-out `CJ_UPDATE_CHECK=false`): once a day the daemon asks the public GitHub
  `releases/latest` endpoint for the newest tag (no data sent) and shows a toast on the TV/phone when it is newer. It never
  downloads or runs anything by itself. Silent self-update is out of scope until the installer is code-signed.
- **Test**: install the published v0.2.0 installer silently into a temp folder, put marker settings and a fake `room.json`
  in place, install the new build over it, then assert: files updated, version registered, settings and `room.json`
  untouched, one Start-menu entry, app relaunches. Risk: the registry entry is shared with any real install on the same
  user, so run it in Windows Sandbox / a throwaway VM, or accept that this PC's own entry is replaced by the newer one.

### 11.5 Risks
- Sliders create a lot of traffic: mitigated by client throttling, a dedicated limiter and sending only the final value.
- Seek on pages without a `<video>` or inside cross-origin frames: falls back to a clear error toast.
- An old daemon with a new PWA: handled by `daemon_version` gating (rule 5 below).
- The installer is unsigned, so Windows SmartScreen warns on first run; unchanged by this work.
- A large visual change is subjective: Phase 3 includes screenshots at phone and tablet width for the user's review.

### 11.6 Out of scope
Shared favourites between phones, silent self-update, code signing, a redesign of the TV overlay beyond the badge.

---

## 12. Release and update rules (binding for every agent)
1. **One version, one place.** Bump `pc-daemon/version.py` (semver); `build.ps1`, `package.json`, the installer and the
   release notes all read it. Patch = fixes, minor = features, major = a breaking change to the contract or data.
2. **Installers always update in place.** Never change the Inno `AppId`. A user must never have to uninstall first.
3. **User data is sacred.** An upgrade never deletes or rewrites `%APPDATA%\CouchJarvis\` (`config.env`, `room.json`, device
   tokens, favourites) or the Edge TV profile. Config and data migrations are additive and idempotent, and are tested
   starting from data written by the previous released version.
4. **Upgrade test before every tag**: install the last two published versions, upgrade each to the candidate, check
   settings, pairing and the app launch. Record the result in the release notes.
5. **The PWA and the daemon ship separately.** Within one `PROTOCOL_VERSION`, changes are backward compatible; a new control
   is hidden when the daemon's `daemon_version` is older than the version that added it. Deploying the PWA must never break
   an installed older daemon, and vice versa. Always redeploy Vercel after UI commits.
6. **Per-install secrets.** Room id/secret/PC key are generated at first run on the user's PC and never shipped. Builds that
   contain a key, `.env`, `config.env` or `room.json` must fail (`Assert-NoSecrets`, `test_installer_secrets`).
7. **Release checklist**: all suites green (`pytest -m ""` = everything incl. real-browser e2e and real-Edge tests, ~3 min; plain `pytest` is the ~45 s quick loop for day-to-day work; vitest, ruff, mypy, tsc, eslint, npm/pip audit) -> secret
   scan of history and build -> build installer + portable zip -> upgrade test -> deploy Vercel -> update README / INSTALL /
   this plan / memory -> ask the user, then push and tag `vX.Y.Z` -> the user publishes the release and attaches the
   installer, the portable zip and their SHA-256 checksums (`gh` is not installed here).
8. **Ask first** before any push, tag, production deploy or anything outward-facing.

### 11.7 Status (2026-10-24): milestones 4A-4G built and verified (v0.3.0; v0.2.1 was folded in, never tagged)

| Milestone | Result |
|---|---|
| 4A | `volume_set` / `seek_to` as `cmd.button` actions with validated numbers; the service applies them at once (own limiter, no queue, no spoken reply); `Browser.seek_to`; `daemon_version` and `update_available` in room state. Real browser check: a volume drag sends a few throttled values ending on the final one; a seek tap sends the right second |
| 4B | Tests: 50 fresh rooms never share an id/secret/key; a new folder gets new keys; the build refuses a bundled `room.json`. The relay already refuses to re-register a room with another PC key (`test_connect`) |
| 4C | Status pill is about 60% of its old size, fades after `CJ_HUD_BADGE_HIDE_S` (10 s) of quiet, returns on any phone message or status change. Painted-pixel test |
| 4D | Remote tab: Now Playing + seek slider, transport, volume slider, 8 favourites (picker with 6 groups, per-phone), trackpad; Keys tab holds the keyboard. Glass UI, springs, tab glide, jiggle edit mode. e2e (real Edge) at 320, 375, 430 and landscape: touch targets >= 44 px, no overflow, no layout shift |
| 4E | Settings > Reset app cache (service workers, Cache Storage, session data, recents; keeps pairing and favourites) |
| 4F | Installer detects the registered version, shows "X is installed. This will update it to Y", stops the windowless app itself, reuses the folder, skips the key page, restarts the app after a silent update. `scripts/test_installer_upgrade.ps1` ran 0.2.0 -> 0.3.0 -> uninstall on this PC: 19/19 checks. Daily GitHub release check (`CJ_UPDATE_CHECK`) shows a notice, never downloads |
| 4G | `pc-daemon/version.py` is the only version; `build.ps1` and `package.json` follow it (tests enforce it); `CLAUDE.md` carries the rules; `plan.md` is no longer ignored |

Bugs the upgrade test found (both fixed, both guarded by `test_release_rules.py`):
- Windows' own "close applications" step cannot close the windowless supervisor, so an update aborted. The installer now runs
  `taskkill /t` first.
- A plain `MsgBox` ignores `/SUPPRESSMSGBOXES`, so a silent uninstall waited forever on "delete your settings?". All prompts
  are `SuppressibleMsgBox` with the safe answer as default.

Not verified on real hardware: sliders and favourites on a physical phone (touch feel, haptics), the TV pill fade on the TV,
and `Browser.setWindowBounds` (v0.2.1 fullscreen fix) on the TV's headed Edge.
