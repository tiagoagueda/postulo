"""Refusing a secret key that is not one, before the first request rather than in a log.

`prod.py` already refused to start with **no** key. It said nothing about a short one, so
`POSTULO_SECRET_KEY=changeme` started perfectly well. Django notices — `security.W009` is
exactly this check — but the container runs `check --deploy --fail-level ERROR` and W009 is a
*warning*, so it printed one line into a start-up log nobody reads while the instance served
traffic (#111).

**Why this key matters more here than in most Django applications.** It does not only sign
sessions and password-reset links. `plugins/secrets.py` derives the Fernet key protecting
every stored connection credential from it — somebody's Telegram bot token, their Paperless
password, their Nextcloud login — through a single unsalted SHA-256. A guessable
`SECRET_KEY` is therefore a guessable encryption key for other people's passwords to other
people's services, and guessing it is cheap.

**Which is also why the fix cannot simply be "generate a new one".** Changing `SECRET_KEY`
on an instance that has been running signs everybody out *and* makes every stored connection
secret unreadable, because the key that encrypted them is gone. The refusal below has to say
so, or it sends an operator to fix one problem by causing a worse one. `POSTULO_FIELD_KEY`
is the way out: set it to the **old** key first, and the credentials stay readable while the
signing key changes underneath them.

`POSTULO_ALLOW_WEAK_SECRET_KEY` exists for the operator this catches at an inconvenient
moment: an instance that has been running happily on a short key, refusing to start after an
upgrade, at the hour when nobody wants to plan a key rotation. It buys time. It does not
apply to a placeholder — somebody who typed `changeme` has not chosen anything, and there is
nothing there to buy time for.
"""

from __future__ import annotations

from django.core.exceptions import ImproperlyConfigured

#: Django's own threshold, and for the same reason: `security.W009` calls a key shorter than
#: this insufficient, and this is that check moved from a warning to a refusal.
MIN_LENGTH = 50

#: Django's other half of W009. `aaaaaaaa…` is long and worthless.
MIN_UNIQUE_CHARACTERS = 5

#: What people actually type. Compared case-insensitively, and `django-insecure-` is the
#: prefix `startproject` writes into a generated settings file — a key that has been publicly
#: documented as not-a-secret since the day it was made.
PLACEHOLDERS = frozenset(
    {
        "changeme",
        "change-me",
        "changethis",
        "secret",
        "secretkey",
        "secret-key",
        "password",
        "postulo",
        "test",
        "testing",
        "development",
        "dev",
        "insecure",
        "notsecret",
        "not-a-secret",
        "xxx",
        "todo",
    }
)

#: The prefix `django-admin startproject` writes into a generated settings file. Named here
#: so a test can hold it against Django's own constant rather than against a copy of it.
PLACEHOLDER_PREFIX = ("django-insecure-",)

GENERATE = "python -c 'import secrets; print(secrets.token_urlsafe(64))'"

#: Said wherever a key is refused, because the obvious fix is the one that loses data.
ROTATION_WARNING = (
    "If this instance has been running, do not simply replace the key: it also encrypts "
    "every stored connection credential, so changing it makes them unreadable and signs "
    "everybody out. Set POSTULO_FIELD_KEY to the current key first, which keeps those "
    "credentials readable while POSTULO_SECRET_KEY changes underneath them."
)


def looks_like_a_placeholder(key: str) -> bool:
    settled = key.strip().lower()
    return settled in PLACEHOLDERS or settled.startswith(PLACEHOLDER_PREFIX)


def weakness_of(key: str) -> str:
    """Why this key is not one, or an empty string if it is fine.

    Length and variety only. A key that passes both may still be a poor one — this cannot
    tell a dice roll from a keyboard mash — and that is the limit of what a check can do.
    """
    if len(key) < MIN_LENGTH:
        return f"it is {len(key)} characters and at least {MIN_LENGTH} are needed"
    if len(set(key)) < MIN_UNIQUE_CHARACTERS:
        return (
            f"it uses only {len(set(key))} distinct characters, "
            f"and at least {MIN_UNIQUE_CHARACTERS} are needed"
        )
    return ""


def refuse_a_weak_key(key: str, *, name: str, allow_weak: bool = False) -> None:
    """Stop the instance rather than let it serve traffic on a guessable key.

    Applied to ``POSTULO_FIELD_KEY`` as well when one is set, because that is *the* key
    protecting stored credentials whenever it exists, and a weak one there is the same hole
    with the same blast radius.
    """
    if not key:
        raise ImproperlyConfigured(f"{name} must be set. Generate one with: {GENERATE}")

    if looks_like_a_placeholder(key):
        raise ImproperlyConfigured(
            f"{name} is a placeholder, not a secret. Generate one with: {GENERATE}\n"
            f"{ROTATION_WARNING}"
        )

    weakness = weakness_of(key)
    if weakness and not allow_weak:
        raise ImproperlyConfigured(
            f"{name} is too weak: {weakness}. Generate one with: {GENERATE}\n"
            f"{ROTATION_WARNING}\n"
            "To start anyway while you plan that, set POSTULO_ALLOW_WEAK_SECRET_KEY=true. "
            "It is a way to buy an afternoon, not an answer."
        )
