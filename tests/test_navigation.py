"""Choosing what the main navigation shows, and what the wordmark does when Dashboard goes.

The observation behind this: clicking "Postulo" already goes to the dashboard, so on every
page there are two controls for one destination. Hiding one is a per-person choice, off by
default — somebody seeing the instance for the first time has no way of knowing the
wordmark is a link — and when it is taken the wordmark has to do the job properly.
"""

from __future__ import annotations

import re

import pytest
from django.urls import reverse

from postulo.accounts.models import Profile
from postulo.core import navigation

pytestmark = pytest.mark.django_db


def appearance(client, **overrides):
    values = {
        "theme": "system",
        # A radio group with a default is always posted by the page that draws it (#292).
        "density": "comfortable",
        "quiet_after_days": 14,
        "navigation": list(navigation.HIDEABLE),
    }
    values.update(overrides)
    return client.post(reverse("settings:appearance"), values, follow=True)


# ------------------------------------------------------------------- the list


def test_every_item_is_offered_and_shown_by_default(client, user):
    client.force_login(user)
    html = client.get(reverse("core:home")).content.decode()
    for item in navigation.ITEMS:
        assert f'data-nav="{item.key}"' in html, item.key
    assert user.profile.hidden_nav_items == []


def test_a_person_can_leave_items_out(client, user):
    client.force_login(user)
    keep = [key for key in navigation.HIDEABLE if key not in {"dashboard", "companies"}]
    response = appearance(client, navigation=keep)
    assert response.status_code == 200

    user.profile.refresh_from_db()
    assert sorted(user.profile.hidden_nav_items) == ["companies", "dashboard"]

    html = client.get(reverse("core:home")).content.decode()
    assert 'data-nav="dashboard"' not in html and 'data-nav="companies"' not in html
    assert 'data-nav="applications"' in html
    # Hidden from the row, still perfectly reachable.
    assert client.get(reverse("jobs:company_list")).status_code == 200


def test_the_settings_page_shows_what_is_on(client, user):
    client.force_login(user)
    appearance(client, navigation=["listings", "applications"])
    html = client.get(reverse("settings:appearance")).content.decode()
    import re

    checked = set(re.findall(r'value="(\w+)"[^>]*checked', html))
    assert {"listings", "applications"} <= checked
    assert "dashboard" not in checked
    assert "Show in the navigation" in html


def test_the_stored_value_is_what_is_hidden_so_a_new_item_appears(client, user):
    """Recording the hidden ones is what makes an upgrade show its new item to everybody."""
    client.force_login(user)
    appearance(client, navigation=["dashboard"])
    user.profile.refresh_from_db()
    assert "dashboard" not in user.profile.hidden_nav_items
    assert "listings" in user.profile.hidden_nav_items

    # A person who has never touched the setting has nothing hidden at all.
    other = navigation.visible_items(None)
    assert [item.key for item in other] == list(navigation.HIDEABLE)


# ---------------------------------------------------------------- the wordmark


def test_the_wordmark_says_where_it_goes_once_dashboard_is_hidden(client, user):
    client.force_login(user)
    html = client.get(reverse("core:home")).content.decode()
    assert "— dashboard" not in html, "with the link there, the wordmark is just a name"

    keep = [key for key in navigation.HIDEABLE if key != "dashboard"]
    appearance(client, navigation=keep)

    html = client.get(reverse("core:home")).content.decode()
    assert 'aria-label="Postulo — dashboard"' in html
    assert "nav-link-active" in html, "and it carries the active style on the dashboard"

    # Somewhere else, it is a link like any other. Each item is in the page twice -- in the
    # line, and under *More* for the widths at which the line has no room for it, only ever
    # one of the two in the layout (#299) -- so counting the marker across the document
    # counts renderings rather than items. What has to hold is that one item is marked,
    # whatever it is rendered into.
    html = client.get(reverse("applications:list")).content.decode()
    assert 'aria-label="Postulo — dashboard"' in html
    marked = set(re.findall(r'nav-link-active[^>]*data-nav="([^"]+)"', html))
    assert marked == {"applications"}, f"only the page you are on is marked, got {marked}"
    # And marked for a screen reader as well as by the tint (#274), in both of its copies.
    assert re.search(r'nav-link-active[^>]*aria-current="page"', html)
    current = re.findall(r'data-nav="([^"]+)"[^>]*aria-current="page"', html)
    assert current == ["applications", "applications"], current
    assert html.count('aria-current="page"') == 2


def test_a_visitor_who_is_not_signed_in_sees_the_plain_wordmark(client):
    html = client.get(reverse("core:home")).content.decode()
    assert "— dashboard" not in html
    assert 'data-nav="dashboard"' not in html


# ------------------------------------------------------------------ the model


def test_the_items_are_the_navigation_and_nothing_else():
    assert navigation.ITEMS[0].key == "dashboard", "first, because it is the one to hide"
    assert set(navigation.BY_KEY) == set(navigation.HIDEABLE)
    for item in navigation.ITEMS:
        assert item.active_names[0] == item.url_name
    assert [key for key, _label in navigation.choices()] == list(navigation.HIDEABLE)
    assert navigation.DEFAULT_ORDER == navigation.HIDEABLE


def test_every_item_has_an_icon_from_the_bundled_set():
    """The row puts an icon beside each label and the bar puts one over it (#299). The
    `{% icon %}` tag raises on a name that is not bundled, so a missing one is every page."""
    from pathlib import Path

    icons = Path(__file__).resolve().parents[1] / "src" / "postulo" / "static" / "icons"
    for item in navigation.ITEMS:
        assert item.icon, f"{item.key} has no icon"
        assert (icons / f"{item.icon}.svg").is_file(), f"{item.icon} is not in the bundled set"
    assert (icons / "ellipsis.svg").is_file(), "More's own icon"


# ------------------------------------------------------ the order (#299)


class Stored:
    """A profile as far as the navigation reads one: two lists, whatever they hold."""

    def __init__(self, nav_order=None, hidden_nav_items=None):
        self.nav_order = nav_order
        self.hidden_nav_items = hidden_nav_items


def keys(items) -> list[str]:
    return [item.key for item in items]


def test_a_profile_with_no_order_reads_the_default_order():
    """Nothing was migrated: every profile that existed has an empty list, and empty is the
    order they have been reading all along."""
    assert keys(navigation.visible_items(Stored([], []))) == list(navigation.DEFAULT_ORDER)
    assert keys(navigation.visible_items(Stored(None, None))) == list(navigation.DEFAULT_ORDER)
    assert keys(navigation.visible_items(None)) == list(navigation.DEFAULT_ORDER)


def test_the_placed_keys_come_first_and_the_rest_follow_in_the_default_order():
    order = keys(navigation.visible_items(Stored(["calendar", "applications"], [])))
    assert order == [
        "calendar",
        "applications",
        "dashboard",
        "listings",
        "documents",
        "companies",
        "reminders",
    ]


def test_an_item_nobody_has_placed_yet_is_drawn_after_the_placed_ones():
    """The property `navigation.py` exists for: an item added in a later release is in
    neither list, and appears -- at the end of somebody's own order, in its own place for
    everybody who never arranged anything. An order stored before *Calendar* existed is
    exactly that."""
    before_calendar = [
        "reminders",
        "companies",
        "documents",
        "applications",
        "listings",
        "dashboard",
    ]
    order = keys(navigation.visible_items(Stored(before_calendar, [])))
    assert order == [*before_calendar, "calendar"]


def test_hidden_wins_over_placed_and_keeps_its_place_in_the_order():
    stored = Stored(["calendar", "applications", "listings"], ["applications"])
    assert keys(navigation.visible_items(stored))[:2] == ["calendar", "listings"]
    assert navigation.order_of(stored)[:3] == ["calendar", "applications", "listings"]


@pytest.mark.parametrize(
    "garbage",
    ["calendar", 5, {"calendar": 1}, ["no-such-item", "calendar", "calendar", 3, None]],
    ids=["a string", "a number", "a mapping", "unknown, repeated and not strings"],
)
def test_a_stored_list_is_believed_only_as_far_as_it_goes(garbage):
    """What is stored arrives from a form and from an archive somebody restored, and a
    navigation that raised would take every page down with it."""
    order = navigation.order_of(Stored(garbage, garbage))
    assert sorted(order) == sorted(navigation.DEFAULT_ORDER)
    assert len(order) == len(set(order))
    navigation.visible_items(Stored(garbage, garbage))
    navigation.dashboard_hidden(Stored(garbage, garbage))


def test_a_move_is_one_place_and_the_ends_are_the_ends():
    default = list(navigation.DEFAULT_ORDER)
    assert navigation.move(default, "calendar", "up")[-2:] == ["calendar", "reminders"]
    assert navigation.move(default, "dashboard", "down")[:2] == ["listings", "dashboard"]
    assert navigation.move(default, "dashboard", "up") == default
    assert navigation.move(default, "calendar", "down") == default
    assert navigation.move(default, "nothing", "up") == default
    assert navigation.move(default, "calendar", "sideways") == default
    assert not navigation.can_move(default, "dashboard", "up")
    assert navigation.can_move(default, "dashboard", "down")


def test_the_default_order_is_stored_as_nothing():
    """So that a later release which changes the default reaches everybody who never chose,
    including the person who only ever pressed Save."""
    assert navigation.to_store(list(navigation.DEFAULT_ORDER)) == []
    assert navigation.to_store([]) == []
    arranged = navigation.move([], "calendar", "up")
    assert navigation.to_store(arranged) == arranged
    assert len(navigation.to_store(arranged)) == len(navigation.DEFAULT_ORDER)


# ------------------------------------------------ arranging it in Settings (#299)


def arrange(client, move="", *, order=None, shown=None, **extra):
    values = {
        "theme": "system",
        "density": "comfortable",
        "quiet_after_days": 14,
        "navigation": list(navigation.HIDEABLE) if shown is None else shown,
        "nav_order": list(navigation.DEFAULT_ORDER) if order is None else order,
    }
    if move:
        values["nav_move"] = move
    values.update(extra)
    return client.post(reverse("settings:appearance"), values)


def line_order(html: str) -> list[str]:
    """The keys the masthead's line draws, in order: the `<a>` children of the nav, not the
    copies under *More*."""
    nav = html[html.index('<nav class="nav-main"') :]
    line = nav[: nav.index("<details")]
    return re.findall(r'data-nav="([^"]+)"', line)


def test_an_arrow_moves_one_item_saves_the_order_and_says_where_it_went(client, user):
    client.force_login(user)
    response = arrange(client, "up:calendar")

    assert response.status_code == 302
    # Back on the arrow that was pressed, so pressing it again moves the item again.
    assert response.url == reverse("settings:appearance") + "#nav-up-calendar"
    user.profile.refresh_from_db()
    assert user.profile.nav_order[-2:] == ["calendar", "reminders"]

    page = client.get(response.url).content.decode()
    assert "Calendar is now number 6 in the navigation." in page
    assert line_order(client.get(reverse("core:home")).content.decode())[-2:] == [
        "calendar",
        "reminders",
    ]


def test_an_item_that_reaches_the_end_hands_focus_to_its_row(client, user):
    """The arrow that brought it there is disabled now, and a disabled button cannot hold
    focus -- so the redirect lands on the row, which can."""
    client.force_login(user)
    response = arrange(client, "up:listings")
    assert response.url == reverse("settings:appearance") + "#nav-row-listings"
    page = client.get(response.url).content.decode()
    assert 'id="nav-row-listings" tabindex="-1"' in page


def test_a_move_saves_a_switch_changed_in_the_same_form(client, user):
    """The arrows are submit buttons of the page's one form, so nothing waiting to be saved
    is lost by pressing one."""
    client.force_login(user)
    shown = [key for key in navigation.HIDEABLE if key != "companies"]
    arrange(client, "down:dashboard", shown=shown)
    user.profile.refresh_from_db()
    assert user.profile.hidden_nav_items == ["companies"]
    assert user.profile.nav_order[:2] == ["listings", "dashboard"]


def test_an_arrow_that_cannot_act_changes_nothing(client, user):
    client.force_login(user)
    response = arrange(client, "up:dashboard")
    assert response.url == reverse("settings:appearance")
    user.profile.refresh_from_db()
    assert user.profile.nav_order == []
    assert "Saved." in client.get(response.url).content.decode()


def test_a_forged_move_or_order_is_passed_over(client, user):
    client.force_login(user)
    response = arrange(client, "up:no-such-item", order=["calendar", "nope", "calendar"])
    assert response.status_code == 302
    user.profile.refresh_from_db()
    assert user.profile.nav_order[0] == "calendar"
    assert sorted(user.profile.nav_order) == sorted(navigation.DEFAULT_ORDER)


def test_saving_without_arranging_stores_no_order(client, user):
    """A person who only changed the theme has not placed anything, and keeps getting
    whatever order a later release thinks is right."""
    client.force_login(user)
    arrange(client)
    user.profile.refresh_from_db()
    assert user.profile.nav_order == []


def test_a_post_without_the_order_keeps_the_one_stored(client, user):
    profile = user.profile
    profile.nav_order = navigation.move([], "calendar", "up")
    profile.save(update_fields=["nav_order"])
    client.force_login(user)
    arrange(client, order=[])
    profile.refresh_from_db()
    assert profile.nav_order[-2:] == ["calendar", "reminders"]


def test_back_to_the_usual_order(client, user):
    client.force_login(user)
    page = client.get(reverse("settings:appearance")).content.decode()
    assert "Back to the usual order" not in page, "nothing to go back to"

    arrange(client, "up:calendar")
    page = client.get(reverse("settings:appearance")).content.decode()
    assert "Back to the usual order" in page

    arrange(client, order=navigation.move([], "calendar", "up"), nav_reset="1")
    user.profile.refresh_from_db()
    assert user.profile.nav_order == []


def test_the_page_lists_every_item_in_the_persons_order_with_its_arrows(client, user):
    profile = user.profile
    profile.nav_order = ["calendar", *[k for k in navigation.DEFAULT_ORDER if k != "calendar"]]
    profile.hidden_nav_items = ["listings"]
    profile.save(update_fields=["nav_order", "hidden_nav_items"])
    client.force_login(user)
    page = client.get(reverse("settings:appearance")).content.decode()

    # Hidden items too: switched off, an item keeps its place for when it comes back.
    assert re.findall(r'name="nav_order" value="([^"]+)"', page) == profile.nav_order
    first_up = re.search(r'<button[^>]*id="nav-up-calendar"[^>]*>', page).group(0)
    last_down = re.search(r'<button[^>]*id="nav-down-reminders"[^>]*>', page).group(0)
    assert "disabled" in first_up and "disabled" in last_down
    assert "disabled" not in re.search(r'<button[^>]*id="nav-down-calendar"[^>]*>', page).group(0)
    assert 'aria-label="Move Calendar up"' in page and 'aria-label="Move Reminders down"' in page


def test_enter_in_a_number_box_presses_save_and_not_an_arrow(client, user):
    """The browser presses a form's first submit button when Enter is pressed in one of its
    boxes. The arrows come before *Save*, and the first of them is disabled on the first
    row -- which would make Enter do nothing at all -- so a Save comes first, for the
    browser's sake only."""
    client.force_login(user)
    page = client.get(reverse("settings:appearance")).content.decode()
    form = page[page.index('<form method="post" novalidate>') :]
    first = re.search(r"<button[^>]*>", form).group(0)
    assert 'type="submit"' in first and "name=" not in first
    assert 'tabindex="-1"' in first and 'aria-hidden="true"' in first


def test_a_refused_page_keeps_the_order_it_was_posted_in(client, user):
    client.force_login(user)
    posted = navigation.move([], "calendar", "up")
    response = arrange(client, order=posted, quiet_after_days=999)
    assert response.status_code == 200, "refused, and drawn again"
    page = response.content.decode()
    assert re.findall(r'name="nav_order" value="([^"]+)"', page) == posted


def test_the_arrangement_travels_with_the_account(user, other_user):
    """A preference is the account's, so it is in the archive and comes back from it: the
    order, and what was switched off, which had never travelled at all before (#299)."""
    import zipfile

    from postulo.core import export, importer

    profile = user.profile
    profile.nav_order = navigation.move([], "calendar", "up")
    profile.hidden_nav_items = ["companies"]
    profile.save(update_fields=["nav_order", "hidden_nav_items"])

    stored = export.build_document(user)["account"]["profile"]
    assert stored["nav_order"] == profile.nav_order
    assert stored["hidden_nav_items"] == ["companies"]

    importer.load(other_user, zipfile.ZipFile(export.write_archive(user)))
    restored = Profile.objects.get(user=other_user)
    assert restored.nav_order == profile.nav_order
    assert restored.hidden_nav_items == ["companies"]


def test_an_archive_that_lies_about_the_navigation_breaks_no_page(client, user, other_user):
    """The importer sets what the archive says. Whatever it says, the pages still draw."""
    import json
    import zipfile
    from io import BytesIO

    from postulo.core import export, importer

    document = export.build_document(user)
    document["account"]["profile"]["nav_order"] = {"calendar": "first"}
    document["account"]["profile"]["hidden_nav_items"] = "dashboard"
    buffer = BytesIO()
    with zipfile.ZipFile(buffer, "w") as archive:
        archive.writestr(export.MANIFEST_NAME, json.dumps(document, default=str))
    buffer.seek(0)
    importer.load(other_user, zipfile.ZipFile(buffer))

    client.force_login(other_user)
    assert client.get(reverse("core:home")).status_code == 200
    assert client.get(reverse("settings:appearance")).status_code == 200


# ------------------------------------------------------- the masthead's shape (#299)


def test_the_navigation_is_one_list_with_more_and_the_search(client, user):
    """One `<nav>`, drawn once: the row on a wide screen and the bar on a phone are the same
    element, so it has one name and one place in the reading order at every width."""
    client.force_login(user)
    html = client.get(reverse("core:home")).content.decode()
    assert html.count('<nav class="nav-main"') == 1
    assert html.count('aria-label="Main"') == 1

    nav = html[html.index('<nav class="nav-main"') : html.index("</nav>")]
    more = nav[nav.index("<details") :]
    # More is navigation, so a disclosure of rows and not a menu of actions (#262).
    assert 'class="popover" data-menu data-nav-more' in more
    assert 'role="menu"' not in more
    # Every item has a copy under More, and More carries the search, which a phone had none of.
    assert re.findall(r'data-nav="([^"]+)"', more) == list(navigation.DEFAULT_ORDER)
    assert f'href="{reverse("core:search")}"' in more
    # Nothing the account menu already holds.
    for elsewhere in ("accounts:profile", "settings:index", "account_logout"):
        assert f'href="{reverse(elsewhere)}"' not in nav


def test_the_masthead_reaches_the_search_at_every_width(client, user):
    """A box from `lg` up, and below it a link to the search page, which has a box of its own
    and works with scripts off (#299)."""
    client.force_login(user)
    html = client.get(reverse("core:home")).content.decode()
    header = html[html.index("<header") : html.index("</header>")]
    link = re.search(r"<a[^>]*data-search-link[^>]*>", header).group(0)
    assert f'href="{reverse("core:search")}"' in link and "lg:hidden" in link
    assert 'aria-label="Search"' in link, "an icon standing alone carries the name"
    assert re.search(r'<form[^>]*role="search"[^>]*class="hidden lg:block"', header)

    search = client.get(reverse("core:search")).content.decode()
    assert re.search(r'<input[^>]*id="search-q"[^>]*data-search-shortcut', search)


# ------------------------------------------- the underline on the current link (#289)


def current_link(html: str) -> str:
    """The masthead's link for the page being shown."""
    found = re.search(r"<a[^>]*nav-link-active[^>]*>", html)
    assert found, "no current navigation link on the page"
    return found.group(0)


def test_the_underline_is_on_by_default(client, user):
    """The default is what the conformance claim rests on, so it stays the marked one."""
    client.force_login(user)
    html = client.get(reverse("core:home")).content.decode()

    assert "data-nav-underline" not in html, "nothing to say while it is on"
    assert 'aria-current="page"' in current_link(html)
    assert Profile.objects.get(user=user).nav_underline is True


def test_turning_it_off_is_written_onto_the_body(client, user):
    profile = Profile.objects.get(user=user)
    profile.nav_underline = False
    profile.save(update_fields=["nav_underline"])
    client.force_login(user)

    html = client.get(reverse("core:home")).content.decode()

    assert 'data-nav-underline="off"' in html
    # The row is unchanged: only the stylesheet reads the attribute, and the link keeps
    # every other cue it had.
    link = current_link(html)
    assert "nav-link-active" in link and 'aria-current="page"' in link


def test_a_stranger_gets_the_default(client, db):
    """No profile to ask, and the pages a stranger sees keep the marked default."""
    assert "data-nav-underline" not in client.get(reverse("account_login")).content.decode()


def test_accessibility_offers_the_switch_and_saves_it(client, user):
    client.force_login(user)
    page = client.get(reverse("settings:accessibility")).content.decode()
    assert 'name="nav_underline"' in page and "Underline the page you are on" in page

    # A checkbox left unticked posts nothing at all, which is how "off" arrives.
    response = client.post(reverse("settings:accessibility"), {})
    assert response.status_code == 302
    assert Profile.objects.get(user=user).nav_underline is False

    response = client.post(reverse("settings:accessibility"), {"nav_underline": "on"})
    assert response.status_code == 302
    assert Profile.objects.get(user=user).nav_underline is True
