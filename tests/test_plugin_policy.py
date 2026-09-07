"""What an administrator has decided about a plugin, for everybody or for one person.

Four states, and the awkward one is *forced on*. Three of the four plugin kinds exist to
move a person's data somewhere else, and Postulo's first commitment is that one person's
data never reaches another's — so an administrator who can compel a store plugin onto an
account is one who could, in principle, route somebody's CV to a place of their choosing.

That power is granted deliberately (#95), and what makes it acceptable is that it cannot be
held quietly: the person is told what was decided and by whom. These check the machinery
under that promise — the order of precedence, that nothing is destroyed on the way, and that
a person cannot be given back a choice an administrator took.
"""

from __future__ import annotations

import pytest
from django.contrib.auth import get_user_model
from django.urls import reverse

from postulo.plugins import policy
from postulo.plugins.models import PluginPolicy

pytestmark = pytest.mark.django_db

User = get_user_model()
PASSWORD = "a-fairly-long-password-42"

#: A source that ships in the box, so every test has something real to decide about.
PLUGIN = "schema.org"


@pytest.fixture
def admin(db):
    return User.objects.create_user(
        email="admin@example.org", password=PASSWORD, username="admin-one", is_staff=True
    )


# ------------------------------------------------------------- the four states


def test_nothing_decided_means_available_and_theirs(user):
    decision = policy.decide(PLUGIN, user)
    assert decision.on and decision.offered and decision.theirs
    assert decision.decided_by == "default"


def test_unavailable_is_not_even_shown(user):
    PluginPolicy.objects.create(plugin=PLUGIN, person=user, state=PluginPolicy.State.UNAVAILABLE)
    decision = policy.decide(PLUGIN, user)
    assert not decision.on and not decision.offered and not decision.theirs


def test_forced_off_is_shown_and_locked(user):
    """The difference from unavailable, and the reason both exist.

    Unavailable means *not part of your Postulo*. Forced off means *you can see this exists
    and that somebody switched it off for you*. The second is the more honest of the two.
    """
    PluginPolicy.objects.create(plugin=PLUGIN, person=user, state=PluginPolicy.State.FORCED_OFF)
    decision = policy.decide(PLUGIN, user)
    assert not decision.on
    assert decision.offered, "the person must be able to see that this was decided for them"
    assert not decision.theirs
    assert decision.imposed


def test_forced_on_is_shown_and_locked(user):
    PluginPolicy.objects.create(plugin=PLUGIN, person=user, state=PluginPolicy.State.FORCED_ON)
    decision = policy.decide(PLUGIN, user)
    assert decision.on and decision.offered and not decision.theirs
    assert decision.imposed


# --------------------------------------------------------------- precedence


def test_a_persons_exception_beats_the_instance_default(user):
    PluginPolicy.objects.create(plugin=PLUGIN, person=None, state=PluginPolicy.State.FORCED_OFF)
    PluginPolicy.objects.create(plugin=PLUGIN, person=user, state=PluginPolicy.State.FORCED_ON)

    assert policy.decide(PLUGIN, user).on


def test_the_instance_default_applies_where_there_is_no_exception(user):
    PluginPolicy.objects.create(plugin=PLUGIN, person=None, state=PluginPolicy.State.FORCED_OFF)

    assert not policy.decide(PLUGIN, user).on


def test_switched_off_for_the_instance_beats_everything(user, tmp_path, settings):
    """A plugin an administrator has disabled is not loaded at all, so nothing below can
    turn it back on for anybody. This is not really a policy decision; it is the code not
    being there."""
    from postulo.plugins import installing

    settings.POSTULO_PLUGINS_DIR = tmp_path / "plugins"
    installing.write_record([installing.Installed(name=PLUGIN, version="1.0", disabled=True)])
    PluginPolicy.objects.create(plugin=PLUGIN, person=user, state=PluginPolicy.State.FORCED_ON)

    decision = policy.decide(PLUGIN, user)
    assert not decision.on
    assert decision.decided_by == "instance"


# ------------------------------------------------------- the person's own choice


def test_a_person_may_switch_an_available_plugin_off(user):
    assert policy.set_choice(user, PLUGIN, on=False)

    decision = policy.decide(PLUGIN, user)
    assert not decision.on
    assert decision.theirs, "still theirs to change back"
    assert decision.decided_by == "person"

    assert policy.set_choice(user, PLUGIN, on=True)
    assert policy.decide(PLUGIN, user).on


@pytest.mark.parametrize(
    "state",
    [PluginPolicy.State.FORCED_ON, PluginPolicy.State.FORCED_OFF, PluginPolicy.State.UNAVAILABLE],
)
def test_a_person_cannot_take_back_a_decision_that_was_made_for_them(user, state):
    PluginPolicy.objects.create(plugin=PLUGIN, person=user, state=state)
    before = policy.decide(PLUGIN, user).on

    assert not policy.set_choice(user, PLUGIN, on=not before)

    assert policy.decide(PLUGIN, user).on is before
    assert user.profile.plugins_off == []


def test_a_choice_survives_being_overruled_and_returns(user):
    """Nothing is destroyed on the way. An administrator's decision hides the person's
    choice; removing the decision gives it back exactly as it was."""
    policy.set_choice(user, PLUGIN, on=False)
    row = PluginPolicy.objects.create(
        plugin=PLUGIN, person=user, state=PluginPolicy.State.FORCED_ON
    )
    assert policy.decide(PLUGIN, user).on

    row.delete()

    assert not policy.decide(PLUGIN, user).on, "their own choice came back"
    assert user.profile.plugins_off == [PLUGIN]


# ----------------------------------------------------------- what it governs


def test_a_plugin_switched_off_does_not_act_for_that_person(user):
    PluginPolicy.objects.create(plugin=PLUGIN, person=user, state=PluginPolicy.State.FORCED_OFF)

    names = [item.name for item in policy.plugins_for(user, "source")]
    assert PLUGIN not in names
    assert names, "the other sources are untouched"


def test_a_transport_is_never_governed_by_this():
    """Mail delivery is instance infrastructure, not a capability a person holds, and
    *forced off* for a transport would be an account nobody can recover (#104)."""
    assert "transport" not in policy.GOVERNED_KINDS


def test_nothing_is_deleted_when_a_plugin_is_switched_off(user):
    """A policy that destroyed data on the way would be a delete button with a confusing
    name."""
    from postulo.plugins.models import Connection

    connection = Connection.objects.create(
        owner=user, kind="notifier", plugin="email", label="Mine", config={"to": "a@b.c"}
    )
    PluginPolicy.objects.create(plugin="email", person=user, state=PluginPolicy.State.FORCED_OFF)

    connection.refresh_from_db()
    assert connection.enabled
    assert connection.config == {"to": "a@b.c"}


# ------------------------------------------------------------- the interface


def test_an_administrator_sets_the_default_for_everybody(client, admin, user):
    client.force_login(admin)

    client.post(reverse("server:plugin_policy"), {f"state:{PLUGIN}": "off"})

    row = PluginPolicy.objects.get(plugin=PLUGIN, person=None)
    assert row.state == PluginPolicy.State.FORCED_OFF
    assert row.decided_by == admin, "who decided is recorded on the row"
    assert not policy.decide(PLUGIN, user).on


def test_an_administrator_sets_an_exception_for_one_account(client, admin, user):
    client.force_login(admin)

    client.post(reverse("server:person_plugins", args=[user.pk]), {f"state:{PLUGIN}": "on"})

    row = PluginPolicy.objects.get(plugin=PLUGIN, person=user)
    assert row.state == PluginPolicy.State.FORCED_ON
    assert row.decided_by == admin


def test_setting_it_back_to_available_removes_the_row(client, admin, user):
    """Absence means available, so the common answer is stored nowhere and a table does not
    grow with the product of two things that both grow."""
    PluginPolicy.objects.create(plugin=PLUGIN, person=user, state=PluginPolicy.State.FORCED_OFF)
    client.force_login(admin)

    client.post(reverse("server:person_plugins", args=[user.pk]), {f"state:{PLUGIN}": "available"})

    assert not PluginPolicy.objects.filter(plugin=PLUGIN, person=user).exists()
    assert policy.decide(PLUGIN, user).theirs


def test_the_page_says_who_decided_and_when(client, admin, user):
    PluginPolicy.objects.create(
        plugin=PLUGIN, person=user, state=PluginPolicy.State.FORCED_OFF, decided_by=admin
    )
    client.force_login(admin)

    html = client.get(reverse("server:person_plugins", args=[user.pk])).content.decode()

    assert f'data-policy="{PLUGIN}"' in html
    assert admin.username in html


@pytest.mark.parametrize("route", ["server:plugin_policy", "server:person_plugins"])
def test_only_an_administrator_decides_anything(client, user, route):
    client.force_login(user)
    url = reverse(route, args=[user.pk]) if route == "server:person_plugins" else reverse(route)

    response = client.post(url, {f"state:{PLUGIN}": "off"})

    assert response.status_code in {302, 403, 404}
    assert not PluginPolicy.objects.exists()


def test_a_change_is_written_to_the_log(client, admin, user, caplog):
    """`Server settings` records nothing anywhere — no action on any of those pages leaves
    a trace. Who and when live on the row itself, and the change is logged besides."""
    client.force_login(admin)

    client.post(reverse("server:person_plugins", args=[user.pk]), {f"state:{PLUGIN}": "off"})

    assert any("set to" in record.getMessage() for record in caplog.records)
