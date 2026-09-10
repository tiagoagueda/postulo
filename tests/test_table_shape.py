"""The parts of Dispatcharr's channel table Postulo had not got (#136).

> on companies listings, i like the sorting and filtered you implemented, but i would
> berform something like dispatcharr implemented on their channel listigs, check repo

Four were missing when the issue was written. Two arrived with their prerequisites —
selection with bulk actions (#134) and editing in the cell (#135) — and this is the other
two, which are small and were the honest reason to do them last rather than first.

**One thing was deliberately not taken.** Dispatcharr keeps the query in session storage and
the sort in memory: reload and the sort is gone, and a filtered view cannot be sent to
anybody. Postulo's sort and filters live in the URL because they are a *question*, and a
question should be shareable, bookmarkable and safe with the back button. That difference is
a decision, not a gap, and there is a test for it below.
"""

from __future__ import annotations

from pathlib import Path

import pytest
from django.urls import reverse

from postulo.accounts.models import Profile
from postulo.core import tables
from postulo.jobs.models import Company
from postulo.jobs.tables import CompaniesTable

pytestmark = pytest.mark.django_db

LIST = "jobs:company_list"


def table_for(params: dict | None = None, settings: dict | None = None) -> CompaniesTable:
    from django.test import RequestFactory

    request = RequestFactory().get("/jobs/companies/", params or {})
    return CompaniesTable(request, settings=settings or {})


def column(key: str):
    return next(c for c in CompaniesTable.columns if c.key == key)


# ------------------------------------------------------- sorting, in three states


def test_a_column_nobody_has_sorted_sorts_one_way_first():
    assert table_for().next_sort(column("postings")) == "-postings"


def test_the_second_click_sorts_the_other_way():
    assert table_for({"sort": "-postings"}).next_sort(column("postings")) == "postings"


def test_the_third_click_gives_up_and_goes_back():
    """There was no way to undo a sort except editing the address, which is a real gap
    however small the fix.
    """
    assert table_for({"sort": "postings"}).next_sort(column("postings")) is None


def test_giving_up_means_the_tables_own_order():
    assert table_for({"sort": ""}).sort == CompaniesTable.default_sort


def test_the_column_the_table_sorts_by_has_no_third_state():
    """There is nothing to go back *to*: the table's own order is that column ascending, so
    a third click would change nothing and pretending otherwise is worse than two states.
    """
    assert table_for().next_sort(column("name")) == "-name"
    assert table_for({"sort": "-name"}).next_sort(column("name")) == "name"
    assert table_for({"sort": "name"}).next_sort(column("name")) == "-name"


def test_the_header_says_what_a_click_would_do(client, user):
    """Three states means the arrow no longer says the whole of it: the third one takes the
    arrow away, and an arrow that disappears announces nothing.
    """
    Company.objects.create(owner=user, name="Aperture Science")
    client.force_login(user)

    html = client.get(reverse(LIST) + "?sort=postings").content.decode()

    assert "Stop sorting by postings" in html
    assert "Sort by applications, highest first" in html


def test_the_link_that_gives_up_drops_the_parameter(client, user):
    Company.objects.create(owner=user, name="Aperture Science")
    client.force_login(user)

    html = client.get(reverse(LIST) + "?sort=postings").content.decode()
    header = html.split('data-col="postings"')[1].split("</th>")[0]

    assert 'href="?"' in header, "no sort at all, which is the third state"


def test_a_sort_still_lives_in_the_address_rather_than_in_a_browser(client, user):
    """Theirs is in session storage, and this one is not. A table narrowed to *quiet
    applications at agencies* is a thing to bookmark.
    """
    Company.objects.create(owner=user, name="Aperture Science")
    client.force_login(user)

    response = client.get(reverse(LIST) + "?sort=-postings")

    assert response.context["table"].sort == "-postings"


# ------------------------------------------------------------- widths, as a preference


def test_a_column_sizes_itself_until_somebody_says_otherwise():
    assert table_for().width_of(column("name")) == 0


def test_a_stored_width_reaches_the_header(client, user):
    Company.objects.create(owner=user, name="Aperture Science")
    tables.save_settings(
        user, "companies", {"columns": ["name"], "page_size": 50, "widths": {"name": 320}}
    )
    client.force_login(user)

    html = client.get(reverse(LIST)).content.decode()

    assert 'data-col-width="320"' in html


def test_a_stored_width_is_never_written_as_a_style_attribute(client, user):
    """`style-src 'self'` refuses an inline style as firmly as it refuses an inline script.

    A width in a `style` attribute works perfectly in development, where the policy is not
    enforced, and is dropped by the browser on a real deployment -- so the column silently
    sizes itself and the preference appears not to save. The script that owns the handle
    applies the width instead, through the DOM, which the policy does not govern.
    """
    Company.objects.create(owner=user, name="Aperture Science")
    tables.save_settings(
        user, "companies", {"columns": ["name"], "page_size": 50, "widths": {"name": 320}}
    )
    client.force_login(user)

    html = client.get(reverse(LIST)).content.decode()

    assert "style=" not in html.split("<thead")[1].split("</thead>")[0]


def test_a_width_is_a_preference_rather_than_a_question(client, user):
    """It follows the person to every device rather than cluttering every link, which is
    where the line between the URL and the profile already was.
    """
    client.force_login(user)

    client.post(
        reverse("core:table_settings", args=["companies"]),
        {"order": ["name"], "show": ["name"], "width": "name", "px": "320", "next": reverse(LIST)},
    )

    stored = Profile.objects.get(user=user).table_settings["companies"]
    assert stored["widths"] == {"name": 320}


def test_a_width_can_be_given_back(client, user):
    """A column dragged too narrow once must not be too narrow for ever."""
    tables.save_settings(
        user, "companies", {"columns": ["name"], "page_size": 50, "widths": {"name": 320}}
    )
    client.force_login(user)

    client.post(
        reverse("core:table_settings", args=["companies"]),
        {"order": ["name"], "show": ["name"], "width": "name", "px": "0", "next": reverse(LIST)},
    )

    assert Profile.objects.get(user=user).table_settings["companies"]["widths"] == {}


@pytest.mark.parametrize(("asked", "kept"), [("1", 64), ("5000", 900), ("nonsense", None)])
def test_a_width_outside_what_a_column_may_be_is_brought_back_in(client, user, asked, kept):
    client.force_login(user)

    client.post(
        reverse("core:table_settings", args=["companies"]),
        {"order": ["name"], "show": ["name"], "width": "name", "px": asked, "next": reverse(LIST)},
    )

    widths = Profile.objects.get(user=user).table_settings["companies"]["widths"]
    assert widths.get("name") == kept


def test_a_width_for_a_column_that_is_not_there_is_dropped(client, user):
    client.force_login(user)

    client.post(
        reverse("core:table_settings", args=["companies"]),
        {
            "order": ["name"],
            "show": ["name"],
            "width": "owner__password",
            "px": "320",
            "next": reverse(LIST),
        },
    )

    assert Profile.objects.get(user=user).table_settings["companies"]["widths"] == {}


def test_a_stored_width_that_is_nonsense_is_ignored_rather_than_rendered():
    """An archive from another instance, or a hand-edited profile."""
    for stored in ({"name": "wide"}, {"name": 4}, {"name": 9000}, "not a map"):
        assert table_for(settings={"widths": stored}).width_of(column("name")) == 0


# ------------------------------------------------- the handle, added rather than drawn


def test_no_handle_is_drawn_by_the_template(client, user):
    """A width is a pointer gesture, so the handle is added by the script. An inert control
    is worse than a missing one — the same reason *Select all* is added rather than drawn.
    """
    Company.objects.create(owner=user, name="Aperture Science")
    client.force_login(user)

    html = client.get(reverse(LIST)).content.decode()

    assert "col-handle" not in html
    assert "data-col-settings" in html, "but the script is told where to save a width"


def test_the_handle_is_a_button_and_not_only_a_drag():
    """A control only a mouse can reach is a control half the people using this cannot."""
    source = Path("src/postulo/static/js/app.js").read_text(encoding="utf-8")
    block = source.split("resizing a column")[1].split("/* ---")[0]

    assert 'handle.type = "button"' in block
    assert "ArrowRight" in block and "ArrowLeft" in block
    assert '"Home"' in block, "and a way to let the column size itself again"
    assert "aria-label" in block


def test_the_handle_flips_with_the_reading_direction():
    source = Path("src/postulo/static/js/app.js").read_text(encoding="utf-8")
    block = source.split("resizing a column")[1].split("/* ---")[0]

    assert block.count('direction === "rtl"') >= 2, "the drag and the keys both"


def test_the_stylesheet_pins_the_handle_to_the_end_edge():
    css = Path("assets/css/app.css").read_text(encoding="utf-8")
    handle = css.split(".col-handle {")[1].split("}")[0]

    assert "end-0" in handle
    assert "focus-visible:outline" in handle, "and it is visible when it has focus"


# ---------------------------------------------------------- what was already here


def test_the_two_that_arrived_with_their_prerequisites_are_here(client, user):
    """Selection with bulk actions, and editing in the cell. Named rather than assumed, so
    that losing one is a failure rather than a quiet absence.
    """
    Company.objects.create(owner=user, name="Aperture Science")
    client.force_login(user)

    html = client.get(reverse(LIST)).content.decode()

    assert "data-bulk-row" in html, "selection"
    assert "data-cell-open" in html, "editing in the cell"


def test_row_dragging_is_not_here_and_is_not_meant_to_be():
    """A channel list has an order somebody chose; a company list has an order somebody
    *sorted*. Dragging a row in a sorted table means abandoning the sort or lying about it.
    """
    row = Path("src/postulo/templates/jobs/partials/company_row.html").read_text(encoding="utf-8")

    assert "draggable" not in row
