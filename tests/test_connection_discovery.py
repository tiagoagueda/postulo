"""A connection's form offers what its plugin found on the service, several chosen (#332)."""

from typing import ClassVar

import pytest
from django.core.cache import cache
from django.urls import reverse

from postulo.plugins import discovery, registry
from postulo.plugins.base import DiscoveredOption, FieldSpec
from postulo.plugins.base import TestResult as Outcome  # not a test class, despite the name
from postulo.plugins.models import Connection

pytestmark = pytest.mark.django_db

MODES = (("read", "Read"), ("write", "Write"), ("both", "Read and write"))


class Collections:
    """A calendar server in miniature: two address books and a calendar, one read only."""

    name = "collections"
    version = "0.1"
    kind = "sync"
    label = "Collections"
    #: What the next `discover` finds, or an exception it raises.
    found: ClassVar[list | Exception] = []
    seen: ClassVar[list] = []

    def config_fields(self):
        return [
            FieldSpec("url", "Server", type="url"),
            FieldSpec("password", "Password", type="password", secret=True),
            FieldSpec("collections", "Collections", type="discovered", modes=MODES),
        ]

    def test(self, config):
        return Outcome(True, "fine")

    def discover(self, config):
        Collections.seen.append(dict(config))
        if isinstance(Collections.found, Exception):
            raise Collections.found
        return {"collections": Collections.found, "not-a-field": [DiscoveredOption("x", "X")]}

    def validate(self, config):
        if config.get("collections", {}).get("/cal/work/") == "write":
            return {"collections": ["Work cannot be written"]}
        return {}

    def sync(self, connection):  # pragma: no cover - never run here
        raise NotImplementedError


FOUND = [
    DiscoveredOption("/card/home/", "Home", group="Address books"),
    DiscoveredOption("/cal/work/", "Work", group="Calendars", modes=("read",), note="Read only"),
    DiscoveredOption("/card/club/", "Club", group="Address books"),
]


@pytest.fixture(autouse=True)
def plugin():
    cache.clear()
    Collections.found = list(FOUND)
    Collections.seen = []
    registry.register_builtin("sync", Collections)
    yield Collections
    registry.unregister_builtin("sync", Collections)


def a_connection(user, **config):
    connection = Connection(
        owner=user,
        kind="sync",
        plugin="collections",
        label="My server",
        config={"url": "https://dav.example.org/", **config},
    )
    connection.secrets = {"password": "s3cret"}
    connection.save()
    return connection


def row_field(connection, value):
    spec = Collections().config_fields()[2]
    row = next(row for row in discovery.rows(spec, connection.config) if row.value == value)
    return f"plugin_collections__{row.key}"


def look(client, connection, data=None):
    return client.post(reverse("connections:discover", args=[connection.pk]), data or {})


# ------------------------------------------------------------------- the field


def test_a_discovered_field_needs_modes_and_nothing_else_takes_them():
    with pytest.raises(ValueError, match="no modes"):
        FieldSpec("x", "X", type="discovered")
    with pytest.raises(ValueError, match="cannot be secret"):
        FieldSpec("x", "X", type="discovered", modes=MODES, secret=True)
    with pytest.raises(ValueError, match="only a discovered field"):
        FieldSpec("x", "X", modes=MODES)
    with pytest.raises(ValueError, match="no value"):
        FieldSpec("x", "X", type="discovered", modes=(("", "Nothing"),))


def test_a_new_connection_says_to_save_first_and_offers_no_look(client, user):
    client.force_login(user)
    html = client.get(reverse("connections:create", args=["sync", "collections"])).content
    html = html.decode()
    assert "Save this first, then look on the server" in html
    assert "Look on the server</button>" not in html
    assert Collections.seen == []


def test_opening_the_form_never_looks(client, user):
    connection = a_connection(user)
    client.force_login(user)
    html = client.get(reverse("connections:edit", args=[connection.pk])).content.decode()
    assert "Nothing has been looked for yet" in html
    assert reverse("connections:discover", args=[connection.pk]) in html
    assert Collections.seen == [], "a page view signs in nowhere"


# ------------------------------------------------------------------- the look


def test_looking_finds_options_and_saves_nothing_typed(client, user):
    connection = a_connection(user)
    client.force_login(user)
    response = look(
        client,
        connection,
        {"label": "Renamed", "enabled": "on", "plugin_url": "https://typed.example.org/"},
    )
    assert response.status_code == 200
    html = response.content.decode()

    # Asked with what was saved, password included, and nothing typed was kept.
    assert Collections.seen[0]["url"] == "https://dav.example.org/"
    assert Collections.seen[0]["password"] == "s3cret"
    connection.refresh_from_db()
    assert connection.label == "My server"
    assert connection.config["url"] == "https://dav.example.org/"
    # ...but it is drawn back as typed.
    assert 'value="Renamed"' in html and "https://typed.example.org/" in html

    # What was found is kept, in order, with when; a name that is not a field is not.
    kept = connection.config[discovery.KEY]
    assert kept["at"] and list(kept["fields"]) == ["collections"]
    assert [entry["value"] for entry in kept["fields"]["collections"]] == [
        "/card/home/",
        "/cal/work/",
        "/card/club/",
    ]
    assert "Found 3 options on the server" in html

    # A real table: a caption, header cells, one labelled select per option, grouped.
    assert "<caption" in html and '<th scope="col">' in html and 'scope="row"' in html
    assert html.index("Address books") < html.index("Club") < html.index("Calendars")
    work = response.context["form"][row_field(connection, "/cal/work/")]
    assert f'<label for="{work.id_for_label}">' in html
    assert [value for value, _label in work.field.choices] == ["", "read"], "read only there"
    home = response.context["form"][row_field(connection, "/card/home/")]
    assert [label for _value, label in home.field.choices] == [
        "Not used",
        "Read",
        "Write",
        "Read and write",
    ]


def test_several_are_chosen_with_a_mode_each_and_not_used_drops_one(client, user):
    connection = a_connection(user)
    client.force_login(user)
    look(client, connection)
    connection.refresh_from_db()
    edit = reverse("connections:edit", args=[connection.pk])
    data = {
        "label": "My server",
        "enabled": "on",
        "plugin_url": "https://dav.example.org/",
        "plugin_interval": "60",
        row_field(connection, "/card/home/"): "both",
        row_field(connection, "/cal/work/"): "read",
        row_field(connection, "/card/club/"): "",
    }
    assert client.post(edit, data).status_code == 302
    connection.refresh_from_db()
    assert connection.config["collections"] == {"/card/home/": "both", "/cal/work/": "read"}
    assert connection.secrets == {"password": "s3cret"}, "the stored password was kept"

    data[row_field(connection, "/card/home/")] = ""
    assert client.post(edit, data).status_code == 302
    connection.refresh_from_db()
    assert connection.config["collections"] == {"/cal/work/": "read"}

    # The page draws the choice back.
    response = client.get(edit)
    assert response.context["form"][row_field(connection, "/cal/work/")].value() == "read"


def test_validate_sees_the_choice_and_a_mode_the_option_does_not_allow_is_refused(client, user):
    connection = a_connection(user)
    client.force_login(user)
    look(client, connection)
    connection.refresh_from_db()
    edit = reverse("connections:edit", args=[connection.pk])
    data = {"label": "x", "plugin_url": "https://dav.example.org/", "plugin_interval": "60"}

    response = client.post(edit, {**data, row_field(connection, "/cal/work/"): "write"})
    assert response.status_code == 200
    assert row_field(connection, "/cal/work/") in response.context["form"].errors

    seen = []
    original = Collections.validate

    def validate(self, config):
        seen.append(config["collections"])
        return original(self, config)

    Collections.validate = validate
    try:
        client.post(edit, {**data, row_field(connection, "/card/home/"): "write"})
    finally:
        Collections.validate = original
    assert seen == [{"/card/home/": "write"}]


def test_an_option_that_vanished_keeps_its_row_until_it_is_let_go(client, user):
    connection = a_connection(user)
    client.force_login(user)
    look(client, connection)
    connection.refresh_from_db()
    connection.config = {**connection.config, "collections": {"/card/club/": "read"}}
    connection.save()

    Collections.found = FOUND[:2]
    html = look(client, connection).content.decode()
    connection.refresh_from_db()
    assert "No longer on the server" in html and "Club" in html
    assert connection.config["collections"] == {"/card/club/": "read"}, "never dropped silently"

    edit = reverse("connections:edit", args=[connection.pk])
    data = {"label": "x", "plugin_url": "https://dav.example.org/", "plugin_interval": "60"}
    client.post(edit, {**data, row_field(connection, "/card/club/"): ""})
    connection.refresh_from_db()
    assert connection.config["collections"] == {}
    assert "Club" not in client.get(edit).content.decode()


def test_a_look_that_fails_says_why_and_keeps_what_was_found(client, user):
    connection = a_connection(user)
    client.force_login(user)
    look(client, connection)
    connection.refresh_from_db()
    before = connection.config[discovery.KEY]

    Collections.found = RuntimeError("the server said 401")
    response = look(client, connection)
    assert response.status_code == 200
    assert "could not look on the server: RuntimeError: the server said 401" in (
        response.content.decode()
    )
    connection.refresh_from_db()
    assert connection.config[discovery.KEY] == before


def test_what_a_plugin_finds_is_read_rather_than_trusted(client, user):
    Collections.found = [
        DiscoveredOption("/a/", "A", modes=("read", "delete")),
        DiscoveredOption("/a/", "A again"),
        DiscoveredOption("", "No value"),
        "not an option",
    ]
    connection = a_connection(user)
    client.force_login(user)
    look(client, connection)
    connection.refresh_from_db()
    assert connection.config[discovery.KEY]["fields"]["collections"] == [
        {"value": "/a/", "label": "A", "group": "", "note": "", "modes": ["read"]}
    ]


def test_looking_shares_the_test_allowance(client, user, settings):
    settings.POSTULO_CONNECTION_TEST_RATE = "1/h"
    connection = a_connection(user)
    client.force_login(user)
    client.post(reverse("connections:test", args=[connection.pk]))
    response = look(client, connection)
    assert "That is a lot of tests" in response.content.decode()
    assert Collections.seen == []


def test_looking_is_a_post_and_somebody_elses_connection_is_not_found(client, user, other_user):
    connection = a_connection(user)
    client.force_login(user)
    assert client.get(reverse("connections:discover", args=[connection.pk])).status_code == 405
    client.force_login(other_user)
    assert look(client, connection).status_code == 404
    assert Collections.seen == []


@pytest.mark.django_db(transaction=True)
def test_looking_calls_the_server_outside_any_transaction(client, user, atomic_requests):
    from django.db import connection as database

    seen = []
    original = Collections.discover

    def discover(self, config):
        seen.append(database.in_atomic_block)
        return original(self, config)

    Collections.discover = discover
    try:
        connection = a_connection(user)
        client.force_login(user)
        look(client, connection)
    finally:
        Collections.discover = original
    assert seen == [False]


# ------------------------------------------------------------------ transports


def test_a_server_page_draws_a_discovered_field_as_a_sentence_and_keeps_nothing():
    from postulo.plugins.forms import form_field_for

    field = form_field_for(FieldSpec("x", "X", type="discovered", modes=MODES))
    assert field.disabled and not field.required
    assert "Not offered here" in str(field.help_text)


def test_the_surface_hands_over_the_option_record():
    from postulo.plugins import api, base

    assert api.DiscoveredOption is base.DiscoveredOption
    assert api.FieldSpec("x", "X", type="discovered", modes=MODES).modes == MODES
