"""What asking the plugin policy costs, and that what it remembers is never out of date (#1112).

A decision is memoised for the request (#231), but each first answer read its own rows: one
query on the policy table and one on the access rules, per plugin, parents and needs included.
A company's page spent 21 of its queries there, and the number followed how many plugins and
modules were installed rather than anything the person had.

Now the rows that can reach one person are read once a request, in two queries, and every
decision is made from them. That is a memo of what decides who may use what, so the second
half of this file is the half that matters: whatever changes an answer has to throw the rows
away, and the next question has to see the change.
"""

from __future__ import annotations

import pytest
from django.contrib.auth.models import Group
from django.db import connection
from django.test.utils import CaptureQueriesContext
from django.urls import reverse

from postulo.jobs.models import Company
from postulo.plugins import policy, registry
from postulo.plugins.models import PluginAccess, PluginPolicy

pytestmark = pytest.mark.django_db

#: Two modules that ship inside Postulo, so there is one to ask about first and another to
#: change afterwards.
FIRST = "websites"
OTHER = "schema.org"


def policy_queries(captured) -> dict[str, int]:
    """How many of these queries read each of the two tables a decision is made from."""
    return {
        table: sum(1 for query in captured if f'FROM "{table}"' in query["sql"])
        for table in ("plugins_pluginpolicy", "plugins_pluginaccess")
    }


def everything_governed() -> list[str]:
    return [item.name for kind in policy.GOVERNED_KINDS for item in registry.plugins(kind)]


# ----------------------------------------------------------------------------- the cost


def test_deciding_every_plugin_reads_the_two_tables_once_each(user, third_party):
    """Each plugin's first answer was two queries of its own, so the cost followed the number
    of plugins installed. Every governed plugin there is, for one person, is two (#1112)."""
    names = everything_governed()
    assert len(names) > 10, "enough of them for a query each to show"

    with CaptureQueriesContext(connection) as captured:
        for name in names:
            policy.decide(name, user)

    assert policy_queries(captured) == {"plugins_pluginpolicy": 1, "plugins_pluginaccess": 1}


def test_a_row_naming_who_decided_costs_no_query_of_its_own(user, other_user):
    """A plugin an administrator switched off for everybody names who decided, and reading
    that name was a third query each time the plugin was asked about (#1112)."""
    PluginPolicy.objects.create(
        plugin=FIRST, state=PluginPolicy.State.FORCED_OFF, decided_by=other_user
    )

    with CaptureQueriesContext(connection) as captured:
        decision = policy.decide(FIRST, user)
        assert decision.who == other_user

    assert len(captured) == 2, [query["sql"] for query in captured]


@pytest.mark.parametrize("page", ["jobs:company_detail", "core:home"])
def test_a_page_asks_the_policy_tables_at_most_once_each(client, user, page):
    """A company's page read the policy table 11 times and the access rules 10 times, of 72
    queries in all; the dashboard is held to the same two (#1112)."""
    company = Company.objects.create(owner=user, name="Aperture")
    client.force_login(user)
    url = reverse(page, args=[company.pk] if page == "jobs:company_detail" else [])

    assert client.get(url).status_code == 200
    with CaptureQueriesContext(connection) as captured:
        assert client.get(url).status_code == 200

    found = policy_queries(captured)
    assert found["plugins_pluginpolicy"] <= 1, found
    assert found["plugins_pluginaccess"] <= 1, found


def test_somebody_signed_out_is_asked_about_with_one_query(third_party):
    """Nobody is nobody's exception and is in no group: only the instance's rows are read."""
    with CaptureQueriesContext(connection) as captured:
        for name in everything_governed():
            policy.decide(name, None)

    assert policy_queries(captured) == {"plugins_pluginpolicy": 1, "plugins_pluginaccess": 0}


# ------------------------------------------------------------- what is remembered is true


def test_the_rows_of_one_person_are_not_the_rows_of_the_next(user, other_user):
    """What was read for one account must not answer for another."""
    PluginPolicy.objects.create(plugin=FIRST, person=user, state=PluginPolicy.State.FORCED_OFF)
    PluginAccess.objects.create(plugin=OTHER, person=user, may_use=False)

    assert not policy.decide(FIRST, user).on
    assert not policy.decide(OTHER, user).on
    assert policy.decide(FIRST, other_user).on
    assert policy.decide(OTHER, other_user).on
    assert not policy.decide(FIRST, user).on, "and still off for the first"


def test_switching_a_plugin_off_reaches_one_not_yet_asked_about(user):
    """The rows are read at the first question, for every plugin: a row an administrator
    writes afterwards, for a plugin nobody has asked about yet, has to be seen (#1112)."""
    assert policy.decide(FIRST, user).on, "the rows are read here"

    PluginPolicy.objects.create(plugin=OTHER, person=user, state=PluginPolicy.State.FORCED_OFF)

    assert not policy.decide(OTHER, user).on


def test_switching_a_plugin_off_for_everybody_reaches_everybody(user, other_user):
    assert policy.decide(FIRST, user).on
    assert policy.decide(FIRST, other_user).on

    PluginPolicy.objects.create(plugin=OTHER, state=PluginPolicy.State.UNAVAILABLE)

    assert not policy.decide(OTHER, user).offered
    assert not policy.decide(OTHER, other_user).offered


def test_rows_deleted_several_at_once_are_forgotten_too(user):
    """Switching a module back on deletes its sub-modules' rows through the queryset, which
    never calls a row's own `delete`: the memo has to hear of it all the same (#1112)."""
    PluginPolicy.objects.create(plugin=OTHER, person=user, state=PluginPolicy.State.FORCED_OFF)
    assert policy.decide(FIRST, user).on
    assert not policy.decide(OTHER, user).on

    PluginPolicy.objects.filter(person=user).delete()

    assert policy.decide(OTHER, user).on


def test_an_access_rule_written_afterwards_is_seen(user):
    assert policy.decide(FIRST, user).on

    rule = PluginAccess.objects.create(plugin=OTHER, person=user, may_use=False)
    assert not policy.decide(OTHER, user).on

    PluginAccess.objects.filter(pk=rule.pk).delete()
    assert policy.decide(OTHER, user).on


def test_joining_and_leaving_a_group_changes_the_answer_at_once(user):
    """The access rules are read through the person's groups, so who is in a group is part
    of what was remembered. Being put in a group that may not use a plugin takes it away
    at the next question, not at the next request (#1112)."""
    group = Group.objects.create(name="Careers team")
    PluginAccess.objects.create(plugin=OTHER, group=group, may_use=False)
    assert policy.decide(FIRST, user).on
    assert policy.decide(OTHER, user).on, "not a member yet"

    user.groups.add(group)
    assert not policy.decide(OTHER, user).on

    group.user_set.remove(user)
    assert policy.decide(OTHER, user).on, "and from the group's side"

    user.groups.set([group])
    assert not policy.decide(OTHER, user).on

    user.groups.clear()
    assert policy.decide(OTHER, user).on


def test_deleting_a_group_takes_its_rule_with_it(user):
    group = Group.objects.create(name="Careers team")
    user.groups.add(group)
    PluginAccess.objects.create(plugin=OTHER, group=group, may_use=False)
    assert not policy.decide(OTHER, user).on

    group.delete()

    assert policy.decide(OTHER, user).on


def test_a_persons_own_choice_is_seen_at_once(user, third_party):
    assert policy.decide(FIRST, user).on
    assert policy.decide(third_party, user).on

    assert policy.set_choice(user, third_party, on=False)

    assert not policy.decide(third_party, user).on


def test_nothing_remembered_outlives_the_request(client, user):
    """A change that tells nobody -- an `UPDATE` from a shell, another worker's write -- is
    seen by the next request, because each one begins by forgetting (#231, #1112)."""
    row = PluginPolicy.objects.create(plugin=OTHER, person=user, state=PluginPolicy.State.FORCED_ON)
    assert policy.decide(OTHER, user).on

    PluginPolicy.objects.filter(pk=row.pk).update(state=PluginPolicy.State.FORCED_OFF)
    assert policy.decide(OTHER, user).on, "nothing said so, and the request is not over"

    client.force_login(user)
    assert client.get(reverse("core:home")).status_code == 200

    assert not policy.decide(OTHER, user).on


def test_the_rows_go_with_the_answers_when_the_plugins_record_moves(user, monkeypatch):
    """Rows and answers are kept under the record's stamp, and are dropped together."""
    from postulo.plugins import installing

    row = PluginPolicy.objects.create(plugin=OTHER, person=user, state=PluginPolicy.State.FORCED_ON)
    assert policy.decide(FIRST, user).on
    PluginPolicy.objects.filter(pk=row.pk).update(state=PluginPolicy.State.FORCED_OFF)

    monkeypatch.setattr(installing, "record_stamp", lambda: "a plugin was installed")

    assert not policy.decide(OTHER, user).on
