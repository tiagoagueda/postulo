"""The dashboard, made of widgets, and the arrangement one person has chosen.

What is being protected here is mostly the *defaults*. A registry with a preference
attached has two ways to go wrong quietly: somebody who never opens the setting sees the
page change under them, or somebody who arranged their page has a new widget shoved onto
it. Which of those happens is decided by whether the stored list says what is shown or
what is hidden, and that is a decision worth a test rather than a comment.
"""

import pytest
from django.urls import reverse

from postulo.core import widgets

pytestmark = pytest.mark.django_db

ARRANGE = "settings:dashboard"


def arrangement(user) -> list[str]:
    user.profile.refresh_from_db()
    return user.profile.dashboard_widgets


def choose(user, keys):
    profile = user.profile
    profile.dashboard_widgets = list(keys)
    profile.save(update_fields=["dashboard_widgets"])
    return profile


# ----------------------------------------------------------------- the registry


def test_every_widget_names_a_template_that_exists():
    from django.template.loader import get_template

    for widget in widgets.all_widgets():
        get_template(widget.template)


def test_every_widget_has_a_sentence_saying_what_it_is_for():
    """The picker is a list of names and sentences; a widget without one is unpickable."""
    for widget in widgets.all_widgets():
        assert str(widget.blurb).strip(), widget.key


def test_a_width_that_is_not_one_of_the_three_is_refused():
    with pytest.raises(ValueError, match="width must be one of"):
        widgets.Widget(
            key="nope",
            label="",
            blurb="x",
            template="widgets/shortcuts.html",
            context=lambda sources: {},
            width="two-thirds",
        )


def test_registering_the_same_key_twice_is_a_mistake_not_an_override():
    existing = widgets.all_widgets()[0]
    with pytest.raises(ValueError, match="already registered"):
        widgets.register(existing)


def test_the_default_set_is_what_the_dashboard_showed_before_it_had_widgets():
    assert widgets.default_keys() == [
        "suggestions",
        "counters",
        "gone_quiet",
        "upcoming_interviews",
        "due_reminders",
        "recent_activity",
        "shortcuts",
    ]


# ------------------------------------------------------------- what is stored


def test_somebody_who_has_never_arranged_anything_gets_the_defaults(user):
    assert widgets.keys_for(user.profile) == widgets.default_keys()


def test_a_widget_that_no_longer_exists_is_passed_over_rather_than_breaking_the_page(user):
    """A plugin removed, or a widget dropped in an upgrade. The page still draws."""
    choose(user, ["counters", "a-widget-from-a-plugin-you-uninstalled", "shortcuts"])

    assert widgets.keys_for(user.profile) == ["counters", "shortcuts"]


def test_the_page_is_built_in_the_order_that_was_chosen(client, user):
    choose(user, ["shortcuts", "counters"])
    client.force_login(user)

    page = client.get(reverse("core:home")).context["page"]

    assert [item.spec.key for item in page] == ["shortcuts", "counters"]


def test_taking_everything_off_leaves_a_page_that_offers_to_put_something_back(client, user):
    """Clearing the dashboard has to stay cleared.

    Never arranged is ``None`` and arranged-to-nothing is ``[]``. If they were the same
    value, taking the last widget off would hand back all seven defaults, which is the
    opposite of what was just asked for.
    """
    choose(user, ["counters"])
    client.force_login(user)
    client.post(reverse(ARRANGE), {"action": "remove", "key": "counters"})

    response = client.get(reverse("core:home"))

    assert response.context["page"] == []
    assert b"Your dashboard is empty" in response.content
    assert arrangement(user) == []


# ------------------------------------------------------------------ arranging


def test_the_arranging_page_needs_an_account(client):
    response = client.get(reverse(ARRANGE))

    assert response.status_code == 302
    assert "login" in response["Location"]


def test_adding_a_widget_puts_it_at_the_end(client, user):
    choose(user, ["counters", "shortcuts"])
    client.force_login(user)

    client.post(reverse(ARRANGE), {"action": "add", "key": "funnel"})

    assert arrangement(user) == ["counters", "shortcuts", "funnel"]


def test_adding_something_already_there_changes_nothing(client, user):
    choose(user, ["counters", "shortcuts"])
    client.force_login(user)

    client.post(reverse(ARRANGE), {"action": "add", "key": "counters"})

    assert arrangement(user) == ["counters", "shortcuts"]


def test_removing_a_widget_takes_it_off(client, user):
    choose(user, ["counters", "shortcuts", "funnel"])
    client.force_login(user)

    client.post(reverse(ARRANGE), {"action": "remove", "key": "shortcuts"})

    assert arrangement(user) == ["counters", "funnel"]


def test_moving_a_widget_up_and_down(client, user):
    choose(user, ["counters", "shortcuts", "funnel"])
    client.force_login(user)

    client.post(reverse(ARRANGE), {"action": "up", "key": "funnel"})
    assert arrangement(user) == ["counters", "funnel", "shortcuts"]

    client.post(reverse(ARRANGE), {"action": "down", "key": "counters"})
    assert arrangement(user) == ["funnel", "counters", "shortcuts"]


def test_moving_the_first_one_up_or_the_last_one_down_does_nothing(client, user):
    choose(user, ["counters", "shortcuts"])
    client.force_login(user)

    client.post(reverse(ARRANGE), {"action": "up", "key": "counters"})
    client.post(reverse(ARRANGE), {"action": "down", "key": "shortcuts"})

    assert arrangement(user) == ["counters", "shortcuts"]


def test_arranging_for_the_first_time_starts_from_the_defaults(client, user):
    """Not from nothing: taking one widget off should leave the other six, not one."""
    client.force_login(user)

    client.post(reverse(ARRANGE), {"action": "remove", "key": "shortcuts"})

    expected = [key for key in widgets.default_keys() if key != "shortcuts"]
    assert arrangement(user) == expected


def test_going_back_to_the_standard_arrangement_forgets_the_choice(client, user):
    choose(user, ["funnel"])
    client.force_login(user)

    client.post(reverse(ARRANGE), {"action": "reset"})

    assert arrangement(user) is None
    assert widgets.keys_for(user.profile) == widgets.default_keys()


def test_a_key_that_is_not_a_widget_is_refused(client, user):
    choose(user, ["counters"])
    client.force_login(user)

    response = client.post(
        reverse(ARRANGE), {"action": "add", "key": "../../etc/passwd"}, follow=True
    )

    assert arrangement(user) == ["counters"]
    assert b"not a widget Postulo knows about" in response.content


def test_one_persons_arrangement_is_not_anothers(client, user, other_user):
    choose(user, ["funnel"])
    client.force_login(other_user)

    page = client.get(reverse("core:home")).context["page"]

    assert [item.spec.key for item in page] == widgets.default_keys()


# -------------------------------------------------------------- what it draws


def test_each_widget_draws_on_an_empty_account(client, user):
    """Every widget, with nothing recorded at all. An empty state is where they break."""
    choose(user, [w.key for w in widgets.all_widgets()])
    client.force_login(user)

    response = client.get(reverse("core:home"))

    assert response.status_code == 200
    body = response.content.decode()
    for widget in widgets.all_widgets():
        assert f'data-widget="{widget.key}"' in body, widget.key


def test_a_widget_cannot_see_what_another_one_computed(client, user):
    """Each is rendered against its own dict, so one cannot lean on another's variable."""
    choose(user, ["counters", "shortcuts"])
    client.force_login(user)

    page = client.get(reverse("core:home")).context["page"]

    by_key = {item.spec.key: item.context for item in page}
    assert "open_count" in by_key["counters"]
    assert by_key["shortcuts"] == {}


def test_the_arranging_page_lists_what_is_on_and_what_is_off(client, user):
    choose(user, ["counters"])
    client.force_login(user)

    response = client.get(reverse(ARRANGE))

    assert [w.key for w in response.context["chosen"]] == ["counters"]
    offered = {w.key for _group, items in response.context["available"] for w in items}
    assert "counters" not in offered
    assert "funnel" in offered
