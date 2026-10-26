# Armrest 0.4.0

Couch Jarvis is now **Armrest**. This release has a remote that follows the tab you're on, buttons you design
yourself, playback speed, a better touchpad, and a proper window on the PC for settings and paired phones. It also
includes everything from the unreleased 0.3.0, such as the seek and volume sliders and favourites.

**Free preview:** 0.x versions stay free with no trial and no licence key. A trial only arrives with 1.0, and it
counts from the day you first run 1.0.

## Update from 0.2.0

Run the new `CouchJarvis-Setup.exe`. It finds your install and updates it in place: it closes the app, installs over
it and starts it again. Your settings, room keys and TV browser sign-ins are kept. Phones pair again once, on the new address
(see *Good to know*). Future updates work the same way.

New here? Install normally, and a short welcome window walks you through the setup.

## What's new

**The remote does what you mean**
- **Commands act on the tab you're looking at.** Before, buttons and voice commands jumped back to the first tab when
  several were open. Now they follow whichever tab is on the TV, including tabs you switched to with the touchpad or a
  keyboard.
- **Seek bar** on the Now Playing card: drag to jump anywhere. It no longer resets halfway through a drag, and it moves
  the real player rather than a hidden preview video. It also works on Netflix.
- **Volume slider** like the iPhone's, with the exact percentage.
- **Playback speed by voice:** "change the speed of the player to 1.25 times speed", "play at 2x", "normal speed",
  "speed up", "slow down".
- **Exit fullscreen** as a button and by voice ("exit fullscreen", "unfullscreen"). It also brings the browser's tabs
  back.
- **Duplicate tab** as a button and by voice ("duplicate this tab").

**Buttons that are yours**
- **8 favourite buttons** under the player. Tap **Edit** (or long-press) and choose from the library.
- **Make your own keyboard-shortcut buttons:** Favourites → Edit → a slot → **New shortcut**. Pick Ctrl, Alt or Shift
  and a key, then give it a name, e.g. "Reopen tab" = Ctrl+Shift+T. Shortcuts the PC blocks for safety (like Alt+F4)
  are explained before you save.
- **Apps row:** one tap opens Netflix, YouTube, Prime Video, Disney+, YouTube Music, Spotify, Twitch, Plex or
  Crunchyroll.

**A better touchpad**
- The pointer now accelerates: a slow finger aims precisely, and a quick flick crosses the whole TV.
- Choose **Slow / Normal / Fast** above the touchpad (each phone remembers its own).
- A small vibration on each click.

**Share it safely**
- **Owner and guest phones:** tick *Guest* for a phone in the Armrest window. Guests get playback, volume and voice;
  only owners get the touchpad, the keys and the settings.
- **Several TVs on one phone:** scan each TV's code, then switch from the header and rename them in Settings.

**On the PC**
- **The Armrest window** (Start menu, or the tray icon) shows the status of the relay, the TV browser, the AI helper
  and voice, with fix-it buttons. It also lists paired phones (mark as guest, remove) and holds the everyday
  settings, the help pages and *Delete my cloud data*.
- A **welcome window** on a new install covers the TV screen, signing in to your streaming sites, the AI helper and
  pairing your first phone.
- Tray **Quit** now really quits.
- More everyday phrases work without the AI helper: "skip intro", "next episode", "start over", "volume 30",
  "open Netflix" and more.
- On PCs without an NVIDIA GPU, the local AI model is now `phi4-mini`.
- "Ad blocking" is now called **pop-up protection**, which describes what it does: it closes tabs a site opens by
  itself and undoes redirects. "Skip ad" still works.
- Spoken-reply voices are renamed (style **Butler**; voices **Sterling, Aurora, Chill, Aria**). The old names still
  work when spoken.

**Under the hood**
- Settings and data files are versioned and written atomically. A crash can't leave a half-written file.
- Activity logs are kept for 14 days at most.
- Optional crash reports are off by default, and the welcome window asks. They contain no names, web addresses or
  anything you said.

## Good to know
- **The phone app has a new address: https://armrest-app.pages.dev.** The old `couch-jarvis-remote.vercel.app`
  address is gone. After updating, the TV shows a QR code for the new address (your settings file doesn't need
  editing). **Scan it once more on each phone** to pair again; a home-screen icon made from the old address must be
  added again too.
- **Update the PC to see the new buttons.** The phone app updates by itself; the PC needs this installer. A phone
  connected to an older PC hides the controls that PC can't do.
- **Spoken replies:** the free Microsoft online voices are no longer included. Replies use ElevenLabs if you add a
  key, otherwise the voices built into Windows.
- **Delete my cloud data** needs a relay update that isn't live yet. Until then, the button says an update is needed.
- The installer isn't code-signed, so Windows SmartScreen may warn on first run ("More info" → "Run anyway").
- A daily check asks GitHub whether a newer release exists. It only tells you; nothing is downloaded. Turn it off with
  `CJ_UPDATE_CHECK=false`.

## Files
| File | SHA-256 |
|---|---|
| `CouchJarvis-Setup.exe` (recommended) | `50c94189247d8311d4181c187fa984aad93266241ed682f2793cb1cc5f762535` |
| `CouchJarvis-portable-0.4.0.zip` | `62ee9a74ad4d7591ba648a66e4d0e4f2057ca81d0f9e9bae366aea7db93501e2` |

## Checked before release
Automated: 1,029 Python tests, 217 phone-app tests, 26 tests against a real Edge browser (including speed, seeking,
exit fullscreen, duplicate tab and following the tab you switch to), linting, type checks, the licence scan and the
secret scan. An update from the published 0.2.0 to this build passed 20 of 20 checks: same folder, the running app
closed and restarted, settings and room keys untouched, the old shortcuts replaced, and uninstall keeps your data.
Not yet checked on a physical phone and TV: the new buttons, the touchpad acceleration and the seek bar on a real
streaming site.
