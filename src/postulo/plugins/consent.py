"""A connection that needs consent instead of a password, and the round trip that gets it.

Every connection in Postulo until now authenticated with something a person can type: a
`FieldSpec`, a form, a stored secret, a *Test* button. OAuth is not that. It is: send the
person to a provider, have them consent, receive a code at an address this instance publishes,
exchange it for tokens, keep the refresh token, and swap it for a fresh access token before
every send. Two of those are HTTP requests the plugin's side makes, one is a redirect the
browser makes, and none of them is a field.

**Tokens live where every other connection credential lives.** In the connection's own
encrypted secrets, under the Fernet key #111 protects — not in allauth's `SocialToken`.

That is the answer to the question the issue asked, and the reasoning is worth keeping. A
person who signed in with a provider already has a token from allauth, and reusing it would
save a great deal. It would also be wrong twice over. The sign-in grant carries the scopes
asked for at sign-in, and asking for a mail scope at sign-in *so it might be useful later* is
exactly the over-broad consent this project should not be teaching. And allauth holds one
token per account per application, so a second grant with different scopes has nowhere to sit
beside the first. Two grants, asked for when each is needed, in one store with one key and one
rule.

**The callback address is instance state and the operator has to register it.** OAuth needs a
redirect URI known to the provider in advance, which is this instance's own public address —
something a self-hosted application behind a proxy or a tunnel may not know about itself, and
which changes when somebody moves it. Getting it wrong ends a consent screen in an error
nobody can read, so Postulo *shows* the exact address to register rather than describing how
to build one.

**A refresh token outlives a password and cannot be changed by changing one.** Somebody who
takes it sends mail as that person until the grant is revoked at the provider — which is a
different thing from "change your password", and which the threat model now says.

**Refreshing happens while somebody's mail is being sent.** That is an outbound HTTPS call on
a path that had none, so it goes through `plugins/http.py` and gets the destination policy,
the timeout and the redirect limit every other outbound request gets, rather than a bare post.
"""

from __future__ import annotations

import time

from django.core import signing
from django.utils.translation import gettext as _

from .base import Consent

#: How long a consent round trip may take. Long enough to read a provider's screen and sign in
#: on the way; short enough that a state value found in a log is worthless.
STATE_MAX_AGE = 15 * 60

#: How long before an access token expires it is treated as expired. A token that dies in the
#: middle of a send is a failure nobody can explain, and a minute costs nothing.
REFRESH_MARGIN = 60

#: The keys the tokens are kept under in a connection's secrets. Names of places, not
#: credentials -- the values are the credentials, and they never appear in this file.
ACCESS_TOKEN = "oauth_access_token"  # noqa: S105
REFRESH_TOKEN = "oauth_refresh_token"  # noqa: S105
EXPIRES_AT = "oauth_expires_at"


class ConsentFailed(Exception):
    """The round trip did not finish, or the provider will not renew the grant."""


class ConsentWithdrawn(ConsentFailed):
    """The provider says the grant is gone.

    Kept apart because it is the useful failure and the one a mail server cannot report:
    nothing is misconfigured, nobody typed anything wrong, and the fix is to consent again.
    """


def wanted_by(plugin) -> Consent | None:
    """What this plugin needs consent for, or nothing if it authenticates by field."""
    asks = getattr(plugin, "needs_consent", None)
    if asks is None:
        return None
    try:
        found = asks()
    except Exception:  # pragma: no cover - a broken plugin must not break the page listing it
        return None
    return found if isinstance(found, Consent) else None


def callback_url(request) -> str:
    """The one address every provider is told to send people back to.

    One for the whole instance rather than one per plugin, because it is the thing an operator
    registers by hand and registering it once is the difference between this being usable and
    being a chore repeated per provider.
    """
    from django.urls import reverse

    return request.build_absolute_uri(reverse("connections:consent_callback"))


# ------------------------------------------------------------------ the round trip


def start(connection, plugin, request) -> str:
    """Where to send the person, with a state value only this instance could have made."""
    from urllib.parse import urlencode

    consent = wanted_by(plugin)
    if consent is None:
        raise ConsentFailed(str(_("That connection does not use consent.")))
    client_id = str((connection.config or {}).get("client_id") or "")
    if not client_id:
        raise ConsentFailed(
            str(_("Register this instance with the provider first, and save the client id."))
        )

    state = signing.dumps({"connection": connection.pk, "owner": connection.owner_id})
    query = {
        "response_type": "code",
        "client_id": client_id,
        "redirect_uri": callback_url(request),
        "scope": consent.scope,
        "state": state,
        **dict(consent.extra or {}),
    }
    joiner = "&" if "?" in consent.authorise_url else "?"
    return f"{consent.authorise_url}{joiner}{urlencode(query)}"


def finish(request, code: str, state: str):
    """Read the state, exchange the code, and keep the tokens. Returns the connection.

    The state is signed and short-lived, and it is checked against the person who is signed in
    now: a callback is a request somebody else's page can cause, and a connection belonging to
    a different account must not be reachable through one.
    """
    from .models import Connection

    try:
        payload = signing.loads(state, max_age=STATE_MAX_AGE)
    except signing.BadSignature as error:
        raise ConsentFailed(
            str(_("That reply did not come from a request Postulo made."))
        ) from error

    connection = Connection.objects.filter(pk=payload.get("connection")).first()
    if connection is None or connection.owner_id != payload.get("owner"):
        raise ConsentFailed(str(_("That connection no longer exists.")))
    if connection.owner_id != getattr(request.user, "pk", None):
        raise ConsentFailed(str(_("That connection belongs to somebody else.")))

    plugin = connection.plugin_instance
    consent = wanted_by(plugin) if plugin is not None else None
    if consent is None:
        raise ConsentFailed(str(_("That connection does not use consent.")))

    tokens = _ask_for_tokens(
        consent,
        connection,
        {
            "grant_type": "authorization_code",
            "code": code,
            "redirect_uri": callback_url(request),
        },
    )
    _keep(connection, tokens, keep_refresh=True)
    return connection


def access_token(connection) -> str:
    """A token good for the next minute at least, refreshing it if it is not.

    Called on the way to sending, which is why the refresh goes through the guarded client:
    an outbound request in the middle of delivering somebody's mail should meet the same
    destination policy, timeout and redirect limit as every other one.
    """
    secrets = connection.secrets
    expires_at = float(secrets.get(EXPIRES_AT) or 0)
    token = str(secrets.get(ACCESS_TOKEN) or "")
    if token and expires_at - REFRESH_MARGIN > time.time():
        return token

    plugin = connection.plugin_instance
    consent = wanted_by(plugin) if plugin is not None else None
    refresh = str(secrets.get(REFRESH_TOKEN) or "")
    if consent is None or not refresh:
        raise ConsentWithdrawn(str(_("Nobody has agreed to this yet. Connect it again.")))

    tokens = _ask_for_tokens(
        consent, connection, {"grant_type": "refresh_token", "refresh_token": refresh}
    )
    # A provider may or may not hand back a new refresh token; keeping the old one when it
    # does not is the difference between a connection that lasts and one that dies quietly.
    _keep(connection, tokens, keep_refresh=bool(tokens.get("refresh_token")))
    return str(tokens.get("access_token") or "")


def is_connected(connection) -> bool:
    """Whether somebody has agreed, whatever state the access token is in."""
    try:
        return bool(connection.secrets.get(REFRESH_TOKEN))
    except Exception:
        return False


def forget(connection) -> None:
    """Drop the tokens. Postulo cannot revoke a grant; only the provider can."""
    secrets = dict(connection.secrets)
    for key in (ACCESS_TOKEN, REFRESH_TOKEN, EXPIRES_AT):
        secrets.pop(key, None)
    connection.secrets = secrets
    connection.save(update_fields=["secrets_encrypted", "updated_at"])


# ------------------------------------------------------------------------ the wire


def _ask_for_tokens(consent: Consent, connection, form: dict) -> dict:
    """One request to the provider's token endpoint, through the guarded client."""
    import httpx

    from . import http

    config = connection.config or {}
    body = {
        **form,
        "client_id": str(config.get("client_id") or ""),
        "client_secret": str(connection.secrets.get("client_secret") or ""),
    }
    try:
        with http.client() as session:
            response = session.post(consent.token_url, data=body)
    except http.DestinationRefused as error:
        raise ConsentFailed(str(error)) from error
    except httpx.HTTPError as error:
        raise ConsentFailed(f"{type(error).__name__}: {error}") from error

    if response.status_code in (400, 401) and _says_the_grant_is_gone(response):
        raise ConsentWithdrawn(
            str(_("The provider will not renew this. Somebody has to agree to it again."))
        )
    if response.status_code >= 400:
        raise ConsentFailed(
            str(_("The provider answered %(status)s.")) % {"status": response.status_code}
        )
    try:
        return response.json()
    except ValueError as error:
        raise ConsentFailed(str(_("The provider's reply was not readable."))) from error


def _says_the_grant_is_gone(response) -> bool:
    """Whether a refusal is "consent withdrawn" rather than "something is misconfigured".

    Both arrive as a 400, and telling them apart is the whole value of the distinction: one
    is fixed by agreeing again and the other by editing a field.
    """
    try:
        error = str(response.json().get("error") or "")
    except ValueError:
        return False
    return error in {"invalid_grant", "unauthorized_client", "access_denied"}


def _keep(connection, tokens: dict, *, keep_refresh: bool) -> None:
    secrets = dict(connection.secrets)
    secrets[ACCESS_TOKEN] = str(tokens.get("access_token") or "")
    if keep_refresh and tokens.get("refresh_token"):
        secrets[REFRESH_TOKEN] = str(tokens.get("refresh_token"))
    try:
        lifetime = int(tokens.get("expires_in") or 0)
    except (TypeError, ValueError):
        lifetime = 0
    secrets[EXPIRES_AT] = time.time() + lifetime if lifetime else 0
    connection.secrets = secrets
    connection.save(update_fields=["secrets_encrypted", "updated_at"])
