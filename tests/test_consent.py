"""A connection that needs consent, not a password (#150).

Every connection until now authenticated with something a person could type: a `FieldSpec`, a
form, a stored secret, a *Test* button. OAuth is none of those — it is a round trip through
somebody else's website — and the contract had no way to say so.

Four decisions are what this file is really testing.

**Tokens live in the connection's own encrypted secrets**, not in allauth's `SocialToken`.
Reusing the sign-in grant would save a great deal and be wrong twice over: it carries the
scopes asked for at sign-in, and asking for a mail scope at sign-in *so it might be useful
later* is the over-broad consent this project should not teach.

**The callback address is shown, not described.** An operator registers it by hand, and
building one from a template is where it goes wrong.

**"Consent was withdrawn" is a distinct failure.** It is the useful one and the one a mail
server cannot report: nothing is misconfigured, and the fix is to agree again.

**The refresh happens through the guarded client**, because it is an outbound request in the
middle of sending somebody's mail.
"""

from __future__ import annotations

import contextlib
import time
from unittest import mock

import pytest
from django.core import signing
from django.urls import reverse

from postulo.plugins import consent as flow
from postulo.plugins import registry
from postulo.plugins.base import Consent, FieldSpec
from postulo.plugins.base import TestResult as PluginTestResult
from postulo.plugins.models import Connection

pytestmark = pytest.mark.django_db


class Consenting:
    """A notifier that authenticates by agreement rather than by password."""

    name = "consenting"
    version = "1.0.0"
    kind = "notifier"
    label = "Consenting"
    description = "Needs somebody to agree."

    def config_fields(self) -> list[FieldSpec]:
        return [
            FieldSpec("client_id", "Client id"),
            FieldSpec("client_secret", "Client secret", type="password", secret=True),
        ]

    def needs_consent(self) -> Consent:
        return Consent(
            authorise_url="https://provider.example/authorise",
            token_url="https://provider.example/token",
            scopes=("https://provider.example/auth/send",),
            provider="Provider",
            extra={"access_type": "offline"},
        )

    def test(self, config: dict) -> PluginTestResult:
        return PluginTestResult(True, "fine")

    def send(self, message, config: dict) -> bool:
        return True


class Typing:
    """The ordinary kind, for contrast."""

    name = "typing"
    version = "1.0.0"
    kind = "notifier"
    label = "Typing"
    description = "Takes a password."

    def config_fields(self) -> list[FieldSpec]:
        return [FieldSpec("token", "Token", type="password", secret=True)]

    def test(self, config: dict) -> PluginTestResult:
        return PluginTestResult(True, "fine")

    def send(self, message, config: dict) -> bool:
        return True


@contextlib.contextmanager
def installed(plugin_class):
    registry.register_builtin("notifier", plugin_class)
    registry.plugins("notifier", refresh=True)
    try:
        yield
    finally:
        registry.unregister_builtin("notifier", plugin_class)
        registry.plugins("notifier", refresh=True)


@pytest.fixture
def connection(user):
    row = Connection.objects.create(
        owner=user,
        kind="notifier",
        plugin="consenting",
        label="Mine",
        config={"client_id": "an-id"},
    )
    row.secrets = {"client_secret": "a-secret"}
    row.save()
    return row


def a_reply(payload: dict, status: int = 200):
    """One httpx response from the provider's token endpoint."""
    response = mock.Mock()
    response.status_code = status
    response.json.return_value = payload
    session = mock.MagicMock()
    session.__enter__.return_value.post.return_value = response
    return session


# ------------------------------------------------------------ what a plugin declares


def test_a_plugin_can_say_it_needs_consent():
    wanted = flow.wanted_by(Consenting())

    assert wanted is not None
    assert wanted.scope == "https://provider.example/auth/send"
    assert wanted.provider == "Provider"


def test_most_plugins_do_not():
    assert flow.wanted_by(Typing()) is None


def test_a_plugin_that_raises_while_asked_is_treated_as_not_needing_it():
    """A broken plugin must not break the page that lists it."""

    class Broken:
        def needs_consent(self):
            raise RuntimeError("no")

    assert flow.wanted_by(Broken()) is None


def test_something_that_is_not_a_consent_is_ignored():
    class Confused:
        def needs_consent(self):
            return {"authorise_url": "https://provider.example"}

    assert flow.wanted_by(Confused()) is None


# --------------------------------------------------------------- where somebody is sent


def test_the_provider_is_asked_for_what_the_plugin_declared(rf, connection, user):
    request = rf.get("/")
    request.user = user

    with installed(Consenting):
        where = flow.start(connection, Consenting(), request)

    assert where.startswith("https://provider.example/authorise?")
    assert "client_id=an-id" in where
    assert "access_type=offline" in where, "the provider's own requirement, carried through"
    assert "response_type=code" in where


def test_the_state_is_signed_so_only_postulo_could_have_made_it(rf, connection, user):
    request = rf.get("/")
    request.user = user

    with installed(Consenting):
        where = flow.start(connection, Consenting(), request)

    state = where.split("state=")[1].split("&")[0]
    from urllib.parse import unquote

    payload = signing.loads(unquote(state), max_age=flow.STATE_MAX_AGE)
    assert payload == {"connection": connection.pk, "owner": user.pk}


def test_without_a_client_id_it_says_what_to_do(rf, connection, user):
    connection.config = {}
    connection.save()
    request = rf.get("/")
    request.user = user

    with installed(Consenting), pytest.raises(flow.ConsentFailed) as raised:
        flow.start(connection, Consenting(), request)

    assert "client id" in str(raised.value)


def test_a_connection_that_does_not_use_consent_refuses(rf, connection, user):
    request = rf.get("/")
    request.user = user

    with pytest.raises(flow.ConsentFailed):
        flow.start(connection, Typing(), request)


# -------------------------------------------------------------------- coming back


def test_a_forged_state_is_refused(rf, user):
    request = rf.get("/")
    request.user = user

    with pytest.raises(flow.ConsentFailed) as raised:
        flow.finish(request, "a-code", "not-a-signed-value")

    assert "did not come from a request Postulo made" in str(raised.value)


def test_somebody_else_s_connection_cannot_be_reached_through_a_callback(
    rf, connection, django_user_model
):
    """A callback is a request another page can cause."""
    stranger = django_user_model.objects.create_user(
        email="stranger@example.org", username="stranger", password="a-long-enough-password-42"
    )
    state = signing.dumps({"connection": connection.pk, "owner": connection.owner_id})
    request = rf.get("/")
    request.user = stranger

    with installed(Consenting), pytest.raises(flow.ConsentFailed) as raised:
        flow.finish(request, "a-code", state)

    assert "belongs to somebody else" in str(raised.value)


def test_a_stale_state_is_refused(rf, connection, user, monkeypatch):
    state = signing.dumps({"connection": connection.pk, "owner": user.pk})
    request = rf.get("/")
    request.user = user
    monkeypatch.setattr(flow, "STATE_MAX_AGE", -1)

    with installed(Consenting), pytest.raises(flow.ConsentFailed):
        flow.finish(request, "a-code", state)


def test_finishing_keeps_the_refresh_token(rf, connection, user):
    state = signing.dumps({"connection": connection.pk, "owner": user.pk})
    request = rf.get("/")
    request.user = user
    reply = a_reply({"access_token": "an-access", "refresh_token": "a-refresh", "expires_in": 3600})

    with installed(Consenting), mock.patch("postulo.plugins.http.client", return_value=reply):
        flow.finish(request, "a-code", state)

    connection.refresh_from_db()
    assert connection.secrets[flow.REFRESH_TOKEN] == "a-refresh"
    assert flow.is_connected(connection)


def test_the_tokens_are_not_stored_in_plain_text(rf, connection, user):
    state = signing.dumps({"connection": connection.pk, "owner": user.pk})
    request = rf.get("/")
    request.user = user
    reply = a_reply({"access_token": "an-access", "refresh_token": "a-refresh", "expires_in": 3600})

    with installed(Consenting), mock.patch("postulo.plugins.http.client", return_value=reply):
        flow.finish(request, "a-code", state)

    connection.refresh_from_db()
    assert "a-refresh" not in connection.secrets_encrypted


# ------------------------------------------------------------------- refreshing


def test_a_fresh_token_is_used_as_it_is(connection):
    connection.secrets = {
        flow.ACCESS_TOKEN: "still-good",
        flow.REFRESH_TOKEN: "a-refresh",
        flow.EXPIRES_AT: time.time() + 3600,
    }
    connection.save()

    with installed(Consenting), mock.patch("postulo.plugins.http.client") as client:
        assert flow.access_token(connection) == "still-good"

    assert not client.called, "nothing was asked of the provider"


def test_an_expiring_token_is_renewed(connection):
    connection.secrets = {
        flow.ACCESS_TOKEN: "about-to-die",
        flow.REFRESH_TOKEN: "a-refresh",
        flow.EXPIRES_AT: time.time() + 5,
    }
    connection.save()
    reply = a_reply({"access_token": "a-new-one", "expires_in": 3600})

    with installed(Consenting), mock.patch("postulo.plugins.http.client", return_value=reply):
        assert flow.access_token(connection) == "a-new-one"


def test_a_provider_that_returns_no_new_refresh_token_keeps_the_old_one(connection):
    """The difference between a connection that lasts and one that dies quietly."""
    connection.secrets = {flow.REFRESH_TOKEN: "a-refresh", flow.EXPIRES_AT: 0}
    connection.save()
    reply = a_reply({"access_token": "a-new-one", "expires_in": 3600})

    with installed(Consenting), mock.patch("postulo.plugins.http.client", return_value=reply):
        flow.access_token(connection)

    connection.refresh_from_db()
    assert connection.secrets[flow.REFRESH_TOKEN] == "a-refresh"


def test_the_refresh_goes_through_the_guarded_client(connection):
    """An outbound request in the middle of sending mail gets the same policy as every other."""
    connection.secrets = {flow.REFRESH_TOKEN: "a-refresh", flow.EXPIRES_AT: 0}
    connection.save()
    reply = a_reply({"access_token": "a-new-one", "expires_in": 3600})

    with (
        installed(Consenting),
        mock.patch("postulo.plugins.http.client", return_value=reply) as client,
    ):
        flow.access_token(connection)

    assert client.called, "not a bare post"


def test_nobody_having_agreed_is_its_own_failure(connection):
    with installed(Consenting), pytest.raises(flow.ConsentWithdrawn) as raised:
        flow.access_token(connection)

    assert "agreed" in str(raised.value)


def test_a_withdrawn_grant_is_told_apart_from_a_misconfiguration(connection):
    """Both arrive as a 400, and telling them apart is the whole value of the distinction."""
    connection.secrets = {flow.REFRESH_TOKEN: "a-refresh", flow.EXPIRES_AT: 0}
    connection.save()
    reply = a_reply({"error": "invalid_grant"}, status=400)

    with installed(Consenting), mock.patch("postulo.plugins.http.client", return_value=reply):
        with pytest.raises(flow.ConsentWithdrawn) as raised:
            flow.access_token(connection)

    assert "agree to it again" in str(raised.value)


def test_another_kind_of_refusal_is_not_called_withdrawal(connection):
    connection.secrets = {flow.REFRESH_TOKEN: "a-refresh", flow.EXPIRES_AT: 0}
    connection.save()
    reply = a_reply({"error": "invalid_request"}, status=400)

    with installed(Consenting), mock.patch("postulo.plugins.http.client", return_value=reply):
        with pytest.raises(flow.ConsentFailed) as raised:
            flow.access_token(connection)

    assert not isinstance(raised.value, flow.ConsentWithdrawn)


def test_forgetting_drops_the_tokens_and_says_nothing_about_the_provider(connection):
    connection.secrets = {
        flow.ACCESS_TOKEN: "a",
        flow.REFRESH_TOKEN: "b",
        flow.EXPIRES_AT: 1,
        "client_secret": "keep",
    }
    connection.save()

    flow.forget(connection)

    connection.refresh_from_db()
    assert not flow.is_connected(connection)
    assert connection.secrets["client_secret"] == "keep", "only the grant is forgotten"


# -------------------------------------------------------------------- the pages


def test_the_callback_address_is_shown_exactly(client, user, connection):
    client.force_login(user)

    with installed(Consenting):
        html = client.get(reverse("connections:edit", args=[connection.pk])).content.decode()

    assert "data-consent-callback" in html
    assert "/settings/connections/consent/" in html


def test_the_scopes_are_shown_so_nobody_agrees_blind(client, user, connection):
    client.force_login(user)

    with installed(Consenting):
        html = client.get(reverse("connections:edit", args=[connection.pk])).content.decode()

    assert "https://provider.example/auth/send" in html


def test_an_ordinary_connection_shows_none_of_it(client, user):
    row = Connection.objects.create(owner=user, kind="notifier", plugin="typing", label="Plain")
    client.force_login(user)

    with installed(Typing):
        html = client.get(reverse("connections:edit", args=[row.pk])).content.decode()

    assert "data-consent" not in html


def test_starting_is_a_post_and_not_a_link(client, user, connection):
    """It ends in a stored credential, and a GET that does that is one another page can cause."""
    client.force_login(user)

    with installed(Consenting):
        response = client.get(reverse("connections:consent", args=[connection.pk]))

    assert response.status_code == 405


def test_pressing_the_button_sends_somebody_to_the_provider(client, user, connection):
    client.force_login(user)

    with installed(Consenting):
        response = client.post(reverse("connections:consent", args=[connection.pk]))

    assert response.status_code == 302
    assert response["Location"].startswith("https://provider.example/authorise?")


def test_saying_no_at_the_provider_is_not_an_error(client, user):
    client.force_login(user)

    response = client.get(
        reverse("connections:consent_callback"), {"error": "access_denied"}, follow=True
    )

    assert "Nobody agreed to anything" in response.content.decode()


def test_the_callback_needs_somebody_signed_in(client):
    response = client.get(reverse("connections:consent_callback"), {"code": "x", "state": "y"})

    assert response.status_code == 302
    assert "login" in response["Location"]


def test_testing_a_consenting_connection_reports_a_withdrawn_grant(client, user, connection):
    """The useful failure, and the one a mail server cannot report."""
    connection.secrets = {flow.REFRESH_TOKEN: "a-refresh", flow.EXPIRES_AT: 0}
    connection.save()
    reply = a_reply({"error": "invalid_grant"}, status=400)
    client.force_login(user)

    with installed(Consenting), mock.patch("postulo.plugins.http.client", return_value=reply):
        response = client.post(reverse("connections:test", args=[connection.pk]), follow=True)

    assert "agree to it again" in response.content.decode()
