"""An education entry states its EQF level, a whole number from 1 to 8 (#684).

The person's own claim, taken from a diploma and never worked out from the qualification's
name. It is a number, so it is never translated; the archive and the candidate file carry it,
and each reader holds it to the form's choices.
"""

from __future__ import annotations

import io
import json
import zipfile

import pytest
from django.urls import reverse

from postulo.core import export, importer
from postulo.resume import candidate
from postulo.resume.forms import EducationForm
from postulo.resume.models import Education
from postulo.resume.translatable import fields_for

pytestmark = pytest.mark.django_db

BASE = {"qualification": "MSc Computer Science", "institution": "Universidade de Lisboa"}


def an_education(user, **fields) -> Education:
    return Education.objects.create(owner=user, **{**BASE, **fields})


# ------------------------------------------------------------------------------ the form


def test_the_form_saves_a_level_and_clears_it(user):
    form = EducationForm({**BASE, "eqf_level": "7"}, user=user)
    assert form.is_valid(), form.errors
    item = form.save(commit=False)
    item.owner = user
    item.save()
    assert Education.objects.get(pk=item.pk).eqf_level == 7

    again = EducationForm({**BASE, "eqf_level": ""}, user=user, instance=item)
    assert again.is_valid(), again.errors
    again.save()
    assert Education.objects.get(pk=item.pk).eqf_level is None


def test_not_stated_is_the_first_choice_and_the_default(user):
    form = EducationForm(user=user)
    choices = list(form.fields["eqf_level"].choices)
    assert str(choices[0][1]) == "Not stated"
    assert [value for value, _label in choices[1:]] == list(range(1, 9))
    assert '<option value="" selected>Not stated</option>' in str(form["eqf_level"])
    assert Education().eqf_level is None


@pytest.mark.parametrize("value", ["0", "9", "-1", "six", "6.5", "٦"])
def test_the_form_refuses_what_is_not_a_level(user, value):
    form = EducationForm({**BASE, "eqf_level": value}, user=user)
    assert not form.is_valid()
    assert list(form.errors) == ["eqf_level"]


def test_a_level_is_a_number_and_is_never_translated():
    assert "eqf_level" not in fields_for(Education)


def test_the_preview_shows_it_and_says_nothing_where_there_is_none(client, user):
    an_education(user, eqf_level=7)
    an_education(user, qualification="Short course", institution="Evening school")
    client.force_login(user)
    html = client.get(reverse("resume:preview")).content.decode()
    assert html.count("EQF level 7") == 1
    assert "EQF level None" not in html


# ------------------------------------------------------------------------------ archive


def _archive_with(user, level) -> zipfile.ZipFile:
    document = export.build_document(user)
    document["resume"]["education"][0]["eqf_level"] = level
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w") as archive:
        archive.writestr(export.MANIFEST_NAME, json.dumps(document, default=str))
    buffer.seek(0)
    return zipfile.ZipFile(buffer)


def test_the_archive_carries_the_level_at_the_new_format(user, other_user):
    an_education(user, eqf_level=6)
    document = export.build_document(user)
    assert document["postulo"]["format"] == export.FORMAT_VERSION >= 44
    assert document["resume"]["education"][0]["eqf_level"] == 6

    importer.load(other_user, zipfile.ZipFile(export.write_archive(user)))
    assert Education.objects.get(owner=other_user).eqf_level == 6


def test_an_archive_without_it_restores_none(user, other_user):
    an_education(user, eqf_level=6)
    document = export.build_document(user)
    del document["resume"]["education"][0]["eqf_level"]
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w") as archive:
        archive.writestr(export.MANIFEST_NAME, json.dumps(document, default=str))
    buffer.seek(0)
    importer.load(other_user, zipfile.ZipFile(buffer))
    assert Education.objects.get(owner=other_user).eqf_level is None


@pytest.mark.parametrize("value", [0, 9, -3, 6.5, "6", "six", True, 10**30, [6]])
def test_the_archive_importer_drops_what_is_not_a_level_and_says_so(user, other_user, value):
    an_education(user, eqf_level=6)
    report = importer.load(other_user, _archive_with(user, value))
    assert Education.objects.get(owner=other_user).eqf_level is None
    assert any("EQF level" in line for line in report.skipped), report.skipped


# ------------------------------------------------------------------------ candidate file


def a_file(level) -> bytes:
    entry = {**BASE, "start_date": None, "end_date": None, "eqf_level": level}
    return json.dumps(
        {
            "postulo": {
                "candidate_format": export.CANDIDATE_FORMAT,
                "version": "0.5.0",
                "exported_at": "2026-09-28T10:00:00+00:00",
            },
            "resume": {"education": [entry]},
        }
    ).encode()


def the_row(user, data):
    plan = candidate.plan(user, candidate.read(data))
    (row,) = [row for s in plan.sections if s.key == "education" for row in s.rows]
    return row


def test_the_candidate_file_carries_the_level_round_trip(user, other_user):
    an_education(user, eqf_level=8)
    data = json.dumps(export.build_candidate_document(user)).encode()
    assert export.CANDIDATE_FORMAT >= 9
    assert json.loads(data)["resume"]["education"][0]["eqf_level"] == 8

    held = candidate.read(data)
    candidate.apply(other_user, held)
    assert Education.objects.get(owner=other_user).eqf_level == 8


@pytest.mark.parametrize("value", [9, 0, -1, "word", 6.5, True, 10**30, [6], {"a": 1}])
def test_a_level_that_is_not_one_is_a_refused_row(user, value):
    row = the_row(user, a_file(value))
    assert row.outcome == candidate.REFUSED
    assert any("EQF level" in note for note in row.notes), row.notes


def test_a_level_the_europass_import_reads_is_stored(user):
    from postulo.resume import importing

    record = importing.Record(
        education=[
            {
                "qualification": "BSc",
                "institution": "ISEL",
                "location": "",
                "start_date": None,
                "end_date": None,
                "grade": "17/20",
                "eqf_level": 6,
                "highlights": "",
            }
        ]
    )
    importing.apply(user, record)
    entry = Education.objects.get(owner=user)
    assert (entry.grade, entry.eqf_level) == ("17/20", 6)


def test_a_level_a_file_gives_is_added(user):
    assert the_row(user, a_file(5)).outcome == candidate.ADD
    candidate.apply(user, candidate.read(a_file(5)))
    assert Education.objects.get(owner=user).eqf_level == 5


# ------------------------------------------------------------ what a CV prints, under a switch

LINE = "EQF level 7"


def a_cv_with_a_level(user, **fields):
    from django.contrib.contenttypes.models import ContentType

    from postulo.documents.models import CV, CVItem

    user.first_name, user.last_name = "Alex", "Morgan"
    user.save(update_fields=["first_name", "last_name"])
    cv = CV.objects.create(owner=user, name="Main", **fields)
    for order, entry in enumerate(
        [
            an_education(user, eqf_level=7, location="Lisboa"),
            an_education(user, qualification="Short course", institution="Evening school"),
        ]
    ):
        CVItem.objects.create(
            owner=user,
            cv=cv,
            content_type=ContentType.objects.get_for_model(Education),
            object_id=entry.pk,
            order=order,
        )
    return cv


@pytest.mark.parametrize("theme", ["plain", "classic"])
@pytest.mark.parametrize("kind", ["cv", "portfolio"])
def test_with_the_switch_off_the_page_the_text_and_the_word_file_say_nothing_of_it(
    user, theme, kind
):
    from postulo.documents import formats, rendering

    cv = a_cv_with_a_level(user, theme=theme, kind=kind)
    assert cv.show_eqf_level is False
    assert "EQF" not in rendering.render_cv_html(cv)
    assert "EQF" not in rendering.cv_text(cv)
    outline = rendering.cv_outline(cv)
    with zipfile.ZipFile(io.BytesIO(formats.get("docx").write(outline))) as word:
        assert "EQF" not in word.read("word/document.xml").decode()


@pytest.mark.parametrize("theme", ["plain", "classic"])
@pytest.mark.parametrize("kind", ["cv", "portfolio"])
def test_with_the_switch_on_all_three_print_the_same_line_once(user, theme, kind):
    import re

    from postulo.documents import formats, printing, rendering

    cv = a_cv_with_a_level(user, theme=theme, kind=kind, show_eqf_level=True)
    assert not printing.is_default(cv)
    html = rendering.render_cv_html(cv)
    assert html.count(LINE) == 1, "once, and not for the entry that states none"
    text = rendering.cv_text(cv)
    assert text.count(LINE) == 1
    outline = rendering.cv_outline(cv)
    with zipfile.ZipFile(io.BytesIO(formats.get("docx").write(outline))) as word:
        assert word.read("word/document.xml").decode().count(LINE) == 1
    page = re.sub(r"\s+", " ", re.sub(r"<[^>]+>", " ", html))
    for line in text.splitlines():
        for part in re.split(r" · | — |: |, ", line.removeprefix(formats.BULLET)):
            assert part.strip() in page, f"{part!r} of {line!r} is not on the page"


def test_the_level_is_set_beside_the_institution_and_the_place(user):
    from postulo.documents import rendering

    text = rendering.cv_text(a_cv_with_a_level(user, show_eqf_level=True))
    assert "Universidade de Lisboa · Lisboa · EQF level 7" in text.splitlines()


def test_the_switch_is_in_the_cv_form_and_off_by_default(user):
    from postulo.documents.forms import CVForm

    assert not CVForm(user=user).fields["show_eqf_level"].initial
    data = {
        "name": "Main",
        "kind": "cv",
        "theme": "plain",
        "language": "",
        "show_contact_details": "on",
        "show_eqf_level": "on",
        "prints_phone": "default",
        "prints_email": "default",
        "prints_social": "default",
        "prints_repository": "default",
        "prints_website": "default",
        "prints_identifiers": "default",
    }
    form = CVForm(data, user=user)
    assert form.is_valid(), form.errors
    assert form.save(commit=False).show_eqf_level is True


def test_the_archive_and_the_api_carry_the_switch(client, user, other_user):
    from postulo.api.models import ApiToken
    from postulo.documents.models import CV

    cv = a_cv_with_a_level(user, show_eqf_level=True)
    prints = export.build_document(user)["documents"]["cvs"][0]["prints"]
    assert prints["eqf_level"] is True
    importer.load(other_user, zipfile.ZipFile(export.write_archive(user)))
    assert CV.objects.get(owner=other_user, name="Main").show_eqf_level is True

    _row, raw = ApiToken.issue(user, "Agent", scopes=("read", "write"))
    headers = {"HTTP_AUTHORIZATION": f"Bearer {raw}"}
    url = f"/api/v1/cvs/{cv.pk}"
    assert client.get(url, **headers).json()["prints"]["eqf_level"] is True
    response = client.patch(
        url,
        data=json.dumps({"prints": {"eqf_level": False}}),
        content_type="application/json",
        **headers,
    )
    assert response.status_code == 200, response.content
    assert response.json()["prints"]["eqf_level"] is False
    cv.refresh_from_db()
    assert cv.show_eqf_level is False


def test_an_archive_that_does_not_say_restores_the_switch_off(user, other_user):
    from postulo.documents.models import CV

    a_cv_with_a_level(user, show_eqf_level=True)
    document = export.build_document(user)
    del document["documents"]["cvs"][0]["prints"]["eqf_level"]
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w") as archive:
        archive.writestr(export.MANIFEST_NAME, json.dumps(document, default=str))
    buffer.seek(0)
    importer.load(other_user, zipfile.ZipFile(buffer))
    assert CV.objects.get(owner=other_user).show_eqf_level is False
