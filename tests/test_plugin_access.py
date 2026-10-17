"""Groups, and who may use, switch and configure each plugin (#886)."""

from __future__ import annotations

import pytest
from django.contrib.auth import get_user_model
from django.contrib.auth.models import Group
from django.db import IntegrityError, transaction
from django.urls import reverse

from postulo.core.memo import forget_decisions
from postulo.plugins import policy
from postulo.plugins.models import PluginAccess, PluginPolicy

pytestmark = pytest.mark.django_db

User = get_user_model()
PASSWORD = "a-fairly-long-password-42"
OWN = "websites"  # ships inside Postulo


@pytest.fixture
def group():
    return Group.objects.create(name="Careers team")


@pytest.fixture
def member(user, group):
    user.groups.add(group)
    return user


def staff(username="adm", **extra):
    return User.objects.create_user(
        email=f"{username}@example.org",
        password=PASSWORD,
        username=username,
        is_staff=True,
        **extra,
    )


# --------------------------------------------------------------- the decision


def test_no_rule_means_the_default(user):
    assert policy.decide(OWN, user).on
    assert policy.access_for(OWN, user) is None


def test_a_group_rule_that_denies_use_takes_it_away(member, group, other_user):
    PluginAccess.objects.create(plugin=OWN, group=group, may_use=False)
    decision = policy.decide(OWN, member)
    assert not decision.on and not decision.offered
    assert decision.decided_by == "access"
    assert policy.decide(OWN, other_user).on, "only the group"


def test_an_account_rule_beats_its_groups(member, group):
    PluginAccess.objects.create(plugin=OWN, group=group, may_use=False)
    PluginAccess.objects.create(plugin=OWN, person=member, may_use=True)
    assert policy.decide(OWN, member).on


def test_several_groups_read_together_the_most_permissive_wins(user):
    one, two = Group.objects.create(name="one"), Group.objects.create(name="two")
    user.groups.add(one, two)
    PluginAccess.objects.create(plugin=OWN, group=one, may_use=False)
    PluginAccess.objects.create(plugin=OWN, group=two, may_use=True)
    assert policy.decide(OWN, user).on


def test_an_installed_plugin_is_theirs_to_switch_only_where_a_rule_says_so(
    user, third_party, group
):
    user.groups.add(group)
    assert policy.decide(third_party, user).theirs, "the default"
    PluginAccess.objects.create(plugin=third_party, group=group, may_switch=False)
    assert not policy.decide(third_party, user).theirs
    assert policy.decide(third_party, user).on
    PluginAccess.objects.filter(plugin=third_party).update(may_switch=True)
    forget_decisions()
    assert policy.decide(third_party, user).theirs


def test_a_rule_lets_someone_switch_what_the_instance_forced_on(user, third_party):
    PluginPolicy.objects.create(plugin=third_party, state=PluginPolicy.State.FORCED_ON)
    assert not policy.decide(third_party, user).theirs
    PluginAccess.objects.create(plugin=third_party, person=user, may_switch=True)
    assert policy.decide(third_party, user).theirs


def test_a_rule_does_not_override_a_row_an_administrator_wrote_for_that_person(user, group):
    user.groups.add(group)
    PluginPolicy.objects.create(plugin=OWN, person=user, state=PluginPolicy.State.FORCED_OFF)
    PluginAccess.objects.create(plugin=OWN, group=group, may_use=True)
    assert not policy.decide(OWN, user).on


def test_a_rule_does_not_switch_on_what_is_off_for_everybody(user):
    PluginPolicy.objects.create(plugin=OWN, state=PluginPolicy.State.FORCED_OFF)
    PluginAccess.objects.create(plugin=OWN, person=user, may_use=True)
    assert not policy.decide(OWN, user).on


def test_a_rule_names_exactly_one_subject(user, group):
    with pytest.raises(IntegrityError), transaction.atomic():
        PluginAccess.objects.create(plugin=OWN, person=user, group=group)
    with pytest.raises(IntegrityError), transaction.atomic():
        PluginAccess.objects.create(plugin=OWN)


# --------------------------------------------------------------- the settings


def test_the_operator_always_may_change_settings():
    operator = staff("op", is_superuser=True)
    PluginAccess.objects.create(plugin="gdpr", person=operator, may_change_settings=False)
    assert policy.may_change_settings(operator, "gdpr")


def test_another_administrator_may_unless_a_rule_says_not():
    other = staff()
    assert policy.may_change_settings(other, "gdpr")
    PluginAccess.objects.create(plugin="gdpr", person=other, may_change_settings=False)
    assert not policy.may_change_settings(other, "gdpr")
    assert policy.may_change_settings(other, "email"), "another plugin"


def test_someone_who_is_not_an_administrator_never_may(user):
    PluginAccess.objects.create(plugin="gdpr", person=user, may_change_settings=True)
    assert not policy.may_change_settings(user, "gdpr")


def test_the_settings_button_is_disabled_for_an_administrator_a_rule_excludes(client):
    other = staff()
    PluginAccess.objects.create(plugin="gdpr", person=other, may_change_settings=False)
    client.force_login(other)
    body = client.get(reverse("server:plugins")).content.decode()
    row = body[body.index('data-policy="gdpr"') :]
    assert 'aria-disabled="true"' in row[: row.index("</li>")]


# ------------------------------------------------------------------ the pages


def test_the_old_checkbox_is_gone_and_an_access_link_is_there(client, admin_user):
    client.force_login(admin_user)
    body = client.get(reverse("server:plugins")).content.decode()
    assert "Each person may change it" not in body
    assert f'href="{reverse("server:plugin_access", args=["websites"])}"' in body


def test_saving_the_list_does_not_change_whether_everybody_may_switch(client, admin_user):
    PluginPolicy.objects.create(plugin="websites", state=PluginPolicy.State.FORCED_ON)
    client.force_login(admin_user)
    client.post(reverse("server:plugin_policy"), {"row:websites": "1", "on:websites": "on"})
    assert PluginPolicy.objects.get(plugin="websites").state == PluginPolicy.State.FORCED_ON


def test_the_access_page_adds_changes_and_removes_a_rule(client, admin_user, group):
    client.force_login(admin_user)
    url = reverse("server:plugin_access", args=["websites"])
    assert client.get(url).status_code == 200
    client.post(
        url, {"action": "add", "subject": f"group:{group.pk}", "use": "on", "settings": "on"}
    )
    rule = PluginAccess.objects.get(plugin="websites", group=group)
    assert rule.may_use and rule.may_change_settings and not rule.may_switch
    client.post(url, {"action": "save", f"settings-{rule.pk}": "on"})
    rule.refresh_from_db()
    assert not rule.may_use
    client.post(url, {"action": "save", f"remove-{rule.pk}": "on"})
    assert not PluginAccess.objects.exists()


def test_everybody_may_switch_is_the_instance_row(client, admin_user, third_party):
    client.force_login(admin_user)
    url = reverse("server:plugin_access", args=[third_party])
    client.post(url, {"action": "everyone"})
    assert PluginPolicy.objects.get(plugin=third_party).state == PluginPolicy.State.FORCED_ON
    client.post(url, {"action": "everyone", "everyone_switches": "on"})
    assert not PluginPolicy.objects.filter(plugin=third_party).exists()


def test_an_unknown_plugin_has_no_access_page(client, admin_user):
    client.force_login(admin_user)
    assert client.get(reverse("server:plugin_access", args=["nothing-here"])).status_code == 404


def test_only_an_administrator_reaches_either_page(client, user):
    client.force_login(user)
    for url in (
        reverse("server:groups"),
        reverse("server:plugin_access", args=["websites"]),
    ):
        assert client.get(url).status_code in (302, 403, 404)


def test_groups_are_made_filled_renamed_and_deleted(client, admin_user, user):
    client.force_login(admin_user)
    url = reverse("server:groups")
    client.post(url, {"action": "create", "name": "Team"})
    group = Group.objects.get(name="Team")
    client.post(url, {"action": "save", "group": group.pk, "name": "Crew", "members": [user.pk]})
    group.refresh_from_db()
    assert group.name == "Crew" and list(group.user_set.all()) == [user]
    assert "Crew" in client.get(url).content.decode()
    client.post(url, {"action": "create", "name": "crew"})
    assert Group.objects.count() == 1, "a name is not made twice"
    client.post(url, {"action": "delete", "group": group.pk})
    assert not Group.objects.exists()


# ------------------------------------------- the page behind the button (#967)

SETTINGS_PAGES = [
    ("maps", "server:maps"),
    ("gdpr", "server:data_protection"),
    ("email", "server:email"),
    ("identifiers", "server:identifier_schemes"),
]


@pytest.mark.parametrize(("plugin", "page"), SETTINGS_PAGES)
def test_a_settings_page_refuses_an_administrator_a_rule_excludes(client, plugin, page):
    other = staff()
    PluginAccess.objects.create(plugin=plugin, person=other, may_change_settings=False)
    client.force_login(other)

    assert client.get(reverse(page)).status_code == 403
    assert client.post(reverse(page), {}).status_code == 403


def test_the_operator_and_an_unexcluded_administrator_still_reach_the_page(client, admin_user):
    other = staff()
    PluginAccess.objects.create(plugin="maps", person=other, may_change_settings=False)
    PluginAccess.objects.create(plugin="maps", person=admin_user, may_change_settings=False)
    client.force_login(admin_user)
    assert client.get(reverse("server:maps")).status_code == 200
    client.force_login(staff("third"))
    assert client.get(reverse("server:maps")).status_code == 200


def test_a_rule_through_a_group_closes_the_page_too(client, group):
    other = staff()
    other.groups.add(group)
    PluginAccess.objects.create(plugin="maps", group=group, may_change_settings=False)
    client.force_login(other)
    assert client.get(reverse("server:maps")).status_code == 403


def test_the_excluded_administrator_cannot_change_the_map_settings(client):
    from postulo.core.models import SiteSettings

    other = staff()
    PluginAccess.objects.create(plugin="maps", person=other, may_change_settings=False)
    before = SiteSettings.get().map_tile_url
    client.force_login(other)
    client.post(
        reverse("server:maps"),
        {"map_tiles": "custom", "map_tile_url": "https://evil.example/{z}/{x}/{y}.png"},
    )
    assert SiteSettings.get().map_tile_url == before


@pytest.mark.parametrize(
    "name", ["server:email_consent", "server:email_test", "server:email_connection_test"]
)
def test_the_mail_pages_actions_are_held_to_the_same_rule(client, name):
    other = staff()
    PluginAccess.objects.create(plugin="email", person=other, may_change_settings=False)
    client.force_login(other)
    assert client.post(reverse(name), {}).status_code == 403


def test_the_identifier_dialog_is_held_to_it_too(client):
    other = staff()
    PluginAccess.objects.create(plugin="identifiers", person=other, may_change_settings=False)
    client.force_login(other)
    response = client.post(
        reverse("server:identifier_schemes"), {"definitions": "[]"}, HTTP_HX_REQUEST="true"
    )
    assert response.status_code == 403


def test_a_page_both_of_two_plugins_share_is_closed_by_either_rule(client):
    other = staff()
    PluginAccess.objects.create(plugin="smtp", person=other, may_change_settings=False)
    client.force_login(other)
    assert client.get(reverse("server:email")).status_code == 403


def test_an_excluded_administrator_cannot_lift_their_own_rule(client, group):
    other = staff()
    rule = PluginAccess.objects.create(plugin="websites", person=other, may_change_settings=False)
    client.force_login(other)
    url = reverse("server:plugin_access", args=["websites"])

    client.post(url, {"action": "save", f"remove-{rule.pk}": "on"})
    assert PluginAccess.objects.filter(pk=rule.pk).exists()
    client.post(url, {"action": "save", f"settings-{rule.pk}": "on"})
    rule.refresh_from_db()
    assert not rule.may_change_settings
    client.post(url, {"action": "add", "subject": f"person:{other.pk}", "settings": "on"})
    rule.refresh_from_db()
    assert not rule.may_change_settings


def test_nor_can_they_write_a_rule_for_a_group_they_are_in_or_change_a_group_a_rule_names(
    client, group
):
    other = staff()
    other.groups.add(group)
    url = reverse("server:plugin_access", args=["websites"])
    client.force_login(other)
    client.post(url, {"action": "add", "subject": f"group:{group.pk}", "settings": "on"})
    assert not PluginAccess.objects.filter(group=group).exists()

    PluginAccess.objects.create(plugin="websites", group=group, may_change_settings=False)
    client.post(reverse("server:groups"), {"action": "save", "group": group.pk, "name": group.name})
    client.post(reverse("server:groups"), {"action": "delete", "group": group.pk})
    assert Group.objects.filter(pk=group.pk).exists()
    assert other.groups.filter(pk=group.pk).exists()


def test_the_operator_may_still_write_rules_about_anyone(client, admin_user):
    other = staff()
    client.force_login(admin_user)
    client.post(
        reverse("server:plugin_access", args=["websites"]),
        {"action": "add", "subject": f"person:{other.pk}", "use": "on"},
    )
    assert PluginAccess.objects.filter(plugin="websites", person=other).exists()
