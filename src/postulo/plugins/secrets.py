"""Encrypting what a connection must be able to present to another server.

A capture token is hashed, because Postulo only ever compares it. A connection's password
or bot token is the opposite case: Postulo has to hand it over, so it must be readable —
which means encryption at rest rather than hashing. Fernet (AES-128-CBC with an HMAC),
under a key derived from ``SECRET_KEY`` by default, or from ``POSTULO_FIELD_KEY`` when the
operator sets one so that rotating Django's key does not silently lock every connection.
"""

from __future__ import annotations

import base64
import hashlib
import json

from cryptography.fernet import Fernet, InvalidToken
from django.conf import settings


class SecretsUnreadable(Exception):
    """The stored secrets were encrypted under a key this instance no longer has."""


def _key() -> bytes:
    material = getattr(settings, "POSTULO_FIELD_KEY", "") or settings.SECRET_KEY
    digest = hashlib.sha256(b"postulo-connection-secrets:" + material.encode("utf-8")).digest()
    return base64.urlsafe_b64encode(digest)


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
            "The connection's secrets cannot be read: they were encrypted under a different "
            "key. If SECRET_KEY was rotated, set POSTULO_FIELD_KEY to the old key, or enter "
            "the secrets again."
        ) from exc
    return json.loads(raw.decode("utf-8"))
