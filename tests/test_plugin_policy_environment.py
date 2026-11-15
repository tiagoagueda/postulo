"""The instance's decision about a plugin, from the environment (#1186).

`POSTULO_PLUGIN_<NAME>` is one of the four states the Plugins page sets. Set, it wins: the
decision is made as if a row said so, the page shows the plugin held with the variable named,
nothing posted changes it, and nothing is written to the database, so unsetting it gives the
stored decision back.
"""

from __future__ import annotations

import pytest
from django.contrib.auth import get_user_model
from django.urls import reverse

from postulo.core import checks, configuration
from postulo.plugins import policy
from postulo.plugins.models import PluginPolicy
from postulo.plugins.policy import environment_variables
from tests.recently import signed_in_recently

pytestmark = pytest.mark.django_db

User = get_user_model()
PLUGIN = "schema.org"
VARIABLE = "POSTULO_PLUGIN_SCHEMA_ORG"


@pytest.fixture
def admin(db):
    return User.objects.create_user(
        email="admin@example.org",
        password="a-fairly-long-password-42",
        username="admin-one",
        is_staff=True,
        is_superuser=True,
    )


def test_the_variable_is_the_plugins_name_in_upper_case():
    assert policy.variable_name(PLUGIN) == VARIABLE
    assert policy.variable_name("france-travail") == "POSTULO_PLUGIN_FRANCE_TRAVAIL"


@pytest.mark.parametrize(
    ("word", "on", "offered"),
    [("off", False, True), ("unavailable", False, False), ("on", True, True), ("OFF", False, True)],
)
def test_the_environment_decides_for_the_instance(user, monkeypatch, word, on, offered):
    monkeypatch.setenv(VARIABLE, word)

    decision = policy.decide(PLUGIN, user)

    assert (decision.on, decision.offered) == (on, offered)
    assert decision.decided_by == "administrator" or not offered
    assert not PluginPolicy.objects.filter(plugin=PLUGIN).exists(), "nothing is written"


def test_the_environment_wins_over_a_stored_decision_and_the_stored_one_returns(user, monkeypatch):
    PluginPolicy.objects.create(plugin=PLUGIN, person=None, state=PluginPolicy.State.FORCED_OFF)
    assert not policy.decide(PLUGIN, user).on
    policy.forget_decisions()

    monkeypatch.setenv(VARIABLE, "on")
    assert policy.decide(PLUGIN, user).on

    monkeypatch.delenv(VARIABLE)
    policy.forget_decisions()
    assert not policy.decide(PLUGIN, user).on


def test_a_word_that_is_not_a_state_decides_nothing_and_is_an_error(user, monkeypatch):
    monkeypatch.setenv(VARIABLE, "maybe")

    assert policy.decide(PLUGIN, user).decided_by == "shipped", "as if it were not set"
    errors = [m for m in checks.environment() if m.id == "postulo.E009"]
    assert any(VARIABLE in m.msg and "available" in m.hint for m in errors)


def test_the_variable_is_known_so_it_is_no_typo_and_is_listed(monkeypatch):
    monkeypatch.setenv(VARIABLE, "off")

    assert VARIABLE in environment_variables()
    assert not [m for m in checks.environment() if m.id == "postulo.W005" and VARIABLE in m.msg]
    assert configuration.unknown(variables=environment_variables()) == []


def test_the_plugins_page_holds_the_plugin_and_names_the_variable(client, admin, monkeypatch):
    monkeypatch.setenv(VARIABLE, "off")
    signed_in_recently(client, admin)

    html = client.get(reverse("server:plugins")).content.decode()

    assert f"Set by {VARIABLE} in the environment" in html


def test_a_post_cannot_change_what_the_environment_holds(client, admin, monkeypatch):
    monkeypatch.setenv(VARIABLE, "off")
    signed_in_recently(client, admin)

    client.post(reverse("server:plugin_policy"), {f"row:{PLUGIN}": "1", f"on:{PLUGIN}": "1"})

    assert not PluginPolicy.objects.filter(plugin=PLUGIN).exists()
    assert not policy.decide(PLUGIN, admin).on


def test_everyone_may_switch_it_is_refused_while_the_environment_decides(
    client, admin, monkeypatch
):
    monkeypatch.setenv(VARIABLE, "on")
    signed_in_recently(client, admin)

    client.post(reverse("server:plugin_access", kwargs={"name": PLUGIN}), {"action": "everyone"})

    assert not PluginPolicy.objects.filter(plugin=PLUGIN).exists()
