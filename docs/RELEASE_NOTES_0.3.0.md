# Couch Jarvis 0.3.0

A new look, sliders you can drag, buttons that are yours, and updates that install over the old version.
This also includes everything from the unreleased 0.2.1 fixes.

## Update from 0.2.0

Run the new `CouchJarvis-Setup.exe`. It finds your install and says *"Couch Jarvis 0.2.0 is installed. This will update it
to 0.3.0."* It closes the app, installs over it in the same folder and starts it again. Your settings, paired phones,
room keys and TV browser sign-ins are kept, and you are not asked for the Gemini key again. Future updates work the same way.

New here? Install normally; the installer offers an optional free Gemini key (fast) or a local AI download.

## What's new

**A redesigned phone remote**
- **Seek slider.** Drag the bar on the Now Playing card to jump anywhere in a video or song; the time follows your finger.
- **Volume slider.** Drag anywhere on the bar like the iPhone Control Centre. Tap the speaker to mute.
- **8 favourite buttons** in two rows under the player. Tap **Edit** (or long-press), tap a slot and choose from the library:
  playback, volume, fullscreen, subtitles, skip ad, screen off, browser buttons, **F1-F12**, Esc, Space, Enter and spoken
  commands. Each phone keeps its own list.
- **Trackpad** sits right under the favourites. The keyboard and function keys moved to the **Keys** tab.
- Glass look, a soft glow, springy buttons, a gliding tab bar and animations throughout (they switch off if your phone has
  reduced motion turned on).
- **Settings > Reset app cache** reloads the newest version of the phone page without un-pairing you.
- A small notice appears on the phone when a newer release exists.

**On the TV**
- The status pill is smaller and fades out after 10 seconds of quiet, so it doesn't sit on your movie. It returns when a
  phone connects or does something. `CJ_HUD_BADGE_HIDE_S=0` keeps it always on.
- Fullscreen now hides Edge's tab strip: the window itself goes fullscreen and the mouse pointer moves out of the way.
  Leaving fullscreen brings the tab strip back.
- Choosing a subtitle language closes the menu and keeps playing (or stays paused).

**Fixes from the first clean-PC test**
- *"My brain is offline"* now says what is wrong: not set up, taking too long, key rejected, or rate-limited. The installer
  offers a free Gemini key and a **Set up AI brain** shortcut that installs Ollama (its installer's signature is verified) and
  a model that fits your PC.
- Faster on PCs without an NVIDIA GPU (smaller speech and language models), and no command waits more than 60 seconds for the brain.
- Press keys by voice or from the phone: "press F11", "hit escape", "press control L". Alt+F4 is blocked.

**Safer**
- Things only you should trigger (reset the QR code, delete saved pages, switch the ad blocker, press keys) can no longer be
  started by text on a web page that confuses the AI.
- Every install makes its own room keys on first run. The installer never contains keys, and the build refuses to ship one.

## Good to know
- The phone page and the PC app update separately. If your PC still runs an older version, the phone shows plain volume
  buttons and a progress bar instead of the sliders until you update the PC.
- The installer isn't code-signed, so Windows SmartScreen may warn on first run ("More info" > "Run anyway").
- A daily check asks GitHub if a newer release exists (it only tells you; nothing is downloaded or run). Turn it off with
  `CJ_UPDATE_CHECK=false`.

## Files
| File | SHA-256 |
|---|---|
| `CouchJarvis-Setup.exe` (recommended) | `2846970514448fc32ff943e4cd27d3c8e5a8ac33fa5dd60f72500b72da517fdf` |
| `CouchJarvis-portable-0.3.0.zip` | `e80e7ad10a65b11795ba4168e80392c0cd65a53ab56cd1bdaae5fbea05d65e78` |

## Checked before release
Automated: 914 Python tests (including real-browser phone tests at four screen sizes and real-Edge tests), 195 web tests,
linting, type checks and dependency audits. An update from the published 0.2.0 to this build passed 19 of 19 checks
(same folder, running app closed and restarted, settings and room keys untouched, uninstall keeps your data).
Not yet checked on a physical phone or the TV itself: slider feel and vibration, the badge fade, and the fullscreen window fix.
