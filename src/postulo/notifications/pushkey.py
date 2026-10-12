"""The salt the browser notifications' signing key is derived with (#821).

Kept here, beside the inbox a notice waits in, because it reads two things a plugin holds no rows
of: the instance's settings row, where the salt lives, and whether anybody has a browser
subscription yet. The plugin asks for the bytes and derives its key from them.
"""

from __future__ import annotations

import os


def salt() -> bytes:
    """This instance's salt, made the first time it is needed; empty for the older derivation.

    An instance that already has browser subscriptions keeps the older, unsalted derivation:
    a subscription is bound to the key it was made against, and a new key would stop every one
    of them being delivered until each person allowed notifications again. So the salt is only
    made where nobody would be cut off.
    """
    from postulo.core.models import SiteSettings
    from postulo.plugins.models import Connection

    row = SiteSettings.get()
    if not row.web_push_salt:
        if Connection.objects.filter(plugin="browser").exists():
            return b""
        row.web_push_salt = os.urandom(32).hex()
        row.save(update_fields=["web_push_salt"])
    return bytes.fromhex(row.web_push_salt)
