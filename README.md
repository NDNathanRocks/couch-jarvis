# Couch Jarvis

Control the Windows PC that's plugged into your TV from any phone, by voice, buttons, touchpad or keyboard, from
anywhere. **No accounts and no Wi-Fi/IP setup.** Scan a QR code on the TV once and the phone stays paired; friends
can scan the same QR and get their own remote.

> **Want to install it?** Read [docs/INSTALL.md](docs/INSTALL.md): a step-by-step guide for the installer, the
> portable zip, or running from source.

---

## What you can do with it

### Talk to it

Hold the big **Hold to talk** button, say a command, release. Or type it in the text box. Commands that don't
need any thinking are matched instantly; anything else goes to the AI router (needs Ollama or a Gemini key, see
[Settings](#settings)).

| You say | What happens |
|---|---|
| "pause" / "stop" / "play" / "resume" | Pause or resume the video |
| "next" / "skip" / "previous" | Next or previous track/video |
| "skip ahead" / "skip ahead 30 seconds" / "rewind" / "rewind 20 seconds" | Seek (default 10 s) |
| "volume up" / "louder" / "volume down" / "quieter" | Volume steps |
| "volume to 30" / "mute" / "unmute" | Exact volume, mute state |
| "fullscreen" | Fullscreen the player (works on YouTube and on most embedded players) |
| "skip the ad" | Skip or close a video ad |
| "new tab" / "close this tab" | Open or close a tab |
| "next tab" / "previous tab" | Switch tabs |
| "go back" / "go forward" / "previous page" | Browser history |
| "reload" / "refresh the page" | Reload the page |
| "scroll down" / "scroll up a bit" | Scroll the page |
| "search for bruno mars" / "search uptown funk on YouTube Music" | Shows the **search results and waits**, so you choose |
| "play bruno mars on YouTube" / "play lofi on YouTube Music" | Searches and plays the first result |
| "play episode 5" / "play the third episode" | Plays that episode from the list on the page you're looking at |
| "open the second result" / "click the first video" / "play number 3" | Picks the Nth item on the current page |
| "set a timer for 10 minutes" / "timer 90 seconds" | A timer with a countdown on the TV |
| "sleep timer 30 minutes" | Pauses the video when it ends |
| "turn off the screen" | Blanks the TV without stopping anything |
| "show the QR code" / "pair a friend" | Shows the pairing QR on the TV for 90 s |

Anything else ("put on the one with the cat", "play the pilot") is understood by the AI router, which sees the
items on the current page and picks one. It stays on the current site unless you say *search*, *YouTube* or *music*.

### Press buttons (Remote tab)

- **Quick deck:** previous, play/pause, next, back/forward 10 s, fullscreen, volume down/mute/up, skip ad, close tab,
  screen off, and a red **Stop** that pauses everything immediately (it jumps the queue).
- **Browser row:** back page, forward page, reload, new tab, tab left/right, scroll up/down.
- **Pair a friend:** shows the QR on the TV.
- **Now playing** card, **timers** with live countdowns, and a **Connected friends** list.

### Use it as a touchpad and keyboard (Touchpad & keys tab)

- **Touchpad:** drag to move the cursor, tap to click, two-finger tap to right-click, two-finger drag to scroll,
  press-and-hold then drag to select or drag things. There are also Left / Double / Right click and Scroll buttons.
- **Keyboard:** tap the box to bring up your phone's keyboard and type straight onto the TV (Enter and Backspace work).
- **Keys:** arrows, Esc, Tab, Enter, Backspace, Delete, Space, Home/End, PgUp/PgDn, F5, F11.
  **Ctrl / Alt / Shift** latch for the next key, so **Alt then ←** is "Back".
- **One-tap shortcuts:** Back (Alt+←), Forward, Reload, New tab, Close tab, Next/Prev tab, Address bar, Zoom in/out/reset,
  Switch window (Alt+Tab).

Some combinations are blocked on purpose (Ctrl+Alt chords, Task Manager, the Start menu, clear-browsing-data, and there
is no Windows key). Turn the whole feature off with `CJ_REMOTE_INPUT=false`.

### Know what went wrong (Log button)

Tap **Log** at the top of the phone app. It shows, newest first, every command you sent: what the PC **heard**, whether
it worked, **which step failed and why**, how long it took, plus connection changes ("Connection lost - retrying").
A red dot means the last thing was a problem. **Copy** puts it on the clipboard to paste somewhere.

### Everyone gets a remote

- Any number of phones at once; everyone sees the same live state. Simultaneous presses are queued, never raced.
- Pairing survives closing the browser, locking the phone or rebooting it.
- The **TV overlay** shows the QR (until someone pairs), toasts, timers and a connection pill.
- **Forget this phone** unpairs a phone; you can also revoke any phone from the PC (see [Security](#security)).

### It's built not to break on the sofa

- Edge is supervised: if it crashes it comes back with your signed-in TV profile.
- The app restarts itself if it crashes, and starts with Windows if you choose that in the installer.
- The cloud connection reconnects by itself with backoff; if the internet is down, the TV shows a local-network QR.
- Voice runs on your GPU (`faster-whisper`), with an optional free cloud fallback (Groq).

### What it won't do (yet)

- It controls **one Edge window** (the "TV profile"), not every app on the PC. The touchpad and keyboard can drive anything.
- Sites full of ads can interfere: pop-ups are closed, redirects are undone, and you may need to tap fullscreen twice.
  DRM sites such as Netflix may refuse a remote-controlled browser.
- The phone talks to the PC through a cloud relay, so touchpad movement has a short delay (typically 0.1-0.3 s).
- Microphone use needs the HTTPS phone app (the hosted one); the plain-HTTP LAN page can't record.
- Windows only.

---

## Get it running

| I want to... | Do this |
|---|---|
| Install it on a PC | [docs/INSTALL.md](docs/INSTALL.md), "Installer" |
| Try it without installing | [docs/INSTALL.md](docs/INSTALL.md), "Portable zip" |
| Run from source / develop | [Developing](#developing) below |

After installing: the TV shows a **QR code**. Scan it with your phone camera, open the link, and you're paired.

## Settings

Settings live in `%APPDATA%\CouchJarvis\config.env` (the installer creates it). A `.env` file next to where you
run from source overrides it. Restart the app after changing anything. The useful ones:

| Setting | Default | What it does |
|---|---|---|
| `CJ_RELAY_WS_URL` | set by the installer | The cloud relay (WebSocket URL from `sam deploy`) |
| `CJ_PWA_BASE_URL` | set by the installer | Where the phone app is hosted; the QR points here |
| `CJ_STT_DEVICE` | `cuda` | `cuda` (NVIDIA GPU), `cpu`, or `auto` |
| `CJ_STT_MODEL` | `large-v3-turbo` | Whisper model; use `small` or `base` on a weak PC |
| `CJ_GROQ_API_KEY` | none | Free cloud speech-to-text, used if local voice fails |
| `CJ_ROUTER_PROVIDER` | `ollama` | `ollama` (local) or `gemini` for understanding free-form commands |
| `CJ_OLLAMA_TEXT_MODEL` / `CJ_OLLAMA_VLM_MODEL` | `qwen2.5:7b` / `qwen2.5vl:7b` | Local models (`ollama pull ...`) |
| `CJ_GEMINI_API_KEY` | none | Free Gemini key for understanding commands / finding buttons on screen |
| `CJ_REMOTE_INPUT` | `true` | Let paired phones move the cursor and type |
| `CJ_HUD_SCREEN_INDEX` | `0` | Which monitor shows the QR overlay (the TV) |
| `CJ_EDGE_PATH` | auto | Path to `msedge.exe` if it isn't found |
| `CJ_LOG_LEVEL` | `INFO` | `DEBUG` for more detail |

Logs: `%LOCALAPPDATA%\CouchJarvis\logs`. They never contain pairing links or secrets.

## Security

**Pair only people you would trust at your keyboard.** A paired phone can play media, and (unless you turn it off with
`CJ_REMOTE_INPUT=false`) move the mouse and type like someone sitting at the PC. You can remove a phone at any time:
on the PC, `curl.exe -X POST http://127.0.0.1:8765/api/devices/<device_id>/revoke` (`GET /api/devices` lists ids), or
rotate the QR secret with `python pc-daemon\main.py --rotate-secret`.

What is protected, in short:

- The QR secret is exchanged for a long-lived per-phone token; the secret is removed from the address bar immediately.
  Room secrets are encrypted at rest with Windows DPAPI. The relay stores only hashes.
- Tokens are per-room signed JWTs (180 days, refreshed while in use). The relay stamps the real sender on every message,
  rejects server-only message types from phones, and re-checks that a phone hasn't been revoked on every command,
  keystroke and mouse message.
- The local API trusts only the PC itself; everything else needs a device token. Because browsers don't apply CORS to
  WebSockets, requests are also checked for same-origin/allow-listed `Origin` and an IP/`localhost` `Host` (stops web
  pages you visit from hijacking the daemon, including DNS rebinding).
- Anything the AI can ask for is policy-checked in code: allow-listed media keys, https media sites for navigation,
  picking items only from a list the browser itself reported (the AI never supplies a selector), and fixed JS snippets
  only. Page text shown to the AI is sanitized and treated as untrusted data.
- Flooding is bounded: per-phone rate limits (commands and, separately, touchpad input), capped queues and audio
  buffers, and 64 KB WebSocket frames. Dependencies are audited (`pip-audit`, `npm audit`) as part of releases.
- A site that tries to redirect your TV tab to another site during a click is undone, and its pop-ups are closed.

## Troubleshooting

Tap **Log** on the phone first: it usually names the cause.

| Symptom | Check |
|---|---|
| "The PC is offline" | Is Couch Jarvis running on the PC? (Start menu -> Couch Jarvis.) Is the PC online? |
| Phone stuck on "Connecting..." | Use Chrome first to rule out a browser quirk; see [INSTALL.md](docs/INSTALL.md#troubleshooting). Check the daemon log for `cloud relay: up` |
| "Recording was incomplete" | Fixed in current versions (parts can arrive out of order); update the PC app **and** redeploy the relay |
| Voice says "I could not hear that" | Check the Log's `heard:` line. Set `CJ_STT_DEVICE=cpu` or add `CJ_GROQ_API_KEY`. First use downloads the speech model |
| A command is misunderstood | The Log shows `heard:`. Free-form commands need Ollama or `CJ_GEMINI_API_KEY` |
| Fullscreen does nothing | Tap it again (ad pages eat the first click); the Log shows which step failed |
| "Commands do nothing in Edge" | Edge is started and supervised by the app on port 9222. Close other Edge windows that use the TV profile |
| The QR keeps showing | It hides when a phone is connected. It shows again with **Pair a friend** |
| Touchpad/keyboard missing | The PC has `CJ_REMOTE_INPUT=false`, or the phone isn't connected yet |

---

## For developers

### How it works

```
 phones (PWA, Vercel)            AWS (free-tier serverless)                 your PC (Windows)
┌──────────────────┐  wss   ┌───────────────────────────────┐  wss   ┌───────────────────────────┐
│ Hold-to-talk     │◄──────►│ API Gateway WebSocket          │◄──────►│ daemon                    │
│ Quick deck       │        │  $connect  → auth (pc_key/JWT) │        │  CommandQueue (priority,  │
│ Touchpad / keys  │        │  $default  → pair / relay /    │        │   idempotency, 1 worker)  │
│ Log              │        │              fan-out           │        │  LangGraph agent          │
└──────────────────┘        │  $disconnect → presence        │        │   router→DOM→vision→verify│
        ▲                   │ Lambda (py3.12) + DynamoDB     │        │  Edge (CDP) + Win32 input │
        │   same LAN        └───────────────────────────────┘        │  faster-whisper / Groq    │
        └─── LAN fallback: phone ⇄ daemon directly (/ws), no internet │  TV HUD (QR, toasts)      │
                                                                      └───────────────────────────┘
```

| Path | What |
|---|---|
| `pc-daemon/` | The Windows agent (Python 3.12): queue, agent, executors, STT, local API, watchdog, HUD |
| `infra/` | AWS SAM template + three Lambdas (`connect_handler`, `disconnect_handler`, `relay_router`) |
| `web-remote/` | The phone PWA (Vite + React 19 + Tailwind 4, TypeScript strict) |
| `shared/contracts/` | JSON Schema exported from `pc-daemon/contracts.py` (the single source of truth) |
| `installer/` | PyInstaller spec, Inno Setup script and `build.ps1` |
| `scripts/` | Edge profile setup, auto-restart wrapper, startup task, contract export |
| `tests/` | `daemon/` and `infra/` (pytest), `e2e/` (real browser), `integration/` (real Edge); web tests live in `web-remote/src/__tests__` |

### Run from source

Prerequisites: Windows 11, Microsoft Edge, [uv](https://docs.astral.sh/uv/), Node 24+, and (optional) an NVIDIA GPU
and [Ollama](https://ollama.com).

```powershell
uv venv --python 3.12 .venv
uv pip install -r requirements.txt
copy .env.example .env                       # then edit
ollama pull qwen2.5:7b ; ollama pull qwen2.5vl:7b      # optional local models

.\.venv\Scripts\python.exe pc-daemon\main.py --dry-run --hud   # no hardware: fakes for Edge/Windows/LLMs
.\.venv\Scripts\python.exe pc-daemon\main.py --hud             # the real thing
```

`.ps1` files open in Notepad when double-clicked; run them with
`powershell -ExecutionPolicy Bypass -File .\scripts\start_daemon.ps1 -Hud`. That wrapper restarts the daemon if it
crashes; `install_startup_task.ps1` starts it at login. `--dry-run` only talks to a throwaway local room: a phone on the
hosted app will sit on "Pairing..." because it uses the cloud relay.

### Deploy the cloud relay (AWS)

```powershell
uv tool install aws-sam-cli
cd infra
sam build
sam deploy --guided          # set BudgetEmail for a $1/month alarm; some organisations only allow one region
```

Copy the **WebSocketUrl** output into `CJ_RELAY_WS_URL` (and `VITE_RELAY_WS_URL` for the phone app). The relay creates its
own master secret on first use. **Cost:** Lambda and DynamoDB (on-demand) have always-free allowances a household never
exceeds. API Gateway WebSocket is free for the first 12 months, then cents per month at this traffic. Log retention is
3 days and throttling is on. After upgrading the daemon, **redeploy the relay**: new message types (touchpad/keyboard)
are rejected by an older relay.

### Deploy the phone app (Vercel)

```bash
cd web-remote
npm ci
vercel --prod            # set VITE_RELAY_WS_URL to the WebSocketUrl; the build command is `vite build`
```

Set `CJ_PWA_BASE_URL` to the Vercel URL so the TV's QR points at it. `vercel.json` ships a strict CSP, `no-referrer`
(the pairing link contains a secret) and the microphone permissions policy.

### Build the installer

```powershell
powershell -ExecutionPolicy Bypass -File .\installer\build.ps1
```

See [docs/INSTALL.md](docs/INSTALL.md#building-the-installer).

### Checks

```powershell
uv pip install -r requirements-dev.txt
ruff check . ; ruff format --check . ; mypy pc-daemon
pytest                                              # unit + e2e + real-Edge integration (e2e runs last)
python scripts/export_contracts.py --check          # JSON Schema in sync with contracts.py
cd web-remote ; npm ci ; npm run typecheck ; npm run lint ; npx vitest run ; npm run build
npm run gen:contracts                               # regenerate src/contracts.gen.ts after changing contracts.py
```

Every hardware/network edge (Edge CDP, screen capture, Windows input, Whisper, LLMs, AWS) sits behind a `Protocol` with an
in-repo fake, so most of the suite is deterministic. Live-check the daemon without a phone:

```powershell
python pc-daemon\main.py --dry-run
curl.exe -N http://127.0.0.1:8765/events            # SSE: state.snapshot, then live events
curl.exe http://127.0.0.1:8765/api/metrics          # latency, token use, DOM-vs-vision rates
```

### Status

Verified in automated tests: contracts, command queue, STT fallback chain, agent graph (including vision fallback and
recovery), page-aware commands, DPI mapping, SSE/WebSocket API, Lambda relay (against moto), reconnect logic, LAN
pairing, HUD rendering, the PWA (reducers, gestures, connection state machine, UI flows, a real-browser e2e suite) and
the CDP executor against a real Edge. Exercised by hand against real services: a deployed AWS stack, Vercel, real Edge on a
streaming site, local Ollama models. Not yet exercised: the packaged installer on a clean PC.
