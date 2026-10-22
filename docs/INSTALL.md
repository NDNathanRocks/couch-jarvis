# Install guide

This gets Couch Jarvis running on a Windows PC that's connected to your TV, and pairs your phone to it.
Time needed: about 10 minutes, plus a few more the first time voice is used (it downloads a speech model).

## What you need

| | |
|---|---|
| **The TV PC** | Windows 10 or 11 (64-bit) with **Microsoft Edge** (it's built into Windows) and an internet connection |
| **A phone** | Any phone with a camera and a browser (Chrome, Safari, Samsung Internet...) |
| **Optional: an NVIDIA GPU** | Makes voice recognition fast. Without one it still works, just slower, or use a free Groq key (below) |
| **Optional: Ollama** | Lets it understand free-form requests like "put on the one with the cat". Basic commands work without it |
| **Disk space** | About 2 GB for the app, plus the speech model (0.5-1.5 GB, downloaded on first voice use) |

## Option A: the installer (recommended)

1. Download **CouchJarvis-Setup.exe** and run it. It installs just for you; no administrator rights needed.
   - Windows may show **"Windows protected your PC"** because the installer isn't code-signed. Click **More info**,
     then **Run anyway**.
2. Choose whether to **start Couch Jarvis when you sign in to Windows**. Say yes if this PC lives next to the TV.
3. Leave **Start Couch Jarvis now** ticked and click **Finish**.
4. A **QR code** appears on the screen (the overlay goes on the monitor set by `CJ_HUD_SCREEN_INDEX`, default `0`).
5. A **Microsoft Edge** window opens: this is the "TV browser" Couch Jarvis controls. **Sign in to YouTube (and anything
   else you use) once here**; it remembers.
6. Windows may ask to allow Couch Jarvis through the firewall. **Allow on private networks** if you want the
   local-network backup mode; it works without it.

## Option B: portable zip

1. Download and unzip **CouchJarvis-portable-x.y.z.zip** anywhere (for example `C:\CouchJarvis`).
2. Double-click `CouchJarvis\CouchJarvis.exe`. Same as steps 4-6 above.

To start it at login, put a shortcut to `CouchJarvis.exe` in `shell:startup` (press Win+R and type that).

## Pair your phone

1. Point the phone camera at the QR code on the TV and open the link. (If the code isn't on the TV, say or tap
   **Pair a friend**, or start the app again.)
2. The remote opens. The badge at the top says **Connected**. You're paired. Closing the browser or locking the phone doesn't
   unpair it; reopening the app reconnects by itself.
3. Try it: tap **Play / Pause**, or hold **Hold to talk** and say "volume to 20".
4. Friends can scan the same QR code and get their own remote. Each phone can be paired with one PC at a time; scanning another
   PC's QR replaces the pairing.

Tip: add the page to your phone's home screen (Share -> Add to Home Screen) so it opens like an app.

## First-use extras

**Voice (speech recognition).** The first voice command downloads the speech model, which takes a minute or two. Options:

- *Have an NVIDIA GPU?* Speech runs on it only if NVIDIA's CUDA 12 libraries (cuBLAS and cuDNN) are present. The
  installer does **not** bundle them (about 1 GB), so out of the box it quietly uses the **CPU** instead, which is slower but
  works; the log says "GPU speech failed ... using the CPU". Running from source, `uv pip install -r requirements-gpu.txt`
  adds them and the GPU is used automatically.
- *No GPU, or the PC is slow?* In your settings file (below) set `CJ_STT_MODEL=small`, or get a free key from
  [console.groq.com](https://console.groq.com) and set `CJ_GROQ_API_KEY=gsk_...` to do voice in the cloud.

**Free-form commands.** "pause", "volume to 30", "new tab", "play episode 5", "search for ..." work immediately. For anything
else the app needs an AI model:

- *Local:* install [Ollama](https://ollama.com), then in a terminal run `ollama pull qwen2.5:7b` and
  `ollama pull qwen2.5vl:7b` (the second one lets it find buttons by looking at the screen).
- *Cloud (no download):* get a free key at [aistudio.google.com](https://aistudio.google.com) and set
  `CJ_ROUTER_PROVIDER=gemini` and `CJ_GEMINI_API_KEY=...`.

## Your settings file

`%APPDATA%\CouchJarvis\config.env` (paste that into the File Explorer address bar). It's a plain text file of `NAME=value`
lines; lines starting with `#` are ignored. **Restart Couch Jarvis after editing**: end `CouchJarvis.exe` in Task Manager, then start it from the Start menu. All settings are listed in the [README](../README.md#settings).

Logs are in `%LOCALAPPDATA%\CouchJarvis\logs`. They never contain pairing links or secrets.

## Updating and uninstalling

- **Update:** run the new installer over the old one. Your settings, paired phones and TV browser sign-ins are kept.
  If the update adds features that need the cloud relay (such as the touchpad), the person who runs the relay must update it too.
- **Uninstall:** Windows Settings -> Apps -> Couch Jarvis -> Uninstall. It asks whether to also delete your settings, paired
  phones and the TV browser profile.

## Troubleshooting

Tap **Log** at the top of the phone app first: it shows what the PC heard and which step failed.

| Problem | What to try |
|---|---|
| No QR code appears | Check it's running (Task Manager -> `CouchJarvis.exe`). The overlay may be on another monitor: set `CJ_HUD_SCREEN_INDEX=1` |
| The phone says **PC offline** | Couch Jarvis isn't running or the PC is asleep. Start it from the Start menu |
| The phone stays on **Connecting...** | Open the link in Chrome to rule out a Safari quirk; check the PC has internet; look at `daemon.log` for `cloud relay: up` |
| **"Recording was incomplete"** | Update both the PC app and the cloud relay to the latest version |
| Voice says it couldn't hear you | The Log shows `heard:`. Allow the mic in the phone browser. First use downloads the model, so wait. Try `CJ_STT_DEVICE=cpu` or a Groq key |
| It misunderstands a command | The Log shows what it heard. Free-form commands need Ollama or a Gemini key |
| Fullscreen doesn't work | Tap it twice (ad pages swallow the first click). The Log shows what failed |
| Edge doesn't open / commands do nothing | Close every Edge window and restart Couch Jarvis; it owns the TV browser on debug port 9222 |
| I want to remove someone's access | On the PC: `curl.exe http://127.0.0.1:8765/api/devices` lists phones; `curl.exe -X POST http://127.0.0.1:8765/api/devices/<id>/revoke` removes one. Or delete `%APPDATA%\CouchJarvis\room.json` and restart: that makes a new room and QR, and every phone has to pair again |
| Touchpad / keyboard tab is missing | `CJ_REMOTE_INPUT=false` is set on the PC, or the phone isn't connected |

---

## Building the installer

For whoever publishes Couch Jarvis (you, not the people installing it).

### One-time prerequisites

- The repo set up for development (see the README: `uv venv`, `uv pip install -r requirements.txt`, Node 24+).
- **Inno Setup 6** to produce the `.exe` installer: `winget install JRSoftware.InnoSetup`. Without it, the build still
  produces the portable zip.
- Your cloud relay deployed and your phone app on Vercel (README: *Deploy the cloud relay*, *Deploy the phone app*), with
  `CJ_RELAY_WS_URL` and `CJ_PWA_BASE_URL` set in the repo `.env` (or pass them as parameters below).

### Build

```powershell
powershell -ExecutionPolicy Bypass -File .\installer\build.ps1
# or, to choose the endpoints and version explicitly:
powershell -ExecutionPolicy Bypass -File .\installer\build.ps1 -RelayUrl wss://xxxx.execute-api.us-east-2.amazonaws.com/prod `
    -PwaUrl https://your-app.vercel.app -Version 0.2.0
```

It builds the phone app, writes the defaults, freezes the daemon with PyInstaller (a few minutes) and writes to
`installer\dist\`:

| File | What |
|---|---|
| `CouchJarvis\` | The frozen app (about 500 MB unpacked) |
| `CouchJarvis-portable-<version>.zip` | The same, zipped (about 200 MB) |
| `CouchJarvis-Setup.exe` | The installer (needs Inno Setup) |

Speech and vision models are **not** bundled: they download on first use, which keeps the installer a reasonable size.
Playwright's browsers are **not** bundled either; the app drives the Edge already on the PC.

Before sharing a build, test it on a PC (or Windows user account) that has no Python, Node or Visual C++ runtime installed. So far it has been verified on the development PC: a silent install into a temp folder, launching the installed app (serves the phone app, connects to the relay, creates its settings, refuses a second copy), and a clean uninstall that leaves your settings alone. A truly clean machine has not been tried yet.

### What every install shares (read this before handing it out)

All installs use **your** cloud relay and **your** hosted phone app, but each install gets its own private room, secret and
keys the first time it runs, so people can't see or control each other's PCs. What you take on by sharing:

- **Cost.** Lambda and DynamoDB are effectively free at household scale. API Gateway WebSocket is free for the first
  12 months, then charges per message (the touchpad sends many small messages). Keep the budget alarm on (`BudgetEmail`)
  and watch usage if many people install it.
- **Availability.** If your AWS or Vercel deployment goes down, every install stops working remotely (the local-network mode
  still works). People can self-host by changing the two URLs in `config.env`.
- **Abuse.** Anyone with the installer can create rooms on your relay. Throttling is on, but there are no accounts. If it
  becomes a problem, add an invite key to the relay or move users to their own deployment.
- **Trust.** The installer is unsigned, so Windows SmartScreen warns on first run. A code-signing certificate removes the
  warning.
