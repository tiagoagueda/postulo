"""One search box: the masthead's searches the page's table, or everything (#313).

*Companies* had two boxes on one screen -- the masthead's, which searched everything, and
the page's own, which narrowed the table -- and so did *Applications*. The masthead's box
does the page's job now on the three table pages, with a second button beside it that
searches everything, and every other page keeps the search over everything it had.

These read the markup: one box named `q` on each of those pages, in the masthead, whose form
goes to the page; the button that searches everything; the page's filter form and the column
filters still carrying the search; and a page without a table left alone. Whether typing
into the box narrows the table the way the page's own did is a question for a browser, and
`tests/e2e/test_site_search.py` asks it.
"""

from __future__ import annotations

import re
from html.parser import HTMLParser

import pytest
from django.urls import reverse

from postulo.accounts.models import Profile
from postulo.applications.models import Application, Status
from postulo.applications.tables import ApplicationsTable
from postulo.core import tables
from postulo.jobs.models import Company, JobPosting
from postulo.jobs.tables import ListingsTable

pytestmark = pytest.mark.django_db

HTMX = {"HTTP_HX_REQUEST": "true"}

#: The three pages whose table the masthead's box narrows, the id of each one's filter form,
#: the id of the element its swaps replace, and what the box is called there.
PAGES = {
    "companies": ("jobs:company_list", "company-filters", "companies-table", "Search companies"),
    "applications": (
        "applications:list",
        "application-filters",
        "applications-table",
        "Search applications",
    ),
    "listings": ("listings:list", "listing-filters", "listings-table", "Search listings"),
}


class Controls(HTMLParser):
    """Every input, select, button and form on a page, each with where it sits: `_header`
    inside the masthead, `_noscript` inside a `<noscript>`, and `_form` the form it is written
    in. And every link that asks htmx for something, for the ones that must not take a turn."""

    VOID = frozenset({"input", "img", "br", "hr", "meta", "link", "source", "col", "area", "wbr"})

    def __init__(self):
        super().__init__()
        self.stack: list[tuple[str, dict]] = []
        self.inputs: list[dict] = []
        self.buttons: list[dict] = []
        self.forms: list[dict] = []
        self.links: list[dict] = []
        self.search_landmarks = 0

    def _where(self) -> dict:
        tags = [tag for tag, _attrs in self.stack]
        forms = [attrs for tag, attrs in self.stack if tag == "form"]
        return {
            "_header": "header" in tags,
            "_noscript": "noscript" in tags,
            "_form": forms[-1] if forms else None,
        }

    def handle_starttag(self, tag, attrs):
        attrs = dict(attrs)
        if attrs.get("role") == "search" or tag == "search":
            self.search_landmarks += 1
        if tag in ("input", "select"):
            self.inputs.append({**attrs, **self._where()})
        elif tag == "button":
            self.buttons.append({**attrs, **self._where()})
        elif tag == "form":
            attrs = {**attrs, **self._where()}
            self.forms.append(attrs)
        elif tag == "a" and "hx-get" in attrs:
            self.links.append(attrs)
        if tag not in self.VOID:
            self.stack.append((tag, attrs))

    def handle_endtag(self, tag):
        for index in range(len(self.stack) - 1, -1, -1):
            if self.stack[index][0] == tag:
                del self.stack[index:]
                break


def controls(html: str) -> Controls:
    parser = Controls()
    parser.feed(html)
    return parser


def the_box(found: Controls) -> dict:
    return next(i for i in found.inputs if i.get("id") == "site-search")


def written_in(found: Controls, form: dict) -> list[dict]:
    return [i for i in found.inputs if i["_form"] is form]


@pytest.fixture
def rows(user):
    """Something on every one of the three pages: companies, an application, listings."""
    aperture = Company.objects.create(owner=user, name="Aperture Science", location="Cambridge")
    mesa = Company.objects.create(owner=user, name="Black Mesa", location="New Mexico")
    applied = JobPosting.objects.create(owner=user, company=aperture, title="Test Engineer")
    Application.objects.create(owner=user, posting=applied, status=Status.APPLIED)
    JobPosting.objects.create(owner=user, company=mesa, title="Research Associate")
    JobPosting.objects.create(
        owner=user, company=aperture, title="Portal Researcher", location="Lisbon"
    )
    return user


# ------------------------------------------------------------- the three table pages


@pytest.mark.parametrize("page", PAGES)
def test_each_table_page_has_one_box_named_q_and_it_is_the_mastheads(client, rows, page):
    """One search input named `q` on the page, in the masthead, whose form goes to the page
    itself -- so Enter with scripts off narrows the table -- and a second submit button that
    sends the same words to the search page instead."""
    url_name, _form_id, _table_id, label = PAGES[page]
    client.force_login(rows)
    path = reverse(url_name)
    found = controls(client.get(path).content.decode())

    boxes = [i for i in found.inputs if i.get("name") == "q" and i.get("type") == "search"]
    assert len(boxes) == 1, boxes
    box = boxes[0]
    assert box["_header"], "the page's own box is gone; the masthead's is the one"
    assert box["id"] == "site-search" and box["placeholder"] == label
    form = box["_form"]
    assert form["action"] == path and form["method"] == "get"
    assert form["role"] == "search" and form["aria-label"] == label
    assert found.search_landmarks == 1, "one search landmark on the page"

    submits = [b for b in found.buttons if b["_form"] is form and b.get("type") == "submit"]
    assert len(submits) == 2, submits
    here, everything = submits
    # The first is the form's default button, which is what Enter presses: the table's.
    assert "formaction" not in here and here["aria-label"] == label
    assert everything["formaction"] == reverse("core:search")
    assert everything["aria-label"] == "Search everything"


@pytest.mark.parametrize("page", PAGES)
def test_the_box_narrows_live_exactly_as_the_pages_own_did(client, rows, page):
    """The same parameter, the same target, the page's filter form included, the address
    pushed -- and the button Enter presses asks for the same, so Enter does not reload."""
    url_name, form_id, table_id, _label = PAGES[page]
    client.force_login(rows)
    path = reverse(url_name)
    found = controls(client.get(path).content.decode())
    box = the_box(found)

    assert box["hx-get"] == path
    assert box["hx-include"] == f"#{form_id}"
    assert box["hx-target"] == f"#{table_id}"
    assert box["hx-swap"] == "outerHTML" and box["hx-push-url"] == "true"
    assert "input changed" in box["hx-trigger"]
    assert "data-table-search" in box

    here = next(b for b in found.buttons if "data-search-here" in b)
    assert here["hx-get"] == path and here["hx-target"] == f"#{table_id}"
    assert here["hx-include"] == f"#site-search, #{form_id}"
    everything = next(b for b in found.buttons if "data-search-everything" in b)
    assert not any(name.startswith("hx-") for name in everything), "a page load, not a swap"


@pytest.mark.parametrize("page", PAGES)
def test_the_page_filter_form_and_its_columns_still_carry_the_search(client, rows, page):
    """The box is outside the page's filter form now, so the form and every column filter
    include it; and with scripts off the form carries the search in force as a hidden field
    inside a `<noscript>`, which a page with scripts running never sends twice."""
    url_name, form_id, _table_id, _label = PAGES[page]
    client.force_login(rows)
    found = controls(client.get(reverse(url_name), {"q": "aperture"}).content.decode())

    form = next(f for f in found.forms if f.get("id") == form_id)
    assert form["hx-include"] == "[data-table-search]"
    carried = [i for i in written_in(found, form) if i.get("name") == "q"]
    assert len(carried) == 1, carried
    assert carried[0]["type"] == "hidden" and carried[0]["value"] == "aperture"
    assert carried[0]["_noscript"]

    live = [i for i in found.inputs if i.get("form") == form_id and "hx-get" in i]
    assert live, "the column filters are drawn"
    for control in live:
        assert control["hx-include"] == f"#{form_id}, [data-table-search]", control


@pytest.mark.parametrize("page", PAGES)
def test_every_live_control_of_a_table_takes_its_turn_on_the_filter_form(client, rows, page):
    """The newest question wins. The box is outside the filter form, so htmx would give each
    its own queue, and a slow answer to one could land over a newer answer to the other; so
    the box, its button, the form and every column filter name one element to take turns on,
    the filter form, and a new request replaces the one in flight.

    The sort and the page links do not: their address was written with the last answer, and
    one of them replacing a newer request would put the older question back."""
    url_name, form_id, table_id, _label = PAGES[page]
    client.force_login(rows)
    found = controls(client.get(reverse(url_name), {"state": "all"}).content.decode())

    form = next(f for f in found.forms if f.get("id") == form_id)
    assert form["hx-sync"] == "this:replace"
    assert the_box(found)["hx-sync"] == f"#{form_id}:replace"
    here = next(b for b in found.buttons if "data-search-here" in b)
    assert here["hx-sync"] == f"#{form_id}:replace"

    live = [i for i in found.inputs if i.get("form") == form_id and "hx-get" in i]
    assert live, "the column filters are drawn"
    assert {i["hx-sync"] for i in live} == {f"#{form_id}:replace"}, live
    if page == "applications":
        # The status, the outcome and *Gone quiet* were controls written in the form, which
        # asked for them; each is a header's control now and asks for itself (#314). The tag
        # is the fourth, for somebody who has one: `tests/test_application_filters.py`.
        assert {"status", "state", "quiet"} <= {i["name"] for i in live}, live

    links = [a for a in found.links if a.get("hx-target") == f"#{table_id}"]
    assert links, "the sort links are drawn"
    assert not any("hx-sync" in a for a in links), links


@pytest.mark.parametrize("page", PAGES)
def test_the_form_the_box_takes_turns_on_is_there_when_nothing_matches(client, rows, page):
    """`hx-sync` names the filter form by id, and htmx stops at a name it cannot find: a
    page that drew the box for its table and left the form out would have a box that does
    nothing. The table's emptiest answer still has both."""
    url_name, form_id, _table_id, _label = PAGES[page]
    client.force_login(rows)
    found = controls(client.get(reverse(url_name), {"q": "zzzz"}).content.decode())
    assert the_box(found)["hx-sync"] == f"#{form_id}:replace"
    assert [f for f in found.forms if f.get("id") == form_id], "the filter form is drawn"


@pytest.mark.parametrize("page", PAGES)
def test_the_two_buttons_add_nothing_to_the_box_but_themselves(client, rows, page):
    """Basecoat's `data-align` puts an addon at one end of an input group and gives a button
    four pixels of margin for it. These two already follow the box in the markup, and the
    eight pixels were what put the masthead on two lines in English at 1280 under the
    text-spacing override with scripts off. And a placeholder that does not fit, which is
    most of them at rest, ends in an ellipsis rather than half a word."""
    url_name, _form_id, _table_id, _label = PAGES[page]
    client.force_login(rows)
    found = controls(client.get(reverse(url_name), {"state": "all"}).content.decode())
    form = the_box(found)["_form"]
    submits = [b for b in found.buttons if b["_form"] is form and b.get("type") == "submit"]
    assert len(submits) == 2
    assert not any("data-align" in b for b in submits), submits
    assert "text-ellipsis" in the_box(found)["class"].split()


@pytest.mark.parametrize("page", PAGES)
def test_a_search_of_any_length_is_answered(client, rows, page):
    """An address is not a form: anything can be typed into it. SQLite refuses a `LIKE`
    pattern of sixty thousand characters with an error, which was a 500 on all three pages;
    the search is read through `clean_query`, as the search over everything reads its own,
    which keeps two hundred characters and tidies the spaces between the words."""
    url_name, _form_id, table_id, _label = PAGES[page]
    client.force_login(rows)
    response = client.get(reverse(url_name), {"q": "a" * 60_000, "state": "all"})
    assert response.status_code == 200
    assert "Nothing matches these filters" in response.content.decode()

    # What is kept is the start of it, and the spaces around and between the words go.
    response = client.get(reverse(url_name), {"q": "  aperture   science ", "state": "all"})
    table = response.content.decode()
    table = table[table.index(f'id="{table_id}"') :]
    assert "Aperture Science" in table and "Black Mesa" not in table
    long = "aperture" + " " * 200 + "x" * 60_000
    response = client.get(reverse(url_name), {"q": long, "state": "all"})
    assert response.status_code == 200
    assert "Nothing matches these filters" in response.content.decode()


def test_search_everything_arrives_with_the_words_and_nothing_else(client, rows):
    """With scripts off, *Search everything* is a second button on the form that narrows the
    table, and that form carries the table's filters and sort so that Enter keeps them. They
    arrived at the search page too, which reads none of them, and stayed in its address:
    `/search/?q=unit&location=ohio&sort=-name`. The page sends them away."""
    client.force_login(rows)
    url = reverse("core:search")

    response = client.get(url, {"q": "aperture", "location": "ohio", "sort": "-name"})
    assert response.status_code == 302
    assert response["Location"] == f"{url}?q=aperture"
    assert client.get(response["Location"]).status_code == 200

    # No words at all is the bare page, and what was typed is tidied on the way.
    response = client.get(url, {"q": "", "location": "ohio"})
    assert (response.status_code, response["Location"]) == (302, url)
    response = client.get(url, {"q": " black   mesa ", "view": "board"})
    assert response["Location"] == f"{url}?q=black+mesa"

    # The words alone are answered where they are, as they always were.
    assert client.get(url, {"q": "aperture"}).status_code == 200
    assert client.get(url).status_code == 200


@pytest.mark.parametrize("page", PAGES)
def test_a_bookmark_with_q_fills_the_box_and_narrows_the_table(client, rows, page):
    """A bookmark, a link, a saved view: whatever carried `?q=` before still does."""
    url_name, _form_id, table_id, _label = PAGES[page]
    client.force_login(rows)
    html = client.get(reverse(url_name), {"q": "aperture", "state": "all"}).content.decode()
    assert the_box(controls(html))["value"] == "aperture"
    table = html[html.index(f'id="{table_id}"') :]
    assert "Clear filters" in html, "a search is a filter, and says so"
    assert "Black Mesa" not in table and "Research Associate" not in table


def test_a_saved_view_holding_q_opens_with_it_in_the_box(client, rows):
    """A view is a name for an address (#259); one kept with a search opens with the search
    in the masthead's box and the table narrowed by it."""
    from postulo.jobs.tables import CompaniesTable

    saved = CompaniesTable.save_view({}, "Portals", "q=aperture&sort=-postings", [])
    tables.save_settings(rows, CompaniesTable.name, CompaniesTable.make_default(saved, "portals"))
    client.force_login(rows)
    # The bare address opens as the default view, which is the view's own address.
    response = client.get(reverse("jobs:company_list"), follow=True)
    assert response.redirect_chain and "q=aperture" in response.redirect_chain[-1][0]
    body = response.content.decode()
    assert the_box(controls(body))["value"] == "aperture"
    assert "Black Mesa" not in body[body.index('id="companies-table"') :]


def test_with_scripts_off_enter_keeps_the_rest_of_the_question(client, rows):
    """The box's form carries every other parameter the page was asked with, so a search
    typed over a filtered, sorted table keeps the filter and the sort -- but not the page
    number, which a new search starts again from, nor the search it replaces, nor the name
    of a saved view the question will no longer be."""
    client.force_login(rows)
    query = {
        "q": "old",
        "location": "cam",
        "sort": "-postings",
        "page": "1",
        "saved": "mine",
        "name": "",
    }
    found = controls(client.get(reverse("jobs:company_list"), query).content.decode())
    hidden = [i for i in written_in(found, the_box(found)["_form"]) if i.get("type") == "hidden"]
    assert sorted((i["name"], i["value"]) for i in hidden) == [
        ("location", "cam"),
        ("sort", "-postings"),
    ]
    assert all(i["_noscript"] for i in hidden), "with scripts on, htmx includes the form instead"


def test_search_keeps_is_the_query_less_the_search_the_page_and_the_view(rf):
    request = rf.get("/", {"q": "x", "page": "3", "saved": "v", "status": "applied", "tag": ""})
    table = ApplicationsTable(request)
    assert table.search == "x"
    assert table.search_keeps == [("status", "applied")]


def test_the_board_is_narrowed_by_the_same_box(client, rows):
    """The applications page in its other shape: the same filters, the same element swapped,
    and the shape asked for by address kept by a search typed with scripts off."""
    client.force_login(rows)
    found = controls(client.get(reverse("applications:list"), {"view": "board"}).content.decode())
    box = the_box(found)
    assert box["hx-target"] == "#applications-table"
    assert box["_form"]["action"] == reverse("applications:list")
    kept = [(i["name"], i["value"]) for i in written_in(found, box["_form"]) if i["_noscript"]]
    assert ("view", "board") in kept


def test_the_key_is_said_where_single_keys_are_on(client, rows):
    """The placeholder is the box's name; the "/" the box over everything shows would be the
    first thing cut off at the width this one has, so the attribute says it instead."""
    client.force_login(rows)
    page = reverse("jobs:company_list")
    box = the_box(controls(client.get(page).content.decode()))
    assert box["aria-keyshortcuts"] == "/" and "data-search-shortcut" in box

    Profile.objects.filter(user=rows).update(keyboard_shortcuts=False)
    box = the_box(controls(client.get(page).content.decode()))
    assert "aria-keyshortcuts" not in box


def test_below_lg_the_magnifier_opens_the_same_form(client, rows):
    """On a phone the mode goes with the search: the icon that is a link to the search page
    elsewhere is the button that opens this box, in a popover the browser opens itself."""
    client.force_login(rows)
    html = client.get(reverse("jobs:company_list")).content.decode()
    header = html[html.index("<header") : html.index("</header>")]
    trigger = next(b for b in controls(header).buttons if "data-search-link" in b)
    assert trigger["popovertarget"] == "site-search-panel" and "lg:hidden" in trigger["class"]
    assert trigger["type"] == "button" and trigger["aria-label"] == "Search"
    panel = re.search(r'<div popover id="site-search-panel"[^>]*>', header).group(0)
    assert "data-site-search" in panel
    assert header.index("data-search-link") < header.index('id="site-search-panel"'), (
        "the panel follows its button, which is where Tab goes next"
    )


def test_a_swap_does_not_redraw_the_masthead(client, rows):
    """The box is outside what the live search replaces, so typing never loses its place."""
    client.force_login(rows)
    html = client.get(reverse("jobs:company_list"), {"q": "aperture"}, **HTMX).content.decode()
    assert "site-search" not in html and "<header" not in html
    assert 'id="companies-table"' in html and "Aperture Science" in html
    assert 'id="companies-count"' in html, "the count is swapped out of band, as it was"


# ------------------------------------------------------------------ Listings gains q


def test_listings_narrow_by_role_company_and_place(client, rows):
    """*Listings* had no search of its own; the masthead's box gives it one, over the three
    things a listing is recognised by -- the same three an application is searched by."""
    client.force_login(rows)
    url = reverse("listings:list")

    def titles(query: str) -> set[str]:
        response = client.get(url, {"q": query, "state": "all"})
        return {listing.title for listing in response.context["listings"]}

    assert titles("research") == {"Research Associate", "Portal Researcher"}
    assert titles("mesa") == {"Research Associate"}
    assert titles("lisbon") == {"Portal Researcher"}
    assert titles("") == {"Test Engineer", "Research Associate", "Portal Researcher"}
    assert "q" in ListingsTable.extra_params, "a search counts as a filter, for Clear"


def test_listings_search_is_one_persons(client, rows, other_user):
    theirs = Company.objects.create(owner=other_user, name="Research Corp")
    JobPosting.objects.create(owner=other_user, company=theirs, title="Research Lead")
    client.force_login(rows)
    response = client.get(reverse("listings:list"), {"q": "research", "state": "all"})
    assert {listing.title for listing in response.context["listings"]} == {
        "Research Associate",
        "Portal Researcher",
    }


# --------------------------------------------------------------- every other page


@pytest.mark.parametrize(
    "url_name", ["core:home", "core:search", "applications:calendar", "jobs:industry_list"]
)
def test_a_page_without_a_table_to_narrow_keeps_the_search_over_everything(client, rows, url_name):
    client.force_login(rows)
    found = controls(client.get(reverse(url_name)).content.decode())
    box = the_box(found)
    assert box["_header"] and box["_form"]["action"] == reverse("core:search")
    assert box["_form"]["aria-label"] == "Site search"
    assert "data-table-search" not in box and "hx-get" not in box
    assert not any("formaction" in b for b in found.buttons if b["_header"])


def test_listings_with_nothing_listed_keep_the_search_over_everything(client, user):
    """At no listings at all the page draws no table (#160), so there is nothing to narrow."""
    client.force_login(user)
    box = the_box(controls(client.get(reverse("listings:list")).content.decode()))
    assert box["_form"]["action"] == reverse("core:search")


def test_every_table_that_reads_q_names_its_box():
    """A table page whose masthead narrows it by `q` needs words for the box; one without
    them would draw a box with no name."""
    for table in tables.TABLES.values():
        if "q" in table.extra_params:
            assert table.search_label, table.name
