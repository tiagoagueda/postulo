"""The page where a person sees what is running for them, and who decided.

*Settings → Connections* answers "what have I set up". It says nothing about the parsers
that read a posting off a page — they need no connection and so appear nowhere — and nothing
about **why** a plugin is available at all.

This page exists mainly for one of its rows: the one an administrator decided. #95 lets them
make a plugin available, unavailable, always on or always off for a named account, and the
whole reason that is acceptable is that it cannot be held quietly. If the policy had landed
and this page had not, an account could be configured by somebody else with no way for its
owner to find out — which is precisely the arrangement worth avoiding.
"""

from __future__ import annotations

import re

import pytest
from django.contrib.auth import get_user_model
from django.urls import reverse

from postulo.plugins import policy
from postulo.plugins.models import PluginPolicy

pytestmark = pytest.mark.django_db

User = get_user_model()
PASSWORD = "a-fairly-long-password-42"
PLUGIN = "schema.org"
URL = "settings:plugins"


@pytest.fixture
def admin(db):
    return User.objects.create_user(
        email="admin@example.org", password=PASSWORD, username="admin-one", is_staff=True
    )


def row_for(html: str, name: str) -> str | None:
    found = re.search(rf'<li[^>]*data-plugin="{re.escape(name)}".*?</li>', html, re.S)
    return found.group(0) if found else None


# --------------------------------------------------------------- what it shows


def test_it_lists_what_was_installed_and_what_postulo_ships_only_when_asked(
    client, user, third_party
):
    """What Postulo ships is the administrator's to switch, so it is out of the way until
    the mark is ticked (#200); what was installed on the instance is the person's, and
    is the page."""
    client.force_login(user)

    html = client.get(reverse(URL)).content.decode()
    assert row_for(html, third_party)
    assert row_for(html, PLUGIN) is None, "a built-in, and nobody decided it for this person"
    assert "Show the plugins Postulo ships" in html

    html = client.get(reverse(URL) + "?internal=1").content.decode()
    row = row_for(html, PLUGIN)
    assert row and "data-shipped" in row
    assert 'type="checkbox"' not in row, "no switch at all, rather than a disabled one"
    # No sentence either, since #287: the mark's own caption says who switches these.
    assert "Shipped inside Postulo" not in row and "why-" not in row


def test_every_row_carries_the_plugins_logo(client, user, third_party):
    """As the server page has since #106; the row never carried the plugin before (#288).
    A plugin with no logo gets the initials tile rather than nothing or a broken image."""
    client.force_login(user)
    html = client.get(reverse(URL) + "?internal=1").content.decode()

    for name in (third_party, PLUGIN):
        row = row_for(html, name)
        assert row, name
        tile = re.search(
            r'<span class="avatar[^"]*"[^>]*>(<img[^>]*>|<span[^>]*>[^<]*</span>)', row
        )
        assert tile, f"{name}: no logo or initials tile in the row"
        assert 'aria-hidden="true"' in tile.group(0) or 'alt=""' in tile.group(1), (
            "decorative: it stands beside the name"
        )


def test_a_source_appears_even_though_it_has_no_connection(client, user, third_party):
    """The gap this page fills. A parser that reads a posting off a page needs nothing from
    anybody, so the connections list has never had a reason to mention it."""
    client.force_login(user)

    html = client.get(reverse(URL)).content.decode()

    assert row_for(html, third_party)


def test_it_says_it_is_per_account_and_not_per_session(client, user):
    """The request said "in their session". Plugin state is per account: signing in
    elsewhere does not change it, and it survives signing out. A page that said *session*
    while meaning *account* would teach people something untrue about where their settings
    live."""
    client.force_login(user)

    html = client.get(reverse(URL)).content.decode()

    assert "per account" in html
    assert "session" not in html.split("</header>")[-1].lower() or "not per browser" in html


# --------------------------------------------------------- what is theirs


def test_a_person_can_switch_one_off_and_back(client, user, third_party):
    client.force_login(user)

    # The view saved through its own `request.user.profile`; this one is a different
    # instance and still holds what it read at the start.
    client.post(reverse(URL), {"on": []})
    user.profile.refresh_from_db()
    assert not policy.decide(third_party, user).on

    client.post(reverse(URL), {"on": [third_party]})
    user.profile.refresh_from_db()
    assert policy.decide(third_party, user).on


def test_the_mark_survives_saving(client, user, third_party):
    """Ticked, saved, and still ticked: the redirect carries it."""
    client.force_login(user)

    response = client.post(reverse(URL), {"on": [third_party], "internal": "1"})

    assert response.url == reverse(URL) + "?internal=1"
    assert client.post(reverse(URL), {"on": [third_party]}).url == reverse(URL)


def test_a_row_that_is_theirs_offers_a_control(client, user, third_party):
    client.force_login(user)

    html = client.get(reverse(URL)).content.decode()

    assert "disabled" not in row_for(html, third_party)


def test_the_checkbox_carries_the_class_that_colours_it(client, user, third_party):
    """Green around a ticked box and red around an unticked one, down the whole list.

    The colour is redundant by design: the checkbox already says which way it is set, to a
    screen reader and to a keyboard, and `state-glow` only makes a dozen rows readable at a
    glance. This asserts the class reaches the markup, because the stylesheet is where the
    two colours live and a class that quietly stops being emitted takes them with it.
    """
    client.force_login(user)

    row = row_for(client.get(reverse(URL)).content.decode(), third_party)

    assert "state-glow" in row


# ------------------------------------------- what an administrator decided


@pytest.mark.parametrize("state", [PluginPolicy.State.FORCED_ON, PluginPolicy.State.FORCED_OFF])
def test_a_decided_row_is_shown_locked_rather_than_hidden(client, user, admin, state):
    """Hiding it would be the quiet version of the very thing this page is against -- and
    that holds for a built-in too, which the mark otherwise keeps off the page (#200)."""
    PluginPolicy.objects.create(plugin=PLUGIN, person=user, state=state, decided_by=admin)
    client.force_login(user)

    row = row_for(client.get(reverse(URL)).content.decode(), PLUGIN)

    assert row, "a plugin decided for you must still be visible, mark or no mark"
    assert 'type="checkbox"' not in row, "a built-in carries no switch"
    assert "Set for your account." in row
    assert "An administrator decided" not in row and admin.username not in row, (
        "the attribution went with #287; the administrator's own view still carries it"
    )


@pytest.mark.parametrize("state", [PluginPolicy.State.FORCED_ON, PluginPolicy.State.FORCED_OFF])
def test_a_decided_installed_row_is_shown_disabled(client, user, admin, state, third_party):
    PluginPolicy.objects.create(plugin=third_party, person=user, state=state, decided_by=admin)
    client.force_login(user)

    row = row_for(client.get(reverse(URL)).content.decode(), third_party)

    assert row and "disabled" in row and "Set for your account." in row
    # A disabled control still points at a sentence, and the sentence is there.
    assert f'aria-describedby="why-{third_party}"' in row
    assert f'id="why-{third_party}"' in row


# `test_the_person_is_told_who_decided` stood here from #96 until #287: the person's own
# page no longer names the administrator. `server/person_plugins.html` still does, and
# `tests/test_server_settings.py` covers that view.


def test_unavailable_is_not_shown_at_all(client, user, admin):
    """The difference between the two off states, on the page rather than only in the model.

    *Unavailable* means not part of your Postulo. *Forced off* means you can see this exists
    and somebody switched it off for you.
    """
    PluginPolicy.objects.create(
        plugin=PLUGIN, person=user, state=PluginPolicy.State.UNAVAILABLE, decided_by=admin
    )
    client.force_login(user)

    assert row_for(client.get(reverse(URL)).content.decode(), PLUGIN) is None


def test_posting_cannot_take_back_a_decision_made_for_them(client, user, admin, third_party):
    """A disabled checkbox submits nothing, so a locked-on plugin would read as "switch me
    off" on every save if the view acted on absence alone."""
    PluginPolicy.objects.create(
        plugin=PLUGIN, person=user, state=PluginPolicy.State.FORCED_ON, decided_by=admin
    )
    client.force_login(user)

    client.post(reverse(URL), {"on": []})

    user.profile.refresh_from_db()
    assert policy.decide(PLUGIN, user).on
    # Posting nothing does mean "switch off everything I control" — and this plugin is not
    # one of those, so it must not appear among the person's own choices either.
    assert PLUGIN not in user.profile.plugins_off
    assert third_party in user.profile.plugins_off, "the rows that are theirs still saved"


def test_saving_the_page_does_not_disturb_a_locked_row(client, user, admin):
    """The same failure from the other side: saving an unrelated change must not switch a
    forced-off plugin on."""
    PluginPolicy.objects.create(
        plugin=PLUGIN, person=user, state=PluginPolicy.State.FORCED_OFF, decided_by=admin
    )
    client.force_login(user)

    client.post(reverse(URL), {"on": [PLUGIN, "page-metadata"]})

    user.profile.refresh_from_db()
    assert not policy.decide(PLUGIN, user).on


# ------------------------------------------------------------------ boundaries


def test_one_persons_page_never_shows_anothers_state(client, user, admin, third_party):
    """Owner scoping, on a page that reads from two places at once."""
    other = User.objects.create_user(
        email="other@example.org", password=PASSWORD, username="other-one"
    )
    PluginPolicy.objects.create(
        plugin=third_party, person=other, state=PluginPolicy.State.FORCED_OFF, decided_by=admin
    )
    client.force_login(user)

    row = row_for(client.get(reverse(URL)).content.decode(), third_party)

    assert "disabled" not in row, "another account's exception leaked onto this page"
    assert policy.decide(third_party, user).theirs


def test_it_is_not_reachable_signed_out(client):
    response = client.get(reverse(URL))
    assert response.status_code == 302
    assert "login" in response["Location"]


def test_the_section_is_in_the_sidebar(client, user):
    client.force_login(user)

    html = client.get(reverse("settings:appearance")).content.decode()

    assert reverse(URL) in html
