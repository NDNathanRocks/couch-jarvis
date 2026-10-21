# Couch Jarvis (AegisRemote)

Control the Windows PC that's plugged into your TV from any phone — by voice or buttons, from anywhere,
with **no accounts and no Wi-Fi IP setup**. Anyone you share the QR code with gets a remote; the pairing
survives closing the browser or locking the phone.

```
 phones (PWA, Vercel)            AWS (free-tier serverless)                 your PC (Windows)
┌──────────────────┐  wss   ┌───────────────────────────────┐  wss   ┌───────────────────────────┐
│ Hold-to-talk     │◄──────►│ API Gateway WebSocket          │◄──────►│ daemon                    │
│ Quick deck       │        │  $connect  → auth (pc_key/JWT) │        │  CommandQueue (priority,  │
│ Timers / friends │        │  $default  → pair / relay /    │        │   idempotency, 1 worker)  │
└──────────────────┘        │              fan-out           │        │  LangGraph agent          │
        ▲                   │  $disconnect → presence        │        │   router→DOM→vision→verify│
        │   same LAN        │ Lambda (py3.12) + DynamoDB     │        │  Edge (CDP) + PyAutoGUI   │
        └───────────────────┴───────────────────────────────┘        │  faster-whisper / Groq    │
          LAN fallback: phone ⇄ daemon directly (/ws), no internet    │  TV HUD (QR, toasts)      │
                                                                      └───────────────────────────┘
```

## What it does

- **Zero-account pairing.** The daemon owns a persistent `room_id`, a QR `room_secret` and a private `pc_key`.
  The TV shows a QR code; scanning it opens the PWA, which swaps the secret for a long-lived device token
  (stored in `localStorage` + an IndexedDB mirror) and removes the secret from the URL.
- **Many phones at once.** Everyone sees the same live state (now playing, volume, timers, who's connected).
  Simultaneous presses are serialized by a priority queue with idempotency keys; **Stop** pre-empts a running
  voice command.
- **Plan → Act → Verify agent (LangGraph).** `IntentRouter → DOMExecutor → VisionFallback → PostActionVerifier → Recovery`.
  Common commands ("pause", "volume to 30", "skip ad") are matched deterministically without an LLM. DOM actions
  go through Playwright over CDP; when an ad overlay or cross-origin iframe blocks the DOM, a VLM
  (Ollama `qwen2.5vl:7b` or Gemini 2.5 Flash) grounds the control on a screenshot and PyAutoGUI clicks it
  at DPI-correct physical coordinates.
- **Never breaks on the sofa.** A watchdog relaunches Edge with the persistent TV profile if it dies (with a
  crash-loop breaker), the cloud client reconnects with full-jitter backoff, and if the internet goes down
  the TV QR switches to a local-network address and phones talk to the PC directly.
- **Observability.** JSONL spans (OpenTelemetry-shaped) with latency, token usage and DOM-vs-vision rates;
  `GET /api/metrics` rolls them up.

## Repository layout

| Path | What |
|---|---|
| `pc-daemon/` | The Windows agent (Python 3.12): queue, agent, executors, STT, local API, watchdog, HUD |
| `infra/` | AWS SAM template + three Lambdas (`connect_handler`, `disconnect_handler`, `relay_router`) |
| `web-remote/` | The phone PWA (Vite + React 19 + Tailwind 4, TypeScript strict) |
| `shared/contracts/` | JSON Schema exported from `pc-daemon/contracts.py` (the single source of truth) |
| `scripts/` | Edge profile setup, auto-restart wrapper, startup task, contract export |
| `tests/` | `daemon/` (pytest), `infra/` (pytest + moto) — the web tests live in `web-remote/src/__tests__` |
| `plan.md` | The architecture/milestone plan this was built from |

## Setup (Windows, one time)

Prerequisites: Windows 11, Microsoft Edge, [uv](https://docs.astral.sh/uv/), Node 24+, and (optional) an NVIDIA GPU
for local Whisper and [Ollama](https://ollama.com) for the router/VLM.

```powershell
# 1. Python env
uv venv --python 3.12 .venv
uv pip install -r requirements.txt

# 2. Edge TV profile (log in to YouTube / YouTube Music once in "Edge (TV)")
.\scripts\setup_edge_profile.ps1 -Launch

# 3. Settings
copy .env.example .env        # then edit; see comments inside

# 4. (optional) local models
ollama pull qwen2.5:7b ; ollama pull qwen2.5vl:7b

# 5. Try it with no hardware: fakes for Edge/Windows/LLMs
.\.venv\Scripts\python.exe pc-daemon\main.py --dry-run --hud
```

Real run: `.\scripts\start_daemon.ps1 -Hud` (restarts the daemon if it crashes; logs in `%LOCALAPPDATA%\CouchJarvis\logs`).
Start at login: `.\scripts\install_startup_task.ps1` (remove with `-Uninstall`; both accept `-WhatIf`).

> Chromium blocks remote debugging on the *default* profile, which is why the daemon uses its own
> `--user-data-dir`. Don't run a second Edge on that profile.

## Deploy the cloud relay (AWS)

```powershell
uv tool install aws-sam-cli        # or the MSI installer
cd infra
sam build
sam deploy --guided                # accept defaults; optionally set BudgetEmail
```

Copy the **WebSocketUrl** output into `.env` as `CJ_RELAY_WS_URL`. There are no secrets to supply: the relay creates
its own master secret on first use and stores only SHA-256 hashes of `pc_key` / `room_secret`.

**Cost.** Lambda and DynamoDB (on-demand) have always-free allowances that a household never exceeds. **API Gateway
WebSocket is free for the first 12 months only**; afterwards expect cents per month at this traffic. Log retention is
3 days, throttling is on, and setting `BudgetEmail` creates a $1/month budget alarm.

## Deploy the phone app (Vercel)

```bash
cd web-remote
npm ci
# Vercel: import the repo, root directory = web-remote, and set the env var
#   VITE_RELAY_WS_URL = <the WebSocketUrl from the SAM output>
```

Then set `CJ_PWA_BASE_URL` in `.env` to your Vercel URL so the TV's QR points at it. `vercel.json` ships a strict
CSP, `Referrer-Policy: no-referrer` (the pairing link contains a secret) and the microphone permission policy.

## Pairing and friends

1. Start the daemon with the HUD; the TV shows a QR code.
2. Scan it with the phone camera → the PWA pairs and shows the remote. Done — it reconnects by itself from now on.
3. The QR hides once someone is paired. A friend wants in? Tap **Pair a friend** in the deck (or say "pair a friend")
   and the QR reappears for 90 s.
4. Lost a QR photo or want to invalidate it? `python pc-daemon\main.py --rotate-secret` issues a new secret
   (already-paired phones keep working). To remove one phone, revoke it with `Store.revoke_device` (admin UI is not built).

### Local-network fallback

When the cloud is unreachable the HUD QR switches to `http://<pc-ip>:8765/?room=…` and the daemon serves the same
PWA plus its own `/ws`. Phones pair directly with the PC (it holds a per-room key the relay hands it on every connect, so
it verifies tokens offline). Limits: voice needs HTTPS for the microphone, so over plain-HTTP LAN use the text box
or deck — or set `CJ_LAN_TLS_CERT` / `CJ_LAN_TLS_KEY` (e.g. from `mkcert`). LAN-paired devices only work on the LAN.

## Developing

```powershell
.\.venv\Scripts\Activate.ps1
uv pip install -r requirements-dev.txt

ruff check . ; ruff format --check .
mypy --strict pc-daemon infra/lambdas
pytest -m "not (edge or gpu or aws or llm)"        # ~290 tests, no hardware/network needed
python scripts/export_contracts.py --check          # JSON Schema in sync with contracts.py

cd infra ; sam validate --lint ; sam build ; cd ..

cd web-remote
npm ci ; npx tsc --noEmit ; npm run lint ; npx vitest run ; npm run build
npm run gen:contracts                                # regenerate src/contracts.gen.ts after changing contracts.py
```

Test layers: every hardware/network edge (Edge CDP, screen capture, Windows input, Whisper, LLMs, AWS) sits behind a
`Protocol` with an in-repo fake, so the suite is deterministic. Markers `edge`, `gpu`, `aws`, `llm`, `gui` are for
opt-in real-device runs (`gui` tests run offscreen in CI).

Live-check the daemon without a phone:

```powershell
python pc-daemon\main.py --dry-run
curl.exe -N http://127.0.0.1:8765/events                       # SSE: state.snapshot, then live events
curl.exe -X POST http://127.0.0.1:8765/api/command -H "Content-Type: application/json" `
  -d '{\"type\":\"cmd.text\",\"text\":\"pause\",\"idempotency_key\":\"k1\"}'
curl.exe http://127.0.0.1:8765/api/metrics
```

## Security model (short)

- `pc_key` never leaves the PC; the relay stores only hashes. The first PC to register a room id owns it.
- Phones authenticate with HS256 JWTs signed with a **per-room key** derived from a deployment master secret
  (`HMAC(master, room_id)`); tokens last 180 days and refresh while the phone is in use.
- The relay stamps the real sender on every forwarded message and rejects server-only message types from phones.
- Pair attempts are rate-limited (5 wrong guesses drop the socket); the QR secret is constant-time compared.
- Only a fixed allowlist of JS snippets is ever evaluated in the browser; LLM output can name one, never supply code.
- The `/events`, `/api/*` and `/ws` endpoints trust loopback and otherwise require a device token.

## Troubleshooting

| Symptom | Check |
|---|---|
| Phone says "PC offline" | Is the daemon running? `CJ_RELAY_WS_URL` set? Log lines `cloud relay: up` |
| Phone says "Re-pair needed" | Token revoked/expired or the room changed: scan the QR again |
| Daemon log: `handshake rejected (401)` | Another PC registered this `room_id`, or `pc_key` changed. Delete `%APPDATA%\CouchJarvis\room.json` for a new room |
| Commands do nothing in Edge | Port 9222 up? `http://127.0.0.1:9222/json/version`; use the "Edge (TV)" shortcut / let the watchdog launch it |
| Voice button disabled | Mic needs HTTPS (hosted PWA is fine); on the LAN use text or configure TLS |
| Voice is slow | Set `CJ_STT_DEVICE=cuda`, or add `CJ_GROQ_API_KEY` for the free-tier fallback |
| Vision fallback never fires | Ollama running with `qwen2.5vl:7b`, or set `CJ_GEMINI_API_KEY` |

## Status

Built and verified in automated tests: contracts, command queue, STT fallback chain, agent graph (incl. vision
fallback and recovery), coordinate/DPI mapping, SSE/WebSocket API, Lambda relay (against moto), cloud reconnect logic,
LAN pairing, HUD rendering (offscreen pixel checks), and the PWA (reducers, connection state machine, UI flows).
**Not yet exercised against real services:** a deployed AWS stack, real YouTube/Edge selectors, a real GPU Whisper run,
and real Ollama/Gemini replies — those are the opt-in `edge` / `gpu` / `aws` / `llm` test markers.
