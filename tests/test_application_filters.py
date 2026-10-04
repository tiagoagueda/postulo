"""Applications: the filters above the table are in its column headers (#314).

*Status*, *Outcome*, *Tag* and *Gone quiet* were a form above the table, which the board
shared. The table narrows from its headers (#253), so the four moved there: the status, the
outcome and *Gone quiet* into the status column's header, the tag into the tags column's --
or the role's, while the tags are drawn under the role. The board has no headers and keeps
the form, less the status since #315: its columns fold to one instead.

What is held here:

* **nothing above the table**: no visible control between the page's heading and the table,
  at the widths the headers are the control;
* **every filter in a header**, belonging to the page's filter form, live, and taking its
  turn on that form like every other live control of a table (#313);
* **a filter whose column is hidden is still reachable**, under *Narrow*, which is then on
  the page at every width, comes back open while such a filter is in use, and whose value
  the headers' form carries;
* **the address means what it meant**: a list of addresses, each answered with the rows the
  queryset gives for it, in both shapes, as a page and as a fragment -- and a saved view
  holding each parameter still applies it;
* **the three faults of the neighbours** are not repeated for these four: a name is posted
  once (#622), the header whose control asked comes back open (#626), and a table with
  nothing in it still carries them (#647);
* **the board's form** is the controls it had, less the status (#315);
* **what a header says is what the view did**: a control reads its parameter as the view
  reads it, the header cell is called by the column's name and not by everything in it,
  and its text starts at the start of the cell;
* **the word *Narrow* says how many filters are in use** under it, at the width it is read.

Whether any of it can be used -- with a keyboard, with scripts off, on a phone -- is a
question for a browser, and `tests/e2e/test_application_filters.py` asks it.
"""

from __future__ import annotations

import datetime as dt

import pytest
from django.urls import reverse
from django.utils import timezone

from postulo.applications import quiet
from postulo.applications.models import BOARD_STATUSES, Application, Status
from postulo.applications.services import change_status
from postulo.applications.tables import ApplicationsTable
from postulo.core import tables
from postulo.core.models import Tag
from postulo.core.tables import Column, ExtraFilter, Table
from postulo.jobs.models import Company, JobPosting
from postulo.jobs.tables import CompaniesTable
from tests.test_site_search import Controls, controls
from tests.test_tables import HTMX, NARROW, form_of, head_of, header_cell, posted_by

pytestmark = pytest.mark.django_db

LIST = "applications:list"
FORM = "application-filters"

#: The four, by the parameter each has always been.
FOUR = ("status", "state", "tag", "quiet")


@pytest.fixture
def search(user):
    """Six applications at two companies: every status the filters tell apart, two tags, one
    gone quiet, one settled."""
    aperture = Company.objects.create(owner=user, name="Aperture Science", location="Cambridge")
    mesa = Company.objects.create(owner=user, name="Black Mesa", location="New Mexico")
    remote, dream = Tag.named(user, ["Remote", "Dream job"])
    made = {}
    for company, title, status, days_ago, labels in [
        (aperture, "Test Engineer", Status.APPLIED, 40, [remote]),
        (aperture, "Portal Researcher", Status.INTERVIEWING, 10, [dream]),
        (aperture, "Enrichment Associate", Status.APPLIED, 2, [remote, dream]),
        (mesa, "Research Associate", Status.REJECTED, 20, [remote]),
        (mesa, "Resonance Technician", Status.DRAFT, 1, []),
        (mesa, "Security Guard", Status.WITHDRAWN, 50, []),
    ]:
        posting = JobPosting.objects.create(owner=user, company=company, title=title)
        application = Application.objects.create(owner=user, posting=posting, status=Status.DRAFT)
        when = timezone.now() - dt.timedelta(days=days_ago)
        if status != Status.DRAFT:
            change_status(application, Status.APPLIED, occurred_at=when)
        if status not in (Status.DRAFT, Status.APPLIED):
            change_status(application, status, occurred_at=when)
        application.tags.set(labels)
        made[title] = application
    return made


def show(user, *columns: str) -> None:
    tables.save_settings(user, "applications", {"columns": list(columns)})


def page(client, user, query: str = "", **extra) -> str:
    client.force_login(user)
    return client.get(f"{reverse(LIST)}?{query}", **extra).content.decode()


def named(found: Controls, name: str, form: str = FORM) -> list[dict]:
    """The controls of one name that a form posts, the hidden ones included."""
    return [c for c in found.inputs if c.get("name") == name and form_of(c) == form]


# ---------------------------------------------------------- nothing above the table


def test_there_is_no_filter_form_above_the_applications_table(client, user, search):
    """The page's filter form is still on the page, because the headers' controls belong to
    it and every live control takes its turn on it -- but nothing is written in it that a
    person can see or use: the search in force, for scripts off, and that is all. And the
    *Narrow* block, which is above the table, is hidden wherever the headers are shown."""
    body = page(client, user, "q=a")
    found = controls(body)
    form = next(f for f in found.forms if f.get("id") == FORM)

    written = [c for c in found.inputs if c["_form"] is form]
    assert [(c["name"], c["type"], c["_noscript"]) for c in written] == [("q", "hidden", True)]
    assert not [b for b in found.buttons if b["_form"] is form], "and no button of its own"
    assert "class" not in form, "nothing to lay out"

    narrow = next(f for f in found.forms if "data-narrow-form" in f)
    assert narrow["class"].split() == ["mb-4", "md:hidden"]

    # What used to be drawn there is drawn once, and not there.
    assert body.count('id="filter-status"') == 1 and body.count('id="filter-state"') == 1
    before_the_table = body[body.index("<main") : body.index('id="applications-table"')]
    for control in ('id="filter-status"', 'id="filter-state"', 'id="filter-tag"', 'name="quiet"'):
        assert control not in before_the_table, control


def test_a_shape_asked_for_by_address_still_travels_with_the_filters(client, user, search):
    found = controls(page(client, user, "view=table"))
    assert [(c["type"], c["value"]) for c in named(found, "view")] == [("hidden", "table")]
    assert not named(controls(page(client, user)), "view")


# ------------------------------------------------------------ every filter in a header


def test_the_status_header_holds_the_status_the_outcome_and_gone_quiet(client, user, search):
    body = page(client, user)
    cell = header_cell(body, "status")
    found = controls(cell)

    assert [c["name"] for c in found.inputs] == ["status", "state", "quiet"]
    status, state, gone_quiet = found.inputs
    assert status["id"] == "filter-status" and state["id"] == "filter-state"
    assert gone_quiet["id"] == "filter-quiet" and gone_quiet["type"] == "checkbox"
    assert gone_quiet["value"] == "1" and "checked" not in gone_quiet

    # The first list sits under the column's own name and is named by it; the second asks
    # something else and has its word written above it; the tick box has its word beside it.
    assert status["aria-label"] == "Filter by Status"
    assert "aria-label" not in state and '<label for="filter-state"' in cell
    assert ">Outcome</label>" in cell and '<label for="filter-status"' not in cell
    assert "Gone quiet" in cell.split('id="filter-quiet"')[1].split("</label>")[0]

    options = cell.split('id="filter-status"')[1].split("</select>")[0]
    assert [value for value, _label in Status.choices] == [
        part.split('"')[0] for part in options.split('<option value="')[2:]
    ], "every status, in their order, after Any"
    outcome = cell.split('id="filter-state"')[1].split("</select>")[0]
    assert 'value="open"' in outcome and "Still live" in outcome
    assert 'value="closed"' in outcome and "Settled" in outcome


def test_the_tag_is_in_the_header_of_the_column_that_shows_the_tags(client, user, search):
    """The tags column is off until somebody adds it, and the tags are drawn under the role
    until then: so that is where the tag filter is. With the tags column on, it is there."""
    body = page(client, user)
    role = controls(header_cell(body, "role"))
    assert [c["name"] for c in role.inputs] == ["role", "tag"]
    assert role.inputs[0]["aria-label"] == "Filter by Role"
    assert '<label for="filter-tag"' in header_cell(body, "role")
    options = header_cell(body, "role").split('id="filter-tag"')[1].split("</select>")[0]
    assert 'value="dream-job"' in options and 'value="remote"' in options

    show(user, "role", "status", "tags")
    body = page(client, user)
    assert [c["name"] for c in controls(header_cell(body, "role")).inputs] == ["role"]
    tags = controls(header_cell(body, "tags"))
    assert [c["name"] for c in tags.inputs] == ["tag"]
    assert "<details" in header_cell(body, "tags"), "a column with no filter of its own opens"


def test_somebody_with_no_tags_is_offered_no_tag_filter(client, user):
    posting = JobPosting.objects.create(
        owner=user, company=Company.objects.create(owner=user, name="Aperture"), title="Tester"
    )
    Application.objects.create(owner=user, posting=posting, status=Status.APPLIED)
    body = page(client, user)
    assert 'name="tag"' not in body and "filter-tag" not in body
    assert [c["name"] for c in controls(header_cell(body, "role")).inputs] == ["role"]


def test_another_persons_tags_are_not_offered(client, user, other_user, search):
    Tag.named(other_user, ["Theirs"])
    assert "Theirs" not in page(client, user) and 'value="theirs"' not in page(client, user)


def test_every_new_live_control_takes_its_turn_on_the_filter_form(client, user, search):
    """As #313 set out: one element, the filter form, and a new request replaces the one in
    flight -- so a slow answer to the status never lands over a newer answer to the search.
    Each sends the whole form and the masthead's box, and swaps the table at its address."""
    found = controls(page(client, user))
    path = reverse(LIST)
    for name in FOUR:
        (control,) = [c for c in named(found, name) if c.get("type") != "hidden"]
        assert control["form"] == FORM, name
        assert control["hx-sync"] == f"#{FORM}:replace", name
        assert control["hx-include"] == f"#{FORM}, [data-table-search]", name
        assert control["hx-get"] == path and control["hx-trigger"] == "change", name
        assert control["hx-target"] == "#applications-table", name
        assert control["hx-swap"] == "outerHTML" and control["hx-push-url"] == "true", name
        assert "hx-preserve" not in control, "replaced, and refocused by its id"


def test_a_header_shows_what_the_address_asked(client, user, search):
    body = page(client, user, "status=rejected&state=closed&tag=remote&quiet=1&sort=role")
    status = header_cell(body, "status")
    assert 'value="rejected" selected' in status and 'value="closed" selected' in status
    assert 'name="quiet" value="1" checked' in status
    assert "data-col-filter open" in status and 'aria-label="Status is filtered"' in status
    role = header_cell(body, "role")
    assert 'value="remote" selected' in role
    assert "data-col-filter open" in role and 'aria-label="Role is filtered"' in role
    assert "data-col-filter open" not in header_cell(body, "company")


# -------------------------------------------------- with scripts off: the header's button


def test_each_header_ends_in_a_button_that_sends_the_form_with_scripts_off(client, user, search):
    """There is no form above the table for a button to sit in, so each header's panel ends
    in one, inside a `<noscript>`: with scripts on every control narrows as it changes."""
    body = page(client, user)
    found = controls(body)
    buttons = [b for b in found.buttons if b.get("form") == FORM]
    opened = body.count("data-col-filter")
    assert opened == 5, "role, company, location, status, applied"
    assert len(buttons) == opened
    for button in buttons:
        assert button["type"] == "submit" and button["_noscript"]
    assert body.count('data-size="xs">Filter</button>') == opened

    # A page with a form of its own above its table keeps its button there, from `md` up.
    # Below it that form is not drawn (#622), so there each panel ends in the same small
    # button, with the form's word on it, and from `md` up none of them is drawn.
    client.force_login(user)
    listed = client.get(reverse("jobs:company_list")).content.decode()
    companies = controls(listed)
    in_headers = [b for b in companies.buttons if b.get("form") == "company-filters"]
    assert len(in_headers) == listed.count("data-col-filter") > 0
    for button in in_headers:
        assert button["type"] == "submit" and button["_noscript"]
        assert "md:hidden" in button["class"].split()
    assert head_of(listed).count('data-size="xs">Apply</button>') == len(in_headers)
    for button in buttons:
        assert "md:hidden" not in button["class"].split(), "the only one there is, at any width"


# ------------------------------------------------- a filter whose column is not showing


def test_a_filter_whose_column_is_hidden_is_under_narrow_at_every_width(client, user, search):
    """A person who takes the status column off the table can still ask for a status. The
    three it held are in the *Narrow* block, which is then shown where the headers are too,
    holding those alone; the rest of its fields stay a phone's."""
    show(user, "role", "company", "applied")
    body = page(client, user)
    found = controls(body)
    narrow = next(f for f in found.forms if "data-narrow-form" in f)
    assert narrow["class"].split() == ["mb-4"], "not hidden above md"
    assert "<details data-narrow >" in body, "folded: nothing in it is in use"

    for name in ("status", "state", "quiet"):
        (control,) = named(found, name, NARROW)
        assert control["id"] == f"filter-{name}-narrow" and "hx-get" not in control
        assert not named(found, name), "and no header holds it"
    panel = body.split("data-narrow ")[1].split("</details>")[0]
    wrappers = {
        name: panel.split(f'id="filter-{name}-narrow"')[0].rsplit("<div", 1)[1].split(">")[0]
        for name in ("status", "state", "role", "company", "tag")
    }
    assert "md:hidden" not in wrappers["status"] and "md:hidden" not in wrappers["state"]
    assert "md:hidden" in wrappers["role"] and "md:hidden" in wrappers["company"]
    assert "md:hidden" in wrappers["tag"], "the tag is in the role's header"

    # The tag goes the same way once neither of its columns is showing.
    show(user, "company", "status")
    found = controls(page(client, user))
    assert not named(found, "tag") and len(named(found, "tag", NARROW)) == 1


def test_a_filter_in_use_on_a_hidden_column_is_on_screen_and_is_carried(client, user, search):
    """Open, holding what it narrows by, so it can be seen and changed; and the headers'
    form carries it hidden, because its control belongs to the *Narrow* form and a header's
    control would otherwise ask for the table without it."""
    show(user, "role", "company", "applied")
    for extra in ({}, HTMX):
        body = page(client, user, "status=rejected&quiet=1&company=mesa", **extra)
        found = controls(body)
        assert "<details data-narrow open>" in body
        (status,) = named(found, "status", NARROW)
        chosen = body.split('id="filter-status-narrow"')[1].split("</select>")[0]
        assert 'value="rejected" selected' in chosen and "hx-get" not in status
        assert "checked" in named(found, "quiet", NARROW)[0]

        carried = {c["name"]: c for c in found.inputs if c.get("form") == FORM}
        assert carried["status"]["type"] == "hidden" and carried["status"]["value"] == "rejected"
        assert carried["quiet"]["type"] == "hidden" and carried["quiet"]["value"] == "1"
        assert "state" not in carried, "nothing is carried for a filter that is not in use"
        assert body.index('name="sort"') < body.index('name="status" value="rejected" form=')

    client.force_login(user)
    response = client.get(reverse(LIST), {"status": "rejected"})
    assert [a.posting.title for a in response.context["applications"]] == ["Research Associate"]


def test_a_hidden_columns_own_filter_in_force_is_shown_too(client, user, search):
    """`?priority=3` narrowed the table with the priority column off and nothing on the page
    to say so, and the next request dropped it. It is under *Narrow* while it is in force,
    and carried; a hidden column's filter that is not in use is not offered -- showing the
    column is how it is asked for."""
    body = page(client, user, "priority=3")
    found = controls(body)
    assert "<details data-narrow open>" in body
    (priority,) = named(found, "priority", NARROW)
    assert priority["id"] == "filter-priority-narrow"
    (carried,) = named(found, "priority")
    assert carried["type"] == "hidden" and carried["value"] == "3"
    assert not named(found, "channel", NARROW) and not named(found, "deadline_from", NARROW)

    found = controls(page(client, user))
    assert not named(found, "priority", NARROW) and not named(found, "priority")

    # The same on a table that has no questions of its own to place.
    client.force_login(user)
    url = reverse("jobs:company_list")
    companies = controls(client.get(url, {"kind": "employer"}).content.decode())
    assert len(named(companies, "kind", NARROW)) == 1
    assert [c["type"] for c in named(companies, "kind", "company-filters")] == ["hidden"]


def test_the_table_places_its_filters_by_the_columns_showing(rf, user, search):
    def table(*columns: str, query: str = "") -> ApplicationsTable:
        request = rf.get(f"/applications/?{query}")
        request.user = user
        return ApplicationsTable(request, {"columns": list(columns)} if columns else {})

    usual = table()
    held = {header.key: [c.name for c in header.controls] for header in usual.headers}
    assert held == {
        "role": ["role", "tag"],
        "company": ["company"],
        "location": ["location"],
        "status": ["status", "state", "quiet"],
        "applied": ["applied"],
    }
    assert usual.loose_controls == [] and not usual.loose_active and usual.loose_kept == []
    titled = {c.name: c.titled for header in usual.headers for c in header.controls}
    assert titled == {
        "role": False,
        "tag": True,
        "company": False,
        "location": False,
        "status": False,
        "state": True,
        "quiet": False,
        "applied": False,
    }

    bare = table("company", query="state=open&tag=remote&deadline_from=2026-01-01")
    assert [c.name for c in bare.loose_controls] == ["status", "state", "quiet", "tag", "deadline"]
    assert all(c.loose for c in bare.loose_controls) and bare.loose_active
    assert bare.loose_kept == [
        ("state", "open"),
        ("tag", "remote"),
        ("deadline_from", "2026-01-01"),
    ]
    assert [c.name for c in bare.narrow_controls][:2] == ["company", "status"]
    assert bare.narrow_names >= {"status", "state", "quiet", "tag", "deadline_from", "deadline_to"}
    assert bare.narrow_keeps == []

    # With no one signed in there are no tags to offer, and nothing is asked of the database.
    anonymous = ApplicationsTable(rf.get("/applications/"))
    assert "tag" not in [c.name for c in anonymous.narrow_controls]


def test_a_question_a_header_carries_is_checked_when_the_table_is_registered():
    columns = (Column("name", "Name", filter="text"), Column("kind", "Kind"))

    def table(**extra) -> type[Table]:
        return type("Odd", (Table,), {"name": "odd", "columns": columns, **extra})

    with pytest.raises(ValueError, match="already taken"):
        tables.register(table(extra_filters=(ExtraFilter("name", "Name", columns=("kind",)),)))
    with pytest.raises(ValueError, match="already taken"):
        tables.register(table(extra_filters=(ExtraFilter("sort", "Sort", columns=("kind",)),)))
    with pytest.raises(ValueError, match="names no column"):
        tables.register(table(extra_filters=(ExtraFilter("live", "Live", columns=("gone",)),)))
    with pytest.raises(ValueError, match="neither a list nor a flag"):
        tables.register(
            table(extra_filters=(ExtraFilter("live", "Live", kind="text", columns=("kind",)),))
        )
    assert "odd" not in tables.TABLES


# ------------------------------------------ what a header says, and what it is called


def header_tag(body: str, key: str) -> str:
    """One column's opening `<th …`, up to the attribute that names the column."""
    return head_of(body).split(f'data-col="{key}"')[0].rsplit("<th", 1)[1]


def test_a_headers_control_says_what_the_view_narrowed_by(client, user, search):
    """The view reads each of the four as the parameter itself, so of two values under one
    name the last is the one that counts. The table reads a column's own filter the other
    way round -- the first that is not empty (`Table.given`), because addresses older than
    #622 hold those twice. A control for one of the four has to read as the view does, or
    `?status=applied&status=rejected` is rejected rows under a list that says *Applied*;
    and the same goes for its copy under *Narrow* and for what the headers' form carries
    when its column is hidden."""
    query = "status=applied&status=rejected&state=open&state=closed&tag=dream-job&tag=remote"
    client.force_login(user)
    for extra in ({}, HTMX):
        response = client.get(f"{reverse(LIST)}?{query}", **extra)
        assert [a.posting.title for a in response.context["applications"]] == ["Research Associate"]
        body = response.content.decode()
        status = header_cell(body, "status")
        assert 'value="rejected" selected' in status and 'value="applied" selected' not in status
        assert 'value="closed" selected' in status and 'value="open" selected' not in status
        role = header_cell(body, "role")
        assert 'value="remote" selected' in role and 'value="dream-job" selected' not in role
        for name, held in (("status", "rejected"), ("state", "closed"), ("tag", "remote")):
            copy = body.split(f'id="filter-{name}-narrow"')[1].split("</select>")[0]
            assert copy.count(" selected") == 1 and f'value="{held}" selected' in copy, name

    # An empty value last is no filter at all to the view, and the control says *Any*.
    response = client.get(f"{reverse(LIST)}?status=rejected&status=&quiet=1&quiet=")
    assert len(response.context["applications"]) == len(search)
    status = header_cell(response.content.decode(), "status")
    assert " selected" not in status and "checked" not in status

    show(user, "role", "company", "applied")
    found = controls(page(client, user, "status=applied&status=rejected"))
    assert [(c["type"], c["value"]) for c in named(found, "status")] == [("hidden", "rejected")]


def test_a_header_cell_is_called_by_its_columns_name(client, user, search):
    """A cell with no name of its own is called by everything in it, and an open header
    holds lists, a tick box and what they are set to: every cell of the status column was
    announced under "Status Status is filtered Applied Outcome Still live Gone quiet Sort
    by status, lowest first". The cell says its name outright -- the column's, and that it
    is filtered while it is -- and the controls in it keep their own."""
    body = page(client, user, "status=applied&state=open&quiet=1&sort=role")
    assert 'aria-label="Status is filtered"' in header_tag(body, "status")
    assert 'aria-label="Role"' in header_tag(body, "role"), "sorted by, and not filtered"
    assert 'aria-label="Company"' in header_tag(body, "company")
    assert 'aria-sort="ascending"' in head_of(body).split('data-col="role"')[1].split(">")[0]

    cell = header_cell(body, "status")
    assert 'aria-label="Filter by Status"' in cell and '<label for="filter-state"' in cell
    assert 'role="img" aria-label="Status is filtered"' in cell, "the mark on the name stays"
    assert 'aria-label="Sort by Status, lowest first"' in cell

    # On every table, and for a column that has no filter to open.
    client.force_login(user)
    companies = client.get(reverse("jobs:company_list"), {"location": "mexico"}).content.decode()
    assert 'aria-label="Location is filtered"' in header_tag(companies, "location")
    assert 'aria-label="Name"' in header_tag(companies, "name")
    show(user, "role", "priority", "created")
    body = page(client, user)
    assert 'aria-label="Recorded"' in header_tag(body, "created")
    assert "<details" not in header_cell(body, "created")


def test_a_header_cells_text_starts_at_the_start_of_the_cell(client, user, search):
    """A browser centres a header cell unless it is told otherwise, which nobody saw while
    a name was all a cell held. With its filter open the cell is wider than the name, and
    the name moved to the middle of it, over a list and a label that did the same. Every
    cell says which edge its text starts from: the start, or the end for a column of
    figures, whose name sits over their last digits."""
    client.force_login(user)
    for url_name, numeric in (
        ("applications:list", ()),
        ("jobs:company_list", ("postings", "applications")),
        ("listings:list", ()),
    ):
        head = head_of(client.get(reverse(url_name), {"state": "all"}).content.decode())
        cells = [cell.split(">")[0] for cell in head.split("<th ")[1:] if "data-col=" in cell]
        assert len(cells) >= 5, url_name
        for cell in cells:
            key = cell.split('data-col="')[1].split('"')[0]
            classes = cell.split('class="')[1].split('"')[0].split()
            wanted = "text-end" if key in numeric else "text-start"
            other = "text-start" if key in numeric else "text-end"
            assert wanted in classes and other not in classes, (url_name, key, classes)


# ---------------------------------------------- the word that opens Narrow, and its count


def narrow_marks(body: str) -> list[tuple[str, list[str], str]]:
    """Each mark on the word that opens *Narrow*: its count, where it is shown, its words."""
    summary = body.split("<details data-narrow")[1].split("</summary>")[0]
    marks = []
    for part in summary.split("data-narrow-count=")[1:]:
        classes = summary.split(f"data-narrow-count={part}")[0].rsplit('class="', 1)[1]
        shown = [c for c in classes.split('"')[0].split() if "hidden" in c or "inline-flex" in c]
        words = " ".join(part.split('class="sr-only">')[1].split("</span>")[0].split())
        marks.append((part.split('"')[1], shown, words))
    return marks


def test_the_word_narrow_says_how_many_filters_are_in_use(client, user, search):
    """Folded, on a phone, the block was the only sign of the filters on the screen and it
    said nothing: the headers that carry the mark are a sideways scroll away. The word
    carries the mark and how many of the fields under it are in force, in a figure and in
    words, and the block stays folded for a filter a header holds."""
    assert narrow_marks(page(client, user)) == []
    assert narrow_marks(page(client, user, "sort=role&q=aperture")) == [], "neither is a field"

    for extra in ({}, HTMX):
        body = page(client, user, "status=applied&tag=remote", **extra)
        assert narrow_marks(body) == [("2", ["inline-flex", "md:hidden"], "2 filters in use")]
        assert "<details data-narrow >" in body, "folded"
        summary = body.split("<details data-narrow")[1].split("</summary>")[0]
        assert 'data-icon="filter"' in summary and 'aria-hidden="true">2</span>' in summary

    body = page(client, user, "applied_from=2020-01-01&applied_to=2030-01-01")
    assert narrow_marks(body) == [("1", ["inline-flex", "md:hidden"], "1 filter in use")]
    body = page(client, user, "status=applied&state=open&quiet=1&tag=remote&role=e&company=a")
    assert narrow_marks(body)[0][0] == "6"


def test_from_md_up_the_count_is_of_the_filters_no_header_holds(client, user, search):
    """With the status column hidden the block is on the page at every width, holding from
    `md` up only what no header holds: the count there is of those, since the rest are in
    their headers and marked there. One mark where the two widths agree."""
    show(user, "role", "company", "applied")
    body = page(client, user, "status=applied&quiet=1&role=e")
    assert narrow_marks(body) == [
        ("3", ["inline-flex", "md:hidden"], "3 filters in use"),
        ("2", ["hidden", "md:inline-flex"], "2 filters in use"),
    ]
    assert "<details data-narrow open>" in body, "a filter no header holds is in force"

    assert narrow_marks(page(client, user, "status=applied")) == [
        ("1", ["inline-flex"], "1 filter in use")
    ]
    body = page(client, user, "role=e")
    assert narrow_marks(body) == [("1", ["inline-flex", "md:hidden"], "1 filter in use")]
    assert "<details data-narrow >" in body

    # The same block on a table with no questions of its own.
    client.force_login(user)
    companies = client.get(reverse("jobs:company_list"), {"name": "a", "kind": "employer"})
    assert narrow_marks(companies.content.decode()) == [
        ("2", ["inline-flex", "md:hidden"], "2 filters in use"),
        ("1", ["hidden", "md:inline-flex"], "1 filter in use"),
    ]


def test_the_table_counts_the_filters_narrow_holds(rf, user, search):
    def marks(*columns: str, query: str = "") -> list[tuple[int, str]]:
        request = rf.get(f"/applications/?{query}")
        request.user = user
        settings = {"columns": list(columns)} if columns else {}
        return ApplicationsTable(request, settings).narrow_marks

    assert marks() == []
    assert marks(query="status=applied&tag=remote") == [(2, "phone")]
    assert marks(query="priority=3") == [(1, "")], "a hidden column's own filter, in force"
    assert marks(query="priority=3&status=applied") == [(2, "phone"), (1, "desktop")]
    assert marks("company", query="status=applied&state=open") == [(2, "")]
    assert marks("company", query="company=a&state=open") == [(2, "phone"), (1, "desktop")]
    assert marks("company", query="company=a") == [(1, "phone")]


def test_with_scripts_off_narrow_says_which_button_applies_what(client, user, search):
    """Two forms, so two kinds of button, and neither can send what was changed in the
    other and not applied. With a script that does not matter; without one the page says
    so, under the button whose reach is not obvious from where it sits."""
    client.force_login(user)
    for url_name in ("applications:list", "jobs:company_list", "listings:list"):
        body = client.get(reverse(url_name), {"state": "all"}).content.decode()
        block = body.split("data-narrow-form")[1].split("</form>")[0]
        note = block.split("<noscript")[1].split("</noscript>")[0]
        assert block.index('type="submit"') < block.index("<noscript"), "after its button"
        assert "data-narrow-reach" in note
        assert "This button applies the filters above it." in note
        assert "have a button of their own" in note
        assert block.count("<noscript") == 1


# ------------------------------------------- the address means what it always meant


def is_quiet(user):
    return Application.objects.for_user(user).quiet(quiet.threshold_for(user))


#: An address, and the rows it has always meant: computed from the queryset, never read off
#: the page. The table reads its own column filters as well; the board never has.
ADDRESSES = [
    ("", lambda rows, user: rows),
    ("status=applied", lambda rows, user: rows.filter(status=Status.APPLIED)),
    ("status=rejected", lambda rows, user: rows.filter(status=Status.REJECTED)),
    ("status=draft", lambda rows, user: rows.filter(status=Status.DRAFT)),
    ("status=nonsense", lambda rows, user: rows.none()),
    ("status=+applied+", lambda rows, user: rows.filter(status=Status.APPLIED)),
    ("status=", lambda rows, user: rows),
    ("state=open", lambda rows, user: rows.open()),
    ("state=closed", lambda rows, user: rows.closed()),
    ("state=nonsense", lambda rows, user: rows),
    ("tag=remote", lambda rows, user: rows.filter(tags__slug="remote")),
    ("tag=dream-job", lambda rows, user: rows.filter(tags__slug="dream-job")),
    ("tag=nobodys", lambda rows, user: rows.none()),
    ("quiet=1", lambda rows, user: is_quiet(user)),
    ("quiet=on", lambda rows, user: is_quiet(user)),
    ("quiet=", lambda rows, user: rows),
    (
        "status=applied&state=open&tag=remote&quiet=1",
        lambda rows, user: is_quiet(user).filter(status=Status.APPLIED, tags__slug="remote"),
    ),
    (
        "status=applied&state=closed",
        lambda rows, user: rows.none(),
    ),
    (
        "q=aperture&status=applied&tag=dream-job",
        lambda rows, user: rows.filter(
            posting__company__name__icontains="aperture",
            status=Status.APPLIED,
            tags__slug="dream-job",
        ),
    ),
    (
        "q=mesa&state=closed",
        lambda rows, user: rows.closed().filter(posting__company__name="Black Mesa"),
    ),
    # The view reads the parameter itself, so the last of two is the one that counts.
    ("status=applied&status=rejected", lambda rows, user: rows.filter(status=Status.REJECTED)),
    ("sort=role&status=applied&page=1", lambda rows, user: rows.filter(status=Status.APPLIED)),
    ("saved=none&tag=remote", lambda rows, user: rows.filter(tags__slug="remote")),
]

#: The same again with a column's own filter beside them, which only the table reads.
WITH_A_COLUMN = [
    (
        "company=mesa&state=closed",
        lambda rows, user: rows.closed().filter(posting__company__name="Black Mesa"),
    ),
    (
        "role=associate&tag=remote&company=&company=aperture",
        lambda rows, user: rows.filter(posting__title="Enrichment Associate"),
    ),
]


@pytest.mark.parametrize("address", [a for a, _rows in ADDRESSES + WITH_A_COLUMN])
def test_the_same_address_gives_the_same_rows_in_the_table(client, user, search, address):
    expected = dict(ADDRESSES + WITH_A_COLUMN)[address]
    wanted = sorted(expected(Application.objects.for_user(user), user).values_list("pk", flat=True))
    client.force_login(user)
    for extra in ({}, HTMX):
        response = client.get(f"{reverse(LIST)}?{address}", **extra)
        assert response.status_code == 200
        assert response.context["shape"] == "table"
        assert sorted(a.pk for a in response.context["applications"]) == wanted, extra
        assert response.context["total"] == len(wanted)


@pytest.mark.parametrize("address", [a for a, _rows in ADDRESSES])
def test_the_same_address_gives_the_same_cards_on_the_board(client, user, search, address):
    """The board draws what is still live of them, and says how many it left out."""
    expected = dict(ADDRESSES)[address]
    rows = expected(Application.objects.for_user(user), user)
    wanted = sorted(rows.filter(status__in=list(BOARD_STATUSES)).values_list("pk", flat=True))
    client.force_login(user)
    for extra in ({}, HTMX):
        response = client.get(f"{reverse(LIST)}?{address}&view=board", **extra)
        cards = [a.pk for column in response.context["columns"] for a in column["applications"]]
        assert sorted(cards) == wanted, extra
        assert response.context["total"] == rows.distinct().count()


def test_the_fixture_tells_the_filters_apart(user, search):
    """Every address above is worth asking only if its answer is not everything or nothing
    by accident."""
    rows = Application.objects.for_user(user)
    assert rows.count() == 6 and rows.open().count() == 4 and rows.closed().count() == 2
    assert [a.posting.title for a in is_quiet(user)] == ["Test Engineer"]
    assert rows.filter(tags__slug="remote").count() == 3


@pytest.mark.parametrize("name", FOUR)
def test_each_is_still_a_filter_the_table_knows(rf, name):
    """For *Clear*, for the empty state, and for what a saved view may hold (#259)."""
    assert name in ApplicationsTable.known_params()
    assert ApplicationsTable(rf.get("/", {name: "x"})).filters_active
    assert not ApplicationsTable(rf.get("/", {name: " "})).filters_active
    assert ApplicationsTable.extra_params == ("q",), "the search has no control of the table's"
    assert CompaniesTable.extra_filters == ()


@pytest.mark.parametrize(
    "query",
    [
        "status=rejected",
        "state=closed",
        "tag=remote",
        "quiet=1",
        "status=applied&tag=remote&quiet=1",
    ],
)
def test_a_saved_view_holding_each_parameter_still_applies(client, user, search, query):
    """A view is a name for an address (#259). One kept before the filters moved holds these
    parameters, and opens to the same rows, with nothing reported as left out and the
    header showing what it narrows by."""
    rows = Application.objects.for_user(user)
    expected = {
        "status=rejected": rows.filter(status=Status.REJECTED),
        "state=closed": rows.closed(),
        "tag=remote": rows.filter(tags__slug="remote"),
        "quiet=1": is_quiet(user),
        "status=applied&tag=remote&quiet=1": is_quiet(user).filter(
            status=Status.APPLIED, tags__slug="remote"
        ),
    }[query]
    wanted = sorted(expected.values_list("pk", flat=True))
    assert wanted and len(wanted) < rows.count(), "a view that narrows"
    kept = ApplicationsTable.save_view({}, "Mine", f"{query}&sort=role", [])
    tables.save_settings(user, "applications", ApplicationsTable.make_default(kept, "mine"))
    client.force_login(user)

    response = client.get(reverse(LIST), follow=True)
    assert response.redirect_chain, "the bare address opens as the view"
    assert f"{query}&sort=role&saved=mine" in response.redirect_chain[-1][0]
    assert sorted(a.pk for a in response.context["applications"]) == wanted
    table = response.context["table"]
    assert table.applied_view.slug == "mine" and table.applied_gaps == ([], [])
    body = response.content.decode()
    assert "data-view-gaps" not in body, "nothing is said to have been left out"
    for pair in query.split("&"):
        name, value = pair.split("=")
        header = header_cell(body, "role" if name == "tag" else "status")
        if name == "quiet":
            assert 'name="quiet" value="1" checked' in header
        else:
            assert f'value="{value}" selected' in header, pair


# ---------------------------------------------- the neighbours' faults, for these four


def test_each_of_the_four_is_posted_by_one_control_in_each_form(client, user, search):
    """#622: a filter the page was loaded with could not be changed from its header, because
    the phone's copy was posted with it and read first. With every one of the four in the
    address, each form posts each of them once, and the phone's copies hold the same."""
    query = "status=applied&state=open&tag=remote&quiet=1"
    for columns in (None, ("role", "status", "tags"), ("company", "applied")):
        if columns:
            show(user, *columns)
        for extra in ({}, HTMX):
            found = controls(page(client, user, query, **extra))
            for form in (FORM, NARROW):
                posted = posted_by(found, form)
                for name in FOUR:
                    assert posted.count(name) == 1, (columns, form, name, posted)
                assert len(posted) == len(set(posted)), (columns, form, posted)
            for name in ("status", "state", "tag"):
                assert named(found, name, NARROW)[0]["id"] == f"filter-{name}-narrow"
            assert "checked" in named(found, "quiet", NARROW)[0]


@pytest.mark.parametrize(
    ("asked_by", "column"),
    [
        ("filter-status", "status"),
        ("filter-state", "status"),
        ("filter-quiet", "status"),
        ("filter-tag", "role"),
        ("filter-role", "role"),
    ],
)
def test_the_header_whose_control_asked_comes_back_open(client, user, search, asked_by, column):
    """#626: choosing *Any*, or unticking the box, leaves a header that narrows nothing, and
    a header drawn shut takes the control off the screen with the focus in it. Any control
    in the header keeps the whole header open -- and no other header."""
    body = page(client, user, "status=&state=&tag=", **HTMX, HTTP_HX_TRIGGER=asked_by)
    assert "data-col-filter open" in header_cell(body, column)
    assert "is filtered" not in header_cell(body, column)
    other = "role" if column == "status" else "status"
    assert "data-col-filter open" not in header_cell(body, other)
    assert "data-col-filter open" not in header_cell(page(client, user, **HTMX), column)


def test_a_table_with_nothing_in_it_still_carries_the_four(client, user, search):
    """#647: the header went with the rows, so a search that matched nothing was followed by
    one sent without the status. The header is drawn over no rows, holding all four."""
    for extra in ({}, HTMX):
        body = page(
            client, user, "status=rejected&state=open&tag=remote&quiet=1&sort=role", **extra
        )
        assert "Nothing matches these filters" in body
        status = header_cell(body, "status")
        assert 'value="rejected" selected' in status and 'value="open" selected' in status
        assert 'name="quiet" value="1" checked' in status
        assert 'value="remote" selected' in header_cell(body, "role")
        assert f'name="sort" value="role" form="{FORM}"' in body

    # And under Narrow, where a hidden column's are.
    show(user, "company")
    body = page(client, user, "status=rejected&state=open")
    assert "Nothing matches these filters" in body and "<details data-narrow open>" in body
    assert [c["value"] for c in named(controls(body), "status")] == ["rejected"]


# ------------------------------------------------------------------------- the board


def test_the_board_keeps_the_form_with_the_three_it_uses(client, user, search):
    """The board has no headers, so the form the two shapes shared is the board's: the
    outcome, the tag and *Gone quiet*, under the id the masthead's box includes and takes
    turns on, narrowing live, with a button for scripts off and *Clear* while anything
    narrows. The status is not one of its controls any more (#315): the board's columns
    fold to it, and the form sends the one in force from a hidden field the board draws
    (`tests/test_board_fold.py`)."""
    body = page(client, user, "view=board&status=applied&tag=remote&quiet=1&state=open")
    found = controls(body)
    form = next(f for f in found.forms if f.get("id") == FORM)
    assert form["hx-get"] == reverse(LIST) and form["hx-target"] == "#applications-table"
    assert form["hx-trigger"] == "input changed delay:300ms, change, submit"
    assert form["hx-include"] == "[data-table-search]" and form["hx-sync"] == "this:replace"

    written = [c for c in found.inputs if c["_form"] is form]
    assert [c["name"] for c in written] == ["q", "state", "tag", "view", "quiet"]
    by_name = {c["name"]: c for c in written}
    assert by_name["view"]["type"] == "hidden" and by_name["view"]["value"] == "board"
    assert by_name["quiet"]["type"] == "checkbox" and "checked" in by_name["quiet"]
    assert not any("form" in c or "hx-get" in c for c in written), "the form asks, not they"
    assert posted_by(found, FORM).count("status") == 1, "and it still sends the status"
    panel = body.split(f'id="{FORM}"')[1].split("</form>")[0]
    assert 'value="open" selected' in panel and 'value="remote" selected' in panel
    assert "Status" not in panel and 'value="applied"' not in panel
    for label in ("Outcome", "Tag", "Gone quiet"):
        assert label in panel
    (button,) = [b for b in found.buttons if b["_form"] is form]
    assert button["type"] == "submit" and ">Filter</button>" in panel
    assert ">Clear</a>" in panel

    # No headers, and nothing of the table's: no Narrow, no column filters.
    assert "data-narrow" not in body and "data-col-filter" not in body
    assert "<thead" not in body
    assert ">Clear</a>" not in page(client, user, "view=board").split(f'id="{FORM}"')[1]


def test_a_fragment_of_either_shape_leaves_the_filter_form_where_it_is(client, user, search):
    """The form is outside what a live filter swaps, so the element every control takes its
    turn on is never replaced under a request in flight."""
    for query in ("status=applied", "view=board&status=applied"):
        fragment = page(client, user, query, **HTMX)
        assert f'id="{FORM}"' not in fragment
        assert 'id="applications-table"' in fragment and 'id="applications-count"' in fragment
