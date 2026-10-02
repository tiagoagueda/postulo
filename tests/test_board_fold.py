"""The Applications board folds to one column, and the address says which (#315).

`?status=applied` narrows the table to *Applied*. On the board it folds the columns to that
one: the column of that status is open and holds its cards, and every other is a strip that
still says its name and its count. The status therefore left the board's row of filters --
the columns do its job -- and a column's heading is the control that sets it.

What is held here is what the server draws for an address:

* **which columns are open**, with scripts on or off, as a page and as the fragment a live
  swap asks for, and what each strip counts;
* **a heading is a link** to the board folded to its column, and on the open column of a
  folded board to the board unfolded; each says whether its column is open, names the list
  it controls, and says what pressing it does;
* ***All columns*** is drawn before the columns whenever a status is in the address, and
  never otherwise; where a default view would answer the bare address, the way back says
  the plain board instead;
* **each control is described by its column's count**, in words, and the page's count
  holds what it says with one card fewer;
* **the fold asks for the question as it stands**: the filter form and the masthead's box,
  taking its turn on the form, with the status put in place of the form's;
* **the status in force is a field of the filter form**, drawn once, with the board, and
  the form has no list for it;
* **a status with no column** leaves every column a strip and is said, with its link,
  whether it matches anything or not; one that is no status at all is said to be none;
* **a move made on the board** says where the card went;
* **the script** scrolls the board only while a card is held, never under reduced motion,
  and does not swap a held card away.

Whether a person can use any of it is a question for a browser, and
`tests/e2e/test_board_fold.py` asks it.
"""

from __future__ import annotations

import json
import re
from html.parser import HTMLParser
from pathlib import Path
from urllib.parse import parse_qs, urlsplit

import pytest
from django.contrib.messages import get_messages
from django.urls import reverse

from postulo.applications.models import BOARD_STATUSES, Application, Status
from postulo.applications.services import change_status
from postulo.applications.tables import ApplicationsTable
from postulo.core import tables
from postulo.jobs.models import Company, JobPosting
from tests import test_application_filters as filter_tests
from tests.test_site_search import controls
from tests.test_tables import HTMX, form_of

pytestmark = pytest.mark.django_db

#: The six applications the filters' tests are run against, under the name they have there:
#: a fixture is found by its name in the module that asks for it.
search = filter_tests.search

LIST = "applications:list"
FORM = "application-filters"
APP_JS = (
    Path(__file__).resolve().parents[1] / "src" / "postulo" / "static" / "js" / "app.js"
).read_text(encoding="utf-8")


class Headings(HTMLParser):
    """Every column of the board as it is written: its section, the control in its
    heading, the count beside it and the list of cards the control names. And the words
    each count is said in, by the id they are named with, and the page's own count."""

    def __init__(self):
        super().__init__()
        self.columns: list[dict] = []
        self.unfold: dict | None = None
        self.order: list[str] = []
        self.words: dict[str, str] = {}
        self.page_count: dict | None = None
        self._count = False
        self._words = ""

    def handle_starttag(self, tag, attrs):
        attrs = dict(attrs)
        if "data-board-section" in attrs:
            self.columns.append(
                {"status": attrs["data-board-section"], "strip": "data-board-strip" in attrs}
            )
        elif tag == "a" and "data-board-fold" in attrs:
            if attrs.get("id") == "board-unfold":
                self.unfold = attrs
                self.order.append("unfold")
            else:
                self.columns[-1]["control"] = attrs
                self.order.append(self.columns[-1]["status"])
        elif "data-column-count" in attrs:
            self._count = True
            self.columns[-1]["figure"] = attrs
        elif "data-column-count-words" in attrs:
            self._words = attrs["id"]
        elif "data-board-column" in attrs:
            self.columns[-1]["list"] = attrs
        elif "data-count-words" in attrs:
            self.page_count = attrs

    def handle_data(self, data):
        if self._count:
            self.columns[-1]["count"] = int(data)
            self._count = False
        elif self._words:
            self.words[self._words] = " ".join(data.split())
            self._words = ""


def board(client, user, query: str = "", **extra) -> tuple[Headings, str, object]:
    client.force_login(user)
    response = client.get(f"{reverse(LIST)}?view=board&{query}".rstrip("&"), **extra)
    assert response.status_code == 200
    body = response.content.decode()
    found = Headings()
    found.feed(body)
    return found, body, response


def in_words(count: int) -> str:
    """A count as the page says it: the words under the page's title, and a column's."""
    return f"{count} application" if count == 1 else f"{count} applications"


def asked_by(href: str) -> dict[str, list[str]]:
    assert urlsplit(href).path == reverse(LIST)
    return parse_qs(urlsplit(href).query, keep_blank_values=True)


def live(user, **wanted) -> dict[str, int]:
    """How many live applications each column holds of what these filters match: the
    queryset's answer, never a number written here."""
    rows = Application.objects.for_user(user)
    if wanted.get("tag"):
        rows = rows.filter(tags__slug=wanted["tag"])
    if wanted.get("q"):
        rows = rows.filter(posting__company__name__icontains=wanted["q"])
    return {str(status): rows.filter(status=status).distinct().count() for status in BOARD_STATUSES}


# ---------------------------------------------------------------- which columns are open


def test_with_no_status_every_column_is_open(client, user, search):
    for extra in ({}, HTMX):
        found, body, response = board(client, user, **extra)
        assert [c["status"] for c in found.columns] == [str(s) for s in BOARD_STATUSES]
        assert not any(c["strip"] for c in found.columns)
        assert response.context["folded"] is False
        for column in found.columns:
            assert column["control"]["aria-expanded"] == "true"
            assert "hidden" not in column["list"]
            assert column["count"] == live(user)[column["status"]]
        assert found.unfold is None and 'id="board-unfold"' not in body


@pytest.mark.parametrize("status", ["applied", "draft", "offer"])
def test_a_status_in_the_address_folds_the_board_to_its_column(client, user, search, status):
    """One column open, holding its cards and nothing else read; the others strips, each
    with the count of what it holds. As a page and as the fragment a swap asks for."""
    for extra in ({}, HTMX):
        found, _body, response = board(client, user, f"status={status}", **extra)
        assert response.context["folded"] is True
        counts = live(user)
        for column in found.columns:
            is_open = column["status"] == status
            assert column["strip"] is not is_open, column
            assert column["control"]["aria-expanded"] == ("true" if is_open else "false")
            assert ("hidden" in column["list"]) is not is_open
            assert column["count"] == counts[column["status"]], column
        drawn = {
            c["status"]: [a.pk for a in c["applications"]] for c in response.context["columns"]
        }
        wanted = Application.objects.for_user(user).filter(status=status)
        assert sorted(drawn.pop(status)) == sorted(wanted.values_list("pk", flat=True))
        assert not any(drawn.values()), "a strip's cards are counted, not read"
        assert response.context["total"] == wanted.count(), "the page counts what was asked"


def test_a_strips_count_is_of_what_the_other_filters_match(client, user, search):
    """A strip says how many cards its column holds under the rest of the question: the
    tag and the search narrow it, the status does not."""
    found, _body, response = board(client, user, "status=applied&tag=remote")
    assert {c["status"]: c["count"] for c in found.columns} == live(user, tag="remote")
    assert response.context["total"] == live(user, tag="remote")["applied"]

    found, _body, _response = board(client, user, "status=interviewing&q=aperture")
    counts = {c["status"]: c["count"] for c in found.columns}
    assert counts == live(user, q="aperture") and counts["applied"] and not counts["draft"]

    # A card moved into a folded column is counted there the next time the board is drawn.
    change_status(search["Resonance Technician"], Status.OFFER)
    found, _body, _response = board(client, user, "status=applied")
    counts = {c["status"]: c["count"] for c in found.columns}
    assert counts == live(user) and counts["offer"] == 1 and counts["draft"] == 0


def test_a_strip_counts_only_this_persons_applications(client, user, other_user, search):
    """The strips are counted by a query of their own, so it is scoped like every other:
    somebody else's offer is not in this person's *Offer* strip."""
    theirs = JobPosting.objects.create(
        owner=other_user,
        company=Company.objects.create(owner=other_user, name="Elsewhere"),
        title="Theirs",
    )
    Application.objects.create(owner=other_user, posting=theirs, status=Status.OFFER)
    Application.objects.create(owner=other_user, posting=theirs, status=Status.APPLIED)

    found, _body, response = board(client, user, "status=applied")
    counts = {c["status"]: c["count"] for c in found.columns}
    assert counts == live(user) and counts["offer"] == 0
    assert response.context["total"] == live(user)["applied"]


def test_of_two_statuses_the_last_is_the_fold_as_it_is_the_tables_filter(client, user, search):
    """The view reads the parameter itself in both shapes: the table narrows to the last of
    two, so the board folds to the last of two, and an empty one last is no fold."""
    found, _body, _response = board(client, user, "status=applied&status=interviewing")
    assert [c["status"] for c in found.columns if not c["strip"]] == ["interviewing"]

    found, _body, response = board(client, user, "status=applied&status=")
    assert not any(c["strip"] for c in found.columns) and response.context["folded"] is False

    found, _body, _response = board(client, user, "status=+applied+")
    assert [c["status"] for c in found.columns if not c["strip"]] == ["applied"]


# ------------------------------------------------------------------ a heading is a link


def test_a_heading_leads_to_the_board_folded_to_its_column(client, user, search):
    """Without a script the heading is a plain link. It keeps the rest of the question and
    names one status; it does not carry a page number, nor a saved view's name, which the
    question no longer is."""
    found, _body, _response = board(
        client, user, "tag=remote&q=aperture&quiet=1&saved=mine&page=3&status=draft&status=offer"
    )
    for column in found.columns:
        control = column["control"]
        assert control["id"] == f"board-fold-{column['status']}"
        assert control["aria-controls"] == column["list"]["id"]
        query = asked_by(control["href"])
        assert "saved" not in query and "page" not in query
        for name, value in (("view", "board"), ("tag", "remote"), ("q", "aperture")):
            assert query[name] == [value], (column["status"], name)
        assert query["quiet"] == ["1"]
        label = Status(column["status"]).label
        if column["strip"]:
            assert query["status"] == [column["status"]], "one status, its own"
            assert control["aria-label"] == f"{label}: show only this column"
        else:
            assert column["status"] == "offer", "the last of the two asked for"
            assert "status" not in query, "the open column's heading unfolds"
            assert control["aria-label"] == f"{label}: show all columns"


def test_a_columns_heading_is_called_by_its_status_and_not_by_its_control(client, user, search):
    """A heading with no name of its own is called by what is in it, and what is in it is a
    control named for what pressing it does: the page's headings would have read "Applied:
    show only this column". Each says its own name, open or folded -- the rule a table's
    header cell follows (#314)."""
    labels = [str(Status(status).label) for status in BOARD_STATUSES]
    for query in ("", "status=applied", "status=rejected"):
        _found, body, _response = board(client, user, query)
        assert re.findall(r'<h2 class="[^"]*" aria-label="([^"]*)">', body) == labels, query


def test_unfolded_every_heading_folds_to_its_own_column(client, user, search):
    found, _body, _response = board(client, user)
    for column in found.columns:
        query = asked_by(column["control"]["href"])
        assert query == {"view": ["board"], "status": [column["status"]]}
        label = Status(column["status"]).label
        assert column["control"]["aria-label"] == f"{label}: show only this column"


def test_the_board_chosen_as_the_shape_folds_without_naming_it(client, user, search):
    """A person whose applications open as the board has no `view` in the address, and a
    fold adds none: the address stays the question and the shape stays the preference."""
    tables.save_settings(user, "applications", {"shape": "board"})
    client.force_login(user)
    body = client.get(reverse(LIST)).content.decode()
    found = Headings()
    found.feed(body)
    assert len(found.columns) == len(BOARD_STATUSES)
    assert asked_by(found.columns[1]["control"]["href"]) == {"status": ["applied"]}

    body = client.get(reverse(LIST), {"status": "applied"}).content.decode()
    found = Headings()
    found.feed(body)
    assert found.unfold["href"] == reverse(LIST), "the bare address, and nothing after it"
    assert [c["status"] for c in found.columns if not c["strip"]] == ["applied"]


@pytest.mark.parametrize("kept", ["status=applied", "tag=remote"])
def test_unfolding_does_not_lead_to_the_bare_address_where_a_default_view_answers_it(
    client, user, search, kept
):
    """The bare address opens as the default view, where one is kept (#259). So with the
    board folded and nothing else asked, *All columns* and the open column's heading led
    to an address that answered with a redirect: back to the fold, where the default view
    is a status, and into the default view and out of the plain board, where it is
    anything else. They say the plain board (`saved=none`), as *Clear filters* does. No
    script runs here: these are the links as the server writes them."""
    settings = ApplicationsTable.save_view({"shape": "board"}, "Mine", kept, [])
    tables.save_settings(user, "applications", ApplicationsTable.make_default(settings, "mine"))
    client.force_login(user)
    opened = client.get(reverse(LIST))
    assert opened.status_code == 302 and "saved=mine" in opened["Location"]

    if kept == "status=applied":
        # The default view is itself a fold.
        body = client.get(opened["Location"]).content.decode()
    else:
        # Out of the default view to the plain board, and fold that.
        plain = Headings()
        plain.feed(client.get(reverse(LIST), {"saved": "none"}).content.decode())
        assert not any(c["strip"] for c in plain.columns)
        body = client.get(plain.columns[1]["control"]["href"]).content.decode()
    folded = Headings()
    folded.feed(body)
    assert [c["status"] for c in folded.columns if not c["strip"]] == ["applied"]

    for way_back in (folded.unfold["href"], folded.columns[1]["control"]["href"]):
        assert asked_by(way_back) == {"saved": ["none"]}, way_back
        answer = client.get(way_back)
        assert answer.status_code == 200, "an answer, and not a redirect to the default view"
        drawn = Headings()
        drawn.feed(answer.content.decode())
        assert len(drawn.columns) == len(BOARD_STATUSES)
        assert not any(c["strip"] for c in drawn.columns), "every column is open"
        assert drawn.unfold is None
        assert answer.context["selected_tag"] == "", "and it is the plain board"

    # With something else still asked the address is not bare, and nothing is added to it.
    found, _body, _response = board(client, user, "status=applied&tag=dream-job")
    assert asked_by(found.unfold["href"]) == {"view": ["board"], "tag": ["dream-job"]}


# -------------------------------------------------------------------------- All columns


def test_all_columns_is_drawn_before_the_columns_while_the_board_is_folded(client, user, search):
    for query in ("status=applied", "status=offer&tag=remote", "status=rejected", "status=zzz"):
        for extra in ({}, HTMX):
            found, body, _response = board(client, user, query, **extra)
            assert found.unfold is not None, query
            assert found.order[0] == "unfold" and found.order.count("unfold") == 1
            assert "All columns" in body.split('id="board-unfold"')[1].split("</a>")[0]
            asks = asked_by(found.unfold["href"])
            assert "status" not in asks and asks["view"] == ["board"]
            if "tag=remote" in query:
                assert asks["tag"] == ["remote"]


def test_all_columns_says_where_the_focus_lands_when_it_has_gone(client, user, search):
    """The control is not there once the board is unfolded. It names the heading of the
    column that was open -- or of the first, where none was -- for the script that puts the
    focus somewhere after a control swaps itself away."""
    found, _body, _response = board(client, user, "status=interviewing")
    assert found.unfold["data-focus-after"] == "#board-fold-interviewing"
    found, _body, _response = board(client, user, "status=rejected")
    assert found.unfold["data-focus-after"] == f"#board-fold-{BOARD_STATUSES[0]}"


# ------------------------------------------------- a control is described by its count


def test_each_control_is_described_by_its_columns_count_in_words(client, user, search):
    """A strip's count was a bare figure after the heading, and no part of the control:
    tabbing through the strips said each name and never how many cards it holds. The
    control is described by the count, and what describes it is the page's own words for a
    count -- "1 application", "3 applications" -- beside the figure, which is the part that
    is seen. The open column's heading the same."""
    for query in ("", "status=applied", "status=offer&tag=remote", "status=rejected"):
        for extra in ({}, HTMX):
            found, _body, _response = board(client, user, query, **extra)
            assert len(found.words) == len(BOARD_STATUSES), query
            for column in found.columns:
                described_by = column["control"]["aria-describedby"]
                assert described_by == f"board-count-{column['status']}"
                assert found.words[described_by] == in_words(column["count"]), column
                assert column["figure"].get("aria-hidden") == "true", "said once, in words"


def test_the_pages_count_holds_what_it_says_with_one_card_fewer(client, user, search):
    """A card dragged out of what the address asks for is one application fewer on the
    page, and the script that moves it has no words of its own: the count carries the
    sentence for one fewer, written by the server in the person's language. Only on the
    board, and only where there is one to take away."""
    found, _body, response = board(client, user, "status=applied")
    assert response.context["total"] == 2
    assert found.page_count["data-one-fewer"] == in_words(1)
    found, _body, response = board(client, user)
    assert found.page_count["data-one-fewer"] == in_words(response.context["total"] - 1)
    found, body, _response = board(client, user, "status=offer", **HTMX)
    assert "data-one-fewer" not in found.page_count and 'hx-swap-oob="true"' in body

    client.force_login(user)
    table = Headings()
    table.feed(client.get(reverse(LIST), {"view": "table"}).content.decode())
    assert "data-one-fewer" not in table.page_count


# ------------------------------------------- the fold asks for the question as it stands


def test_a_fold_takes_its_turn_on_the_filter_form_and_sends_the_whole_question(
    client,
    user,
    search,
):
    """A sort link's address was written with the last answer, which is why it takes no
    turn (#313, #648). A fold is not that: with a script it asks for this page with the
    filter form and the masthead's box, as every live control does, and replaces a request
    in flight. Its own status is put in place of the form's; the way back leaves the status
    out."""
    found, _body, _response = board(client, user, "status=applied&tag=remote")
    every = [*(column["control"] for column in found.columns), found.unfold]
    for control in every:
        assert control["hx-get"] == reverse(LIST)
        assert control["hx-include"] == f"#{FORM}, [data-table-search]"
        assert control["hx-sync"] == f"#{FORM}:replace"
        assert control["hx-target"] == "#applications-table"
        assert control["hx-swap"] == "outerHTML" and control["hx-push-url"] == "true"
    for column in found.columns:
        control = column["control"]
        if column["strip"]:
            assert json.loads(control["hx-vals"]) == {"status": column["status"]}
            assert "hx-params" not in control
        else:
            assert control["hx-params"] == "not status" and "hx-vals" not in control
    assert found.unfold["hx-params"] == "not status" and "hx-vals" not in found.unfold


# ------------------------------------------ the status in force, and the row of filters


def test_the_status_left_the_boards_row_of_filters(client, user, search):
    """Outcome, tag and gone quiet stay, with the button and *Clear*. There is no list of
    statuses in the form, on a folded board or an open one."""
    for query in ("", "status=applied&tag=remote&quiet=1&state=open"):
        _found, body, _response = board(client, user, query)
        seen = controls(body)
        form = next(f for f in seen.forms if f.get("id") == FORM)
        written = [c for c in seen.inputs if c["_form"] is form]
        assert [c["name"] for c in written] == ["q", "state", "tag", "view", "quiet"]
        panel = body.split(f'id="{FORM}"')[1].split("</form>")[0]
        assert 'id="filter-status"' not in panel and ">Status</label>" not in panel
        for label in ("Outcome", "Tag", "Gone quiet"):
            assert label in panel
        assert ">Filter</button>" in panel


def test_the_status_in_force_is_a_hidden_field_of_the_form_drawn_with_the_board(
    client,
    user,
    search,
):
    """A tag chosen above a folded board must keep it folded, with scripts on and off, so
    the form still sends the status: one hidden field, belonging to the form by `form=`,
    written inside what a live swap replaces. So it is the status the board was last drawn
    with, whatever folded it, and it comes with the fragment."""
    for extra in ({}, HTMX):
        _found, body, _response = board(client, user, "status=applied&status=offer", **extra)
        seen = controls(body)
        sent = [c for c in seen.inputs if c.get("name") == "status" and form_of(c) == FORM]
        assert [(c["type"], c["value"], c.get("form")) for c in sent] == [("hidden", "offer", FORM)]
        inside = body.split('id="applications-table"')[1]
        assert 'name="status" value="offer"' in inside
        before = body.split('id="applications-table"')[0]
        assert 'name="status"' not in before.split("<main")[-1]

    # With no status asked for there is no field: an empty one would be an empty parameter
    # in every address a filter wrote.
    _found, body, _response = board(client, user, "tag=remote")
    seen = controls(body)
    assert not [c for c in seen.inputs if c.get("name") == "status" and form_of(c) == FORM]


def test_the_table_is_untouched_by_the_fold(client, user, search):
    """The table keeps its own status filter, in its header (#314), and draws nothing of
    the board's."""
    client.force_login(user)
    body = client.get(reverse(LIST), {"status": "applied"}).content.decode()
    assert "data-board" not in body and "board-unfold" not in body
    assert 'id="filter-status"' in body


# ------------------------------------------------------------- a status with no column


def test_a_status_with_no_column_leaves_every_column_a_strip_and_is_said(client, user, search):
    """*Rejected* is settled, so the board has no column to open for it. Every column is a
    strip with its count, the sentence says how many applications the status matches and
    links to them in the table, and the page's count is theirs."""
    for extra in ({}, HTMX):
        found, body, response = board(client, user, "status=rejected", **extra)
        assert all(c["strip"] for c in found.columns)
        assert {c["status"]: c["count"] for c in found.columns} == live(user)
        assert not any(c["applications"] for c in response.context["columns"])
        assert 'data-off-board="1"' in body
        assert 'href="/applications/?view=table&amp;status=rejected"' in body
        assert response.context["total"] == 1
        for column in found.columns:
            assert asked_by(column["control"]["href"])["status"] == [column["status"]]


def said_above_the_board(body: str, marked: str) -> str:
    """The sentence the board says in the paragraph marked ``marked``, as plain words."""
    paragraph = re.search(rf"<p[^>]*\s{marked}[^>]*>(.*?)</p>", body, re.DOTALL)
    assert paragraph and 'role="status"' in paragraph.group(0), marked
    return " ".join(re.sub(r"<[^>]+>", " ", paragraph.group(1)).split())


def test_a_settled_status_that_matches_nothing_is_still_said(client, user, search):
    """Nobody here was ghosted. The board folded to *Ghosted* drew seven strips and no word
    of why, since the sentence was said only of a count above none. It is said whenever
    the status asked for has no column: there are none, and the table is where they would
    be."""
    assert not Application.objects.for_user(user).filter(status=Status.GHOSTED).exists()
    for extra in ({}, HTMX):
        found, body, response = board(client, user, "status=ghosted", **extra)
        assert all(c["strip"] for c in found.columns) and found.unfold is not None
        assert response.context["total"] == 0
        assert 'data-off-board="0"' in body
        said = said_above_the_board(body, "data-off-board")
        assert said.startswith("0 settled applications match these filters."), said
        assert 'href="/applications/?view=table&amp;status=ghosted"' in body
        assert "data-no-such-status" not in body


def test_a_status_that_is_no_status_is_said_to_be_none(client, user, search):
    """An address can ask for anything. A status Postulo does not have matches nothing in
    either shape, and on the board leaves seven strips: the page says that this is what
    happened, and names the way back, which is drawn just below it."""
    for extra in ({}, HTMX):
        found, body, response = board(client, user, "status=nonsense", **extra)
        assert all(c["strip"] for c in found.columns) and found.unfold is not None
        assert response.context["total"] == 0
        assert "data-off-board" not in body, "nothing is settled about it"
        said = said_above_the_board(body, "data-no-such-status")
        assert "a status that Postulo does not have" in said and "All columns" in said
        assert body.index("data-no-such-status") < body.index('id="board-unfold"')

    # Said of nothing else: not of a column, not of a settled status, not of an open board.
    for query in ("", "status=applied", "status=rejected", "tag=remote"):
        _found, body, _response = board(client, user, query)
        assert "data-no-such-status" not in body, query
    for query in ("", "status=applied", "tag=dream-job"):
        _found, body, _response = board(client, user, query)
        assert "data-off-board" not in body, query


# ------------------------------------------------------- a move made on the board is said


@pytest.mark.parametrize(
    ("shape", "back_to", "said"),
    [
        ("table", "/applications/?view=board&status=applied", "Moved to Offer."),
        ("board", "/applications/?status=applied&tag=remote", "Moved to Offer."),
        ("board", "/applications/", "Moved to Offer."),
        ("board", "/applications/?view=table", "Status updated."),
        ("table", "/applications/?status=applied", "Status updated."),
        ("board", "/", "Status updated."),
        ("board", "", "Status updated."),
    ],
)
def test_a_move_made_on_the_board_says_where_the_card_went(
    client, user, search, shape, back_to, said
):
    """On a folded board a card moved into a strip is gone from the screen, under a message
    that said only that a status was updated. A move made on the board says where the card
    went. What tells this view the board from the application's own page and from the
    dashboard is where the form asks to go back to: the Applications page, drawn as the
    board, by the address or by the person's own choice of shape. The others keep the
    message they had."""
    tables.save_settings(user, "applications", {"shape": shape})
    client.force_login(user)
    application = search["Test Engineer"]
    response = client.post(
        reverse("applications:status", args=[application.pk]),
        {"status": Status.OFFER, **({"next": back_to} if back_to else {})},
    )
    assert response.status_code == 302
    assert response["Location"] == (back_to or application.get_absolute_url())
    assert Application.objects.get(pk=application.pk).status == Status.OFFER
    assert [str(m) for m in get_messages(response.wsgi_request)] == [said]


def test_a_move_on_the_board_to_where_the_card_already_is_says_nothing(client, user, search):
    """From a status to itself, with no reason given, is no move, on the board as anywhere."""
    client.force_login(user)
    application = search["Test Engineer"]
    response = client.post(
        reverse("applications:status", args=[application.pk]),
        {"status": Status.APPLIED, "next": "/applications/?view=board"},
    )
    assert [str(m) for m in get_messages(response.wsgi_request)] == []


# ----------------------------------------------------------------------------- queries


def test_a_folded_board_reads_one_column_and_counts_the_rest(client, user, search):
    """One query more than an open board under a filter, and no more rows than the open
    column's: the strips are one `GROUP BY`."""
    from django.db import connection
    from django.test.utils import CaptureQueriesContext

    client.force_login(user)
    client.get(reverse(LIST), {"view": "board", "tag": "remote"})
    with CaptureQueriesContext(connection) as unfolded:
        client.get(reverse(LIST), {"view": "board", "tag": "remote"})
    with CaptureQueriesContext(connection) as folded:
        client.get(reverse(LIST), {"view": "board", "tag": "remote", "status": "applied"})
    assert len(folded) <= len(unfolded) + 1, (len(unfolded), len(folded))
    grouped = [q["sql"] for q in folded.captured_queries if "GROUP BY" in q["sql"]]
    assert any('"status"' in sql and "COUNT(DISTINCT" in sql for sql in grouped), grouped


# -------------------------------------------------------------------------- the script


def test_the_board_scrolls_only_while_a_card_is_held_and_never_under_reduced_motion():
    """The behaviour is the browser suite's to show. This holds the two rules to the
    source, where a change that dropped either would be one line: the scroll is started
    from the two events a drag reports its place with and nowhere else, behind the held
    card and the preference."""
    assert '"(prefers-reduced-motion: reduce)"' in APP_JS
    follow = APP_JS.split("function followTheEdge(event) {")[1].split("\n  }\n")[0]
    assert "dragging && box && !(lessMotion && lessMotion.matches)" in follow
    assert APP_JS.count("followTheEdge(") == 3, "defined once and called from two listeners"
    for reported_by in ("dragenter", "dragover"):
        listener = APP_JS.split(f'document.addEventListener("{reported_by}", function (event) {{')[
            1
        ]
        assert listener.index("if (!dragging)") < listener.index("followTheEdge(event)")
    for stops_on in ('"drop"', "function endDrag()", "!event.relatedTarget"):
        after = APP_JS.split(stops_on)[1][:600]
        assert "stopEdgeScroll()" in after, stops_on
    assert re.search(r"scrollLeft \+= ", APP_JS), "the script moves the box, not a style"


def test_a_held_card_keeps_the_board_it_was_picked_up_from():
    """Nothing is asked for the board and no answer is put in it while a card is held."""
    for event in ('"htmx:confirm"', '"htmx:beforeSwap"'):
        handler = APP_JS.split(
            f"document.addEventListener({event}, function (event) {{\n    if (holdsTheBoard"
        )
        assert len(handler) == 2, event
    assert "event.detail.shouldSwap = false" in APP_JS
    assert 'window.htmx.trigger(filters, "submit")' in APP_JS


def test_a_heading_is_called_a_button_only_where_it_folds_in_place():
    ready = APP_JS.split("function readyBoardFolds(event) {")[1].split("\n  }\n")[0]
    assert "if (window.htmx)" in ready and 'setAttribute("role", "button")' in ready
    assert "onContentReady(readyBoardFolds)" in APP_JS
