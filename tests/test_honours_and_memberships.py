"""Honours and awards, and memberships, in the career (#693).

Two sections of the career record, each a small typed entry: what is held here is that each
is made, edited, deleted and moved through the registry, that a CV prints the one line a
certification is printed as, in both themes, the text and the Word file, that a new entry is
placed by its date, that the forms carry the rules for choosing between an honour, a
certification and a grade, and that the archive and the candidate file carry them.
"""

from __future__ import annotations

import datetime as dt
import io
import json
import zipfile

import pytest
from django.contrib.contenttypes.models import ContentType
from django.urls import reverse

from postulo.core import export, importer
from postulo.documents import docx, rendering
from postulo.documents.models import CV, CVItem
from postulo.resume import candidate, ordering
from postulo.resume.models import Honour
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


def read_archive(user):
    document = export.build_document(user)
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w") as archive:
        archive.writestr(export.MANIFEST_NAME, json.dumps(document))
    buffer.seek(0)
    return zipfile.ZipFile(buffer), document


def a_file(block: str, *rows) -> bytes:
    return json.dumps(
        {
            "postulo": {"candidate_format": export.CANDIDATE_FORMAT, "version": "0.5.0"},
            "resume": {block: list(rows)},
        }
    ).encode()


def outcomes(user, data: bytes) -> list[str]:
    plan = candidate.plan(user, candidate.read(data))
    return [row.outcome for section in plan.sections for row in section.rows]


@pytest.fixture
def cv(user):
    return CV.objects.create(owner=user, name="Prizes", language="en-GB")


# ======================================================================== honours


def honour(user, **changes) -> Honour:
    return Honour.objects.create(
        owner=user,
        **{
            "title": "Best paper",
            "awarded_by": "Lisbon Systems Workshop",
            "awarded_on": dt.date(2022, 9, 15),
            **changes,
        },
    )


def test_the_section_is_in_the_registry_after_the_certifications():
    assert SECTIONS["honour"].model is Honour
    assert str(SECTIONS["honour"].plural) == "Honours and awards"
    order = list(OVERVIEW_ORDER)
    assert order.index("honour") == order.index("certification") + 1


def test_an_honour_is_made_edited_moved_and_deleted_through_the_registry(client, user):
    client.force_login(user)
    create = reverse("resume:item_create", args=["honour"])

    assert client.post(create, {"title": ""}).status_code == 200, "a title is required"
    made = client.post(
        create,
        {"title": "Best paper", "awarded_by": "LSW", "awarded_on": "2022-09-15", "summary": "x"},
    )
    assert made.status_code == 302
    second = client.post(create, {"title": "Scholarship"})
    assert second.status_code == 302
    mine = Honour.objects.for_user(user).get(title="Best paper")

    edit = reverse("resume:item_update", args=["honour", mine.pk])
    assert client.post(edit, {"title": "Best student paper"}).status_code == 302
    mine.refresh_from_db()
    assert (mine.title, mine.awarded_by, mine.awarded_on) == ("Best student paper", "", None)

    other = Honour.objects.for_user(user).get(title="Scholarship")
    before = [h.title for h in Honour.objects.for_user(user)]
    client.post(reverse("resume:item_move", args=["honour", other.pk, "down"]))
    after = [h.title for h in Honour.objects.for_user(user)]
    assert after == before[::-1]

    gone = reverse("resume:item_delete", args=["honour", mine.pk])
    assert client.post(gone).status_code == 302
    assert not Honour.objects.filter(pk=mine.pk).exists()


def test_the_career_page_lists_an_honour_with_who_gave_it_and_the_year(client, user):
    honour(user)
    client.force_login(user)

    html = client.get(reverse("resume:overview")).content.decode()

    assert 'id="section-honour"' in html
    assert "Best paper" in html and "Lisbon Systems Workshop" in html and "2022" in html


def test_a_new_honour_lands_by_its_date(user):
    undated = honour(user, title="Undated", awarded_on=None, order=0)
    older = honour(user, title="Older", awarded_on=dt.date(2015, 1, 1), order=1)
    newer = honour(user, title="Newer", awarded_on=dt.date(2021, 1, 1), order=2)

    ordering.place_new(newer)

    assert [h.title for h in Honour.objects.for_user(user)] == ["Undated", "Newer", "Older"]
    assert ordering.DATE_FIELDS["Honour"] == "awarded_on"
    assert older.pk and undated.pk


def test_the_form_says_how_an_honour_differs_from_a_certification_and_a_grade(client, user):
    client.force_login(user)

    html = client.get(reverse("resume:item_create", args=["honour"])).content.decode()

    assert "nothing to check and no expiry" in html
    assert "certification" in html and "grade" in html
    assert "Only the year prints" in html


@pytest.mark.parametrize("theme", ["plain", "classic"])
def test_both_themes_print_title_giver_and_the_year(user, cv, theme):
    cv.theme = theme
    cv.save(update_fields=["theme"])
    put_on(cv, honour(user))

    html = rendering.render_cv_html(cv)

    assert "Best paper, Lisbon Systems Workshop" in html
    assert ">2022<" in html
    assert "Honours and awards" in html
    assert "2022-09-15" not in html and "15 September" not in html


def test_the_text_and_the_word_file_say_what_the_page_does(user, cv):
    put_on(cv, honour(user))

    text = rendering.cv_text(cv)
    with zipfile.ZipFile(io.BytesIO(docx.write(rendering.cv_outline(cv)))) as package:
        document = package.read("word/document.xml").decode()

    assert "Best paper, Lisbon Systems Workshop · 2022" in text
    assert "Best paper, Lisbon Systems Workshop · 2022" in document


def test_an_honour_without_a_giver_or_a_date_prints_just_its_title(user, cv):
    put_on(cv, honour(user, awarded_by="", awarded_on=None))

    assert "Best paper" in rendering.cv_text(cv)
    assert "Best paper," not in rendering.cv_text(cv)
    assert "Best paper ·" not in rendering.cv_text(cv)


def test_a_portfolio_prints_it_and_no_empty_heading_when_there_is_none(user):
    portfolio = CV.objects.create(owner=user, name="Work", kind="portfolio", language="en-GB")
    assert "Honours and awards" not in rendering.render_cv_html(portfolio)

    put_on(portfolio, honour(user))

    assert "Best paper, Lisbon Systems Workshop" in rendering.render_cv_html(portfolio)


def test_the_preview_and_the_picker_know_it(client, user, cv):
    from postulo.documents.forms import AddCVItemsForm

    honour(user)
    client.force_login(user)

    assert "Best paper" in client.get(reverse("resume:preview")).content.decode()
    assert "honour" in [group["slug"] for group in AddCVItemsForm(cv=cv).groups]


def test_the_summary_is_the_only_thing_an_honour_translates():
    from postulo.resume import translating

    assert translating.fields_for(Honour) == ("summary",)


def test_search_finds_an_honour_by_title_and_by_giver(client, user):
    from postulo.core import search

    honour(user, title="Zebra prize", awarded_by="Quokka Society")

    for query in ("Zebra", "Quokka"):
        (group,) = [g for g in search.search(user, query) if g.kind == "career"]
        assert group.hits[0].url.endswith("#section-honour")


# ------------------------------------------------------------ the archive


def test_the_archive_round_trips_an_honour_and_a_cv_that_holds_it(user, other_user):
    mine = honour(user, summary="For the portals paper", url="https://example.org/prize")
    put_on(CV.objects.create(owner=user, name="Prizes"), mine)
    archive, document = read_archive(user)

    assert document["postulo"]["format"] == export.FORMAT_VERSION
    assert document["resume"]["honours"][0]["awarded_by"] == "Lisbon Systems Workshop"

    importer.load(other_user, archive)

    back = Honour.objects.for_user(other_user).get()
    assert (back.title, back.awarded_by, back.awarded_on) == (
        "Best paper",
        "Lisbon Systems Workshop",
        dt.date(2022, 9, 15),
    )
    assert (back.summary, back.url) == ("For the portals paper", "https://example.org/prize")
    assert [i.item for i in CV.objects.for_user(other_user).get().items.all()] == [back]


def test_an_honours_summary_translation_travels_in_the_archive(user, other_user):
    from postulo.resume.models import Translation

    mine = honour(user, summary="English")
    Translation.objects.create(
        owner=user,
        content_type=ContentType.objects.get_for_model(Honour),
        object_id=mine.pk,
        language="fr-FR",
        field="summary",
        text="Français",
    )
    archive, _document = read_archive(user)

    importer.load(other_user, archive)

    back = Honour.objects.for_user(other_user).get()
    assert back.translations.get().text == "Français"


# ------------------------------------------------------- the candidate file


def test_a_good_honour_is_added_and_the_same_one_is_already_there(user):
    row = {"id": 1, "title": "Best paper", "awarded_by": "LSW", "awarded_on": "2022-09-15"}

    assert outcomes(user, a_file("honours", row)) == [candidate.ADD]
    candidate.apply(user, candidate.read(a_file("honours", row)))
    assert Honour.objects.for_user(user).get().awarded_on == dt.date(2022, 9, 15)

    assert outcomes(user, a_file("honours", row)) == [candidate.PRESENT]
    other_year = {**row, "awarded_on": "2023-09-15"}
    assert outcomes(user, a_file("honours", other_year)) == [candidate.ADD]
    other_giver = {**row, "awarded_by": "Somebody else"}
    assert outcomes(user, a_file("honours", other_giver)) == [candidate.ADD]


@pytest.mark.parametrize(
    "bad",
    [
        {"title": ""},
        {"title": "x" * 500},
        {"title": "A", "awarded_on": "2022"},
        {"title": "A", "awarded_on": "not a date"},
        {"title": "A", "url": "javascript:alert(1)"},
        {"title": ["A"]},
    ],
)
def test_a_refused_honour_is_a_refused_row_and_stores_nothing(user, bad):
    data = a_file("honours", {"id": 1, **bad})

    assert outcomes(user, data) == [candidate.REFUSED]
    candidate.apply(user, candidate.read(data))
    assert not Honour.objects.for_user(user).exists()


def test_the_candidate_file_writes_an_honour_and_reads_it_back(user, other_user):
    honour(user)
    document = export.build_candidate_document(user)

    assert document["postulo"]["candidate_format"] == export.CANDIDATE_FORMAT
    candidate.apply(other_user, candidate.read(json.dumps(document).encode()))

    assert Honour.objects.for_user(other_user).get().title == "Best paper"
