"""Connections: a plugin's configuration and secrets, held per person, tested for real."""

import pytest
from django.urls import reverse

from postulo.plugins import http, registry, secrets
from postulo.plugins.base import FieldSpec
from postulo.plugins.base import TestResult as Outcome  # not a test class, despite the name
from postulo.plugins.models import Connection

pytestmark = pytest.mark.django_db


class EchoNotifier:
    """A connected plugin as a package would ship it: four names and two methods."""

    name = "echo"
    version = "0.1"
    kind = "notifier"
    label = "Echo"
    fail_with: str | None = None

    def config_fields(self):
        return [
            FieldSpec("url", "Where to post", type="url", help="Any https address."),
            FieldSpec("token", "Bot token", type="password", secret=True),
            FieldSpec("quiet", "Quiet hours", type="boolean", required=False, default=False),
            FieldSpec(
                "tone",
                "Tone",
                type="choice",
                choices=(("terse", "Terse"), ("chatty", "Chatty")),
                default="terse",
            ),
        ]

    def test(self, config):
        if EchoNotifier.fail_with:
            raise RuntimeError(EchoNotifier.fail_with)
        if not config.get("token"):
            return Outcome(False, "no token")
        return Outcome(True, f"posted to {config['url']} as {config['tone']}")


@pytest.fixture(autouse=True)
def echo_plugin():
    EchoNotifier.fail_with = None
    registry.register_builtin("notifier", EchoNotifier)
    yield EchoNotifier
    registry.unregister_builtin("notifier", EchoNotifier)


def a_connection(user, **overrides):
    connection = Connection(
        owner=user,
        kind="notifier",
        plugin="echo",
        label="My echo",
        config={"url": "https://echo.example.org/hook", "quiet": False, "tone": "terse"},
    )
    connection.secrets = {"token": "s3cret"}
    for key, value in overrides.items():
        setattr(connection, key, value)
    connection.save()
    return connection


# -------------------------------------------------------------------- secrets


def test_secrets_round_trip_and_are_not_stored_in_the_clear(settings):
    token = secrets.encrypt({"token": "s3cret", "n": 1})
    assert "s3cret" not in token
    assert secrets.decrypt(token) == {"token": "s3cret", "n": 1}
    assert secrets.encrypt({}) == "" and secrets.decrypt("") == {}


def test_a_dedicated_field_key_survives_a_secret_key_rotation(settings):
    settings.POSTULO_FIELD_KEY = "the-operators-own-key"
    token = secrets.encrypt({"token": "kept"})
    settings.SECRET_KEY = "rotated"
    assert secrets.decrypt(token) == {"token": "kept"}, "the field key, not SECRET_KEY, was used"

    settings.POSTULO_FIELD_KEY = ""
    with pytest.raises(secrets.SecretsUnreadable, match="different key"):
        secrets.decrypt(token)


def test_the_model_encrypts_on_the_way_in_and_merges_on_the_way_out(user):
    connection = a_connection(user)
    stored = Connection.objects.get(pk=connection.pk)
    assert "s3cret" not in stored.secrets_encrypted
    assert stored.secrets == {"token": "s3cret"}
    assert stored.full_config["token"] == "s3cret" and stored.full_config["url"].startswith("https")
    assert stored.plugin_instance.name == "echo" and stored.is_installed


# ------------------------------------------------------------------- registry


def test_the_registry_knows_kinds_and_finds_plugins_by_name():
    names = [plugin.name for plugin in registry.plugins("notifier")]
    assert "echo" in names and "email" in names, "the test plugin beside the built-in one"
    assert registry.find_plugin("notifier", "echo").label == "Echo"
    assert registry.find_plugin("notifier", "nope") is None
    assert "echo" in [plugin.name for plugin in registry.connected_plugins()]
    # The local store is built in, in shape only: it needs no connection and is not offered.
    assert [plugin.name for plugin in registry.plugins("store")] == ["local"]
    assert "local" not in [plugin.name for plugin in registry.connected_plugins()]
    with pytest.raises(ValueError, match="Unknown plugin kind"):
        registry.plugins("weather")
    # Sources are untouched by the generalisation.
    assert [source.name for source in registry.available_sources()] == [
        "schema.org",
        "page-metadata",
    ]


def test_field_specs_refuse_nonsense():
    with pytest.raises(ValueError, match="Unknown field type"):
        FieldSpec("x", "X", type="colour")
    with pytest.raises(ValueError, match="no choices"):
        FieldSpec("x", "X", type="choice")


# ------------------------------------------------------------------ the pages


def test_the_list_offers_installed_plugins_and_nothing_else(client, user):
    client.force_login(user)
    html = client.get(reverse("connections:list")).content.decode()
    assert "No connections yet" in html and reverse("connections:pick") in html
    html = client.get(reverse("connections:pick")).content.decode()
    assert "Echo" in html and reverse("connections:create", args=["notifier", "echo"]) in html
    assert client.get(reverse("connections:create", args=["notifier", "nope"])).status_code == 404
    assert client.get(reverse("connections:create", args=["weather", "echo"])).status_code == 404


def test_the_form_is_drawn_from_the_plugin_and_secrets_are_never_echoed(client, user):
    client.force_login(user)
    url = reverse("connections:create", args=["notifier", "echo"])
    html = client.get(url).content.decode()
    for name in ("plugin_url", "plugin_token", "plugin_quiet", "plugin_tone"):
        assert f'name="{name}"' in html, name
    assert 'type="password"' in html
    assert 'value="Echo"' in html, "the plugin's label is the default label"

    response = client.post(
        url,
        {
            "label": "Telegram",
            "enabled": "on",
            "plugin_url": "https://echo.example.org/hook",
            "plugin_token": "s3cret",
            "plugin_tone": "chatty",
        },
    )
    assert response.status_code == 302
    connection = Connection.objects.get(owner=user)
    assert connection.label == "Telegram" and connection.kind == "notifier"
    assert connection.config == {
        "url": "https://echo.example.org/hook",
        "quiet": False,
        "tone": "chatty",
        # A notifier connection carries a switch per event; none were ticked here.
        "event_reminder_due": False,
        "event_capture_received": False,
        "event_went_quiet": False,
    }
    assert connection.secrets == {"token": "s3cret"}

    html = client.get(reverse("connections:edit", args=[connection.pk])).content.decode()
    assert "s3cret" not in html, "a stored secret is never shown back"
    assert "leave blank to keep" in html

    # Editing with the secret left blank keeps it; filling it replaces it.
    client.post(
        reverse("connections:edit", args=[connection.pk]),
        {
            "label": "Telegram",
            "enabled": "on",
            "plugin_url": "https://echo.example.org/v2",
            "plugin_tone": "terse",
        },
    )
    connection.refresh_from_db()
    assert connection.secrets == {"token": "s3cret"} and connection.config["url"].endswith("v2")
    client.post(
        reverse("connections:edit", args=[connection.pk]),
        {
            "label": "Telegram",
            "plugin_url": "https://echo.example.org/v2",
            "plugin_token": "new",
            "plugin_tone": "terse",
        },
    )
    connection.refresh_from_db()
    assert connection.secrets == {"token": "new"} and connection.enabled is False


def test_the_form_insists_on_what_the_plugin_requires(client, user):
    client.force_login(user)
    response = client.post(
        reverse("connections:create", args=["notifier", "echo"]),
        {"label": "x", "plugin_url": "not a url", "plugin_tone": "terse"},
    )
    assert response.status_code == 200
    errors = response.context["form"].errors
    assert "plugin_url" in errors and "plugin_token" in errors


def test_testing_records_the_outcome_either_way(client, user):
    connection = a_connection(user)
    client.force_login(user)
    response = client.post(reverse("connections:test", args=[connection.pk]), follow=True)
    assert "posted to https://echo.example.org/hook as terse" in response.content.decode()
    connection.refresh_from_db()
    assert connection.last_ok_at is not None and connection.last_error == ""

    EchoNotifier.fail_with = "the wire is down"
    response = client.post(reverse("connections:test", args=[connection.pk]), follow=True)
    assert "RuntimeError: the wire is down" in response.content.decode()
    connection.refresh_from_db()
    assert connection.last_error == "RuntimeError: the wire is down"
    html = client.get(reverse("connections:list")).content.decode()
    assert "the wire is down" in html


def test_removing_a_connection_takes_its_secrets_with_it(client, user):
    connection = a_connection(user)
    client.force_login(user)
    html = client.get(reverse("connections:delete", args=[connection.pk])).content.decode()
    assert "secrets stored for it deleted" in html
    response = client.post(reverse("connections:delete", args=[connection.pk]))
    assert response.status_code == 302
    assert not Connection.objects.filter(pk=connection.pk).exists()


def test_connections_are_private_to_their_owner(client, user, other_user):
    connection = a_connection(user)
    client.force_login(other_user)
    for name in ("edit", "delete"):
        assert client.get(reverse(f"connections:{name}", args=[connection.pk])).status_code == 404
    assert client.post(reverse("connections:test", args=[connection.pk])).status_code == 404
    assert "My echo" not in client.get(reverse("connections:list")).content.decode()


class PickyNotifier(EchoNotifier):
    """A plugin that checks its configuration as a whole and describes it, masked."""

    name = "picky"
    label = "Picky"

    def config_fields(self):
        return [
            FieldSpec("urls", "Addresses", type="textarea", secret=True),
            FieldSpec("room", "Room", type="text", required=False),
        ]

    def validate(self, config):
        problems = {}
        bad = [line for line in config.get("urls", "").splitlines() if "://" not in line]
        if bad:
            problems["urls"] = [f"{len(bad)} line(s) are not addresses"]
        if config.get("room") == "forbidden":
            problems[""] = "not that room"
        return problems

    def summary(self, config):
        return " · ".join(
            line.split("://", 1)[0] + "://…" for line in config.get("urls", "").splitlines()
        )


def test_a_plugin_may_check_the_whole_configuration_at_the_form(client, user):
    registry.register_builtin("notifier", PickyNotifier)
    try:
        client.force_login(user)
        url = reverse("connections:create", args=["notifier", "picky"])
        html = client.get(url).content.decode()
        assert "<textarea" in html and 'name="plugin_urls"' in html, "a secret may be several lines"

        response = client.post(
            url, {"label": "x", "plugin_urls": "tgram://a/b\nnot an address", "plugin_room": "r"}
        )
        assert response.status_code == 200
        assert response.context["form"].errors["plugin_urls"] == ["1 line(s) are not addresses"]

        response = client.post(
            url, {"label": "x", "plugin_urls": "tgram://a/b", "plugin_room": "forbidden"}
        )
        assert response.context["form"].non_field_errors() == ["not that room"]

        response = client.post(
            url, {"label": "x", "plugin_urls": "tgram://a/b\nntfy://c", "plugin_room": "r"}
        )
        assert response.status_code == 302
        connection = Connection.objects.get(owner=user, plugin="picky")
        assert connection.secrets == {"urls": "tgram://a/b\nntfy://c"}

        # The list shows the plugin's masked description, never the secret itself.
        html = client.get(reverse("connections:list")).content.decode()
        assert "tgram://… · ntfy://…" in html and "tgram://a/b" not in html

        # Editing with the secret left blank validates against the stored value.
        edit = reverse("connections:edit", args=[connection.pk])
        html = client.get(edit).content.decode()
        assert "tgram://a/b" not in html and "leave blank to keep" in html
        response = client.post(edit, {"label": "y", "plugin_room": "forbidden"})
        assert response.context["form"].non_field_errors() == ["not that room"]
        response = client.post(edit, {"label": "y", "plugin_room": "z"})
        assert response.status_code == 302
        connection.refresh_from_db()
        assert connection.secrets == {"urls": "tgram://a/b\nntfy://c"} and connection.label == "y"
    finally:
        registry.unregister_builtin("notifier", PickyNotifier)


def test_a_plugin_that_cannot_check_or_describe_does_not_take_the_page_down(client, user):
    class Broken(PickyNotifier):
        name = "broken"

        def validate(self, config):
            raise RuntimeError("no idea")

        def summary(self, config):
            raise RuntimeError("no idea")

    registry.register_builtin("notifier", Broken)
    try:
        client.force_login(user)
        url = reverse("connections:create", args=["notifier", "broken"])
        response = client.post(url, {"label": "x", "plugin_urls": "tgram://a/b"})
        assert response.status_code == 200
        assert "RuntimeError: no idea" in response.context["form"].non_field_errors()[0]
        connection = Connection(owner=user, kind="notifier", plugin="broken", label="b")
        connection.secrets = {"urls": "tgram://a/b"}
        connection.save()
        html = client.get(reverse("connections:list")).content.decode()
        assert response.status_code == 200 and f'data-connection="{connection.pk}"' in html
    finally:
        registry.unregister_builtin("notifier", Broken)


def test_connections_have_a_settings_section(client, user):
    client.force_login(user)
    html = client.get(reverse("connections:list")).content.decode()
    assert 'aria-label="Settings sections"' in html and html.count('aria-current="page"') == 1


# -------------------------------------------------------- the outbound policy


def test_private_destinations_need_the_operators_say_so(settings):
    settings.POSTULO_CONNECTIONS_ALLOW_PRIVATE = False
    with pytest.raises(http.DestinationRefused, match="POSTULO_CONNECTIONS_ALLOW_PRIVATE"):
        http.check_destination("http://127.0.0.1:8000/api/")
    with pytest.raises(http.DestinationRefused):
        http.check_destination("http://192.168.1.20/paperless/")
    settings.POSTULO_CONNECTIONS_ALLOW_PRIVATE = True
    http.check_destination("http://192.168.1.20/paperless/")


def test_the_client_checks_every_request_it_makes(settings, monkeypatch):
    settings.POSTULO_CONNECTIONS_ALLOW_PRIVATE = False
    import httpx

    def handler(request):
        if request.url.host == "public.example.org":
            return httpx.Response(302, headers={"Location": "http://127.0.0.1/admin"})
        return httpx.Response(200, text="ok")

    from postulo.plugins import fetching

    monkeypatch.setattr(
        fetching,
        "_addresses_for",
        lambda host: (
            [fetching.ipaddress.ip_address("93.184.216.34")]
            if host == "public.example.org"
            else [fetching.ipaddress.ip_address("127.0.0.1")]
        ),
    )
    with http.client(transport=httpx.MockTransport(handler)) as session:
        assert session.headers["User-Agent"].startswith("Postulo")
        with pytest.raises(http.DestinationRefused):
            session.get("https://public.example.org/start")
