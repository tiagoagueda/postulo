"""Date and place of birth: optional, never worked out, and printed only where a CV says so (#679).

The date is the text of an ISO 8601 reduced form held to one rule, `core.personal`'s, which
the page, the API, the candidate file and the archive importer all run through. The place is
the text typed and a country's code. Neither is printed anywhere by default; a CV that
chooses prints one "Born ..." line, in the document's language.
"""

from __future__ import annotations

import datetime as dt
import io
import json
import re
import zipfile

import pytest
from django.core.exceptions import ValidationError
from django.urls import reverse
from django.utils import timezone, translation

from postulo.accounts.forms import ProfileForm
from postulo.api.models import ApiToken
from postulo.core import export, importer, personal
from postulo.core.search import search
from postulo.documents import formats, printing, rendering
from postulo.documents.forms import CVForm
from postulo.documents.models import CV
from postulo.resume import candidate

pytestmark = pytest.mark.django_db


def posted(**fields) -> dict:
    data = {
        "first_name": "Alex",
        "last_name": "Morgan",
        "form_of_address": "",
        "form_of_address_other": "",
        "pronouns": "",
        "pronouns_other": "",
        "headline": "",
        "location": "",
        "record_language": "",
    }
    data.update(fields)
    return data


def born(user, date="1990-03-12", place="Porto", country="PT"):
    user.first_name, user.last_name = "Alex", "Morgan"
    user.save()
    user.profile.birth_date, user.profile.birth_place = date, place
    user.profile.birth_country = country
    user.profile.save()
    return user


# ------------------------------------------------------------------------ the validator


@pytest.mark.parametrize(
    "text", ["1990", "1990-03", "1990-03-12", "1900", "1900-01-01", "2000-02-29"]
)
def test_a_year_a_year_and_month_and_a_date_are_dates_of_birth(text):
    personal.validate_birth_date(text)


@pytest.mark.parametrize(
    "text",
    [
        "1990-02-30",
        "1999-02-29",
        "1990-13",
        "1990-00",
        "1990-03-00",
        "1899",
        "1899-12-31",
        "9999",
        "next-year",
        "1990/03/12",
        "90",
        "1990-3",
        "١٩٩٠",
        "1990\x00",
        "1990-03-12 ",
        " 1990",
    ],
)
def test_anything_else_is_refused(text):
    with pytest.raises(ValidationError):
        personal.validate_birth_date(text)


def test_a_date_in_the_future_is_refused_and_today_is_not():
    today = timezone.localdate()
    personal.validate_birth_date(today.isoformat())
    personal.validate_birth_date(str(today.year))
    tomorrow = today + dt.timedelta(days=1)
    with pytest.raises(ValidationError) as refused:
        personal.validate_birth_date(tomorrow.isoformat())
    assert refused.value.code == "future"
    with pytest.raises(ValidationError):
        personal.validate_birth_date(str(today.year + 1))


def test_nothing_is_a_date_of_birth_too():
    personal.validate_birth_date("")


def test_the_column_carries_the_rule_so_every_form_of_it_does(user):
    user.profile.birth_date = "1990-02-30"
    with pytest.raises(ValidationError) as refused:
        user.profile.full_clean(exclude=["user"])
    assert "birth_date" in refused.value.message_dict
    user.profile.birth_date = ""
    user.profile.birth_country = "ZZ"
    with pytest.raises(ValidationError) as refused:
        user.profile.full_clean(exclude=["user"])
    assert "birth_country" in refused.value.message_dict


def test_the_boxes_join_into_the_column_and_split_back_out():
    assert personal.birth_date_from_parts("12", "3", "1990") == "1990-03-12"
    assert personal.birth_date_from_parts("", "3", "1990") == "1990-03"
    assert personal.birth_date_from_parts("", "", "1990") == "1990"
    assert personal.birth_date_from_parts("", "", "") == ""
    assert personal.birth_parts("1990-03-12") == ("12", "3", "1990")
    assert personal.birth_parts("1990-03") == ("", "3", "1990")
    assert personal.birth_parts("") == ("", "", "")
    for bad in (("12", "", "1990"), ("", "3", ""), ("1", "1", ""), ("x", "1", "1990")):
        with pytest.raises(ValidationError):
            personal.birth_date_from_parts(*bad)


def test_the_date_is_worded_in_the_language_it_is_printed_in():
    with translation.override("en-GB"):
        assert personal.birth_date_text("1990-03-12") == "12 Mar 1990"
        assert personal.birth_date_text("1990-03") == "March 1990"
        assert personal.birth_date_text("1990") == "1990"
    with translation.override("fr-FR"):
        assert personal.birth_date_text("1990-03-12") == "12 mars 1990"
    assert personal.birth_date_text("nonsense") == ""


@pytest.mark.parametrize(
    ("language", "order"),
    [
        ("en-GB", ("day", "month", "year")),
        ("pt-PT", ("day", "month", "year")),
        ("fr-FR", ("day", "month", "year")),
        ("en-US", ("month", "day", "year")),
        ("ja", ("year", "month", "day")),
    ],
)
def test_the_boxes_follow_the_order_the_language_writes_a_date(language, order):
    with translation.override(language):
        assert personal.box_order() == order


# ----------------------------------------------------------------------------- the page


def test_the_page_draws_three_boxes_in_a_fieldset_with_their_tokens(client, user):
    client.force_login(user)
    html = client.get(reverse("accounts:profile")).content.decode()
    card = html[html.index('id="section-personal"') :]
    card = card[: card.index('id="section-contact"')]
    assert "<legend" in card and "Date of birth" in card
    for token, name in (
        ("bday-day", "birth_day"),
        ("bday-month", "birth_month"),
        ("bday-year", "birth_year"),
    ):
        tag = re.search(rf'<input[^>]*name="{name}"[^>]*>', card).group(0)
        assert f'autocomplete="{token}"' in tag and 'inputmode="numeric"' in tag
    assert re.findall(r'name="birth_(day|month|year)"', card) == [
        "day",
        "month",
        "year",
    ]
    assert "Place of birth" in card and "Country of birth" in card
    assert "printed until a CV is set to print it" in " ".join(card.split())


def test_the_sidebar_lists_the_card(client, user):
    client.force_login(user)
    html = client.get(reverse("accounts:profile")).content.decode()
    assert 'href="#section-personal"' in html and "Personal details" in html


def test_the_page_opens_on_what_is_held(client, user):
    born(user)
    client.force_login(user)
    html = client.get(reverse("accounts:profile")).content.decode()
    for name, value in (("birth_day", "12"), ("birth_month", "3"), ("birth_year", "1990")):
        tag = re.search(rf'<input[^>]*name="{name}"[^>]*>', html).group(0)
        assert f'value="{value}"' in tag
    assert 'value="Porto"' in html
    assert re.search(r'<option value="PT"[^>]*selected', html)


def test_the_form_saves_each_and_clears_each(client, user):
    client.force_login(user)
    url = reverse("accounts:profile")
    response = client.post(
        url,
        posted(
            birth_day="12",
            birth_month="3",
            birth_year="1990",
            birth_place="Porto",
            birth_country="PT",
        ),
    )
    assert response.status_code == 302, response.content[:300]
    user.profile.refresh_from_db()
    assert (user.profile.birth_date, user.profile.birth_place, user.profile.birth_country) == (
        "1990-03-12",
        "Porto",
        "PT",
    )
    client.post(url, posted(birth_year="1990"))
    user.profile.refresh_from_db()
    assert user.profile.birth_date == "1990"
    assert (user.profile.birth_place, user.profile.birth_country) == ("", "")
    client.post(url, posted(birth_day="", birth_month="", birth_year=""))
    user.profile.refresh_from_db()
    assert user.profile.birth_date == ""


def test_a_page_posted_without_the_boxes_leaves_the_date_alone(user):
    born(user)
    form = ProfileForm(posted(birth_place="Porto", birth_country="PT"), instance=user.profile)
    assert form.is_valid(), form.errors
    form.save()
    user.profile.refresh_from_db()
    assert user.profile.birth_date == "1990-03-12"


@pytest.mark.parametrize(
    "boxes",
    [
        {"birth_day": "30", "birth_month": "2", "birth_year": "1990"},
        {"birth_day": "1", "birth_month": "1", "birth_year": "1899"},
        {"birth_day": "1", "birth_month": "1", "birth_year": "3000"},
        {"birth_day": "5", "birth_month": "", "birth_year": "1990"},
        {"birth_day": "", "birth_month": "5", "birth_year": ""},
        {"birth_day": "x", "birth_month": "", "birth_year": "1990"},
    ],
)
def test_a_bad_date_is_refused_and_nothing_is_stored(client, user, boxes):
    born(user)
    client.force_login(user)
    response = client.post(reverse("accounts:profile"), posted(**boxes))
    assert response.status_code == 200
    assert 'data-invalid="true"' in response.content.decode()
    user.profile.refresh_from_db()
    assert user.profile.birth_date == "1990-03-12"


def test_a_country_that_is_not_in_the_list_is_refused(user):
    form = ProfileForm(posted(birth_country="ZZ"), instance=user.profile)
    assert not form.is_valid() and "birth_country" in form.errors


# ------------------------------------------------------------------------ on a CV


def a_cv(user, **fields) -> CV:
    return CV.objects.create(owner=user, name="Main", **fields)


def test_nothing_is_printed_by_default(user):
    born(user)
    cv = a_cv(user)
    assert (cv.show_birth_date, cv.show_birth_place) == (False, False)
    details = rendering.contact_details(user, cv)
    assert details["birth_date"] == details["birth_place"] == ""
    assert details["personal"] == []
    html = rendering.render_cv_html(cv)
    text = rendering.cv_text(cv)
    for printed in ("Born", "1990", "Porto", "March"):
        assert printed not in html and printed not in text
    assert printing.is_default(cv)
    assert "Born" not in rendering.contact_details(user)["personal"]


@pytest.mark.parametrize("theme", ["plain", "classic"])
@pytest.mark.parametrize("kind", ["cv", "portfolio"])
def test_two_switches_print_one_line_in_the_preview_the_text_and_the_word_file(user, theme, kind):
    born(user)
    cv = a_cv(user, theme=theme, kind=kind, show_birth_date=True, show_birth_place=True)
    assert not printing.is_default(cv)
    line = "Born 12 Mar 1990 in Porto, Portugal"
    html = rendering.render_cv_html(cv)
    assert html.count("Born") == 1 and line in html
    assert rendering.cv_text(cv).count(line) == 1
    outline = rendering.cv_outline(cv)
    assert line in formats.get("txt").write(outline).decode()
    with zipfile.ZipFile(io.BytesIO(formats.get("docx").write(outline))) as word:
        assert line in word.read("word/document.xml").decode()


def test_each_switch_prints_its_own_part(user):
    born(user)
    only_date = rendering.contact_details(user, a_cv(user, show_birth_date=True))
    assert only_date["personal"] == ["Born 12 Mar 1990"]
    assert only_date["birth_place"] == ""
    cv = CV.objects.create(owner=user, name="Place", show_birth_place=True)
    only_place = rendering.contact_details(user, cv)
    assert only_place["personal"] == ["Born in Porto, Portugal"]
    assert only_place["birth_date"] == ""


def test_a_year_only_is_printed_as_a_year_and_a_blank_prints_nothing(user):
    born(user, date="1990", place="", country="")
    cv = a_cv(user, show_birth_date=True, show_birth_place=True)
    assert rendering.contact_details(user, cv)["personal"] == ["Born 1990"]
    user.profile.birth_date = ""
    user.profile.save()
    assert rendering.contact_details(user, cv)["personal"] == []
    assert "Born" not in rendering.render_cv_html(cv)


def test_the_line_is_in_the_documents_language_and_date_style(user):
    born(user)
    cv = a_cv(user, language="fr-FR", show_birth_date=True, show_birth_place=True)
    text = rendering.cv_text(cv)
    # The date is in the document's style; the word before it is a string the French catalogue
    # has yet to be given, so it is the source's until then.
    assert "12 mars 1990" in text and "Portugal" in text
    assert "12 Mar 1990" not in text


def test_what_was_typed_is_text_on_the_page(user):
    born(user, place="<b>x</b>")
    cv = a_cv(user, show_birth_place=True)
    html = rendering.render_cv_html(cv)
    assert "<b>x</b>" not in html and "&lt;b&gt;x&lt;/b&gt;" in html


def test_a_letter_prints_neither(user):
    born(user)
    assert rendering.contact_details(user)["personal"] == []


def test_the_cv_page_offers_the_two_switches_off_and_says_what_they_would_print(client, user):
    born(user)
    cv = a_cv(user)
    client.force_login(user)
    html = client.get(reverse("documents:cv_update", args=[cv.pk])).content.decode()
    for name in ("show_birth_date", "show_birth_place"):
        tag = re.search(rf'<input[^>]*name="{name}"[^>]*>', html).group(0)
        assert "checked" not in tag
    assert "Print your date of birth" in html and "Print your place of birth" in html
    assert "12 Mar 1990" in html and "Porto, Portugal" in html


def test_the_cv_form_saves_the_switches(user):
    form = CVForm(
        {
            "name": "Main",
            "kind": "cv",
            "theme": "plain",
            "language": "",
            "show_contact_details": "on",
            "show_birth_date": "on",
            "prints_phone": "default",
            "prints_email": "default",
            "prints_social": "default",
            "prints_repository": "default",
            "prints_website": "default",
            "prints_identifiers": "default",
        },
        user=user,
    )
    assert form.is_valid(), form.errors
    cv = form.save(commit=False)
    assert (cv.show_birth_date, cv.show_birth_place) == (True, False)


# ------------------------------------------------------------------ archive and candidate


def test_the_archive_carries_the_fields_and_the_switches_at_the_new_format(user, other_user):
    born(user)
    cv = a_cv(user, show_birth_date=True)
    document = export.build_document(user)
    assert document["postulo"]["format"] == export.FORMAT_VERSION >= 34
    profile = document["account"]["profile"]
    assert (profile["birth_date"], profile["birth_place"], profile["birth_country"]) == (
        "1990-03-12",
        "Porto",
        "PT",
    )
    prints = next(entry for entry in document["documents"]["cvs"] if entry["name"] == cv.name)[
        "prints"
    ]
    assert (prints["birth_date"], prints["birth_place"]) == (True, False)

    importer.load(other_user, zipfile.ZipFile(export.write_archive(user)))
    other_user.profile.refresh_from_db()
    assert other_user.profile.birth_date == "1990-03-12"
    assert other_user.profile.birth_place == "Porto"
    assert other_user.profile.birth_country == "PT"
    restored = CV.objects.get(owner=other_user, name=cv.name)
    assert (restored.show_birth_date, restored.show_birth_place) == (True, False)


def _archive_with(user, **profile) -> zipfile.ZipFile:
    document = export.build_document(user)
    document["account"]["profile"].update(profile)
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w") as archive:
        archive.writestr(export.MANIFEST_NAME, json.dumps(document, default=str))
    buffer.seek(0)
    return zipfile.ZipFile(buffer)


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("birth_date", "1990-02-30"),
        ("birth_date", "2999"),
        ("birth_date", "1899"),
        ("birth_date", "soon"),
        ("birth_date", 1990),
        ("birth_date", "1990\x00"),
        ("birth_country", "ZZ"),
        ("birth_country", "portugal"),
        ("birth_place", "x" * 121),
    ],
)
def test_the_archive_importer_drops_one_the_page_would_not_take_and_says_so(
    user, other_user, field, value
):
    born(user)
    report = importer.load(other_user, _archive_with(user, **{field: value}))
    other_user.profile.refresh_from_db()
    assert getattr(other_user.profile, field) == ""
    lines = [line for line in report.skipped if "was left out" in line]
    assert lines, report.skipped
    assert str(value)[:10].replace("\x00", "") in "".join(lines).replace("\\x00", "")


def test_the_candidate_file_carries_them_and_fills_a_blank(user, other_user):
    born(user)
    data = json.dumps(export.build_candidate_document(user)).encode()
    assert export.CANDIDATE_FORMAT >= 5
    held = candidate.read(data)
    assert held["details"]["birth_date"] == "1990-03-12"

    other_user.profile.birth_place = "Lisboa"
    other_user.profile.save()
    plan = candidate.plan(other_user, held)
    rows = {
        row.label: row
        for section in plan.sections
        if section.key == "details"
        for row in section.rows
    }
    assert rows["Date of birth"].outcome == candidate.ADD
    assert rows["Place of birth"].outcome == candidate.KEPT
    candidate.apply(other_user, held)
    other_user.profile.refresh_from_db()
    assert other_user.profile.birth_date == "1990-03-12"
    assert other_user.profile.birth_place == "Lisboa", "an answer is kept"
    assert other_user.profile.birth_country == "PT"


def test_a_malformed_date_in_a_candidate_file_is_a_refused_row_and_nothing_is_stored(
    user, other_user
):
    born(user)
    document = export.build_candidate_document(user)
    document["account"]["profile"]["birth_date"] = "1990-02-30"
    held = candidate.read(json.dumps(document).encode())
    plan = candidate.plan(other_user, held)
    refused = [
        row
        for section in plan.sections
        if section.key == "details"
        for row in section.rows
        if row.outcome == candidate.REFUSED
    ]
    assert [row.label for row in refused] == ["Date of birth"]
    candidate.apply(other_user, held)
    other_user.profile.refresh_from_db()
    assert other_user.profile.birth_date == ""
    assert other_user.profile.birth_place == "Porto", "the rest of the file is read"


# ------------------------------------------------------------------------------ the API


def bearer(user, *scopes) -> dict:
    _record, raw = ApiToken.issue(user, "Agent", scopes=scopes or ("read",))
    return {"HTTP_AUTHORIZATION": f"Bearer {raw}"}


def patch(client, payload, **headers):
    return client.patch(
        "/api/v1/profile", data=json.dumps(payload), content_type="application/json", **headers
    )


def test_the_api_reads_and_writes_them(client, user):
    response = patch(
        client,
        {"birth_date": " 1990-03 ", "birth_place": " Porto ", "birth_country": "pt"},
        **bearer(user, "write"),
    )
    assert response.status_code == 200, response.content
    body = response.json()
    assert (body["birth_date"], body["birth_place"], body["birth_country"]) == (
        "1990-03",
        "Porto",
        "PT",
    )
    read = client.get("/api/v1/profile", **bearer(user)).json()
    assert read["birth_date"] == "1990-03"
    cleared = patch(client, {"birth_date": "", "birth_place": ""}, **bearer(user, "write")).json()
    assert cleared["birth_date"] == cleared["birth_place"] == ""
    assert cleared["birth_country"] == "PT", "a field left out is left alone"


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("birth_date", "1990-02-30"),
        ("birth_date", "3000"),
        ("birth_date", "1899"),
        ("birth_date", "not a date"),
        ("birth_date", "1990-03-12T00:00"),
        ("birth_place", "x" * 121),
        ("birth_place", "a\x00b"),
        ("birth_country", "ZZ"),
        ("birth_country", "PRT"),
    ],
)
def test_the_api_refuses_what_the_page_would_and_names_the_field(client, user, field, value):
    born(user)
    response = patch(client, {field: value}, **bearer(user, "write"))
    assert response.status_code == 422, response.content
    assert field in json.dumps(response.json())
    user.profile.refresh_from_db()
    assert user.profile.birth_date == "1990-03-12" and user.profile.birth_place == "Porto"


def test_the_prints_of_a_cv_carry_the_two_switches(client, user):
    cv = a_cv(user)
    body = client.get(f"/api/v1/cvs/{cv.pk}", **bearer(user)).json()
    assert body["prints"]["birth_date"] is False and body["prints"]["birth_place"] is False
    response = client.patch(
        f"/api/v1/cvs/{cv.pk}",
        data=json.dumps({"prints": {"birth_date": True}}),
        content_type="application/json",
        **bearer(user, "write"),
    )
    assert response.status_code == 200, response.content
    cv.refresh_from_db()
    assert (cv.show_birth_date, cv.show_birth_place) == (True, False)


# ------------------------------------------------------------------------------ search


def test_nothing_here_is_found_by_search(user):
    born(user, place="Zanzibarville")
    for query in ("Zanzibarville", "1990-03-12"):
        groups = search(user, query)
        assert not [hit for group in groups for hit in group.hits], query
