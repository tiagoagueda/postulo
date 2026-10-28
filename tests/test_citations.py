"""Citation counts in the career (#697).

How often the person's published work has been cited, as a source counted it on a given day:
for all of it, or for one publication (#687). What is held here is that an entry is made,
edited, moved and deleted through the registry; that the count is a whole number from one to
ten million and the day is required and proposed as today; that only the owner's own papers
can be named, by the form and by the save; that a CV prints the one plural-aware line, with
the paper's title first where there is one, in both themes, the text and the Word file; and
that the archive and the candidate file carry the entries and the paper each one counts.
"""

from __future__ import annotations

import datetime as dt
import io
import json
import zipfile

import pytest
from django.contrib.contenttypes.models import ContentType
from django.urls import reverse
from django.utils import timezone

from postulo.core import export
from postulo.documents import docx, rendering
from postulo.documents.models import CV, CVItem
from postulo.resume import candidate, ordering
from postulo.resume.forms import CitationForm
from postulo.resume.models import Citation, Publication
from postulo.resume.registry import OVERVIEW_ORDER, SECTIONS

pytestmark = pytest.mark.django_db


def put_on(cv, entry, order: int = 0):
    return CVItem.objects.create(
        owner=cv.owner,
        cv=cv,
        content_type=ContentType.objects.get_for_model(type(entry)),
        object_id=entry.pk,
        order=order,
    )


def a_file(*blocks) -> bytes:
    return json.dumps(
        {
            "postulo": {"candidate_format": export.CANDIDATE_FORMAT, "version": "0.5.0"},
            "resume": dict(blocks),
        }
    ).encode()


def rows_of(user, data: bytes) -> list:
    plan = candidate.plan(user, candidate.read(data))
    return [row for section in plan.sections if section.key == "citations" for row in section.rows]


def paper(user, **changes) -> Publication:
    return Publication.objects.create(
        owner=user, **{"title": "On portals", "authors": "Morgan, Alex", "date": "2022", **changes}
    )


def citation(user, **changes) -> Citation:
    return Citation.objects.create(
        owner=user,
        **{"source": "Scopus", "count": 1240, "as_of": dt.date(2026, 9, 14), **changes},
    )


@pytest.fixture
def cv(user):
    return CV.objects.create(owner=user, name="Research", language="en-GB")


# ===================================================================== the section


def test_the_section_is_in_the_registry_after_the_publications():
    assert SECTIONS["citation"].model is Citation
    assert str(SECTIONS["citation"].plural) == "Citations"
    order = list(OVERVIEW_ORDER)
    assert order.index("citation") == order.index("publication") + 1


def test_a_count_is_made_edited_moved_and_deleted_through_the_registry(client, user):
    client.force_login(user)
    create = reverse("resume:item_create", args=["citation"])

    made = client.post(create, {"source": "Scopus", "count": "1240", "as_of": "2026-09-14"})
    assert made.status_code == 302
    second = client.post(create, {"source": "Google Scholar", "count": "2", "as_of": "2025-01-01"})
    assert second.status_code == 302
    mine = Citation.objects.for_user(user).get(source="Scopus")
    assert mine.publication is None, "empty is the body of work as a whole"

    edit = reverse("resume:item_update", args=["citation", mine.pk])
    changed = client.post(edit, {"source": "Web of Science", "count": "7", "as_of": "2026-10-01"})
    assert changed.status_code == 302
    mine.refresh_from_db()
    assert (mine.source, mine.count, mine.as_of) == ("Web of Science", 7, dt.date(2026, 10, 1))

    before = [c.source for c in Citation.objects.for_user(user)]
    other = Citation.objects.for_user(user).get(source="Google Scholar")
    client.post(reverse("resume:item_move", args=["citation", other.pk, "up"]))
    assert [c.source for c in Citation.objects.for_user(user)] == before[::-1]

    gone = reverse("resume:item_delete", args=["citation", mine.pk])
    assert client.post(gone).status_code == 302
    assert not Citation.objects.filter(pk=mine.pk).exists()


@pytest.mark.parametrize("count", ["1", "10000000", "1240"])
def test_a_count_from_one_to_ten_million_is_taken(user, count):
    form = CitationForm(data={"source": "Scopus", "count": count, "as_of": "2026-09-14"}, user=user)

    assert form.is_valid(), form.errors


@pytest.mark.parametrize("count", ["0", "-3", "10000001", "many", "1.5", ""])
def test_nought_a_negative_too_many_and_text_are_refused(user, count):
    form = CitationForm(data={"source": "Scopus", "count": count, "as_of": "2026-09-14"}, user=user)

    assert not form.is_valid()
    assert "count" in form.errors


def test_the_day_is_required_and_proposed_as_today(user):
    unbound = CitationForm(user=user)
    assert unbound.initial["as_of"] == timezone.localdate()
    assert unbound.fields["as_of"].required

    form = CitationForm(data={"source": "Scopus", "count": "3", "as_of": ""}, user=user)
    assert not form.is_valid()
    assert "as_of" in form.errors

    held = citation(user, as_of=dt.date(2020, 1, 1))
    assert CitationForm(user=user, instance=held).initial["as_of"] == dt.date(2020, 1, 1)


def test_the_form_offers_the_usual_sources_and_the_persons_own_first(client, user):
    citation(user, source="ResearchGate")
    client.force_login(user)

    html = client.get(reverse("resume:item_create", args=["citation"])).content.decode()

    assert 'list="citation-source-suggestions"' in html
    assert html.index("ResearchGate") < html.index("Google Scholar")
    for name in ("Scopus", "Web of Science", "Crossref", "OpenCitations"):
        assert name in html
    assert "Postulo counts nothing itself" in html


def test_a_source_that_is_not_suggested_is_kept_as_typed(user):
    form = CitationForm(
        data={"source": "Semantic Scholar", "count": "3", "as_of": "2026-09-14"}, user=user
    )

    assert form.is_valid(), form.errors


def test_an_entry_may_name_one_of_the_owners_publications(user, other_user):
    mine, theirs = paper(user), paper(other_user, title="Theirs")

    form = CitationForm(user=user)
    assert list(form.fields["publication"].queryset) == [mine]

    taken = CitationForm(
        data={"source": "Scopus", "count": "3", "as_of": "2026-09-14", "publication": mine.pk},
        user=user,
    )
    assert taken.is_valid(), taken.errors
    refused = CitationForm(
        data={"source": "Scopus", "count": "3", "as_of": "2026-09-14", "publication": theirs.pk},
        user=user,
    )
    assert not refused.is_valid()
    assert refused.errors["publication"] == ["Choose one of your own publications."]


def test_the_save_refuses_a_foreign_publication_whoever_writes_the_row(user, other_user):
    from django.core.exceptions import ValidationError

    theirs = paper(other_user)

    with pytest.raises(ValidationError):
        citation(user, publication=theirs)
    assert not Citation.objects.exists()
    mine = citation(user)
    mine.publication = theirs
    with pytest.raises(ValidationError):
        mine.save()


def test_deleting_a_publication_deletes_its_counts_and_takes_them_off_a_cv(user, cv):
    counted = paper(user)
    of_it = citation(user, publication=counted)
    whole = citation(user, source="Google Scholar")
    put_on(cv, of_it)

    counted.delete()

    assert list(Citation.objects.for_user(user)) == [whole]
    assert not cv.items.exists()


def test_a_new_count_lands_by_its_day(user):
    older = citation(user, source="Older", as_of=dt.date(2024, 1, 1), order=0)
    newer = citation(user, source="Newer", as_of=dt.date(2026, 1, 1), order=1)

    ordering.place_new(newer)

    assert [c.source for c in Citation.objects.for_user(user)] == ["Newer", "Older"]
    assert ordering.DATE_FIELDS["Citation"] == "as_of"
    assert older.pk


def test_the_career_page_lists_a_count_with_its_paper(client, user):
    citation(user, publication=paper(user), summary="Excluding self-citations")
    client.force_login(user)

    html = client.get(reverse("resume:overview")).content.decode()

    assert 'id="section-citation"' in html
    assert "On portals: cited 1,240 times (Scopus, September 2026)" in html
    assert "Excluding self-citations" in html


def test_the_preview_and_the_picker_know_it(client, user, cv):
    from postulo.documents.forms import AddCVItemsForm

    citation(user)
    client.force_login(user)

    assert "Cited 1,240 times" in client.get(reverse("resume:preview")).content.decode()
    assert "citation" in [group["slug"] for group in AddCVItemsForm(cv=cv).groups]


# ============================================================================= the CV


def test_the_line_says_how_often_by_whom_and_when():
    one = Citation(source="Scopus", count=1, as_of=dt.date(2026, 9, 14))
    many = Citation(source="Scopus", count=1240, as_of=dt.date(2026, 9, 14))

    assert one.cv_line == "Cited 1 time (Scopus, September 2026)"
    assert many.cv_line == "Cited 1,240 times (Scopus, September 2026)"


def test_the_line_is_in_the_language_being_read():
    from postulo.core import languages

    many = Citation(source="Scopus", count=1240, as_of=dt.date(2026, 9, 14))

    with languages.override("fr-FR"):
        line = many.cv_line

    assert "1,240" not in line, "French groups its thousands its own way"
    assert "2026" in line and "Scopus" in line


@pytest.mark.parametrize("theme", ["plain", "classic"])
def test_both_themes_print_the_line_with_the_title_first(user, cv, theme):
    cv.theme = theme
    cv.save(update_fields=["theme"])
    put_on(cv, citation(user, source="Google Scholar", count=3100), order=0)
    put_on(cv, citation(user, count=1, publication=paper(user)), order=1)

    html = rendering.render_cv_html(cv)

    assert "Citations" in html
    assert "Cited 3,100 times (Google Scholar, September 2026)" in html
    assert "On portals: cited 1 time (Scopus, September 2026)" in html
    assert "2026-09-14" not in html, "a month and a year, never the day"


def test_the_text_and_the_word_file_say_what_the_page_does(user, cv):
    put_on(cv, citation(user, publication=paper(user), summary="Excluding self-citations"))

    text = rendering.cv_text(cv)
    with zipfile.ZipFile(io.BytesIO(docx.write(rendering.cv_outline(cv)))) as package:
        document = package.read("word/document.xml").decode()

    line = "On portals: cited 1,240 times (Scopus, September 2026)"
    assert line in text and line in document
    assert "Excluding self-citations" in text and "Excluding self-citations" in document


def test_a_portfolio_prints_it_and_no_empty_heading_when_there_is_none(user):
    portfolio = CV.objects.create(owner=user, name="Work", kind="portfolio", language="en-GB")
    assert "Citations" not in rendering.render_cv_html(portfolio)

    put_on(portfolio, citation(user))

    assert "Cited 1,240 times (Scopus, September 2026)" in rendering.render_cv_html(portfolio)


def test_a_cv_prints_a_count_only_when_the_person_put_it_there(user, cv):
    citation(user)

    assert "Cited" not in rendering.cv_text(cv)


def test_a_translated_summary_prints_in_the_cvs_language(user):
    from postulo.resume.models import Translation

    mine = citation(user, summary="Excluding self-citations")
    Translation.objects.create(
        owner=user,
        content_type=ContentType.objects.get_for_model(Citation),
        object_id=mine.pk,
        language="fr-FR",
        field="summary",
        text="Hors autocitations",
    )
    french = CV.objects.create(owner=user, name="FR", language="fr-FR")
    put_on(french, mine)

    assert "Hors autocitations" in rendering.cv_text(french)


# ============================================================================ search


def test_search_finds_a_count_by_source_summary_and_paper(user):
    from postulo.core import search

    citation(user, source="Zebra Index", summary="Quokka only", publication=paper(user))

    for query in ("Zebra", "Quokka", "portals"):
        groups = [g for g in search.search(user, query) if g.kind == "career"]
        hits = [hit for group in groups for hit in group.hits if hit.subtitle == "citation count"]
        assert hits, query
        assert hits[0].url.endswith("#section-citation")


# ================================================================ the candidate file
#
# The whole-account archive's round trip is in `tests/test_export.py`.


def test_the_candidate_file_writes_a_count_and_its_paper_and_reads_them_back(user, other_user):
    citation(user, publication=paper(user), url="https://example.org/profile")
    citation(user, source="Google Scholar", count=3)
    document = export.build_candidate_document(user)

    assert document["postulo"]["candidate_format"] == export.CANDIDATE_FORMAT
    candidate.apply(other_user, candidate.read(json.dumps(document).encode()))

    back = {c.source: c for c in Citation.objects.for_user(other_user)}
    assert back["Scopus"].publication == Publication.objects.for_user(other_user).get()
    assert back["Scopus"].url == "https://example.org/profile"
    assert back["Google Scholar"].publication is None


# ============================================================== the candidate file's review


PAPER = {"id": 7, "title": "On portals", "entry_type": "article", "date": "2022"}
GOOD = {"id": 1, "source": "Scopus", "count": 1240, "as_of": "2026-09-14", "publication_id": 7}


def test_a_good_count_is_added_with_its_paper_and_the_same_one_is_then_there(user):
    data = a_file(("publications", [PAPER]), ("citations", [GOOD]))

    (row,) = rows_of(user, data)
    assert row.outcome == candidate.ADD
    assert row.label == "Cited 1,240 times (Scopus, September 2026)"
    assert row.sub == "On portals"

    candidate.apply(user, candidate.read(data))
    held = Citation.objects.for_user(user).get()
    assert held.publication.title == "On portals"

    (again,) = rows_of(user, data)
    assert again.outcome == candidate.PRESENT


def test_a_later_count_from_the_same_source_is_another_entry_like_one_held(user):
    candidate.apply(user, candidate.read(a_file(("publications", [PAPER]), ("citations", [GOOD]))))

    later = {**GOOD, "count": 1300, "as_of": "2026-10-20"}
    (row,) = rows_of(user, a_file(("publications", [PAPER]), ("citations", [later])))

    assert row.outcome == candidate.ADD
    assert "You have one like it with other dates. Both will be in your record." in row.notes


def test_the_same_source_for_another_paper_or_for_the_whole_is_another_count(user):
    candidate.apply(user, candidate.read(a_file(("publications", [PAPER]), ("citations", [GOOD]))))

    whole = {**GOOD, "publication_id": None}
    other = {"id": 8, "title": "On mirrors", "entry_type": "article", "date": "2023"}
    elsewhere = {**GOOD, "publication_id": 8}

    assert [r.outcome for r in rows_of(user, a_file(("citations", [whole])))] == [candidate.ADD]
    data = a_file(("publications", [other]), ("citations", [elsewhere]))
    assert [r.outcome for r in rows_of(user, data)] == [candidate.ADD]


def test_a_paper_the_file_does_not_hold_leaves_a_count_for_the_whole_and_says_so(user):
    data = a_file(("citations", [GOOD]))

    (row,) = rows_of(user, data)
    assert row.outcome == candidate.ADD
    assert any("not in the file" in note for note in row.notes)

    candidate.apply(user, candidate.read(data))
    assert Citation.objects.for_user(user).get().publication is None


def test_a_paper_the_file_cannot_add_leaves_a_count_for_the_whole_and_says_so(user):
    refused_paper = {**PAPER, "title": ""}
    data = a_file(("publications", [refused_paper]), ("citations", [GOOD]))

    (row,) = rows_of(user, data)

    assert row.outcome == candidate.ADD
    assert any("cannot be added" in note for note in row.notes)


@pytest.mark.parametrize(
    "bad",
    [
        {"count": 0},
        {"count": -1},
        {"count": 10_000_001},
        {"count": "many"},
        {"as_of": ""},
        {"as_of": "2026"},
        {"source": ""},
        {"url": "javascript:alert(1)"},
    ],
)
def test_a_refused_count_is_a_refused_row_and_stores_nothing(user, bad):
    data = a_file(("citations", [{**GOOD, "publication_id": None, **bad}]))

    assert [row.outcome for row in rows_of(user, data)] == [candidate.REFUSED]
    candidate.apply(user, candidate.read(data))
    assert not Citation.objects.for_user(user).exists()
