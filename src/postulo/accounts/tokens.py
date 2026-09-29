"""A single-use token, and the only thing about it that is ever stored: its fingerprint.

Two things are handed over as one-time URLs -- a recovery link an administrator issues (#103)
and an invitation -- and both keep the fingerprint rather than the token. The rows that hold
them reach these at save time, so they sit below the models; in `recovery`, which reads the
models, the models reaching back for them was a cycle (#248). `recovery` still hands them out.
"""

from __future__ import annotations

import hashlib
import secrets

#: 32 bytes, so guessing is not a strategy. Long enough that the URL is obviously a secret.
TOKEN_BYTES = 32


def new_token() -> str:
    return secrets.token_urlsafe(TOKEN_BYTES)


def fingerprint(token: str) -> str:
    """What is stored. A link only ever needs checking, so nothing keeps the token itself.

    Unsalted SHA-256 rather than a password hash, and that is the right choice here: the
    input is 32 random bytes, so there is no dictionary to run and nothing for a salt to
    frustrate. A slow hash would only slow the person using their own link.
    """
    return hashlib.sha256(token.encode()).hexdigest()
