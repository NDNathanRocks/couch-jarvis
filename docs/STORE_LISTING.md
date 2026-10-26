# Microsoft Store listing and certification notes (draft)

Draft for review. Items in `[BRACKETS]` need the user (price, links, names from Partner Center). Keep claims to what
the app really does today; the Store rejects listings that overpromise.

## Name and short text

- **Name:** Armrest
- **Tagline (max 100 chars):** Your PC, within arm's reach: turn any phone into the remote for the PC on your TV.
- **Category:** Utilities & tools (alternative: Entertainment)
- **Price:** free trial, then `[PRICE]`. The trial length is shown in the app (14 days on the website build).

## Description (Store "Description", plain text)

Armrest turns your phone into the remote for the Windows PC connected to your TV.

Scan the QR code on the TV once. After that, your phone reconnects by itself, even after it locks, and friends can scan
the same code to join. No accounts and no Wi-Fi settings.

What you can do from the couch:
- Say it or type it: "play lofi on YouTube", "pause", "volume 30", "turn on subtitles", "go fullscreen".
- Ask follow-up things: "do that again in a new tab". Armrest remembers your last few commands and asks when it needs
  to know more.
- Search the web and pick the result yourself. Questions like "what is the weather today" open a search in Edge.
- One-tap deck: play/pause, skip, back/forward 10 seconds, volume, subtitles, fullscreen.
- Apps row: Netflix, YouTube, Prime Video, Disney+, Spotify, Twitch, Plex, Crunchyroll.
- Saved pages: tap to reopen your favourite pages.
- A touchpad and keyboard on your phone for everything else.
- Several phones at once, several TVs on one phone, and guest phones that cannot change settings.
- Optional spoken replies from the TV after you speak a command.
- Blocks pop-up ads that other sites open. You can switch this off from the couch.

Works with Microsoft Edge on Windows 10 and 11. Your commands go to your own PC. The AI that understands commands can
run on your PC; see the privacy policy for what leaves your PC.

## Feature list (max 20 bullets)

1. Phone as remote, by voice, buttons, touchpad and keyboard
2. QR pairing, no accounts
3. Multiple phones and multiple TVs
4. Owner and guest phones
5. Play, pause, skip, volume, fullscreen, subtitles
6. Search and open results on the page you choose
7. Follow-up questions and short-term memory
8. Saved pages
9. Apps row for popular streaming sites
10. Pop-up ad blocking you can switch off
11. Spoken replies (optional)
12. Works over the internet or on your home network
13. English and French (when shipped; remove this line if not)

## Keywords (max 7)

remote control, tv remote, voice remote, phone remote, media remote, couch, htpc

## Age rating questionnaire (IARC) answers

| Question | Answer |
|---|---|
| Violence, sexual content, language, drugs, gambling | None, the app contains no such content of its own |
| User-generated content or social features | No (phones only control one PC; there is no chat or sharing) |
| Shares user location | No |
| Allows purchases | Yes (the app licence) |
| Unrestricted internet access | Yes: it opens web pages in Edge on the user's request, so say so (this can raise the rating) |

Expected rating: PEGI 3 / ESRB Everyone, possibly raised because of web browsing.

## Notes for certification (paste into "Notes for certification")

Armrest is a remote control. It runs on the Windows PC and is operated from a phone. To test it you need only this PC
and a phone, or the built-in "Try the remote on this PC" option `[TBD: see below]`.

1. Install and start Armrest. A QR code appears over the screen, and an "A" icon appears in the system tray.
2. Scan the QR code with a phone camera and open the link. The phone shows the remote. (If you have no phone, open the
   link shown in the Armrest window, under Phones, in a browser on any device.)
3. Say or type "new tab" on the phone. Microsoft Edge opens a new tab on the PC.

Why the app asks for these abilities:
- **It starts and controls Microsoft Edge** (through Edge's built-in debugging port, on this PC only, address
  127.0.0.1) so it can open pages, play videos and click buttons for the user. It uses its own Edge profile for this.
- **It moves the mouse and presses keys** on the PC when the user uses the phone's touchpad and keyboard. This is the
  purpose of a remote control. Only the paired owner phone can do this; guest phones cannot.
- **It uses the internet** to connect your phone to your PC through a small relay we run, and to look up commands with
  an AI service only if the user turns one on.
- **It runs in the background** with a tray icon so the phone keeps working while the TV shows other things.

No personal accounts are needed to test. No test credentials are required.
Privacy policy: `[PRIVACY URL]`. Support: `[SUPPORT EMAIL]`.

## Screenshots to capture (see `tests/e2e/test_store_screenshots.py`)

- TV with the QR code overlay.
- Armrest Control Center, Status page.
- Phone remote at three sizes: 360x640, 390x844, 430x932.
