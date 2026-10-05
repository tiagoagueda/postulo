"""Gender, in your own words, optional, and never printed or inferred unless you say so (#681).

It sits in the *Personal details* card beside the two #309 put next to the name, and follows
the same promises: the person gives it in their own words or not at all, it is stored as the
text itself, nothing is ever worked out from any other field, and no document prints it until
a CV is set to.
"""

from __future__ import annotations

import io
import json
import re
import zipfile

import pytest
from django.urls import reverse

from postulo.accounts import addressing
from postulo.accounts.forms import ProfileForm
from postulo.api.models import ApiToken
from postulo.core import export, importer
from postulo.core.search import search
from postulo.documents import formats, printing, rendering
from postulo.documents.models import CV
from postulo.jobs.models import Contact
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
        "gender": "",
        "gender_other": "",
        "headline": "",
        "location": "",
        "record_language": "",
    }
    data.update(fields)
    return data


def named(user, **profile):
    user.first_name, user.last_name = "Alex", "Morgan"
    user.save()
    for name, value in profile.items():
        setattr(user.profile, name, value)
    user.profile.save()
    return user


def options(html: str, name: str) -> list[str]:
    select = re.search(rf'<select name="{name}"[^>]*>(.*?)</select>', html, re.S)
    assert select, f"no menu called {name}"
    return re.findall(r'<option value="([^"]*)"', select.group(1))


def chosen(html: str, name: str) -> str:
    select = re.search(rf'<select name="{name}"[^>]*>(.*?)</select>', html, re.S)
    found = re.search(r'<option value="([^"]*)"[^>]*selected', select.group(1))
    return found.group(1) if found else ""


def page(client, user) -> str:
    client.force_login(user)
    return client.get(reverse("accounts:profile")).content.decode()


# ------------------------------------------------------------------------- the lists


@pytest.mark.parametrize(
    ("language", "words"),
    [
        ("en-GB", ("Woman", "Man", "Non-binary")),
        ("fr-FR", ("Femme", "Homme", "Non binaire")),
        ("pt-PT", ("Mulher", "Homem", "Não binário")),
        ("pt-BR", ("Mulher", "Homem", "Não binário")),
    ],
)
def test_each_language_has_its_own_list(language, words):
    assert addressing.genders(language) == words


def test_a_language_with_no_list_offers_only_other():
    assert addressing.genders("de") == ()


def test_the_module_names_where_the_lists_come_from():
    assert (
        "Gender (#681)" in addressing.__doc__ and "Oxford English Dictionary" in addressing.__doc__
    )
    assert "GENDER:;Non-binary" in addressing.__doc__, "the vCard mapping is recorded"


def test_the_menu_offers_not_stated_the_list_and_other(client, user):
    named(user, record_language="en-GB")
    assert options(page(client, user), "gender") == ["", "Woman", "Man", "Non-binary", "other"]
    named(user, record_language="fr-FR")
    assert options(page(client, user), "gender") == ["", "Femme", "Homme", "Non binaire", "other"]
    named(user, record_language="de")
    assert options(page(client, user), "gender") == ["", "other"]


def test_nothing_is_chosen_for_somebody_who_has_said_nothing(client, user):
    html = page(client, user)
    assert chosen(html, "gender") == ""
    assert re.search(r'<input type="text" name="gender_other"[^>]*>', html)
    assert 'value=""' in re.search(r'<input[^>]*name="gender_other"[^>]*>', html).group(0) or (
        "value=" not in re.search(r'<input[^>]*name="gender_other"[^>]*>', html).group(0)
    )


def test_a_stored_word_in_the_list_is_chosen_and_one_that_is_not_opens_other(client, user):
    named(user, gender="Man", record_language="en-GB")
    html = page(client, user)
    assert chosen(html, "gender") == "Man"
    named(user, gender="agender", record_language="en-GB")
    html = page(client, user)
    assert chosen(html, "gender") == "other"
    assert 'value="agender"' in re.search(r'<input[^>]*name="gender_other"[^>]*>', html).group(0)


def test_the_field_is_labelled_described_and_carries_its_token(client, user):
    named(user, record_language="pt-PT")
    html = page(client, user)
    tag = re.search(r'<select name="gender"[^>]*>', html).group(0)
    assert 'autocomplete="sex"' in tag
    assert 'aria-describedby="personal-details-help"' in tag
    assert '<label for="id_gender">Gender</label>' in html
    assert 'id="personal-details-help"' in html
    box = re.search(r'<input[^>]*name="gender_other"[^>]*>', html).group(0)
    assert 'autocomplete="sex"' in box and 'dir="auto"' in box
    # The options say what language they are in, so a screen reader says them in it.
    assert re.search(r'<option value="Mulher"[^>]*lang="pt-PT"', html)
    assert re.search(r'<option value="Mulher"[^>]*dir="ltr"', html)
    assert not re.search(r'<option value=""[^>]*lang=', html), "Not stated is the interface's"


def test_a_refused_gender_names_its_error_in_its_description(client, user):
    client.force_login(user)
    html = client.post(reverse("accounts:profile"), posted(gender="g" * 41)).content.decode()
    tag = re.search(r'<select name="gender"[^>]*>', html).group(0)
    assert 'aria-describedby="personal-details-help id_gender_error"' in tag
    assert html.count('id="id_gender_error"') == 1


# ------------------------------------------------------------------------------ the form


def test_a_listed_value_saves_an_other_text_saves_and_a_blank_clears(client, user):
    client.force_login(user)
    url = reverse("accounts:profile")
    assert client.post(url, posted(gender="Non-binary")).status_code == 302
    user.profile.refresh_from_db()
    assert user.profile.gender == "Non-binary"
    client.post(url, posted(gender="other", gender_other=" genderqueer "))
    user.profile.refresh_from_db()
    assert user.profile.gender == "genderqueer"
    client.post(url, posted())
    user.profile.refresh_from_db()
    assert user.profile.gender == ""


def test_a_value_outside_the_list_is_kept_as_text(user):
    """The menu accepts what it is sent, as #309 does: a page drawn in another language."""
    form = ProfileForm(posted(gender="Femme"), instance=user.profile)
    assert form.is_valid(), form.errors
    assert form.cleaned_data["gender"] == "Femme"


def test_a_box_left_behind_is_not_the_answer(user):
    form = ProfileForm(posted(gender="Man", gender_other="stale"), instance=user.profile)
    assert form.is_valid() and form.cleaned_data["gender"] == "Man"


def test_other_with_nothing_typed_is_refused_and_says_where(user):
    form = ProfileForm(posted(gender="other"), instance=user.profile)
    assert not form.is_valid()
    assert form.errors["gender_other"] == ["Type it here, or choose one from the list."]


def test_it_is_held_to_forty_characters_and_refuses_a_nul(user):
    assert ProfileForm(posted(gender="g" * 40), instance=user.profile).is_valid()
    assert "gender" in ProfileForm(posted(gender="g" * 41), instance=user.profile).errors
    long_other = ProfileForm(posted(gender="other", gender_other="g" * 41), instance=user.profile)
    assert not long_other.is_valid() and "gender_other" in long_other.errors
    nul = ProfileForm(posted(gender="other", gender_other="a\x00b"), instance=user.profile)
    assert not nul.is_valid() and "gender_other" in nul.errors


# ----------------------------------------------------------- independent of the other two


def test_setting_a_gender_changes_neither_the_form_of_address_nor_the_pronouns(client, user):
    named(user, form_of_address="Dr", pronouns="they/them", record_language="en-GB")
    client.force_login(user)
    client.post(
        reverse("accounts:profile"),
        posted(form_of_address="Dr", pronouns="they/them", gender="Woman"),
    )
    user.profile.refresh_from_db()
    assert (user.profile.form_of_address, user.profile.pronouns) == ("Dr", "they/them")
    assert user.profile.gender == "Woman"


def test_and_the_reverse(client, user):
    named(user, gender="Man", record_language="en-GB")
    client.force_login(user)
    client.post(
        reverse("accounts:profile"),
        posted(gender="Man", form_of_address="Mx", pronouns="she/her"),
    )
    user.profile.refresh_from_db()
    assert user.profile.gender == "Man"
    assert (user.profile.form_of_address, user.profile.pronouns) == ("Mx", "she/her")


@pytest.mark.parametrize("gender", ["", "Woman", "Man", "Non-binary", "agender"])
def test_what_the_other_two_offer_does_not_depend_on_it(client, user, gender):
    named(user, gender=gender, record_language="en-GB")
    html = page(client, user)
    assert options(html, "form_of_address") == ["", *addressing.FORMS_OF_ADDRESS["en"], "other"]
    assert options(html, "pronouns") == ["", *addressing.PRONOUNS["en"], "other"]


def test_nothing_in_the_lists_module_reads_one_to_choose_another():
    """No function takes a gender and gives back a form of address or pronouns, or the other
    way round: the three are read by language alone."""
    import inspect

    for function in (addressing.genders, addressing.forms_of_address, addressing.pronouns):
        assert list(inspect.signature(function).parameters) == ["language"]


def test_a_contact_has_no_gender():
    from postulo.jobs.forms import ContactForm

    assert "gender" not in {field.name for field in Contact._meta.get_fields()}
    assert "gender" not in ContactForm._meta.fields and "gender" not in export.CONTACT_FIELDS


# ------------------------------------------------------------------------ on a CV


def a_cv(user, **fields) -> CV:
    return CV.objects.create(owner=user, name="Main", **fields)


def test_nothing_is_printed_by_default(user):
    named(user, gender="Woman")
    cv = a_cv(user)
    assert cv.show_gender is False and printing.is_default(cv)
    assert rendering.contact_details(user, cv)["gender"] == ""
    html, text = rendering.render_cv_html(cv), rendering.cv_text(cv)
    assert "Gender" not in html and "Woman" not in html and "Woman" not in text


@pytest.mark.parametrize("theme", ["plain", "classic"])
@pytest.mark.parametrize("kind", ["cv", "portfolio"])
def test_the_switch_prints_one_line_in_the_preview_the_text_and_the_word_file(user, theme, kind):
    named(user, gender="Woman")
    cv = a_cv(user, theme=theme, kind=kind, show_gender=True)
    assert rendering.render_cv_html(cv).count("Gender: Woman") == 1
    assert rendering.cv_text(cv).count("Gender: Woman") == 1
    outline = rendering.cv_outline(cv)
    assert "Gender: Woman" in formats.get("txt").write(outline).decode()
    with zipfile.ZipFile(io.BytesIO(formats.get("docx").write(outline))) as word:
        assert "Gender: Woman" in word.read("word/document.xml").decode()


def test_it_is_one_of_the_personal_details_on_their_line(user):
    named(user, gender="Man", birth_date="1990", nationalities=["PT"])
    cv = a_cv(user, show_birth_date=True, show_nationality=True, show_gender=True)
    assert rendering.contact_details(user, cv)["personal"] == [
        "Born 1990",
        "Nationality: Portugal",
        "Gender: Man",
    ]


def test_a_blank_prints_nothing_and_a_letter_prints_none(user):
    cv = a_cv(user, show_gender=True)
    assert rendering.contact_details(user, cv)["personal"] == []
    named(user, gender="Woman")
    assert rendering.contact_details(user)["personal"] == []


def test_what_was_typed_is_text_on_the_page(user):
    named(user, gender="<b>x</b>")
    html = rendering.render_cv_html(a_cv(user, show_gender=True))
    assert "<b>x</b>" not in html and "&lt;b&gt;x&lt;/b&gt;" in html


def test_the_cv_page_offers_the_switch_off_and_says_what_it_would_print(client, user):
    named(user, gender="Woman")
    cv = a_cv(user)
    client.force_login(user)
    html = client.get(reverse("documents:cv_update", args=[cv.pk])).content.decode()
    tag = re.search(r'<input[^>]*name="show_gender"[^>]*>', html).group(0)
    assert "checked" not in tag and "Print your gender" in html
    assert "Woman" in html


# ------------------------------------------------------------------ archive and candidate


def test_the_archive_carries_it_and_the_switch_at_the_new_format(user, other_user):
    named(user, gender="Non-binary")
    cv = a_cv(user, show_gender=True)
    document = export.build_document(user)
    assert document["postulo"]["format"] == export.FORMAT_VERSION >= 36
    assert document["account"]["profile"]["gender"] == "Non-binary"
    prints = next(entry for entry in document["documents"]["cvs"] if entry["name"] == cv.name)[
        "prints"
    ]
    assert prints["gender"] is True

    importer.load(other_user, zipfile.ZipFile(export.write_archive(user)))
    other_user.profile.refresh_from_db()
    assert other_user.profile.gender == "Non-binary"
    assert CV.objects.get(owner=other_user, name=cv.name).show_gender is True


def _archive_with(user, **profile) -> zipfile.ZipFile:
    document = export.build_document(user)
    document["account"]["profile"].update(profile)
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w") as archive:
        archive.writestr(export.MANIFEST_NAME, json.dumps(document, default=str))
    buffer.seek(0)
    return zipfile.ZipFile(buffer)


@pytest.mark.parametrize("bad", ["g" * 41, "a\x00b", 7, ["Woman"]])
def test_the_importer_reads_it_through_the_columns_rule(user, other_user, bad):
    named(user, gender="Woman")
    report = importer.load(other_user, _archive_with(user, gender=bad))
    other_user.profile.refresh_from_db()
    assert other_user.profile.gender == ""
    assert [line for line in report.skipped if "gender" in line.lower()], report.skipped


def test_an_archive_without_it_restores_it_blank(user, other_user):
    named(user, gender="Woman")
    document = export.build_document(user)
    del document["account"]["profile"]["gender"]
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w") as archive:
        archive.writestr(export.MANIFEST_NAME, json.dumps(document, default=str))
    buffer.seek(0)
    importer.load(other_user, zipfile.ZipFile(buffer))
    other_user.profile.refresh_from_db()
    assert other_user.profile.gender == ""


def test_the_candidate_file_carries_it_and_fills_a_blank_and_keeps_an_answer(user, other_user):
    named(user, gender="Woman")
    assert export.CANDIDATE_FORMAT >= 7
    held = candidate.read(json.dumps(export.build_candidate_document(user)).encode())
    assert held["details"]["gender"] == "Woman"
    plan = candidate.plan(other_user, held)
    rows = {row.label: row for s in plan.sections if s.key == "details" for row in s.rows}
    assert rows["Gender"].outcome == candidate.ADD
    candidate.apply(other_user, held)
    other_user.profile.refresh_from_db()
    assert other_user.profile.gender == "Woman"

    named(other_user, gender="Man")
    again = candidate.plan(other_user, held)
    rows = {row.label: row for s in again.sections if s.key == "details" for row in s.rows}
    assert rows["Gender"].outcome == candidate.KEPT
    candidate.apply(other_user, held)
    other_user.profile.refresh_from_db()
    assert other_user.profile.gender == "Man"


def test_a_candidate_file_with_one_too_long_is_refused_for_that_row(user):
    named(user)
    document = export.build_candidate_document(user)
    document["account"]["profile"]["gender"] = "g" * 41
    plan = candidate.plan(user, candidate.read(json.dumps(document).encode()))
    refused = [
        row
        for s in plan.sections
        if s.key == "details"
        for row in s.rows
        if row.outcome == candidate.REFUSED
    ]
    assert [row.label for row in refused] == ["Gender"]


# ------------------------------------------------------------------------------ the API


def bearer(user, *scopes) -> dict:
    _record, raw = ApiToken.issue(user, "Agent", scopes=scopes or ("read",))
    return {"HTTP_AUTHORIZATION": f"Bearer {raw}"}


def patch(client, payload, **headers):
    return client.patch(
        "/api/v1/profile", data=json.dumps(payload), content_type="application/json", **headers
    )


def test_the_api_reads_and_writes_it_as_text(client, user):
    response = patch(client, {"gender": " agender "}, **bearer(user, "write"))
    assert response.status_code == 200, response.content
    assert response.json()["gender"] == "agender"
    assert client.get("/api/v1/profile", **bearer(user)).json()["gender"] == "agender"
    assert patch(client, {"gender": ""}, **bearer(user, "write")).json()["gender"] == ""


def test_the_api_holds_it_to_the_pages_bounds_and_names_the_field(client, user):
    named(user, gender="Woman")
    for value in ("g" * 41, "a\x00b"):
        response = patch(client, {"gender": value}, **bearer(user, "write"))
        assert response.status_code == 422
        assert "gender" in json.dumps(response.json())
    user.profile.refresh_from_db()
    assert user.profile.gender == "Woman"


def test_a_field_left_out_is_left_alone(client, user):
    named(user, gender="Woman", form_of_address="Dr")
    patch(client, {"pronouns": "she/her"}, **bearer(user, "write"))
    user.profile.refresh_from_db()
    assert (user.profile.gender, user.profile.form_of_address) == ("Woman", "Dr")


def test_the_prints_of_a_cv_carry_the_boolean(client, user):
    cv = a_cv(user)
    assert client.get(f"/api/v1/cvs/{cv.pk}", **bearer(user)).json()["prints"]["gender"] is False
    response = client.patch(
        f"/api/v1/cvs/{cv.pk}",
        data=json.dumps({"prints": {"gender": True}}),
        content_type="application/json",
        **bearer(user, "write"),
    )
    assert response.status_code == 200, response.content
    cv.refresh_from_db()
    assert cv.show_gender is True


def test_nothing_here_is_found_by_search(user):
    named(user, gender="Zanzibarian")
    assert search(user, "Zanzibarian") == []
