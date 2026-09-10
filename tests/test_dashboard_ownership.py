"""Every account owns its dashboard arrangement from the day it exists (#123).

> the dashboard, even if inicilized for a new user, must be unique for each user.

Nothing was ever shared between accounts, and that is worth saying before anything else:
two people who had never arranged anything were looking at the same *list of keys*, each
computed against their own records. What was missing was **ownership** — the arrangement
itself belonged to nobody until somebody touched the setting, and a grid has to store what
a widget was dragged into.

The interesting half is what the null was load-bearing for. It carried the rule that a
widget added in a later release reaches somebody who never arranged anything; with every
account holding a list, nobody is ever "never arranged". So the rule is rebuilt as a seen
set, the trade is made knowingly, and both halves are tested here: a new widget no longer
walks onto anybody's page, and it is not lost either.
"""

from __future__ import annotations

import pytest
from django.urls import reverse

from postulo.accounts.models import Profile
from postulo.core import widgets

pytestmark = pytest.mark.django_db

ARRANGE = "settings:dashboard"


@pytest.fixture
def a_new_widget():
    """A widget that arrives after an account was set up, and is gone again afterwards."""
    widget = widgets.register(
        widgets.Widget(
            key="test_arrival",
            label="Test arrival",
            blurb="Something a later release added.",
            template="core/widgets/shortcuts.html",
            context=lambda sources: {},
        )
    )
    yield widget
    widgets.REGISTRY.pop("test_arrival", None)


# ------------------------------------------------------------------ from day one


def test_an_account_has_its_own_arrangement_before_anybody_touches_anything(user):
    """Stored, not computed. That is the whole of what the issue asked for."""
    profile = Profile.objects.get(user=user)

    assert profile.dashboard_widgets == widgets.default_keys()
    assert profile.dashboard_known == widgets.all_keys()


def test_two_accounts_own_two_arrangements(user, other_user):
    mine = Profile.objects.get(user=user)
    theirs = Profile.objects.get(user=other_user)

    mine.dashboard_widgets = ["counters"]
    mine.save()

    theirs.refresh_from_db()
    assert theirs.dashboard_widgets == widgets.default_keys()


def test_a_profile_that_missed_the_seeding_is_not_told_everything_is_new(user):
    """An archive restored from before this, or a row made by a route nobody thought of.

    The seen set only ever grows and starts as every key there is, so an empty one is
    *nothing recorded* rather than *nothing decided* — and announcing every widget in
    Postulo as new to somebody who has been reading their own dashboard for a year is the
    worse of the two mistakes.
    """
    Profile.objects.filter(user=user).update(dashboard_known=[])
    profile = Profile.objects.get(user=user)

    assert widgets.known_to(profile) == set(widgets.all_keys())
    assert widgets.new_for(profile) == []


def test_nothing_chosen_is_still_a_choice(user):
    """Clearing the dashboard used to be `[]` against a `None` that meant *never arranged*.
    One of those states is gone; the other has to keep meaning what it meant.
    """
    profile = Profile.objects.get(user=user)
    profile.dashboard_widgets = []
    profile.save()

    assert widgets.keys_for(profile) == []
    assert not widgets.is_standard(profile)


# ------------------------------------------- what the null was load-bearing for


def test_a_widget_added_later_does_not_walk_onto_anybodys_page(user, a_new_widget):
    profile = Profile.objects.get(user=user)

    assert a_new_widget.key not in widgets.keys_for(profile)
    assert [w.key for w in widgets.new_for(profile)] == ["test_arrival"]


def test_and_it_is_not_lost_either(client, user, a_new_widget):
    """The trade, made knowingly: offered instead of imposed, and said out loud."""
    client.force_login(user)

    dashboard = client.get(reverse("core:home")).content.decode()
    arrange = client.get(reverse(ARRANGE)).content.decode()

    assert "arrange your dashboard" in dashboard
    assert "New since you last arranged this" in arrange
    assert "Test arrival" in arrange


def test_saying_yes_puts_it_on_the_page(client, user, a_new_widget):
    client.force_login(user)

    client.post(reverse(ARRANGE), {"action": "add", "key": "test_arrival"})

    profile = Profile.objects.get(user=user)
    assert "test_arrival" in widgets.keys_for(profile)
    assert widgets.new_for(profile) == [], "and it stops being new"


def test_saying_no_is_an_answer_and_is_remembered(client, user, a_new_widget):
    """Without this, a widget somebody declined would be offered again on every visit."""
    client.force_login(user)

    client.post(reverse(ARRANGE), {"action": "dismiss", "key": "test_arrival"})

    profile = Profile.objects.get(user=user)
    assert "test_arrival" not in widgets.keys_for(profile)
    assert widgets.new_for(profile) == []
    assert client.get(reverse("core:home")).content.decode().count("arrange your dashboard") == 0


def test_taking_a_widget_off_does_not_make_it_new_again(client, user):
    client.force_login(user)

    client.post(reverse(ARRANGE), {"action": "remove", "key": "counters"})

    profile = Profile.objects.get(user=user)
    assert "counters" not in widgets.keys_for(profile)
    assert widgets.new_for(profile) == []


def test_reset_gives_this_account_the_standard_page_rather_than_an_absence(client, user):
    client.force_login(user)
    client.post(reverse(ARRANGE), {"action": "remove", "key": "counters"})

    client.post(reverse(ARRANGE), {"action": "reset"})

    profile = Profile.objects.get(user=user)
    assert profile.dashboard_widgets == widgets.default_keys()
    assert widgets.is_standard(profile)


def test_a_stored_key_that_no_longer_exists_is_passed_over(user):
    """Uninstalling a plugin is a normal event and not an error."""
    profile = Profile.objects.get(user=user)
    profile.dashboard_widgets = ["counters", "acme:gone"]
    profile.save()

    assert widgets.keys_for(profile) == ["counters"]


# ------------------------------------------------------- the key namespace, decided now


def test_a_bare_key_belongs_to_postulo():
    """Decided while it is free. A key lands inside every stored arrangement, so a
    collision discovered afterwards is a data migration of every one of them.
    """
    with pytest.raises(ValueError, match="needs a key beginning"):
        widgets.register(
            widgets.Widget(
                key="counters_two",
                label="x",
                blurb="x",
                template="core/widgets/shortcuts.html",
                context=lambda s: {},
                provider="acme",
            )
        )


def test_a_namespaced_key_needs_the_provider_that_owns_it():
    with pytest.raises(ValueError, match="provider that owns it"):
        widgets.register(
            widgets.Widget(
                key="acme:counters",
                label="x",
                blurb="x",
                template="core/widgets/shortcuts.html",
                context=lambda s: {},
            )
        )


def test_a_plugins_widget_and_postulos_can_share_a_name():
    """`counters` and `acme:counters` are two widgets, which is the point of namespacing."""
    widget = widgets.register(
        widgets.Widget(
            key="acme:counters",
            label="Acme counters",
            blurb="x",
            template="core/widgets/shortcuts.html",
            context=lambda s: {},
            provider="acme",
        )
    )
    try:
        assert widgets.get("acme:counters") is widget
        assert widgets.get("counters") is not widget
    finally:
        widgets.REGISTRY.pop("acme:counters", None)


# ---------------------------------------------------------------- and the archive


def test_the_arrangement_travels_with_the_account(user, other_user):
    from postulo.core import export

    profile = Profile.objects.get(user=user)
    profile.dashboard_widgets = ["funnel", "counters"]
    profile.save()

    document = export.build_document(user)
    stored = document["account"]["profile"]

    assert stored["dashboard_widgets"] == ["funnel", "counters"]
    assert stored["dashboard_known"] == widgets.all_keys()


def test_the_migration_gives_everybody_their_own_without_reading_the_registry():
    """A migration that imported `core.widgets` would do something different the day a
    widget is renamed. It records what was true when it was written.
    """
    from pathlib import Path

    source = Path("src/postulo/accounts/migrations/0015_owned_dashboard.py").read_text(
        encoding="utf-8"
    )

    # The code, not the prose: the docstring explains what it does not import.
    code = source.split('"""', 2)[2]

    assert "core.widgets" not in code and "import" in code
    assert "STANDARD = [" in code and "EVERYTHING = [" in code
    assert "RunPython" in code
