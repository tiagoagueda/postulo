"""A driving licence in the career: the categories as codes, and never the number (#691).

Held here: the table of the fifteen categories; the form that ticks them and the line a CV
prints from them, in both themes, the text and the Word file; that the archive and the
candidate file carry a licence and refuse a code that is not on the table; and that the model
has no field for anything else a licence holds.
"""

from __future__ import annotations

import datetime
import io
import json
import zipfile

import pytest
from django.contrib.contenttypes.models import ContentType
from django.urls import reverse

from postulo.core import export, importer
from postulo.documents import docx, rendering
from postulo.documents.models import CV, CVItem
from postulo.resume import candidate, driving
from postulo.resume.forms import DrivingLicenceForm
from postulo.resume.models import DrivingLicence
from postulo.resume.registry import OVERVIEW_ORDER, SECTIONS

pytestmark = pytest.mark.django_db

FIFTEEN = ["AM", "A1", "A2", "A", "B1", "B", "BE", "C1", "C1E", "C", "CE", "D1", "D1E", "D", "DE"]


def posted(**changes) -> dict:
    return {"country": "", "categories": ["B"], "other_categories": "", **changes}


def licence(user, **changes) -> DrivingLicence:
    return DrivingLicence.objects.create(
        owner=user, **{"country": "PT", "categories": ["A2", "B"], **changes}
    )


def put_on(cv, entry, order: int = 0):
    return CVItem.objects.create(
        owner=cv.owner,
        cv=cv,
        content_type=ContentType.objects.get_for_model(type(entry)),
        object_id=entry.pk,
        order=order,
    )


# ------------------------------------------------------------------ the table


def test_the_table_holds_the_fifteen_categories_in_the_directives_order():
    assert list(driving.CODES) == FIFTEEN
    assert all(str(summary) for _code, summary in driving.CATEGORIES)


def test_the_section_is_after_certifications_and_has_its_own_slug():
    assert SECTIONS["driving-licence"].model is DrivingLicence
    assert OVERVIEW_ORDER.index("driving-licence") == OVERVIEW_ORDER.index("certification") + 1


# ------------------------------------------------------------------ the form


def test_several_categories_save_and_show_in_the_directives_order(user):
    form = DrivingLicenceForm(
        data=posted(categories=["DE", "B", "A2"], country="PT"),
        user=user,
        instance=DrivingLicence(owner=user),
    )
    assert form.is_valid(), form.errors
    saved = form.save()

    assert saved.categories == ["A2", "B", "DE"], "stored as the table has them, not as ticked"
    assert saved.codes == "A2, B, DE"


@pytest.mark.parametrize("bad", ["XX", "b", "B2", "<script>"])
def test_a_code_outside_the_table_is_refused_by_the_form(user, bad):
    form = DrivingLicenceForm(
        data=posted(categories=["B", bad]), user=user, instance=DrivingLicence(owner=user)
    )

    assert not form.is_valid()
    assert "categories" in form.errors


def test_a_licence_says_something_or_is_refused(user):
    empty = DrivingLicenceForm(
        data=posted(categories=[]), user=user, instance=DrivingLicence(owner=user)
    )
    only_national = DrivingLicenceForm(
        data=posted(categories=[], other_categories="Class 5"),
        user=user,
        instance=DrivingLicence(owner=user),
    )

    assert not empty.is_valid()
    assert only_national.is_valid(), only_national.errors


def test_the_other_categories_are_bounded(user):
    long = DrivingLicenceForm(
        data=posted(other_categories="x" * (driving.MAX_OTHER + 1)),
        user=user,
        instance=DrivingLicence(owner=user),
    )
    fits = DrivingLicenceForm(
        data=posted(other_categories="x" * driving.MAX_OTHER),
        user=user,
        instance=DrivingLicence(owner=user),
    )

    assert not long.is_valid()
    assert fits.is_valid(), fits.errors


def test_a_country_is_one_on_the_list_or_nothing(user):
    wrong = DrivingLicenceForm(
        data=posted(country="ZZ"), user=user, instance=DrivingLicence(owner=user)
    )

    assert not wrong.is_valid()
    assert "country" in wrong.errors


def test_an_expiry_before_the_first_issue_is_refused(user):
    form = DrivingLicenceForm(
        data=posted(first_issued_on="2020-01-01", expires_on="2019-01-01"),
        user=user,
        instance=DrivingLicence(owner=user),
    )

    assert not form.is_valid()
    assert "expires_on" in form.errors


def test_one_person_may_hold_two_licences(user):
    licence(user, country="PT", categories=["B"])
    licence(user, country="GB", categories=["B"])

    assert DrivingLicence.objects.for_user(user).count() == 2


def test_the_page_makes_a_licence_and_the_categories_are_a_fieldset(client, user):
    client.force_login(user)
    url = reverse("resume:item_create", args=["driving-licence"])

    html = client.get(url).content.decode()
    assert "<fieldset" in html and "<legend>" in html
    assert "cars and light vans" in html

    sent = client.post(url, posted(categories=["B", "A"], country="PT"))
    assert sent.status_code == 302
    assert DrivingLicence.objects.for_user(user).get().categories == ["A", "B"]


def test_the_career_page_lists_the_licence(client, user):
    licence(user)
    client.force_login(user)

    html = client.get(reverse("resume:overview")).content.decode()

    assert 'id="section-driving-licence"' in html
    assert "A2, B (Portugal)" in html


# ------------------------------------------------------- what is never kept


def test_the_model_has_no_field_for_a_number_a_photograph_a_signature_or_a_restriction():
    """The field list, asserted: a column of that kind fails here before it is a decision."""
    kept = {field.name for field in DrivingLicence._meta.get_fields() if field.concrete}

    assert kept == {
        "id",
        "owner",
        "created_at",
        "updated_at",
        "order",
        "country",
        "categories",
        "other_categories",
        "first_issued_on",
        "expires_on",
    }
    assert not any(
        word in name
        for name in kept
        for word in ("number", "photo", "signature", "residence", "restriction", "code")
    )


def test_the_form_and_the_archive_ask_for_nothing_beyond_those_fields():
    assert set(DrivingLicenceForm.base_fields) <= {
        "country",
        "categories",
        "other_categories",
        "first_issued_on",
        "expires_on",
        "order",
    }
    assert set(export.RESUME_FIELDS["driving_licences"]) <= {
        "id",
        "country",
        "categories",
        "other_categories",
        "first_issued_on",
        "expires_on",
        "order",
    }


# --------------------------------------------------------------- what a CV prints


@pytest.fixture
def cv(user):
    return CV.objects.create(owner=user, name="Drivers", language="en-GB")


@pytest.mark.parametrize("theme", ["plain", "classic"])
def test_both_themes_print_one_line_with_the_country_and_no_date(user, cv, theme):
    cv.theme = theme
    cv.save(update_fields=["theme"])
    put_on(
        cv,
        licence(
            user, first_issued_on=datetime.date(2001, 5, 17), expires_on=datetime.date(2031, 5, 17)
        ),
    )

    html = rendering.render_cv_html(cv)

    assert "Driving licence: A2, B (Portugal)" in html
    assert "2001" not in html and "2031" not in html


def test_the_country_is_printed_only_where_it_is_filled(user, cv):
    put_on(cv, licence(user, country="", categories=["B"]))

    html = rendering.render_cv_html(cv)

    assert "Driving licence: B<" in html
    assert "(" not in html.split("Driving licence: B")[1].split("<")[0]


def test_the_text_the_word_file_and_the_page_say_the_same(user, cv):
    put_on(cv, licence(user))

    text = rendering.cv_text(cv)
    words = docx.write(rendering.cv_outline(cv))
    with zipfile.ZipFile(io.BytesIO(words)) as package:
        document = package.read("word/document.xml").decode()

    assert "Driving licence: A2, B (Portugal)" in text
    assert "Driving licence: A2, B (Portugal)" in document
    assert "Driving licence: A2, B (Portugal)" in rendering.render_cv_html(cv)


def test_the_preview_prints_it_too(client, user, cv):
    put_on(cv, licence(user))
    client.force_login(user)

    html = client.get(reverse("documents:cv_preview", args=[cv.pk])).content.decode()

    assert "Driving licence: A2, B (Portugal)" in html


def test_a_cv_without_the_entry_prints_nothing_about_driving(user, cv):
    licence(user)

    assert "iving licence" not in rendering.render_cv_html(cv)
    assert "iving licence" not in rendering.cv_text(cv)


def test_the_picker_offers_the_entry(user, cv):
    from postulo.documents.forms import AddCVItemsForm

    licence(user)

    form = AddCVItemsForm(cv=cv)

    assert [group["slug"] for group in form.groups] == ["driving-licence"]


def test_the_line_is_in_the_documents_language(user, cv):
    cv.language = "fr-FR"
    cv.save(update_fields=["language"])
    put_on(cv, licence(user))

    assert "A2, B" in rendering.cv_text(cv)


# --------------------------------------------------------------------- the archive


def read_archive(user):
    document = export.build_document(user)
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w") as archive:
        archive.writestr(export.MANIFEST_NAME, json.dumps(document))
    buffer.seek(0)
    return zipfile.ZipFile(buffer), document


def test_the_archive_carries_the_licence_and_a_restore_brings_it_back(user, other_user):
    mine = licence(user, first_issued_on=datetime.date(2001, 5, 17), other_categories="Class 5")
    cv = CV.objects.create(owner=user, name="Drivers")
    put_on(cv, mine)
    archive, document = read_archive(user)

    assert document["postulo"]["format"] == export.FORMAT_VERSION == 51
    assert document["resume"]["driving_licences"][0]["categories"] == ["A2", "B"]
    assert "number" not in json.dumps(document["resume"]["driving_licences"])

    importer.load(other_user, archive)

    back = DrivingLicence.objects.for_user(other_user).get()
    assert (back.country, back.categories, back.other_categories) == ("PT", ["A2", "B"], "Class 5")
    assert back.first_issued_on == datetime.date(2001, 5, 17)
    kept = CV.objects.for_user(other_user).get()
    assert [item.item for item in kept.items.all()] == [back]


@pytest.mark.parametrize("bad", [["B", "ZZ"], ["B", "B"], "B", [7], []])
def test_the_importer_refuses_a_licence_with_a_code_off_the_table(user, other_user, bad):
    licence(user)
    _archive, document = read_archive(user)
    document["resume"]["driving_licences"][0]["categories"] = bad
    document["resume"]["driving_licences"][0]["other_categories"] = ""
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w") as archive:
        archive.writestr(export.MANIFEST_NAME, json.dumps(document))
    buffer.seek(0)

    importer.load(other_user, zipfile.ZipFile(buffer))

    assert not DrivingLicence.objects.for_user(other_user).exists()


def test_the_importer_bounds_the_note_and_blanks_a_country_that_is_not_one(user, other_user):
    licence(user)
    _archive, document = read_archive(user)
    row = document["resume"]["driving_licences"][0]
    row["other_categories"] = "x" * 500
    row["country"] = "ZZ"
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w") as archive:
        archive.writestr(export.MANIFEST_NAME, json.dumps(document))
    buffer.seek(0)

    importer.load(other_user, zipfile.ZipFile(buffer))

    back = DrivingLicence.objects.for_user(other_user).get()
    assert len(back.other_categories) == driving.MAX_OTHER
    assert back.country == ""


# ------------------------------------------------------------ the candidate file


def a_file(*rows) -> bytes:
    return json.dumps(
        {
            "postulo": {"candidate_format": export.CANDIDATE_FORMAT, "version": "0.5.0"},
            "resume": {"driving_licences": list(rows)},
        }
    ).encode()


def outcomes(user, data: bytes) -> list[str]:
    plan = candidate.plan(user, candidate.read(data))
    return [row.outcome for section in plan.sections for row in section.rows]


def test_a_good_licence_is_added_in_the_tables_order(user):
    data = a_file({"id": 1, "country": "PT", "categories": ["B", "A2"]})

    assert outcomes(user, data) == [candidate.ADD]
    candidate.apply(user, candidate.read(data))

    assert DrivingLicence.objects.for_user(user).get().categories == ["A2", "B"]


@pytest.mark.parametrize("bad", [["ZZ"], ["B", 7], "B", {"B": 1}, ["x" * 100], ["B", None]])
def test_a_code_off_the_table_is_a_refused_row(user, bad):
    data = a_file({"id": 1, "country": "PT", "categories": bad})

    assert outcomes(user, data) == [candidate.REFUSED]
    candidate.apply(user, candidate.read(data))
    assert not DrivingLicence.objects.for_user(user).exists()


def test_the_same_licence_in_another_order_is_already_there(user):
    licence(user, country="PT", categories=["A2", "B"])
    data = a_file({"id": 1, "country": "PT", "categories": ["B", "A2"]})

    assert outcomes(user, data) == [candidate.PRESENT]
    candidate.apply(user, candidate.read(data))
    assert DrivingLicence.objects.for_user(user).count() == 1


def test_the_same_categories_in_another_country_are_another_licence(user):
    licence(user, country="PT", categories=["B"])
    data = a_file({"id": 1, "country": "GB", "categories": ["B"]})

    assert outcomes(user, data) == [candidate.ADD]


def test_the_candidate_file_writes_the_licence_and_reads_it_back(user, other_user):
    licence(user)
    document = export.build_candidate_document(user)

    assert document["postulo"]["candidate_format"] == export.CANDIDATE_FORMAT
    candidate.apply(other_user, candidate.read(json.dumps(document).encode()))

    assert DrivingLicence.objects.for_user(other_user).get().codes == "A2, B"
