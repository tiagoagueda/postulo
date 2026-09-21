"""Every refusal this API makes, in the shape RFC 9457 gives them (#296).

Until now a refusal came back as django-ninja's default — ``{"detail": "…"}`` under
``application/json``. It was consistent, and the OpenAPI description described it, so a
generated client coped. What it was not is the *standardised* shape, which meant a client
written against Postulo needed Postulo-specific error handling: the one thing an OpenAPI
description is supposed to spare it.

**RFC 9457** (*Problem Details for HTTP APIs*, obsoleting RFC 7807) is that shape. A
refusal is ``application/problem+json`` carrying five members — ``type``, ``title``,
``status``, ``detail``, ``instance`` — and whatever extensions the type defines.

``detail`` was already a member and already a sentence, so for most refusals this is
additive: a client reading ``detail`` and printing it keeps working. The exception is a
validation failure, whose ``detail`` was the raw Pydantic error list; the RFC says
``detail`` is a string, so the list moved to the ``errors`` extension.

**``type`` is the thing a client branches on, and most refusals do not need one.** RFC 9457
§4.1 keeps ``about:blank`` for when the status code already says everything, and a 404 is
exactly that: a type URI beside it would be ceremony. The five below are the refusals where
a client does something *different* — wait and retry, ask for another scope, read the field
errors — and those are worth naming.

**``title`` is not translated, and ``detail`` is.** They are for different readers. ``detail``
says what is wrong with this request and a person debugging reads it, so it goes through
``gettext`` like every other message here. ``title`` is a label for the *type* — the RFC says
it SHOULD be the same for every occurrence — so a client that switches on it must not have
it move with ``Accept-Language``. A reader who wants that branch has ``type``, and ``title``
stays beside it.

**``instance`` names the address that refused, not the occurrence.** The RFC asks for a URI
identifying the specific occurrence, and doing that honestly needs a request id minted here
and written to the log, so that quoting it leads somewhere. Nothing in Postulo mints one.
An id nobody can look up is worse than the path, which at least says where; when request
ids arrive, this is the line that changes.
"""

from __future__ import annotations

import re
from typing import Any

from django.http import HttpRequest, HttpResponse
from ninja import Schema
from ninja.errors import HttpError, ValidationError
from pydantic import ConfigDict

#: What the RFC calls a problem document, and what this API answers a refusal with.
CONTENT_TYPE = "application/problem+json"

#: Where a problem type is written down. The types below are fragments of it, so a reader
#: who follows one lands on the paragraph that explains the refusal rather than on a 404.
_GUIDE = "https://source.tiagoagueda.com/postulo/postulo/wiki/The-capture-API"

#: The URI for "the status code says it all", straight from RFC 9457 §4.1.
BLANK = "about:blank"

#: slug → the title that goes with it. A type is added here when a client would *act*
#: differently on it; everything else is `about:blank` and its status code.
TITLES = {
    "validation-failed": "The request was not in the shape this call takes",
    "rate-limited": "The allowance for this window is spent",
    "insufficient-scope": "This token does not carry the scope this call needs",
    "idempotency-key-in-use": "Another request is still using this Idempotency-Key",
    "idempotency-key-reused": "This Idempotency-Key was used for a different request",
}

#: What `about:blank` says instead of a type's title: the status code's own phrase. Only
#: the codes this API actually refuses with — a phrase for a code nothing raises would be
#: a line nobody ever reads.
PHRASES = {
    400: "Bad Request",
    401: "Unauthorized",
    403: "Forbidden",
    404: "Not Found",
    405: "Method Not Allowed",
    409: "Conflict",
    422: "Unprocessable Content",
    429: "Too Many Requests",
}


def uri(kind: str | None) -> str:
    """The `type` for a slug, or `about:blank` where the status code is the whole story."""
    return f"{_GUIDE}#{kind}" if kind else BLANK


def title_of(kind: str | None, status: int) -> str:
    return TITLES[kind] if kind else PHRASES.get(status, "Error")


class Problem(Schema):
    """One refusal, as RFC 9457 describes it. Here so the OpenAPI description carries it.

    Declared rather than inferred because this is the one response shape a client must
    understand before it has made a single successful call, and a schema it can generate a
    type from is the difference between that and reading prose.
    """

    model_config = ConfigDict(extra="allow")

    type: str = BLANK
    title: str
    status: int
    detail: str = ""
    instance: str = ""


class Refused(HttpError):
    """An `HttpError` that knows which problem type it is, and what to hang off it.

    Most refusals need nothing but a status and a sentence, and go on raising `HttpError`.
    This is for the handful a client acts on: it carries the slug and whatever extension
    members that type defines, so the handler below does not have to recognise a refusal
    by reading its message.
    """

    def __init__(self, status_code: int, message: str, *, kind: str, **extensions: Any) -> None:
        super().__init__(status_code, message)
        self.kind = kind
        self.extensions = extensions


def document(
    request: HttpRequest, status: int, detail: str = "", *, kind: str | None = None, **extensions
) -> dict:
    """The body of a refusal: the five members, then whatever the type adds."""
    return {
        "type": uri(kind),
        "title": title_of(kind, status),
        "status": status,
        "detail": detail,
        # The address that refused. See the note at the top about what this is not.
        "instance": request.path,
        **extensions,
    }


def refuse(request, api, status: int, detail: str = "", *, kind=None, **extensions):
    """A refusal, rendered and typed.

    Built here rather than through `api.create_response`, whose content type is the one the
    API answers *successes* with. A refusal says `application/problem+json`, which is how a
    client tells a problem document from a body it asked for without looking inside it.
    """
    body = document(request, status, detail, kind=kind, **extensions)
    return HttpResponse(
        api.renderer.render(request, body, response_status=status),
        status=status,
        content_type=CONTENT_TYPE,
    )


def install(api) -> None:
    """Answer every refusal with a problem document, from three handlers rather than twenty.

    Registered over django-ninja's own defaults, which is why this is three handlers and
    not an edit to each of the twenty-odd places that raise: a refusal added tomorrow is in
    the right shape without anybody remembering, which is the only way a rule like this
    holds. django-ninja's `AuthenticationError` and `AuthorizationError` are `HttpError`s,
    so a 401 and a 403 come through the same one.

    `Exception` is deliberately left alone — an unhandled error is Django's to deal with,
    and dressing a crash up as a problem document would hide it. `throttle.TooOften` has
    its own handler beside the API, because it sets a header as well as a body.
    """
    from django.http import Http404

    @api.exception_handler(Http404)
    def _not_found(request, exc):
        return refuse(request, api, 404, str(exc) if str(exc) else "")

    @api.exception_handler(HttpError)
    def _http_error(request, exc: HttpError):
        extensions = getattr(exc, "extensions", {})
        response = refuse(
            request,
            api,
            exc.status_code,
            str(exc),
            kind=getattr(exc, "kind", None),
            **extensions,
        )
        if "retry_after" in extensions:
            # A type that carries a wait carries it in the header too, wherever it was
            # raised. The header is what an HTTP client obeys without being taught to.
            response["Retry-After"] = str(extensions["retry_after"])
        return response

    @api.exception_handler(ValidationError)
    def _validation(request, exc: ValidationError):
        """The one shape that changes: `detail` is a sentence and the list is `errors`.

        RFC 9457 says `detail` is a string, and it was the raw Pydantic list. A client that
        printed it is unaffected; one that walked it reads `errors`, which holds exactly
        what `detail` held.
        """
        return refuse(
            request,
            api,
            422,
            _validation_sentence(exc.errors),
            kind="validation-failed",
            errors=exc.errors,
        )


def _validation_sentence(errors: list[dict]) -> str:
    """One line naming what was refused, because `detail` has to be readable on its own.

    The fields rather than the reasons: a caller that wants the reasons has `errors`, and a
    sentence that tried to carry several of them would be a worse version of the list it
    sits beside. Untranslated, like the field names it quotes -- those are the wire's
    spelling, and a message that translated half of itself would be harder to act on.
    """
    fields = []
    for error in errors:
        location = [str(part) for part in error.get("loc", ()) if part not in ("body", "payload")]
        if location:
            fields.append(".".join(location))
    if not fields:
        return "The request was not in the shape this call takes."
    unique = list(dict.fromkeys(fields))
    named = ", ".join(unique[:5]) + (", …" if len(unique) > 5 else "")
    return f"Refused: {named}. See `errors` for what is wrong with each."


#: What every call can refuse with, whatever it is: no live token, and the allowance spent.
ALWAYS = (401, 429)


def _guards(api, router, operation) -> list:
    """What authenticates this call, following the inheritance django-ninja gives it.

    An operation that names no auth of its own is guarded by its router's, and a router
    that names none by the API's. Reading only `auth_callbacks` sees the first of those
    three and misses the scope on almost every call here, which is how a first attempt at
    this described sixty calls as unable to answer 403.
    """
    from ninja.constants import NOT_SET

    if operation.auth_callbacks:
        return list(operation.auth_callbacks)
    inherited = router.auth if router.auth not in (None, NOT_SET) else api.auth
    return list(inherited) if isinstance(inherited, list | tuple) else [inherited]


#: A router writes a path parameter with the converter it is parsed by -- `{int:pk}` --
#: and the description writes it without -- `{pk}`. Looking one up among the other finds
#: nothing at all, silently, for every call that names a record.
_CONVERTER = re.compile(r"\{(?:[a-z_]+:)?([^}]+)\}")


def _address(prefix: str, path: str) -> str:
    """The address as the OpenAPI description spells it.

    Not as *the wiki* spells it: `tests/test_wiki_surface.py` rewrites `{pk}` to `{id}`
    because that reads better in a table for a person. The schema keeps the parameter's
    real name, and this has to match the schema.
    """
    full = ("/api/v1" + (prefix.rstrip("/") + "/" + path.lstrip("/")).rstrip("/")).replace(
        "//", "/"
    )
    return _CONVERTER.sub(r"{\1}", full)


def _statuses(api, router, path: str, operation) -> list[int]:
    """Which refusals *this* call can actually make.

    Declared per call rather than a blanket five, because a description that says a
    collection may answer 404 is a description that lies: a client generated from it writes
    a branch that never runs, and the one that does run is the one nobody wrote.
    """
    from .auth import ScopedAuth

    statuses = set(ALWAYS)
    if any(isinstance(auth, ScopedAuth) for auth in _guards(api, router, operation)):
        statuses.add(403)  # a live token that does not carry this call's scope
    if "{" in path:
        statuses.add(404)  # an address naming a record, which may be nobody's or gone
    if operation.models:
        # Anything the request itself carries -- a body, a query parameter, an id in the
        # path -- is something that can be the wrong shape. A call that takes none of the
        # three cannot be refused for its shape, and `/me` is the one such call.
        statuses.add(422)
    return sorted(statuses)


def describe(api, schema: dict) -> dict:
    """Put the problem documents into the OpenAPI description, for every call at once.

    django-ninja describes what a call answers *with*; what it refuses with was nowhere, so
    a generated client had a type for an application and none for the refusal it is far
    likelier to meet first. Written here from the routers rather than as a `responses=` on
    each of the sixty-odd operations, for the reason `install` gives: the call added
    tomorrow is described without anybody remembering to describe it.
    """
    schema.setdefault("components", {}).setdefault("schemas", {})["Problem"] = Problem.json_schema()
    reference = {
        "content": {CONTENT_TYPE: {"schema": {"$ref": "#/components/schemas/Problem"}}},
    }
    for prefix, router in api._routers:
        for path, view in router.path_operations.items():
            full = _address(prefix, path)
            described = schema.get("paths", {}).get(full)
            if described is None:
                continue
            for operation in view.operations:
                for method in operation.methods:
                    entry = described.get(method.lower())
                    if entry is None:
                        continue
                    answers = entry.setdefault("responses", {})
                    for status in _statuses(api, router, full, operation):
                        # Keyed by the integer, which is how django-ninja keys the success
                        # it already described; the two spellings would be two entries for
                        # one status once this is serialised.
                        if status in answers or str(status) in answers:
                            continue  # the call described this refusal itself; leave it
                        answers[status] = {
                            "description": PHRASES.get(status, "Error"),
                            **reference,
                        }
    return schema
