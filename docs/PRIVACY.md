# Armrest privacy policy

Last updated: [DATE OF PUBLICATION]

> Draft for the publisher to review before publishing. It describes how the app works as built; it is not legal
> advice. Replace every [BRACKETED] item.

Armrest lets your phones control the Windows PC connected to your TV. It is built to keep your data on your own
devices: there are no accounts, no advertising and no selling or sharing of personal data.

## Who we are

Armrest is published by [PUBLISHER NAME], [CITY, COUNTRY]. Contact: [SUPPORT EMAIL].

## What stays on your PC

- Your settings (`config.env`), including any API keys you add yourself.
- This TV's pairing identity, the list of paired phones and their roles (owner or guest).
- Saved pages, spoken-reply preferences and the last few commands (so "do that again" works).
- Logs (a few megabytes; the oldest lines are overwritten) and performance records (kept for 14 days).
- Your sign-ins to streaming sites, inside the app's own Microsoft Edge profile.

Voice commands are turned into text on your PC. Screenshots of the TV (used when the app has to find a button on the
screen) are processed on your PC. Nothing in this list leaves your PC unless a section below says so.

## What stays on your phone

The phone's pairing token for each TV, your favourite buttons, the TV names you choose and recent commands, in your
browser's storage. "Forget this phone" in the app's settings removes them.

## The cloud relay

So that a phone can reach your TV from anywhere, messages pass through a relay we run on Amazon Web Services (United
States, Ohio region). The relay:

- passes commands, the TV's status (for example the title playing) and voice audio between your phones and your PC.
  It does not store them;
- stores this TV's random room ID and scrambled (hashed) versions of its pairing secret and PC key. These are
  deleted 400 days after the PC last connected;
- stores each phone's random ID, the device name the phone reports (such as "iPhone") and when it was last seen.
  These are deleted 200 days after the phone was last seen;
- keeps the network connection records for at most 3 hours, and short-lived records used to stop a command from
  running twice for 15 minutes;
- uses your IP address to limit abuse (too many connections or new rooms). It is kept for at most one hour;
- writes technical logs without message contents, kept for 3 days.

**Delete my cloud data:** in the Armrest window on the PC, Help > Delete my cloud data removes this TV's room and its
phones from the relay at once. Uninstalling the app and not using it again lets everything expire as above.

## Services you choose to use

These are off unless you turn them on or add your own key. When they are on, the data described is sent to that
company and handled under its own privacy policy:

| Service | When | What is sent |
|---|---|---|
| Google Gemini | you add a Gemini key | the text of commands, and screenshots of the TV when the app has to find something on the screen |
| Groq | you add a Groq key | voice recordings, to turn them into text |
| ElevenLabs | you add an ElevenLabs key | the text of spoken replies |
| Sentry (crash reports) | you turn on crash reports | the error and where in the app it happened, the app version and Windows version. Names, addresses, IP, keys and what you watch are removed first |
| GitHub | direct download only, unless you turn it off | a daily check for a newer version (no personal data) |
| Microsoft Store | Store version | your purchase and trial status, handled by Microsoft |

The streaming sites you open (YouTube, Netflix and others) see what any web browser shows them. Your use of them is
covered by their terms and privacy policies.

## The phone app website

The phone app is a web page hosted by Cloudflare. Cloudflare processes your IP address and basic request details to
deliver it. We do not use cookies, analytics or trackers.

## Children

Armrest is not directed at children under 13 and does not knowingly collect their personal information.

## Your rights

You can see and delete everything stored on your PC and phone yourself (above), and remove your cloud data at any
time. For questions or requests, including requests under privacy laws such as PIPEDA, GDPR or CCPA, contact
[SUPPORT EMAIL].

## Changes

If this policy changes, the new version is published at this address with a new date.
