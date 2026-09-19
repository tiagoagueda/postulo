"""Keyboard, focus and headings — the accessibility failures axe cannot see (#227).

axe reads a document. It cannot press Tab and say where focus landed, it cannot tell that a
single letter fires an action with no way to switch it off, it cannot ask what a page looks
like in a high-contrast theme, and it will not say that the one `h1` on a page names the
sidebar rather than the page. Every one of those was true of Postulo and every page passed.

What is testable without a browser is testable here, and that is most of it: the markup that
carries the fix, the preference behind it, the compiled stylesheet, and the shape of the
headings on the pages that had them wrong. The two halves that are genuinely browser
behaviour — where focus goes after an htmx swap, and what a forced-colours theme paints —
are in `tests/e2e/test_keyboard_and_focus.py`.
"""

from __future__ import annotations

import datetime as dt
import re
from pathlib import Path

import pytest
from django.contrib.auth import get_user_model
from django.urls import reverse

from postulo.accounts.models import Profile

pytestmark = pytest.mark.django_db

ROOT = Path(__file__).resolve().parents[1]
APP_JS = (ROOT / "src" / "postulo" / "static" / "js" / "app.js").read_text(encoding="utf-8")
COMPILED_CSS = (ROOT / "src" / "postulo" / "static" / "css" / "app.css").read_text(encoding="utf-8")
SOURCE_CSS = (ROOT / "assets" / "css" / "app.css").read_text(encoding="utf-8")

User = get_user_model()
HEADING = re.compile(r"<h([1-6])[^>]*>(.*?)</h\1>", re.DOTALL | re.IGNORECASE)


def headings(html: str) -> list[tuple[int, str]]:
    """Every heading on the page, in document order, as (level, its words)."""
    found = []
    for level, inside in HEADING.findall(html):
        words = re.sub(r"<[^>]+>", " ", inside)
        found.append((int(level), " ".join(words.split())))
    return found


@pytest.fixture
def administrator(db):
    return User.objects.create_user(
        email="ada@example.org",
        password="a-fairly-long-password-42",
        username="ada",
        first_name="Ada",
        last_name="Min",
        is_staff=True,
        is_superuser=True,
    )


@pytest.fixture
def a_company(user):
    """One row, so the companies table draws a header rather than its empty state."""
    from postulo.jobs.models import Company

    return Company.objects.create(owner=user, name="Aperture Science")


@pytest.fixture
def board(user):
    """One application, in a company and a posting, so the board has a card on it."""
    from postulo.applications.models import Application, Status
    from postulo.jobs.models import Company, JobPosting

    company = Company.objects.create(owner=user, name="Black Mesa")
    posting = JobPosting.objects.create(owner=user, company=company, title="Research Engineer")
    return Application.objects.create(owner=user, posting=posting, status=Status.APPLIED)


# ------------------------------------------ 1. focus after a sort, a page or a theme change


def test_a_sort_link_carries_an_id_so_focus_comes_back(client, user, a_company):
    """htmx puts focus back after a swap only for an element that has an `id`, and these
    links swap themselves away. Without one, Enter on a column header dropped focus to the
    body and the next Tab started again at the skip link."""
    client.force_login(user)
    html = client.get(reverse("jobs:company_list")).content.decode()

    assert 'id="sort-name"' in html
    # And the id is on the thing that is focused, not on the cell around it.
    assert re.search(r'<a id="sort-name"[^>]*hx-get=', html)


def test_previous_and_next_carry_ids_so_focus_comes_back(client, user):
    from postulo.jobs.models import Company

    Company.objects.bulk_create(
        [Company(owner=user, name=f"Company {number:03d}") for number in range(60)]
    )
    client.force_login(user)
    html = client.get(reverse("jobs:company_list")).content.decode()

    assert 'id="page-next"' in html
    assert 'id="page-prev"' not in html, "there is no previous page from the first one"

    second = client.get(reverse("jobs:company_list"), {"page": 2}).content.decode()
    assert 'id="page-prev"' in second


def test_the_theme_button_carries_an_id_so_focus_comes_back(client, user):
    """The reply replaces the whole form, so the button needs a name htmx can find it by —
    otherwise switching theme from inside the account menu drops focus out of the menu."""
    client.force_login(user)
    html = client.get(reverse("core:home")).content.decode()

    assert 'id="theme-switch-button"' in html
    assert html.count('id="theme-switch-button"') == 1, "and only one of them on a page"


# ----------------------------------------------- 2. single-key shortcuts that can be turned off


def test_shortcuts_are_on_by_default_and_the_body_says_nothing(client, user):
    client.force_login(user)
    html = client.get(reverse("core:home")).content.decode()

    assert Profile.objects.get(user=user).keyboard_shortcuts is True
    assert "data-shortcuts" not in html


def test_turning_them_off_is_written_onto_the_body(client, user):
    profile = Profile.objects.get(user=user)
    profile.keyboard_shortcuts = False
    profile.save(update_fields=["keyboard_shortcuts"])

    client.force_login(user)
    html = client.get(reverse("core:home")).content.decode()

    assert 'data-shortcuts="off"' in html


def test_accessibility_offers_the_switch_and_saves_it(client, user):
    """Under *Settings -> Accessibility* since #281, where somebody who needs it will look."""
    client.force_login(user)
    page = client.get(reverse("settings:accessibility")).content.decode()
    assert 'name="keyboard_shortcuts"' in page
    assert "WCAG 2.2" in page and "wiki/Accessibility" in page, (
        "the page says what it is checked against"
    )

    # A checkbox left unticked posts nothing at all, which is how "off" arrives.
    response = client.post(reverse("settings:accessibility"), {})

    assert response.status_code == 302
    assert Profile.objects.get(user=user).keyboard_shortcuts is False


def test_the_script_asks_the_body_before_acting_on_a_single_key(client, user):
    """The gate is one function, and both single-key handlers go through it. `isComposing`
    as well: while an input method is open every keystroke belongs to the character being
    built, not to this page."""
    assert 'dataset.shortcuts !== "off"' in APP_JS
    assert APP_JS.count("singleKeysAllowed()") >= 2, "the capture keys and the search key"
    assert "isComposing" in APP_JS


def test_the_search_box_stops_advertising_a_key_that_does_nothing(client, user):
    client.force_login(user)
    assert "Search  /" in client.get(reverse("core:home")).content.decode()

    profile = Profile.objects.get(user=user)
    profile.keyboard_shortcuts = False
    profile.save(update_fields=["keyboard_shortcuts"])
    html = client.get(reverse("core:home")).content.decode()

    assert "Search  /" not in html
    assert 'placeholder="Search"' in html


def test_the_capture_review_says_which_keys_it_actually_has(client, user):
    from postulo.jobs.models import Capture

    capture = Capture.objects.create(
        owner=user,
        url="https://example.org/research-engineer",
        data={"title": "Research Engineer", "company_name": "Black Mesa"},
    )
    client.force_login(user)
    url = reverse("jobs:capture_review", args=[capture.pk])

    assert "<kbd>d</kbd>" in client.get(url).content.decode()

    profile = Profile.objects.get(user=user)
    profile.keyboard_shortcuts = False
    profile.save(update_fields=["keyboard_shortcuts"])
    html = client.get(url).content.decode()

    assert "<kbd>d</kbd>" not in html
    assert "<kbd>Ctrl</kbd>" in html, "a shortcut with a modifier is never switched off"


# --------------------------------------------- 3. a focus indicator in forced colours


def test_a_field_no_longer_throws_its_focus_outline_away():
    """`outline-none` compiles to `outline-style: none` and beat the base `:focus-visible`
    rule, leaving a border colour and a ring — both of which forced colours discard."""
    assert "focus:outline-none" not in SOURCE_CSS
    assert "focus:outline-hidden" in SOURCE_CSS


def test_the_stylesheet_says_where_focus_is_in_forced_colours():
    assert "forced-colors" in COMPILED_CSS, "the compiled sheet, or the page never sees it"
    assert "Highlight" in COMPILED_CSS


# ----------------------------------------------------------- 4. the board's status menus


def test_each_card_names_the_application_its_menu_belongs_to(client, user, board):
    client.force_login(user)
    html = client.get(reverse("applications:list"), {"view": "board"}).content.decode()

    assert 'aria-label="Change status"' not in html, "thirty controls with one name between them"
    assert "Status of Research Engineer at Black Mesa" in html


def test_the_move_help_hangs_off_the_menu_rather_than_the_card(client, user, board):
    """It was on the `<article>`, which nothing can focus, so it was never announced."""
    client.force_login(user)
    html = client.get(reverse("applications:list"), {"view": "board"}).content.decode()

    assert 'id="board-move-help"' in html
    assert "board-drag-help" not in html
    assert re.search(r'<select[^>]*aria-describedby="board-move-help"', html)


def test_nothing_on_the_board_is_draggable_without_a_script(client, user, board):
    """The rule the dashboard's rows are already held to: an affordance that does nothing
    is worse than none, so `draggable` is the script's to add."""
    client.force_login(user)
    html = client.get(reverse("applications:list"), {"view": "board"}).content.decode()

    assert "draggable" not in html
    assert "data-card=" in html, "but the script is told what to make draggable"
    assert "readyBoardCards" in APP_JS


def test_a_status_chosen_with_the_keyboard_is_not_saved_until_it_is_finished():
    """Arrowing through a closed select in Chromium fires `change` at every step, and every
    one of those was a status change and a timeline entry for a status nobody chose."""
    assert "autosubmitChoosing" in APP_JS
    assert 'document.addEventListener("focusout"' in APP_JS, "leaving the menu commits it"
    assert "commitAutosubmit" in APP_JS


# -------------------------------------------------------------- 5. one h1, naming the page

SETTINGS_PAGES = [
    ("settings:appearance", "Appearance"),
    ("settings:locale", "Language and time"),
    ("settings:account", "Account"),
    ("settings:plugins", "Plugins"),
    ("connections:list", "Connections"),
    ("api:token_list", "API tokens"),
    ("core:export", "Export everything"),
    ("core:import_csv", "Import from a spreadsheet"),
]

SERVER_PAGES = [
    ("server:overview", "Overview"),
    ("server:people", "People"),
    ("server:signin", "Sign-in"),
    ("server:email", "Email"),
    ("server:plugins", "Plugins"),
    ("server:capture", "Capture"),
    ("server:logs", "Logs"),
    ("server:defaults", "Defaults"),
    ("accounts:invite_list", "Invitations"),
]


def assert_headings_are_sound(html: str, name: str) -> None:
    found = headings(html)
    firsts = [words for level, words in found if level == 1]
    assert len(firsts) == 1, f"exactly one h1, found {firsts}"
    assert firsts[0] == name, f"and it names the page, not the sidebar: {firsts[0]!r}"
    assert found[0][0] == 1, f"and it comes first: {found[:2]}"

    # No level skipped on the way down, which is what promoting the title would have caused
    # had the sections below it been left where they were.
    previous = 1
    for level, words in found:
        assert level <= previous + 1, f"{words!r} is an h{level} under an h{previous}"
        previous = level


@pytest.mark.parametrize(("url_name", "name"), SETTINGS_PAGES)
def test_a_settings_page_is_named_by_its_own_h1(client, user, url_name, name):
    client.force_login(user)
    response = client.get(reverse(url_name))

    assert response.status_code == 200
    assert_headings_are_sound(response.content.decode(), name)


@pytest.mark.parametrize(("url_name", "name"), SERVER_PAGES)
def test_a_server_settings_page_is_named_by_its_own_h1(client, administrator, url_name, name):
    client.force_login(administrator)
    response = client.get(reverse(url_name))

    assert response.status_code == 200
    assert_headings_are_sound(response.content.decode(), name)


def test_the_sidebar_label_is_no_longer_a_heading(client, user):
    """It was the `h1` on about twenty pages, so jumping to the first heading said
    "Settings" wherever you were."""
    client.force_login(user)
    html = client.get(reverse("settings:appearance")).content.decode()

    assert "<h1" in html
    assert not re.search(r"<h1[^>]*>\s*Settings\s*</h1>", html)
    assert 'aria-label="Settings sections"' in html, "the list is still named for a reader"


# ------------------------------------------------------------------- 6. column resizing


def test_the_resize_handle_is_named_for_what_it_is(client, user, a_company):
    """ "Widen Name" was a lie half the time: ArrowLeft narrows it."""
    client.force_login(user)
    html = client.get(reverse("jobs:company_list")).content.decode()

    assert "data-col-wider" not in html
    assert "Widen" not in html
    assert 'data-col-resize="Width of {column}"' in html
    assert "data-col-said" in html, "and an arrow press has words to say"


def test_the_handle_is_a_splitter_that_reports_its_width():
    assert 'setAttribute("role", "separator")' in APP_JS
    assert "aria-valuenow" in APP_JS and "aria-valuemin" in APP_JS and "aria-valuemax" in APP_JS


def test_what_a_resize_says_is_not_the_table_s_name():
    """It was a `<caption>` inserted into the table, and a caption *is* the accessible name:
    announcing a width renamed the table to it."""
    assert 'createElement("caption")' not in APP_JS
    assert 'live.dataset.colLive = ""' in APP_JS
    assert "table.nextSibling" in APP_JS, "a sibling of the table, not a child of it"


def test_a_drag_the_browser_takes_away_is_handled():
    """Without it the handle stayed in `dragging` for ever and the width was never saved."""
    assert 'addEventListener("pointercancel"' in APP_JS


# --------------------------------------------------- 7. destructive actions, said once


def test_there_is_one_way_to_draw_a_destructive_button():
    """Two shapes, `destructive` and `destructive-ghost`, painted once in the style pack
    (#262) and never composed by hand from a red and a white in a template."""
    templates = ROOT / "src" / "postulo" / "templates"
    offenders = [
        path.relative_to(ROOT).as_posix()
        for path in templates.rglob("*.html")
        if "bg-red-600 text-white" in path.read_text(encoding="utf-8")
        or "btn text-red-600" in path.read_text(encoding="utf-8")
    ]

    assert offenders == [], "these hand-roll a danger button instead of using the component"
    style_pack = (ROOT / "assets" / "css" / "basecoat.css").read_text(encoding="utf-8")
    assert '.btn[data-variant="destructive"] {' in style_pack
    assert '.btn[data-variant="destructive-ghost"] {' in style_pack


def test_cancel_goes_where_the_view_says_not_where_the_browser_came_from(client, user):
    from postulo.jobs.models import Company

    company = Company.objects.create(owner=user, name="Aperture Science")
    client.force_login(user)
    response = client.get(
        reverse("jobs:company_delete", args=[company.pk]),
        headers={"referer": "http://testserver/somewhere/else/"},
    )
    html = response.content.decode()

    assert response.context["cancel_url"] == company.get_absolute_url()
    assert f'href="{company.get_absolute_url()}"' in html
    assert "HTTP_REFERER" not in html and "/somewhere/else/" not in html


def test_cancel_falls_back_to_where_deleting_would_have_gone(client, user):
    """A career entry has no page of its own; the overview it lives on is the next best
    answer, and the view already names it."""
    from django.contrib.contenttypes.models import ContentType

    from postulo.documents.models import CV, CVItem
    from postulo.resume.models import Experience

    cv = CV.objects.create(owner=user, name="Main CV")
    experience = Experience.objects.create(
        owner=user,
        role="Engineer",
        organisation="Black Mesa",
        start_date=dt.date(2020, 1, 1),
    )
    item = CVItem.objects.create(
        owner=user,
        cv=cv,
        content_type=ContentType.objects.get_for_model(Experience),
        object_id=experience.pk,
        order=0,
    )
    client.force_login(user)
    response = client.get(reverse("documents:cv_item_delete", args=[item.pk]))

    assert response.context["cancel_url"] == cv.get_absolute_url()
