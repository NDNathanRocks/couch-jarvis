"""The product name people see. Change it here (and in web-remote/src/brand.ts).

Internal names stay ``CouchJarvis`` on purpose: the exe, the data folders under %APPDATA% and
%LOCALAPPDATA%, the installer AppId, the ``CJ_`` settings and the wire ids. Renaming those would
strand existing installs, settings and paired phones.
"""

from __future__ import annotations

APP_NAME = "Armrest"
TAGLINE = "Your PC, within arm's reach"
# Where "Buy" goes. The Store build opens the Store page instead, once STORE_PRODUCT_ID is set
# (Partner Center > the app > Product identity > Store ID, e.g. 9NBLGGH4R315).
SITE_URL = "https://armrest-app.pages.dev"  # the phone app's site also serves these pages
BUY_URL = f"{SITE_URL}/get.html"
PRIVACY_URL = f"{SITE_URL}/privacy.html"
TERMS_URL = f"{SITE_URL}/terms.html"
SUPPORT_URL = f"{SITE_URL}/support.html"
GEMINI_KEY_URL = "https://aistudio.google.com/apikey"
STORE_PRODUCT_ID = ""

# The phone app is moving from Vercel (non-commercial plan) to Cloudflare Pages (5E). Until the new
# site is live and the old one forwards pairings to it, QR codes keep the address in config.env.
# Flip PWA_MOVE_LIVE once that is deployed: the QR then uses the new address WITHOUT rewriting
# anyone's config.env.
PWA_MOVE_LIVE = False
PWA_MOVES = {"https://couch-jarvis-remote.vercel.app": SITE_URL}


def qr_base_url(configured: str, *, live: bool | None = None) -> str:
    """The address to put in the pairing QR for a configured CJ_PWA_BASE_URL."""
    on = PWA_MOVE_LIVE if live is None else live
    return PWA_MOVES.get(configured.rstrip("/"), configured) if on else configured
