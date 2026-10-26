# Developing Armrest

This is for people working on the code. For using the app, see the [README](../README.md); for installing it, see
[INSTALL.md](INSTALL.md). Agents working in this repo should also read `CLAUDE.md` and `AGENT_PROTOCOL.md`.

> The visible name is **Armrest**. Internal names stay `CouchJarvis` on purpose (the exe, the
> `%APPDATA%\CouchJarvis` folders, the Inno `AppId`, `CJ_` settings, storage keys), so existing installs and paired
> phones keep working through updates. Don't rename them.

## How it works

```
 phones (web app)                AWS relay (serverless)                     your PC (Windows)
┌──────────────────┐  wss   ┌───────────────────────────────┐  wss   ┌───────────────────────────┐
│ voice, buttons   │◄──────►│ API Gateway WebSocket          │◄──────►│ daemon                    │
│ touchpad, keys   │        │  $connect  → auth (pc key/JWT) │        │  CommandQueue (priority,  │
│ favourites, log  │        │  $default  → pair / relay /    │        │   idempotency, 1 worker)  │
└──────────────────┘        │              fan-out           │        │  LangGraph agent          │
        ▲                   │  $disconnect → presence        │        │   route→DOM→vision→verify │
        │   same LAN        │ Lambda (py3.12) + DynamoDB     │        │  Edge (CDP) + Win32 input │
        └─── LAN fallback: phone ⇄ daemon directly (/ws) ─────────────►│  faster-whisper / Groq    │
                            └───────────────────────────────┘        │  TV overlay, Control Ctr  │
                                                                      └───────────────────────────┘
```

- A **phone** pairs by scanning the TV's QR code (`?room=…&secret=…`). The secret is exchanged for a long-lived
  per-phone token and removed from the address bar. Pairings live in the phone's IndexedDB/localStorage.
- The **relay** passes envelopes between phones and the PC. It validates who may send what, but not payload
  contents, so a new button name or field needs no relay deploy. A new *message type* does.
- The **daemon** queues every command (`command_queue.py`: priority, idempotency keys, one worker). Common phrases go
  through a deterministic fast path (`agent/fastpath.py`); everything else goes to the LLM router. The agent graph
  (`agent/graph.py`) runs the plan in the order DOM, then JS, then a vision fallback, then a verify step and recovery.
  Sliders and touchpad/keys skip the queue and have their own rate limits.
- **Edge** runs with a dedicated TV profile and CDP on port 9222. `executors/edge_cdp.py` acts on the tab Edge showed
  last (the `/json/list` order), so commands follow manual tab switches. Only allowlisted JS snippets ever run.

## Repository map

| Path | What |
|---|---|
| `pc-daemon/` | The Windows app (Python 3.12): queue, agent, executors, speech, local API, watchdog, TV overlay, Control Center (`control/`), licensing |
| `pc-daemon/contracts.py` | Wire contracts: **the single source of truth** for phone ⇄ relay ⇄ PC messages |
| `infra/` | AWS SAM template + Lambdas (`connect_handler`, `disconnect_handler`, `relay_router`) |
| `web-remote/` | The phone web app (Vite + React 19 + Tailwind 4, TypeScript strict) |
| `shared/contracts/` | JSON Schema exported from `contracts.py`; `web-remote/src/contracts.gen.ts` is generated from it |
| `installer/` | PyInstaller spec, Inno Setup script, `build.ps1`, MSIX packaging |
| `scripts/` | Edge profile setup, start/restart wrappers, contract export, legal pages, licence keys, load test |
| `tests/` | `daemon/` and `infra/` (pytest), `integration/` (real Edge), `e2e/` (real browser + live daemon) |
| `docs/` | Install guide, command list, legal drafts, Store listing, release notes |

## Run from source

Prerequisites: Windows 10/11, Microsoft Edge, [uv](https://docs.astral.sh/uv/), Node 24+, and optionally an NVIDIA GPU
and [Ollama](https://ollama.com).

```powershell
uv venv --python 3.12 .venv
uv pip install -r requirements.txt -r requirements-dev.txt
copy .env.example .env                                   # then edit
ollama pull qwen2.5:7b ; ollama pull qwen2.5vl:7b        # optional local models

.\.venv\Scripts\python.exe pc-daemon\main.py --dry-run --hud   # no hardware: fakes for Edge/Windows/LLMs
.\.venv\Scripts\python.exe pc-daemon\main.py --hud             # the real thing
```

`.ps1` files open in Notepad when double-clicked; run them with
`powershell -ExecutionPolicy Bypass -File .\scripts\start_daemon.ps1 -Hud`. `--dry-run` uses a throwaway local room,
so a phone on the hosted app will sit on "Pairing…".

Phone app dev server: `cd web-remote ; npm ci ; npm run dev`.

Live-check the daemon without a phone:

```powershell
python pc-daemon\main.py --dry-run
curl.exe -N http://127.0.0.1:8765/events            # SSE: state.snapshot, then live events
curl.exe http://127.0.0.1:8765/api/metrics          # latency, token use, DOM-vs-vision rates
```

## Checks

```powershell
ruff check pc-daemon tests scripts infra ; ruff format --check pc-daemon tests scripts infra
mypy --strict pc-daemon
pytest                                       # quick: unit tests (~50 s)
pytest -m edge tests/integration             # the CDP executor against a real headless Edge
pytest -m ""                                 # everything, incl. real-browser e2e (the release gate)
python scripts/export_contracts.py --check   # schema in sync with contracts.py
python scripts/legal_pages.py --check        # website legal pages in sync with docs/*.md
cd web-remote ; npx tsc --noEmit ; npx eslint src ; npx vitest run ; npm run build
```

Every hardware or network edge (Edge CDP, screen capture, Windows input, Whisper, LLMs, AWS) sits behind a `Protocol`
with an in-repo fake (`pc-daemon/executors/fakes.py`, `tests/support.py`), so most of the suite is deterministic.

## Common changes

**A new voice phrase.** Add it to `agent/fastpath.py` (and a test in `tests/daemon/`). If it needs a new action, add a
`StepOp` in `agent/state.py`, run it in `run_step` in `agent/graph.py`, document it for the router in
`agent/prompts.py`, and add wording in `voice/replies.py`.

**A new phone button.** Add the name to `QuickAction` in `contracts.py`, map it in `fastpath.plan_button`, then run
`python scripts/export_contracts.py` and `npm run gen:contracts`. In `web-remote/src/utils/favorites.ts`, give it a
`since` version so phones hide it from PCs that can't do it.

**Anything the phone sends to the PC** must keep working with older PCs: hide new controls behind
`daemon_version` (`web-remote/src/utils/version.ts`). See the release rules below.

## Deploy the cloud relay (AWS)

```powershell
uv tool install aws-sam-cli
cd infra
sam build
sam deploy --no-execute-changeset     # review the change set, then execute it
```

The first deploy needs `--guided` (set `BudgetEmail` for a budget alarm). Copy the **WebSocketUrl** output into
`CJ_RELAY_WS_URL` and `VITE_RELAY_WS_URL`. The relay creates its own master secret on first use. Lambda and DynamoDB
(on-demand) stay inside the always-free allowances for a household; API Gateway WebSocket costs cents per month at this
traffic. Measured cost and load numbers are in `plan.md` section 13.2.

## Deploy the phone app

The phone app is hosted on **Cloudflare Pages**, project `armrest-app`, at https://armrest-app.pages.dev (free,
commercial use allowed). Log in once with `npx wrangler login`, then:

```powershell
cd web-remote
npm ci
$env:VITE_RELAY_WS_URL = "wss://<api-id>.execute-api.us-east-2.amazonaws.com/prod"   # the relay's WebSocketUrl
npm run build
npx wrangler pages deploy dist --project-name armrest-app --branch main
```

`CJ_PWA_BASE_URL` (and `brand.SITE_URL`) point the TV's QR code at this address. The security headers (strict CSP,
`no-referrer` because the pairing link holds a secret, microphone-only permissions) come from `public/_headers`, and
`public/_redirects` sends every path to the app. **Redeploy after every UI commit**: phones load the hosted app, not
the repo. A phone's pairing is stored per web address, so changing the address unpairs every phone.

## Build the installer

```powershell
powershell -ExecutionPolicy Bypass -File .\installer\build.ps1                  # direct download (Inno)
powershell -ExecutionPolicy Bypass -File .\installer\build.ps1 -Channel store   # then installer\build_msix.ps1
```

See [INSTALL.md](INSTALL.md#building-the-installer). Builds fail if an API key, room secret or GPL/non-commercial
dependency would be bundled.

## Release rules (binding)

The full text is in `plan.md` section 12.

- **One version:** bump `pc-daemon/version.py` (semver). `installer/build.ps1` and `web-remote/package.json` follow it.
- **Installers update in place:** never change the Inno `AppId`. An update never touches `%APPDATA%\CouchJarvis\`
  or the Edge TV profile.
- **Upgrade test before every tag:** install the previous published version, install the candidate over it, and check
  that settings and `room.json` are untouched (`scripts/test_installer_upgrade.ps1`).
- **The phone app and the PC app ship separately:** keep changes backward compatible within a protocol version.
- **Secrets are per install:** room id, secret and PC key are generated on the user's PC and never shipped.

## Security model

- The QR secret is exchanged for a per-phone token (signed JWT, 180 days, refreshed while in use). Room secrets are
  encrypted at rest with Windows DPAPI; the relay stores only hashes.
- The relay stamps the real sender on every message, rejects server-only message types from phones, and re-checks
  revocation on every command, keystroke and pointer message.
- The local API trusts only the PC itself; everything else needs a device token. WebSocket `Origin` and `Host` are
  checked (no DNS rebinding).
- Owner and guest roles are enforced on the PC (`roles.py`, `agent/policy.py`).
- LLM output is untrusted: navigation is limited to allow-listed https media sites, clicks only pick items the browser
  itself reported or words the user said, and only fixed JS snippets run. Ops such as key presses, QR control and
  settings (`USER_ONLY_OPS`) are refused unless they came from the user's own words.
- Touchpad/keys refuse dangerous chords (`remote_input.py`); there is no Windows key.
- Flooding is bounded: per-phone rate limits, capped queues and audio buffers, 64 KB frames, per-IP limits on the relay.
