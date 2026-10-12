"""Encrypting what a connection must be able to present to another server.

A capture token is hashed, because Postulo only ever compares it. A connection's password
or bot token is the opposite case: Postulo has to hand it over, so it must be readable —
which means encryption at rest rather than hashing. Fernet (AES-128-CBC with an HMAC),
under a key derived from ``SECRET_KEY`` by default, or from ``POSTULO_FIELD_KEY`` when the
operator sets one so that rotating Django's key does not silently lock every connection.

The key never leaves the instance, and a backup archive is the one place it would be
tempting to put it: `fingerprint()` is what goes in instead, so that a restore can tell
an operator their secrets are unreadable without the archive carrying what would read
them.
"""

from __future__ import annotations

import base64
import hashlib
import hmac
import json
import secrets as secrets_module

from cryptography.fernet import Fernet, InvalidToken
from django.conf import settings
from django.utils.translation import gettext as _


class SecretsUnreadable(Exception):
    """The stored secrets were encrypted under a key this instance no longer has."""


def _material() -> str:
    return getattr(settings, "POSTULO_FIELD_KEY", "") or settings.SECRET_KEY


def _key() -> bytes:
    digest = hashlib.sha256(b"postulo-connection-secrets:" + _material().encode("utf-8")).digest()
    return base64.urlsafe_b64encode(digest)


def key_source() -> str:
    """Which setting the key is being derived from here, for a message to name."""
    return "POSTULO_FIELD_KEY" if getattr(settings, "POSTULO_FIELD_KEY", "") else "SECRET_KEY"


def fingerprint() -> str:
    """The unsalted mark of the key that archives before #811 carry. Read, no longer written.

    A plain SHA-256 of the material is a verifier for it that costs one hash a guess, so a copy
    of any backup was an offline test of a guessed key. :func:`mark` replaces it.
    """
    marked = b"postulo-field-key-fingerprint:" + _material().encode("utf-8")
    return hashlib.sha256(marked).hexdigest()


#: scrypt's cost for the mark: tens of milliseconds a guess, which is nothing to a restore
#: and a great deal to someone guessing a key from a copy of a backup (#811).
_MARK_COST = {"n": 2**14, "r": 8, "p": 1}


def _stretched(salt: bytes) -> str:
    return hashlib.scrypt(
        b"postulo-field-key-mark:" + _material().encode("utf-8"),
        salt=salt,
        dklen=32,
        **_MARK_COST,
    ).hex()


def mark() -> dict:
    """A salted, stretched mark of the key, for a backup's manifest to carry (#811).

    A fresh random salt per archive and scrypt over the material, so the manifest is not a
    cheap verifier for a guessed key and two archives of one instance do not show the same
    value. It still answers the one question a restore has to ask -- is this the same key the
    secrets were written under? -- through :func:`matches`.
    """
    salt = secrets_module.token_bytes(16)
    return {"kdf": "scrypt", "salt": salt.hex(), "mark": _stretched(salt)}


def matches(recorded) -> bool:
    """Whether a manifest's mark, new or old, is this instance's key."""
    if isinstance(recorded, dict):
        try:
            salt = bytes.fromhex(str(recorded.get("salt", "")))
        except ValueError:
            return False
        return bool(salt) and hmac.compare_digest(str(recorded.get("mark", "")), _stretched(salt))
    return hmac.compare_digest(str(recorded or ""), fingerprint())


def backup_mac(payload: bytes) -> str:
    """A MAC of ``payload`` under a key derived from the instance's key, for a backup (#772).

    Under its own domain prefix, like `fingerprint`, so what an archive carries is neither the
    encryption key nor a way to the connection secrets. It says one thing: whoever wrote the
    manifest and the member digests held this instance's key, which an attacker who can only
    alter a copy of the archive does not.
    """
    key = hashlib.sha256(b"postulo-backup-mac:" + _material().encode("utf-8")).digest()
    return hmac.new(key, payload, hashlib.sha256).hexdigest()


def encrypt(data: dict) -> str:
    """Serialise and encrypt; an empty dict becomes an empty string."""
    if not data:
        return ""
    return Fernet(_key()).encrypt(json.dumps(data).encode("utf-8")).decode("ascii")


def decrypt(token: str) -> dict:
    if not token:
        return {}
    try:
        raw = Fernet(_key()).decrypt(token.encode("ascii"))
    except InvalidToken as exc:
        raise SecretsUnreadable(
            _(
                "The connection's secrets cannot be read: they were encrypted under a different "
                "key. If SECRET_KEY was rotated, set POSTULO_FIELD_KEY to the old key, or enter "
                "the secrets again."
            )
        ) from exc
    return json.loads(raw.decode("utf-8"))


#: What marks a stored string as encrypted, so one written before encryption was added still
#: reads (#743). Neither a TOTP secret (base32) nor a recovery-code seed (hex) can start so.
TEXT_PREFIX = "enc1:"


def encrypt_text(text: str) -> str:
    """One string, encrypted under the instance's key and marked as such."""
    if not text or text.startswith(TEXT_PREFIX):
        return text
    return TEXT_PREFIX + Fernet(_key()).encrypt(text.encode("utf-8")).decode("ascii")


def decrypt_text(stored: str) -> str:
    """The string `encrypt_text` made; an unmarked value is legacy plaintext, returned as is."""
    if not stored or not stored.startswith(TEXT_PREFIX):
        return stored
    try:
        return Fernet(_key()).decrypt(stored[len(TEXT_PREFIX) :].encode("ascii")).decode("utf-8")
    except InvalidToken as exc:
        raise SecretsUnreadable(
            _(
                "An authenticator's secret cannot be read: it was encrypted under a different "
                "key. If SECRET_KEY was rotated, set POSTULO_FIELD_KEY to the old key."
            )
        ) from exc
