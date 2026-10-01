"""Bearer tokens with scopes.

A route declares the scope it needs; a token either holds it or is told, in plain words,
that it does not. A missing or wrong token is a 401 and says nothing else — the existence
of a token is not something to confirm. A valid token without the scope is a 403 that
names the scope, because the person who made the token needs to know which box to tick.
"""

from __future__ import annotations

from django.http import JsonResponse
from django.utils.translation import gettext as _
from ninja.security import HttpBearer

from postulo.core import preferences, throttle

from . import problems
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
        # The same problem type, and the same `Retry-After`, as the handler in `api.py`
        # gives. Before #296 this path raised a bare 429 and the other carried the wait,
        # so whether a client was told *when* depended on which layer refused it.
        raise problems.Refused(
            429,
            str(too_often),
            kind="rate-limited",
            retry_after=too_often.retry_after,
        ) from None


def lookup(raw: str) -> ApiToken | None:
    if not raw:
        return None
    record = (
        ApiToken.objects.active()
        .select_related("owner", "owner__profile")
        .filter(token_hash=ApiToken.hash_token(raw))
        .first()
    )
    if record is None or not record.owner.is_active:
        return None
    return record


def _in_their_own_words(request, record: ApiToken) -> None:
    """From here on, this request is in the language and time zone of the token's owner.

    A call made with a token signs nobody in, so `UserPreferencesMiddleware` saw nobody and
    the request was running in whatever its ``Accept-Language`` named: English for a script
    that sends none, the browser's for an extension. Neither is a choice anybody made about
    Postulo. So a refusal's sentence, which the wiki says is in the account's language, was
    in the client's; and what a call writes down -- an interview's line on the timeline, the
    reminder that names its time -- was worded for the tool and timed for the instance.

    Called as soon as the token is known and before anything can refuse, so that the scope
    refusal and the spent allowance are said the same way as everything after them. An
    owner who has set no language, or one the instance no longer offers, leaves the
    request's own standing, which is the rule a signed-in page follows (#393).
    """
    preferences.apply(getattr(record.owner, "profile", None), request)


class TokenAuth(HttpBearer):
    """Any active token. Used only where knowing the token is the point, such as /me."""

    def authenticate(self, request, token: str):
        record = lookup(token)
        if record is None:
            return None
        _in_their_own_words(request, record)
        _within_its_allowance(record)
        record.record_use()
        # Nothing here logs the caller in: a token can never be mistaken for a session.
        return record


class ScopedAuth(HttpBearer):
    """An active token holding one particular scope, or any one of several.

    Several where a narrow scope and a wide one both cover a call (#270): recording an entry
    in a listing's history is what `listings:bind` is for, and `write` already records and
    changes listings, so either will do. The first named is the narrowest, and it is the one
    a refusal names -- the scope a client should ask for is the least that would work.
    """

    def __init__(self, scope: str, *others: str) -> None:
        super().__init__()
        self.scope = scope
        self.scopes = (scope, *others)

    def authenticate(self, request, token: str):
        record = lookup(token)
        if record is None:
            return None
        _in_their_own_words(request, record)
        if not any(record.has_scope(name) for name in self.scopes):
            # Typed, with the scope beside it: a client refused here has something to do
            # about it -- ask for a token carrying that scope -- and should not have to
            # parse the sentence to learn which one it is (#296). Where more than one would
            # do, `scopes` lists them all, narrowest first.
            if len(self.scopes) == 1:
                raise problems.Refused(
                    403,
                    _("This token does not have the %(scope)s scope.")
                    % {"scope": repr(self.scope)},
                    kind="insufficient-scope",
                    scope=self.scope,
                )
            # A list, not a phrase: an "or" between the names was English inside a
            # sentence that is now translated.
            named = ", ".join(repr(name) for name in self.scopes)
            raise problems.Refused(
                403,
                _("This token has none of the scopes this call takes: %(scopes)s.")
                % {"scopes": named},
                kind="insufficient-scope",
                scope=self.scope,
                scopes=list(self.scopes),
            )
        _within_its_allowance(record)
        record.record_use()
        return record


def scope(name: str, *others: str) -> ScopedAuth:
    """The guard for a call: a token holding ``name``, or any of ``others``."""
    return ScopedAuth(name, *others)


def for_readers_of_the_api(view):
    """Let the schema through to a live token or a signed-in person, and nobody else.

    ``docs_url`` has always been off, but django-ninja guards the schema view only when it
    is given a decorator to guard it with, so ``openapi.json`` answered anyone who asked —
    against the threat model's promise that the API answers 401 to everything without a
    live token, and enough on its own to tell a Postulo from anything else at that address
    (#230). A signed-in person is let through as well as a token: they can make themselves
    a token in two presses, and opening the schema in a browser is how somebody finds out
    what there is to build against.

    It is not an ``HttpBearer``, because this is a plain Django view rather than an
    operation, and it spends no allowance and records no use: asking what the API looks
    like is not using it.
    """

    def guarded(request, *args, **kwargs):
        if getattr(request.user, "is_authenticated", False):
            return view(request, *args, **kwargs)
        scheme, _space, raw = request.headers.get("Authorization", "").partition(" ")
        if scheme.lower() == "bearer" and lookup(raw.strip()) is not None:
            return view(request, *args, **kwargs)
        # Word for word what every other refusal without a token says, for the same reason:
        # confirming that a token exists is itself something not to confirm. Built by hand
        # rather than raised, because this is a plain Django view and no handler of the
        # API's runs over it -- which is exactly how it would drift out of shape, so
        # `tests/test_api.py` compares it against a refusal from a real call (#296).
        return JsonResponse(
            problems.document(request, 401, problems.no_token()),
            status=401,
            content_type=problems.CONTENT_TYPE,
        )

    return guarded


def actor_of(request) -> str:
    """How a write through the API signs the timeline."""
    token: ApiToken = request.auth
    return f"API token {token.name}"
