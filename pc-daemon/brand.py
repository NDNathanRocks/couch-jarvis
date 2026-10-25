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
BUY_URL = "https://armrest-app.pages.dev/buy"
STORE_PRODUCT_ID = ""
