"""Courses in the career: learning taken, with a provider, a length and a link (#695).

Held here: the registry's create, edit, delete and move; the bounds on the hours, by the form,
the archive and the candidate file; the line a CV prints, in both themes, the text and the Word
file; the order a new entry lands in; and that the form says where a course ends and
Education and Certifications begin.
"""

from __future__ import annotations

import datetime as dt
import io
import json
import zipfile

import pytest
from django.contrib.contenttypes.models import ContentType
from django.urls import reverse

from postulo.core import export, importer, search
from postulo.documents import docx, rendering
from postulo.documents.models import CV, CVItem, CVKind
from postulo.resume import candidate
from postulo.resume.forms import CourseForm
from postulo.resume.models import Course
from postulo.resume.registry import OVERVIEW_ORDER, SECTIONS

pytestmark = pytest.mark.django_db


def posted(**changes) -> dict:
    return {"title": "Site Reliability Engineering", "provider": "Linux Foundation", **changes}


def course(user, **changes) -> Course:
    return Course.objects.create(
        owner=user,
        **{"title": "Site Reliability Engineering", "provider": "Linux Foundation", **changes},
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
    return CV.objects.create(owner=user, name="Learning", language="en-GB")


# ------------------------------------------------------------------ the registry


def test_the_section_is_registered_and_on_the_career_page(client, user):
    assert SECTIONS["course"].model is Course
    assert "course" in OVERVIEW_ORDER
    course(user, hours=40)
    client.force_login(user)

    html = client.get(reverse("resume:overview")).content.decode()

    assert 'id="section-course"' in html
    assert "Linux Foundation" in html
    assert "40 hours" in html


def test_a_course_is_created_edited_deleted_and_moved_by_the_pages(client, user):
    client.force_login(user)
    create = reverse("resume:item_create", args=["course"])

    assert client.post(create, posted(hours="40")).status_code == 302
    first = Course.objects.for_user(user).get()
    assert (first.title, first.provider, first.hours) == (
        "Site Reliability Engineering",
        "Linux Foundation",
        40,
    )

    client.post(create, posted(title="Evening pottery", provider=""))
    second = Course.objects.for_user(user).get(title="Evening pottery")
    assert (first.order, second.order) == (0, 1)

    edit = reverse("resume:item_update", args=["course", second.pk])
    assert client.post(edit, posted(title="Evening pottery II", provider="")).status_code == 302
    second.refresh_from_db()
    assert second.title == "Evening pottery II"

    move = reverse("resume:item_move", args=["course", second.pk, "up"])
    client.post(move)
    assert [c.title for c in Course.objects.for_user(user)] == [
        "Evening pottery II",
        "Site Reliability Engineering",
    ]

    gone = reverse("resume:item_delete", args=["course", second.pk])
    assert client.post(gone).status_code == 302
    assert Course.objects.for_user(user).count() == 1


def test_a_new_course_lands_by_its_end_date(client, user):
    client.force_login(user)
    create = reverse("resume:item_create", args=["course"])
    client.post(create, posted(title="Old", end_date="2019-06-01"))
    client.post(create, posted(title="Recent", end_date="2024-06-01"))
    client.post(create, posted(title="Middle", end_date="2021-06-01"))

    assert [c.title for c in Course.objects.for_user(user)] == ["Recent", "Middle", "Old"]


def test_the_form_says_where_a_course_ends_and_the_other_sections_begin(client, user):
    client.force_login(user)

    html = client.get(reverse("resume:item_create", args=["course"])).content.decode()

    assert "belongs in Education" in html
    assert "belongs in Certifications" in html
    assert "belongs in Education" in client.get(reverse("resume:overview")).content.decode()


# ------------------------------------------------------------------ the hours


@pytest.mark.parametrize("good", ["1", "40", "10000"])
def test_hours_from_one_to_ten_thousand_are_taken(user, good):
    form = CourseForm(data=posted(hours=good), user=user, instance=Course(owner=user))

    assert form.is_valid(), form.errors


@pytest.mark.parametrize("bad", ["0", "-3", "10001", "99999999999", "forty", "2.5"])
def test_any_other_hours_are_refused(user, bad):
    form = CourseForm(data=posted(hours=bad), user=user, instance=Course(owner=user))

    assert not form.is_valid()
    assert "hours" in form.errors


def test_the_hours_may_be_left_out(user):
    form = CourseForm(data=posted(hours=""), user=user, instance=Course(owner=user))

    assert form.is_valid(), form.errors
    assert form.save().hours is None


def test_an_end_before_the_start_is_refused(user):
    form = CourseForm(
        data=posted(start_date="2024-05-01", end_date="2024-01-01"),
        user=user,
        instance=Course(owner=user),
    )

    assert not form.is_valid()
    assert "end_date" in form.errors


# ------------------------------------------------------------ what a CV prints


@pytest.mark.parametrize("theme", ["plain", "classic"])
@pytest.mark.parametrize("kind", [CVKind.CV, CVKind.PORTFOLIO])
def test_both_themes_print_title_provider_hours_and_year(user, cv, theme, kind):
    cv.theme, cv.kind = theme, kind
    cv.save(update_fields=["theme", "kind"])
    put_on(
        cv,
        course(
            user,
            hours=40,
            start_date=dt.date(2022, 9, 1),
            end_date=dt.date(2022, 11, 30),
            summary="Service level objectives and error budgets.",
        ),
    )

    html = rendering.render_cv_html(cv)

    assert "Site Reliability Engineering</bdi>, <bdi>Linux Foundation" in html
    assert "40 hours" in html
    assert "2022" in html
    assert "Courses" in html
    assert "Service level objectives and error budgets." in html


def test_the_line_is_plural_aware_and_prints_one_year_or_two(user):
    assert course(user, hours=1).hours_text == "1 hour"
    assert course(user, hours=40).hours_text == "40 hours"
    assert course(user).hours_text == ""
    assert course(user, end_date=dt.date(2020, 5, 1)).years_text == "2020"
    both = course(user, start_date=dt.date(2020, 5, 1), end_date=dt.date(2021, 2, 1))
    assert both.years_text == "2020–2021"
    same = course(user, start_date=dt.date(2020, 1, 1), end_date=dt.date(2020, 3, 1))
    assert same.years_text == "2020"
    assert course(user).years_text == ""


def test_the_text_and_the_word_file_say_the_same_line(user, cv):
    put_on(
        cv,
        course(user, hours=40, end_date=dt.date(2022, 11, 30), summary="What it covered."),
    )
    line = "Site Reliability Engineering, Linux Foundation · 40 hours · 2022"

    text = rendering.cv_text(cv)
    with zipfile.ZipFile(io.BytesIO(docx.write(rendering.cv_outline(cv)))) as package:
        document = package.read("word/document.xml").decode()

    assert line in text
    assert line in document
    assert text.index(line) < text.index("What it covered.")
    assert "What it covered." in document


def test_an_entry_with_only_a_title_prints_just_that(user, cv):
    put_on(cv, Course.objects.create(owner=user, title="Welding basics"))

    html = rendering.render_cv_html(cv)
    text = rendering.cv_text(cv)

    assert "Welding basics" in html
    assert "Welding basics\n" in text + "\n"
    assert "·" not in text.split("Courses")[1]
    assert "hour" not in html.lower().split("welding basics")[1]
    assert "<p></p>" not in html


def test_a_cv_without_a_course_prints_no_courses_heading(user, cv):
    course(user)

    assert "Courses" not in rendering.render_cv_html(cv)
    assert "Courses" not in rendering.cv_text(cv)


def test_the_preview_prints_it_too(client, user, cv):
    put_on(cv, course(user, hours=40))
    client.force_login(user)

    html = client.get(reverse("documents:cv_preview", args=[cv.pk])).content.decode()

    assert "Linux Foundation" in html
    assert "40 hours" in html


def test_a_summary_in_another_language_is_the_one_a_cv_in_it_prints(user, cv):
    from postulo.resume.models import Translation

    entry = course(user, summary="What it covered.")
    Translation.objects.create(
        owner=user,
        content_type=ContentType.objects.get_for_model(Course),
        object_id=entry.pk,
        field="summary",
        language="fr-FR",
        text="Ce que cela couvrait.",
    )
    cv.language = "fr-FR"
    cv.save(update_fields=["language"])
    put_on(cv, entry)

    html = rendering.render_cv_html(cv)

    assert "Ce que cela couvrait." in html
    assert "Linux Foundation" in html


def test_the_picker_offers_a_course(user, cv):
    from postulo.documents.forms import AddCVItemsForm

    course(user)

    form = AddCVItemsForm(cv=cv)

    assert [group["slug"] for group in form.groups] == ["course"]


# ------------------------------------------------------------------ search


def test_search_finds_a_course_by_title_and_provider(user):
    course(user, title="Welding basics", provider="Zorbco Academy")

    by_title = search.search(user, "Welding")
    by_provider = search.search(user, "Zorbco")

    for found in (by_title, by_provider):
        hit = next(group for group in found if group.kind == "career").hits[0]
        assert hit.title == "Welding basics"
        assert hit.url.endswith("#section-course")


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


def test_the_archive_carries_a_course_and_a_cv_holding_one_and_restores_both(user, other_user):
    mine = course(
        user,
        hours=40,
        start_date=dt.date(2022, 9, 1),
        end_date=dt.date(2022, 11, 30),
        summary="Covered.",
        url="https://example.org/c",
    )
    put_on(CV.objects.create(owner=user, name="Learning"), mine)
    document, pack = archive_of(user)

    assert document["postulo"]["format"] == export.FORMAT_VERSION
    assert document["resume"]["courses"][0]["hours"] == 40

    importer.load(other_user, pack(document))

    back = Course.objects.for_user(other_user).get()
    assert (back.title, back.provider, back.hours, back.url) == (
        mine.title,
        mine.provider,
        40,
        "https://example.org/c",
    )
    assert (back.start_date, back.end_date) == (mine.start_date, mine.end_date)
    kept = CV.objects.for_user(other_user).get()
    assert [item.item for item in kept.items.all()] == [back]


def test_an_older_archive_without_courses_still_loads(user, other_user):
    course(user)
    document, pack = archive_of(user)
    del document["resume"]["courses"]
    document["postulo"]["format"] = export.FORMAT_VERSION - 1

    importer.load(other_user, pack(document))

    assert not Course.objects.for_user(other_user).exists()


@pytest.mark.parametrize("bad", [0, -1, 10_001, "40", 2.5, True])
def test_the_importer_leaves_out_hours_the_page_would_refuse(user, other_user, bad):
    course(user, hours=40)
    document, pack = archive_of(user)
    document["resume"]["courses"][0]["hours"] = bad

    report = importer.load(other_user, pack(document))

    back = Course.objects.for_user(other_user).get()
    assert back.hours is None
    assert any("hours" in line for line in report.skipped)


# ------------------------------------------------------------ the candidate file


def a_file(*rows) -> bytes:
    return json.dumps(
        {
            "postulo": {"candidate_format": export.CANDIDATE_FORMAT, "version": "0.5.0"},
            "resume": {"courses": list(rows)},
        }
    ).encode()


def outcomes(user, data: bytes) -> list[str]:
    plan = candidate.plan(user, candidate.read(data))
    return [row.outcome for section in plan.sections for row in section.rows]


def test_a_good_course_is_added_and_a_refused_one_is_not(user):
    data = a_file(
        {"id": 1, "title": "Welding", "provider": "Academy", "hours": 12},
        {"id": 2, "title": "Pottery", "hours": 0},
    )

    assert outcomes(user, data) == [candidate.ADD, candidate.REFUSED]
    candidate.apply(user, candidate.read(data))

    assert [c.title for c in Course.objects.for_user(user)] == ["Welding"]


def test_the_same_course_is_already_there(user):
    course(user, title="Welding", provider="Academy")
    data = a_file({"id": 1, "title": "welding", "provider": "Academy"})

    assert outcomes(user, data) == [candidate.PRESENT]


def test_the_candidate_file_writes_a_course_and_reads_it_back(user, other_user):
    course(user, hours=40)
    document = export.build_candidate_document(user)

    candidate.apply(other_user, candidate.read(json.dumps(document).encode()))

    assert Course.objects.for_user(other_user).get().hours == 40
