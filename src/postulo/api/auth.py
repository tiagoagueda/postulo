"""Bearer tokens with scopes.

A route declares the scope it needs; a token either holds it or is told, in plain words,
that it does not. A missing or wrong token is a 401 and says nothing else — the existence
of a token is not something to confirm. A valid token without the scope is a 403 that
names the scope, because the person who made the token needs to know which box to tick.
"""

from __future__ import annotations

from ninja.errors import HttpError
from ninja.security import HttpBearer

from postulo.core import throttle

from .models import ApiToken


def _within_its_allowance(record: ApiToken) -> None:
    """Count this call against the token, or refuse it with a 429.

    Per token rather than per account, so a token handed to something that misbehaves can be
    revoked without touching the person's own allowance. Nothing at any layer bounded this
    before: `lookup` found a token and returned it, and that was the whole of the check (#112).
    """
    try:
        throttle.api(record)
    except throttle.TooOften as too_often:
        raise HttpError(429, str(too_often)) from None


def lookup(raw: str) -> ApiToken | None:
    if not raw:
        return None
    record = (
        ApiToken.objects.active()
        .select_related("owner")
        .filter(token_hash=ApiToken.hash_token(raw))
        .first()
    )
    if record is None or not record.owner.is_active:
        return None
    return record


class TokenAuth(HttpBearer):
    """Any active token. Used only where knowing the token is the point, such as /me."""

    def authenticate(self, request, token: str):
        record = lookup(token)
        if record is None:
            return None
        _within_its_allowance(record)
        record.record_use()
        # Nothing here logs the caller in: a token can never be mistaken for a session.
        return record


class ScopedAuth(HttpBearer):
    """An active token holding one particular scope."""

    def __init__(self, scope: str) -> None:
        super().__init__()
        self.scope = scope

    def authenticate(self, request, token: str):
        record = lookup(token)
        if record is None:
            return None
        if not record.has_scope(self.scope):
            raise HttpError(403, f"This token does not have the '{self.scope}' scope.")
        _within_its_allowance(record)
        record.record_use()
        return record


def scope(name: str) -> ScopedAuth:
    return ScopedAuth(name)


def actor_of(request) -> str:
    """How a write through the API signs the timeline."""
    token: ApiToken = request.auth
    return f"API token {token.name}"
