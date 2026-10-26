# Handoff: Armrest v1.0.0 (Microsoft Store release)

Written 2026-10-25 for the next agent. Read this first, then `CLAUDE.md`, `AGENT_PROTOCOL.md` and `plan.md` sections 12
(binding release rules) and 13 (the v1.0.0 plan, with status in 13.9 and decisions in 13.10).

## What this is
"Couch Jarvis" was renamed **Armrest** (tagline "Your PC, within arm's reach"). The name is visible-only: internal ids
stay `CouchJarvis`, so existing installs and pairings keep working. Do not rename the exe, the
`%APPDATA%`/`%LOCALAPPDATA%\CouchJarvis` folders, the Inno `AppId {7B1E3C52-5A9D-4B5E-9C1A-2F4D8E6A0C31}`, `CJ_`
settings, the IndexedDB name `couch-jarvis`, or the voice-style id `jarvis`. The brand lives in `pc-daemon/brand.py` and
`web-remote/src/brand.ts`.

## State of the repo
- Branch `main`. Every change is committed locally; **nothing has been pushed, tagged or deployed** since v0.3.0.
  The latest commit is `98f4c8f`.
- Checks, all green when written:

  | Check | Command |
  |---|---|
  | Python tests (quick) | `.venv\Scripts\python.exe -m pytest -q` (996 pass, ~50 s; `pytest -m ""` is the full release gate) |
  | Lint, format | `ruff check pc-daemon tests scripts infra`, `ruff format --check ...` |
  | Types | `mypy --strict pc-daemon` |
  | Web | in `web-remote`: `npx vitest run` (203), `npx tsc --noEmit`, `npx eslint src` |
  | Generated files | `python scripts/export_contracts.py --check` (then `npm run gen:contracts` in web-remote), `python scripts/legal_pages.py --check` |

## Milestones (plan.md 13.3; details in 13.9)
| Milestone | State |
|---|---|
| 5A MSIX spike | go; `installer/build_msix.ps1`, `installer/msix/AppxManifest.xml` |
| 5B Brand | done |
| 5C Licence-safe defaults | done (phi4-mini CPU model, no edge-tts/GPL in builds, licence gate) |
| 5D Relay | done; staging load test run and the stack deleted (results in 13.2) |
| 5F Licensing | done (`licensing.py`, `scripts/license_keys.py`) |
| 5G PC Control Center | done (`pc-daemon/control/`) |
| 5I Reliability | done |
| 5H Phone app for buyers | partly done, see below |
| 5K Legal and Store kit | partly done, see below |
| 5E Phone hosting move | not started |
| 5J Packaging and release | not started |
| 5L Critic | not started |

### 5H: done and left
- **Done** (all in `web-remote`, plus PC enforcement):
  - Landing page for visitors with no QR (`components/QRScanner.tsx` `Landing`).
  - Apps row (`components/AppsRow.tsx`, shown for PC >= 1.0.0, `utils/version.ts` `APPS_SINCE`).
  - Owner and guest phones: `pc-daemon/roles.py`, `DevicePresence.role`, the service and agent gates (`agent/policy.py`
    `guest_refusal`), a Guest checkbox in the Control Center, and the phone hides input when it is a guest.
  - Several TVs on one phone (`utils/tvs.ts`, a header switcher, Settings "Your TVs").
  - The trial banner with Buy.
  - Legal links in the Settings sheet.
- **Left:**
  1. **English and French**: no `t()` yet. Every user-facing string goes through one dictionary module, a language
     picker, and a test that both dictionaries have the same keys.
  2. **Accessibility pass**: labels, focus order, AA contrast, 200% text. Add axe (e.g. `vitest-axe` on the rendered
     App, or axe-core in the e2e tests).
  3. **Guest passes that expire after 24 h**: optional, from the plan.
  4. **"Try the remote on this PC"**: loopback pairing for Store reviewers (listed in 5G).

### 5K: done and left
- **Done (drafts):**
  - `docs/PRIVACY.md`, `docs/TERMS.md`, `docs/GET.md`, `docs/SUPPORT.md`, rendered by `scripts/legal_pages.py` into
    `web-remote/public/{privacy,terms,get,support,licenses}.html`. Edit the Markdown, then rerun the script.
  - Proprietary `LICENSE`.
  - All of these contain `[BRACKETED]` placeholders for the user.
- **Left:**
  1. Store listing text, feature list, keywords, age-rating answers, and certification notes for reviewers (explain
     that the app drives Edge and the mouse, and how to test with a phone).
  2. A screenshot script (TV overlay, Control Center, the phone at 3 sizes).
  (`build.ps1` already regenerates the website pages before the web build.)

### 5E: the phone hosting move (needs the user)
- **Move:** the phone app goes from Vercel Hobby (non-commercial) to Cloudflare Pages, project **`armrest-app`**,
  address `https://armrest-app.pages.dev`. Decided: that address, not the user's free `nathandsouza.dpdns.org` domain
  (see 13.10 for why).
- **Build:**
  1. The fragment handoff: the old Vercel URL forwards the pairing to the new origin in the URL fragment, never sent
     to a server. Phones and home-screen icons must keep working (vitest + e2e).
  2. The daemon maps the old `CJ_PWA_BASE_URL` to the new one in the QR, without rewriting `config.env`.
- **Deploy:** needs the user to run `! npx wrangler login`, then ask before deploying. The phone app must be
  redeployed after UI commits (memory note). The live Vercel site still says "Couch Jarvis".

### 5J: packaging and release
- **Version:**
  1. Bump to **1.0.0** in `pc-daemon/version.py`; `web-remote/package.json` follows (`test_release_rules`).
  2. The Apps row and the `buy` action assume the PC reports >= 1.0.0.
- **Exe details:** add version info so Task Manager shows "Armrest" (PyInstaller `version=` file in
  `installer/couch_jarvis.spec`).
- **Store build:**
  - `installer\build.ps1 -Channel store`, then `installer\build_msix.ps1 -IdentityName <Partner Center> -Publisher
    <Partner Center>`.
  - The makeappx/signtool tools were extracted from the `Microsoft.Windows.SDK.BuildTools` NuGet package. If
    `-ToolsDir` is missing, download it again.
- **`installer\dist` warning:** it currently holds a *store-channel* build. Rebuild with `-Channel direct` before
  handing out `CouchJarvis-Setup.exe`.
- **Tests:**
  - An MSIX -> MSIX update keeps the data.
  - Inno 0.3.0 -> 1.0.0 with `scripts/test_installer_upgrade.ps1`. It now expects the `Armrest.lnk` shortcut and the
    old "Couch Jarvis" shortcuts gone.
  - Inno -> Store migration: the Store app detects an Inno install and offers to remove it, keeping the data.
  - A WACK run (Windows App Certification Kit), and a clean PC in Windows Sandbox.
- **Packaged Edge profile:** a packaged app keeps its Edge TV profile in the package folder
  (`build_info.edge_profile_dir`). Verified in the 5A spike.

### 5L: Critic
Run the full gate (`pytest -m ""`, web, lint, types, licence scan, secret scan), WACK, both upgrade tests, and a review
of every screen at phone and TV size.

## Things only the user can do (plan.md 13.10, "Further action")
- Trademark search for "Armrest" (USPTO class 9/42, CIPO), then reserve the name in Partner Center. The Store ID goes
  in `pc-daemon/brand.py` `STORE_PRODUCT_ID`.
- **Back up `C:\Users\Nathan\.armrest\licence-signing-key.pem`.** It is the Ed25519 key that signs licence keys, and
  it lives outside the repo. Never commit it; a test enforces this.
- Ask AWS to raise the Lambda concurrent executions limit from **10** to 1,000 (Service Quotas, free). The load test
  hit throttling at about 7 commands/s.
- Sentry: create an account (free, no card) and a Python project `armrest`. Turn on "Prevent Storing of IP
  Addresses". Put `CJ_CRASH_DSN=...` in the repo `.env`.
- `! npx wrangler login` for Cloudflare Pages.
- Fill in the `[BRACKETED]` items in the legal drafts, and pick a support email.

## Production deploys waiting for the user's OK
- **Relay:** production still runs the pre-5D code (no `room.delete`, no API client cache, no per-IP limits). The safe
  flow is in memory `project-deployment-facts.md`:
  1. `sam build`.
  2. `sam deploy --no-execute-changeset`.
  3. Review the change set.
  4. `aws cloudformation execute-change-set`.

  Deploy the relay **before** any PC sends `room.delete`. Note the account's 10-concurrency limit.
- **Phone app:** after 5E.

## Hard rules (from the user; violating them caused real problems before)
- **Ask before outward-facing actions:** any push, tag, production deploy, AWS change or other outward-facing action.
  Local commits are fine.
- **User data is sacred:** never delete or rewrite `%APPDATA%\CouchJarvis` or the Edge TV profile. Back it up before
  installer or package tests. The real `room.json` SHA-256 is `36F07BF8...DE42`; a backup is in this session's
  scratchpad.
- **Silent installer prompts:** never use a plain Inno `MsgBox` (use `SuppressibleMsgBox`), and never leave an
  installer or uninstaller dialog running.
- **No secrets in builds:** API keys and room secrets are never shipped (`Assert-NoSecrets`, `test_installer_secrets`).
- **Editing on this PC:**
  - Use the Edit/Write tools.
  - Bash heredocs collapse `\\` and break on apostrophes. Put larger scripts in a scratch file instead.
  - PowerShell 5.1 turns native stderr into errors when redirected, so run `build.ps1` without `*>&1`.
  - In Git Bash, set `MSYS_NO_PATHCONV=1` for `aws logs` paths.
- **Release rules** in plan.md section 12 are binding. Rule 5: a new phone app must not break older PCs. Hide new
  controls behind `daemon_version`, or behind a field only a new PC sends.
- **Keep the cloud near 0 CAD:** the user wants an "as free as possible" setup.
