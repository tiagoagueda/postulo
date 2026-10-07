"""Presentations, conferences and seminars in the career: one section with a role and a type (#694).

Held here: the registry's create, edit, delete and move; a role and a type of "Not stated"
printing nothing; the line a CV prints, in both themes, the text and the Word file; the order a
new entry lands in; the archive and the candidate file, with a role or a type this version does
not list; and search.
"""

from __future__ import annotations

import datetime as dt
import io
import json
import zipfile

import pytest
from django.contrib.contenttypes.models import ContentType
from django.urls import reverse
from django.utils import translation

from postulo.core import export, importer, search
from postulo.documents import docx, rendering
from postulo.documents.models import CV, CVItem, CVKind
from postulo.resume import candidate
from postulo.resume.forms import ParticipationForm
from postulo.resume.models import Participation, ParticipationKind, ParticipationRole
from postulo.resume.registry import OVERVIEW_ORDER, SECTIONS

pytestmark = pytest.mark.django_db


def posted(**changes) -> dict:
    return {"event": "PyCon Portugal 2025", "role": "speaker", **changes}


def made(user, **changes) -> Participation:
    return Participation.objects.create(
        owner=user, **{"event": "PyCon Portugal 2025", "role": "speaker", **changes}
    )


def put_on(cv, entry, order: int = 0):
    return CVItem.objects.create(
        owner=cv.owner,
        cv=cv,
        content_type=ContentType.objects.get_for_model(type(entry)),
        object_id=entry.pk,
        order=order,
    )


@pytest.fixture
def cv(user):
    return CV.objects.create(owner=user, name="Talks", language="en-GB")


# ------------------------------------------------------------------ the registry


def test_the_section_is_registered_and_on_the_career_page(client, user):
    assert SECTIONS["participation"].model is Participation
    assert "participation" in OVERVIEW_ORDER
    made(user, title="Error budgets", kind="conference", start_date=dt.date(2025, 10, 17))
    client.force_login(user)

    html = client.get(reverse("resume:overview")).content.decode()

    assert 'id="section-participation"' in html
    assert "Presentations, conferences and seminars" in html
    assert "PyCon Portugal 2025" in html
    assert "Speaker" in html and "Conference" in html


def test_an_event_is_created_edited_deleted_and_moved_by_the_pages(client, user):
    client.force_login(user)
    create = reverse("resume:item_create", args=["participation"])

    assert client.post(create, posted(title="Error budgets", place="Lisbon")).status_code == 302
    first = Participation.objects.for_user(user).get()
    assert (first.event, first.role, first.title, first.place) == (
        "PyCon Portugal 2025",
        "speaker",
        "Error budgets",
        "Lisbon",
    )

    assert client.post(create, posted(event="EuroPython 2025", role="")).status_code == 302
    second = Participation.objects.for_user(user).get(event="EuroPython 2025")

    edit = reverse("resume:item_update", args=["participation", second.pk])
    assert client.post(edit, posted(event="EuroPython 2024", role="attendee")).status_code == 302
    second.refresh_from_db()
    assert (second.event, second.role) == ("EuroPython 2024", "attendee")

    move = reverse("resume:item_move", args=["participation", second.pk, "up"])
    assert client.post(move).status_code == 302
    assert Participation.objects.for_user(user).first().event == "EuroPython 2024"

    gone = reverse("resume:item_delete", args=["participation", second.pk])
    assert client.post(gone).status_code == 302
    assert Participation.objects.for_user(user).count() == 1


def test_a_new_event_lands_by_its_start_date(client, user):
    client.force_login(user)
    create = reverse("resume:item_create", args=["participation"])
    client.post(create, posted(event="Old", start_date="2019-06-01"))
    client.post(create, posted(event="Recent", start_date="2024-06-01"))
    client.post(create, posted(event="Middle", start_date="2021-06-01"))

    assert [e.event for e in Participation.objects.for_user(user)] == ["Recent", "Middle", "Old"]


def test_the_form_asks_for_the_event_and_the_role_first_and_says_what_each_is_for(client, user):
    client.force_login(user)

    html = client.get(reverse("resume:item_create", args=["participation"])).content.decode()

    assert list(ParticipationForm.Meta.fields[:2]) == ["event", "role"]
    assert html.index('name="event"') < html.index('name="role"') < html.index('name="title"')
    assert "What you presented, if you presented something." in html
    assert "belongs in Courses" in html and "paper in Publications" in html


def test_an_event_is_the_only_thing_required(user):
    form = ParticipationForm(data={"event": "A meetup"}, user=user)
    assert form.is_valid(), form.errors
    assert not ParticipationForm(data={"role": "speaker"}, user=user).is_valid()


def test_not_stated_is_accepted_for_the_role_and_the_type(user):
    form = ParticipationForm(data={"event": "A meetup", "role": "", "kind": ""}, user=user)
    assert form.is_valid(), form.errors
    assert form.cleaned_data["role"] == "" and form.cleaned_data["kind"] == ""


@pytest.mark.parametrize("field", ["role", "kind"])
def test_a_choice_outside_the_list_is_refused(user, field):
    form = ParticipationForm(data={"event": "A meetup", field: "keynote"}, user=user)
    assert not form.is_valid()
    assert field in form.errors


def test_both_lists_offer_not_stated_and_the_names_asked_for():
    assert [value for value, _label in ParticipationRole.choices] == [
        "",
        "speaker",
        "poster",
        "panellist",
        "workshop",
        "organiser",
        "committee",
        "attendee",
    ]
    assert [value for value, _label in ParticipationKind.choices] == [
        "",
        "conference",
        "seminar",
        "workshop",
        "other",
    ]


def test_an_end_before_the_start_is_refused(user):
    form = ParticipationForm(data=posted(start_date="2025-10-18", end_date="2025-10-17"), user=user)
    assert not form.is_valid()
    assert "end_date" in form.errors


# ------------------------------------------------------------ what a CV prints


@pytest.mark.parametrize("theme", ["plain", "classic"])
@pytest.mark.parametrize("kind", [CVKind.CV, CVKind.PORTFOLIO])
def test_both_themes_print_role_title_event_place_and_month(user, cv, theme, kind):
    cv.theme, cv.kind = theme, kind
    cv.save(update_fields=["theme", "kind"])
    put_on(
        cv,
        made(
            user,
            title="Error budgets",
            place="Lisbon, Portugal",
            start_date=dt.date(2025, 10, 17),
            summary="Service level objectives for a small team.",
        ),
    )

    html = rendering.render_cv_html(cv)

    assert "Speaker, “Error budgets”, PyCon Portugal 2025, Lisbon, Portugal" in html
    assert "October 2025" in html
    assert "Presentations, conferences and seminars" in html
    assert "Service level objectives for a small team." in html


def test_the_text_and_the_word_file_say_the_same_line(user, cv):
    put_on(
        cv,
        made(
            user,
            title="Error budgets",
            place="Lisbon",
            start_date=dt.date(2025, 10, 17),
            summary="What it was about.",
        ),
    )
    line = "Speaker, “Error budgets”, PyCon Portugal 2025, Lisbon · October 2025"

    text = rendering.cv_text(cv)
    with zipfile.ZipFile(io.BytesIO(docx.write(rendering.cv_outline(cv)))) as package:
        document = package.read("word/document.xml").decode()

    assert line in text
    assert line in document
    assert text.index(line) < text.index("What it was about.")
    assert "What it was about." in document


def test_a_role_and_a_type_of_not_stated_print_nothing(user, cv):
    put_on(cv, made(user, event="Local meetup", role="", kind=""))

    html = rendering.render_cv_html(cv)
    text = rendering.cv_text(cv)

    assert "Local meetup" in html
    assert "Not stated" not in html and "Not stated" not in text
    assert "Local meetup\n" in text + "\n"
    assert "“" not in text.split("Presentations, conferences and seminars")[1]
    assert "<p></p>" not in html


def test_an_entry_with_only_an_event_prints_just_that(user, cv):
    put_on(cv, Participation.objects.create(owner=user, event="A meetup"))

    text = rendering.cv_text(cv)

    assert "A meetup\n" in text + "\n"
    assert "·" not in text.split("Presentations, conferences and seminars")[1]
    assert ", ," not in text


def test_the_month_and_year_are_one_or_two(user):
    assert made(user).when == ""
    assert made(user, start_date=dt.date(2025, 10, 17)).when == "October 2025"
    same = made(user, start_date=dt.date(2025, 10, 17), end_date=dt.date(2025, 10, 18))
    assert same.when == "October 2025"
    span = made(user, start_date=dt.date(2025, 10, 30), end_date=dt.date(2025, 11, 2))
    assert span.when == "October 2025 – November 2025"
    assert made(user, end_date=dt.date(2025, 3, 1)).when == "March 2025"


def test_the_role_is_drawn_in_the_language_being_read(user):
    entry = made(user, role="workshop")
    with translation.override("en-GB"):
        assert entry.role_text == "Workshop leader"
    assert made(user, role="").role_text == ""
    assert made(user, kind="seminar").kind_text == "Seminar"
    assert made(user, kind="").kind_text == ""


def test_the_lists_use_contexts_of_their_own():
    """`Role` is a job and `Type` is typography in the catalogues: these are neither."""
    field = Participation._meta.get_field("role")
    assert field.verbose_name != "role"
    assert str(field.verbose_name) == "your role"
    assert str(Participation._meta.get_field("kind").verbose_name) == "type"


def test_a_cv_without_an_event_prints_no_heading(user, cv):
    made(user)

    assert "Presentations, conferences" not in rendering.render_cv_html(cv)
    assert "Presentations, conferences" not in rendering.cv_text(cv)


def test_a_portfolio_prints_it_without_an_empty_heading(user, cv):
    cv.kind = CVKind.PORTFOLIO
    cv.save(update_fields=["kind"])

    assert "Presentations, conferences" not in rendering.render_cv_html(cv)
    put_on(cv, made(user))
    assert "Presentations, conferences and seminars" in rendering.render_cv_html(cv)


def test_the_preview_prints_it_too(client, user, cv):
    put_on(cv, made(user, place="Lisbon"))
    client.force_login(user)

    html = client.get(reverse("documents:cv_preview", args=[cv.pk])).content.decode()

    assert "PyCon Portugal 2025" in html
    assert "Lisbon" in html


def test_the_career_preview_prints_it_too(client, user):
    made(user, title="Error budgets")
    client.force_login(user)

    html = client.get(reverse("resume:preview")).content.decode()

    assert "Presentations, conferences and seminars" in html
    assert "Error budgets" in html


def test_a_place_in_another_language_is_the_one_a_cv_in_it_prints(user, cv):
    from postulo.resume.models import Translation

    entry = made(user, place="Lisbon, Portugal", summary="What it was about.")
    for field, text in (("place", "Lisbonne, Portugal"), ("summary", "De quoi il s'agissait.")):
        Translation.objects.create(
            owner=user,
            content_type=ContentType.objects.get_for_model(Participation),
            object_id=entry.pk,
            field=field,
            language="fr-FR",
            text=text,
        )
    cv.language = "fr-FR"
    cv.save(update_fields=["language"])
    put_on(cv, entry)

    html = rendering.render_cv_html(cv)
    text = rendering.cv_text(cv)

    assert "Lisbonne, Portugal" in html and "Lisbonne, Portugal" in text
    assert "De quoi il s&#x27;agissait." in html or "De quoi il s'agissait." in html
    assert "PyCon Portugal 2025" in html


def test_the_picker_offers_an_event(user, cv):
    from postulo.documents.forms import AddCVItemsForm

    made(user)

    form = AddCVItemsForm(cv=cv)

    assert [group["slug"] for group in form.groups] == ["participation"]


# ------------------------------------------------------------------ search


def test_search_finds_an_event_by_event_title_and_place(user):
    made(user, event="Zorbcon", title="Quokka husbandry", place="Reykjavik")

    for query in ("Zorbcon", "Quokka", "Reykjavik"):
        hit = next(group for group in search.search(user, query) if group.kind == "career").hits[0]
        assert hit.title == "Zorbcon"
        assert hit.url.endswith("#section-participation")


# ------------------------------------------------------------------ the archive


def archive_of(user):
    document = export.build_document(user)
    return document, lambda doc: _zip(doc)


def _zip(document):
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w") as archive:
        archive.writestr(export.MANIFEST_NAME, json.dumps(document))
    buffer.seek(0)
    return zipfile.ZipFile(buffer)


def test_the_archive_carries_an_event_and_a_cv_holding_one_and_restores_both(user, other_user):
    mine = made(
        user,
        title="Error budgets",
        kind="conference",
        start_date=dt.date(2025, 10, 17),
        end_date=dt.date(2025, 10, 18),
        place="Lisbon",
        summary="Covered.",
        url="https://example.org/talk",
    )
    put_on(CV.objects.create(owner=user, name="Talks"), mine)
    document, pack = archive_of(user)

    assert document["postulo"]["format"] == export.FORMAT_VERSION
    assert document["resume"]["participations"][0]["role"] == "speaker"

    importer.load(other_user, pack(document))

    back = Participation.objects.for_user(other_user).get()
    assert (back.event, back.title, back.role, back.kind, back.place, back.url) == (
        mine.event,
        "Error budgets",
        "speaker",
        "conference",
        "Lisbon",
        "https://example.org/talk",
    )
    assert (back.start_date, back.end_date) == (mine.start_date, mine.end_date)
    kept = CV.objects.for_user(other_user).get()
    assert [item.item for item in kept.items.all()] == [back]


def test_an_older_archive_without_events_still_loads(user, other_user):
    made(user)
    document, pack = archive_of(user)
    del document["resume"]["participations"]
    document["postulo"]["format"] = export.FORMAT_VERSION - 1

    importer.load(other_user, pack(document))

    assert not Participation.objects.for_user(other_user).exists()


@pytest.mark.parametrize("field", ["role", "kind"])
@pytest.mark.parametrize("bad", ["keynote", 7, ["speaker"], True])
def test_the_importer_reads_a_choice_it_does_not_list_as_not_stated(user, other_user, field, bad):
    made(user, kind="seminar")
    document, pack = archive_of(user)
    document["resume"]["participations"][0][field] = bad

    report = importer.load(other_user, pack(document))

    back = Participation.objects.for_user(other_user).get()
    assert getattr(back, field) == ""
    assert any(field in line for line in report.skipped)


# ------------------------------------------------------------ the candidate file


def a_file(*rows) -> bytes:
    return json.dumps(
        {
            "postulo": {"candidate_format": export.CANDIDATE_FORMAT, "version": "0.5.0"},
            "resume": {"participations": list(rows)},
        }
    ).encode()


def outcomes(user, data: bytes) -> list[str]:
    plan = candidate.plan(user, candidate.read(data))
    return [row.outcome for section in plan.sections for row in section.rows]


def test_a_good_event_is_added_and_a_refused_one_is_not(user):
    data = a_file(
        {"id": 1, "event": "PyCon", "role": "speaker", "kind": "conference"},
        {"id": 2, "event": "EuroPython", "role": "keynote"},
        {"id": 3, "event": "A meetup", "role": "", "kind": ""},
    )

    assert outcomes(user, data) == [candidate.ADD, candidate.REFUSED, candidate.ADD]
    candidate.apply(user, candidate.read(data))

    assert sorted(p.event for p in Participation.objects.for_user(user)) == ["A meetup", "PyCon"]


def test_the_same_annual_event_in_another_year_is_not_the_same_entry(user):
    made(user, event="PyCon", title="Talk", start_date=dt.date(2024, 10, 1))
    same = {
        "id": 1,
        "event": "pycon",
        "title": "Talk",
        "role": "speaker",
        "start_date": "2024-10-01",
    }
    other_year = {**same, "id": 2, "start_date": "2025-10-01"}

    assert outcomes(user, a_file(same)) == [candidate.PRESENT]
    assert outcomes(user, a_file(other_year)) == [candidate.ADD]


def test_the_candidate_file_writes_an_event_and_reads_it_back(user, other_user):
    made(user, title="Error budgets", kind="conference", start_date=dt.date(2025, 10, 17))
    document = export.build_candidate_document(user)

    assert document["postulo"]["candidate_format"] == export.CANDIDATE_FORMAT
    candidate.apply(other_user, candidate.read(json.dumps(document).encode()))

    back = Participation.objects.for_user(other_user).get()
    assert (back.event, back.role, back.kind, back.start_date) == (
        "PyCon Portugal 2025",
        "speaker",
        "conference",
        dt.date(2025, 10, 17),
    )
