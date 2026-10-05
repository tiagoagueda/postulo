"""Nationalities: one or several, or only whether you are a citizen of the EU, the EEA or
Switzerland (#680).

A list of country codes on the profile, held to one function, `core.personal.clean_nationalities`,
at every door; and a scope for somebody who would rather say less, which is the answer only
while the list is empty. Nothing is printed unless a CV says so.
"""

from __future__ import annotations

import io
import json
import re
import zipfile

import pytest
from django.core.exceptions import ValidationError
from django.urls import reverse

from postulo.accounts.forms import ProfileForm
from postulo.api.models import ApiToken
from postulo.core import country_sets, export, importer, personal, phones
from postulo.core.search import search
from postulo.documents import formats, printing, rendering
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


def holding(user, *codes, scope=""):
    user.first_name, user.last_name = "Alex", "Morgan"
    user.save()
    user.profile.nationalities = list(codes)
    user.profile.nationality_scope = scope
    user.profile.save()
    return user


def a_cv(user, **fields) -> CV:
    return CV.objects.create(owner=user, name="Main", **fields)


# ------------------------------------------------------------------------ the validator


def test_known_codes_are_kept_in_capitals_in_order_each_once():
    assert personal.clean_nationalities(["pt", "BR", " pt ", "Br", "no"]) == ["PT", "BR", "NO"]
    assert personal.clean_nationalities([]) == [] and personal.clean_nationalities(None) == []
    assert personal.clean_nationalities(["PT", "", "  "]) == ["PT"]


@pytest.mark.parametrize(
    "value",
    [["ZZ"], ["PT", "XX"], ["Portugal"], "PT", {"PT": 1}, [1], [None], [["PT"]], ["PT\x00"]],
)
def test_an_unknown_code_or_a_wrong_type_is_refused(value):
    with pytest.raises(ValidationError):
        personal.clean_nationalities(value)


def test_ten_are_allowed_and_eleven_are_not():
    ten = [code for code, _prefix, _name in phones.COUNTRIES][:10]
    assert personal.clean_nationalities(ten) == ten
    with pytest.raises(ValidationError) as refused:
        personal.clean_nationalities([code for code, _p, _n in phones.COUNTRIES][:11])
    assert refused.value.code == "too_many"


def test_the_column_carries_the_rule(user):
    user.profile.nationalities = ["ZZ"]
    with pytest.raises(ValidationError) as refused:
        user.profile.full_clean(exclude=["user"])
    assert "nationalities" in refused.value.message_dict


def test_the_scope_is_derived_from_the_countries():
    for held in (["PT"], ["NO"], ["CH"], ["BR", "PT"]):
        assert personal.derived_scope(held) == personal.SCOPE_EU, held
    for held in (["GB"], ["BR"], ["GB", "BR"]):
        assert personal.derived_scope(held) == personal.SCOPE_OTHER, held
    assert personal.derived_scope([]) == ""


def test_every_code_of_the_set_is_a_country_the_list_knows():
    assert country_sets.EU_EEA_CH <= set(phones.BY_CODE)
    assert len(country_sets.EU) == 27
    assert country_sets.EEA_NOT_EU == {"IS", "LI", "NO"}
    assert country_sets.SWITZERLAND == {"CH"}
    assert len(country_sets.EU_EEA_CH) == 31
    assert not {"GB", "UA", "TR", "RS"} & country_sets.EU_EEA_CH


def test_the_scope_is_cleared_when_countries_are_listed(user):
    holding(user, scope="other")
    assert user.profile.nationality_scope == "other"
    user.profile.nationalities = ["PT"]
    user.profile.save()
    user.profile.refresh_from_db()
    assert user.profile.nationality_scope == ""
    user.profile.nationalities = []
    user.profile.nationality_scope = "eu"
    user.profile.save(update_fields=["nationality_scope", "updated_at"])
    user.profile.refresh_from_db()
    assert user.profile.nationality_scope == "eu", "the answer while the list is empty"


def test_a_partial_save_of_the_list_clears_the_scope_too(user):
    holding(user, scope="other")
    user.profile.nationalities = ["BR"]
    user.profile.save(update_fields=["nationalities", "updated_at"])
    user.profile.refresh_from_db()
    assert user.profile.nationality_scope == ""


# ----------------------------------------------------------------------------- the page


def menus(html: str) -> list[str]:
    return re.findall(r'<select name="(nationality_\d+)"', html)


def chosen(html: str, name: str) -> str:
    select = re.search(rf'<select name="{name}"[^>]*>(.*?)</select>', html, re.S)
    found = re.search(r'<option value="([^"]*)"[^>]*selected', select.group(1))
    return found.group(1) if found else ""


def test_the_page_draws_one_empty_menu_for_a_person_with_none(client, user):
    client.force_login(user)
    html = client.get(reverse("accounts:profile")).content.decode()
    assert menus(html) == ["nationality_1"]
    assert 'name="nationality_scope"' in html
    assert "Nationalities" in html


def test_it_draws_a_menu_per_nationality_held_and_one_empty(client, user):
    holding(user, "PT", "BR")
    client.force_login(user)
    html = client.get(reverse("accounts:profile")).content.decode()
    assert menus(html) == ["nationality_1", "nationality_2", "nationality_3"]
    assert [chosen(html, name) for name in menus(html)] == ["PT", "BR", ""]


def test_each_menu_offers_every_country_with_its_flag(client, user):
    client.force_login(user)
    html = client.get(reverse("accounts:profile")).content.decode()
    select = re.search(r'<select name="nationality_1"[^>]*>(.*?)</select>', html, re.S).group(1)
    assert len(re.findall(r"<option ", select)) == len(phones.COUNTRIES) + 1
    assert re.search(r'<option value="PT"[^>]*data-flag="[^"]+"', select)


def test_the_form_saves_adds_a_second_and_clears(client, user):
    client.force_login(user)
    url = reverse("accounts:profile")
    assert client.post(url, posted(nationality_1="PT")).status_code == 302
    user.profile.refresh_from_db()
    assert user.profile.nationalities == ["PT"]
    assert client.post(url, posted(nationality_1="PT", nationality_2="BR")).status_code == 302
    user.profile.refresh_from_db()
    assert user.profile.nationalities == ["PT", "BR"]
    client.post(url, posted(nationality_1="", nationality_2="BR"))
    user.profile.refresh_from_db()
    assert user.profile.nationalities == ["BR"], "an empty menu in the middle is skipped"
    client.post(url, posted(nationality_1="", nationality_2="", nationality_3=""))
    user.profile.refresh_from_db()
    assert user.profile.nationalities == []


def test_a_repeated_country_is_kept_once(client, user):
    client.force_login(user)
    client.post(reverse("accounts:profile"), posted(nationality_1="PT", nationality_2="PT"))
    user.profile.refresh_from_db()
    assert user.profile.nationalities == ["PT"]


def test_a_country_that_is_not_in_the_list_is_refused(user):
    form = ProfileForm(posted(nationality_1="ZZ"), instance=user.profile)
    assert not form.is_valid() and "nationality_1" in form.errors


def test_a_page_posted_without_the_menus_leaves_the_list_alone(user):
    holding(user, "PT", scope="")
    form = ProfileForm(posted(), instance=user.profile)
    assert form.is_valid(), form.errors
    form.save()
    user.profile.refresh_from_db()
    assert user.profile.nationalities == ["PT"]


def test_more_than_ten_menus_are_refused(user):
    codes = [code for code, _p, _n in phones.COUNTRIES][:11]
    data = posted(**{f"nationality_{index}": code for index, code in enumerate(codes, 1)})
    form = ProfileForm(data, instance=user.profile)
    assert not form.is_valid()


def test_the_scope_is_saved_while_the_list_is_empty_and_cleared_when_not(client, user):
    client.force_login(user)
    url = reverse("accounts:profile")
    client.post(url, posted(nationality_scope="eu"))
    user.profile.refresh_from_db()
    assert (user.profile.nationalities, user.profile.nationality_scope) == ([], "eu")
    client.post(url, posted(nationality_1="GB", nationality_scope="eu"))
    user.profile.refresh_from_db()
    assert (user.profile.nationalities, user.profile.nationality_scope) == (["GB"], "")


def test_the_scope_menu_says_both_wordings(client, user):
    client.force_login(user)
    html = client.get(reverse("accounts:profile")).content.decode()
    assert "Citizen of an EU or EEA country, or of Switzerland" in html
    assert "Citizen of another country" in html


# ------------------------------------------------------------------------ on a CV


def test_nothing_is_printed_by_default(user):
    holding(user, "PT", "BR")
    cv = a_cv(user)
    assert cv.show_nationality is False and printing.is_default(cv)
    details = rendering.contact_details(user, cv)
    assert details["nationalities"] == [] and details["nationality_scope"] == ""
    assert details["personal"] == []
    html, text = rendering.render_cv_html(cv), rendering.cv_text(cv)
    for printed in ("Nationality", "Portugal", "Brazil"):
        assert printed not in html and printed not in text


@pytest.mark.parametrize("theme", ["plain", "classic"])
@pytest.mark.parametrize("kind", ["cv", "portfolio"])
def test_the_switch_prints_the_line_in_the_preview_the_text_and_the_word_file(user, theme, kind):
    holding(user, "PT", "BR")
    cv = a_cv(user, theme=theme, kind=kind, show_nationality=True)
    line = "Nationality: Portugal, Brazil"
    assert rendering.render_cv_html(cv).count(line) == 1
    assert rendering.cv_text(cv).count(line) == 1
    outline = rendering.cv_outline(cv)
    assert line in formats.get("txt").write(outline).decode()
    with zipfile.ZipFile(io.BytesIO(formats.get("docx").write(outline))) as word:
        assert line in word.read("word/document.xml").decode()


def test_the_scope_prints_its_wording_where_no_country_is_listed(user):
    holding(user, scope="eu")
    cv = a_cv(user, show_nationality=True)
    details = rendering.contact_details(user, cv)
    assert details["nationalities"] == []
    assert details["nationality_scope"] == "Citizen of an EU or EEA country, or of Switzerland"
    assert details["personal"] == ["Citizen of an EU or EEA country, or of Switzerland"]
    holding(user, scope="")
    assert rendering.contact_details(user, cv)["personal"] == []


def test_it_goes_on_the_one_line_with_the_birth_details(user):
    holding(user, "PT")
    user.profile.birth_date = "1990"
    user.profile.save()
    cv = a_cv(user, show_birth_date=True, show_nationality=True)
    assert rendering.contact_details(user, cv)["personal"] == [
        "Born 1990",
        "Nationality: Portugal",
    ]


def test_the_cv_page_offers_the_switch_off(client, user):
    holding(user, "PT")
    cv = a_cv(user)
    client.force_login(user)
    html = client.get(reverse("documents:cv_update", args=[cv.pk])).content.decode()
    tag = re.search(r'<input[^>]*name="show_nationality"[^>]*>', html).group(0)
    assert "checked" not in tag and "Print your nationality" in html
    assert "Portugal" in html


# ------------------------------------------------------------------ archive and candidate


def test_the_archive_carries_both_columns_and_the_switch(user, other_user):
    holding(user, "PT", "BR")
    cv = a_cv(user, show_nationality=True)
    document = export.build_document(user)
    assert document["postulo"]["format"] == export.FORMAT_VERSION >= 35
    profile = document["account"]["profile"]
    assert profile["nationalities"] == ["PT", "BR"] and profile["nationality_scope"] == ""
    prints = next(entry for entry in document["documents"]["cvs"] if entry["name"] == cv.name)[
        "prints"
    ]
    assert prints["nationality"] is True

    importer.load(other_user, zipfile.ZipFile(export.write_archive(user)))
    other_user.profile.refresh_from_db()
    assert other_user.profile.nationalities == ["PT", "BR"]
    assert CV.objects.get(owner=other_user, name=cv.name).show_nationality is True


def test_the_scope_travels_too(user, other_user):
    holding(user, scope="other")
    importer.load(other_user, zipfile.ZipFile(export.write_archive(user)))
    other_user.profile.refresh_from_db()
    assert (other_user.profile.nationalities, other_user.profile.nationality_scope) == (
        [],
        "other",
    )


def _archive_with(user, **profile) -> zipfile.ZipFile:
    document = export.build_document(user)
    document["account"]["profile"].update(profile)
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w") as archive:
        archive.writestr(export.MANIFEST_NAME, json.dumps(document, default=str))
    buffer.seek(0)
    return zipfile.ZipFile(buffer)


@pytest.mark.parametrize(
    "bad",
    [
        ["PT", "ZZ"],
        "PT",
        {"PT": 1},
        [1, 2],
        [["PT"]],
        ["PT"] * 11 + ["BR"],
        [code for code, _p, _n in phones.COUNTRIES][:11],
    ],
)
def test_the_importer_drops_a_bad_list_and_says_so(user, other_user, bad):
    holding(user, "PT")
    report = importer.load(other_user, _archive_with(user, nationalities=bad))
    other_user.profile.refresh_from_db()
    assert other_user.profile.nationalities == []
    assert [line for line in report.skipped if "nationalities" in line.lower()], report.skipped


def test_the_importer_drops_a_scope_that_is_not_one(user, other_user):
    holding(user, "PT")
    report = importer.load(other_user, _archive_with(user, nationality_scope="european"))
    other_user.profile.refresh_from_db()
    assert other_user.profile.nationality_scope == ""
    assert [line for line in report.skipped if "scope" in line.lower()]


def test_a_list_in_an_archive_is_cleaned_as_the_page_cleans_it(user, other_user):
    holding(user, "PT")
    importer.load(other_user, _archive_with(user, nationalities=["pt", "PT", "br"]))
    other_user.profile.refresh_from_db()
    assert other_user.profile.nationalities == ["PT", "BR"]


def _plan(user, **profile):
    document = export.build_candidate_document(user)
    document["account"]["profile"].update(profile)
    held = candidate.read(json.dumps(document).encode())
    plan = candidate.plan(user, held)
    rows = [row for section in plan.sections if section.key == "details" for row in section.rows]
    return held, rows


def test_the_candidate_file_has_a_review_row_per_listed_code(user, other_user):
    holding(user, "PT", "BR")
    assert export.CANDIDATE_FORMAT >= 6
    other_user.profile.nationalities = ["PT"]
    other_user.profile.save()
    held, rows = _plan(other_user, nationalities=["PT", "BR", "NO"])
    by_sub = {row.sub: row.outcome for row in rows if row.label == "Nationality"}
    assert by_sub == {
        "Portugal": candidate.PRESENT,
        "Brazil": candidate.ADD,
        "Norway": candidate.ADD,
    }
    candidate.apply(other_user, held)
    other_user.profile.refresh_from_db()
    assert other_user.profile.nationalities == ["PT", "BR", "NO"], "nothing held is replaced"


def test_a_candidate_list_never_takes_the_profile_past_ten(user, other_user):
    codes = [code for code, _p, _n in phones.COUNTRIES][:10]
    other_user.profile.nationalities = codes[:9]
    other_user.profile.save()
    held, rows = _plan(other_user, nationalities=[codes[9], "ZW", "ZM"])
    outcomes = [row.outcome for row in rows if row.label == "Nationality"]
    assert outcomes.count(candidate.ADD) == 1 and candidate.REFUSED in outcomes
    candidate.apply(other_user, held)
    other_user.profile.refresh_from_db()
    assert len(other_user.profile.nationalities) == 10


def test_a_candidate_scope_is_a_row_only_while_there_is_no_list(user, other_user):
    held, rows = _plan(other_user, nationalities=[], nationality_scope="eu")
    assert [row.outcome for row in rows if row.label == "Nationality scope"] == [candidate.ADD]
    candidate.apply(other_user, held)
    other_user.profile.refresh_from_db()
    assert other_user.profile.nationality_scope == "eu"
    held, rows = _plan(other_user, nationalities=["PT"], nationality_scope="other")
    assert not [row for row in rows if row.label == "Nationality scope"]


@pytest.mark.parametrize("bad", ["PT", {"a": 1}, 7, [1, "PT"]])
def test_a_candidate_list_of_the_wrong_type_is_one_refused_row(user, other_user, bad):
    _held, rows = _plan(other_user, nationalities=bad)
    refused = [row for row in rows if row.outcome == candidate.REFUSED]
    assert refused and all(row.label == "Nationality" for row in refused)
    other_user.profile.refresh_from_db()
    assert other_user.profile.nationalities == []


# ------------------------------------------------------------------------------ the API


def bearer(user, *scopes) -> dict:
    _record, raw = ApiToken.issue(user, "Agent", scopes=scopes or ("read",))
    return {"HTTP_AUTHORIZATION": f"Bearer {raw}"}


def patch(client, payload, **headers):
    return client.patch(
        "/api/v1/profile", data=json.dumps(payload), content_type="application/json", **headers
    )


def test_the_api_reads_and_writes_both(client, user):
    response = patch(client, {"nationalities": ["pt", "BR", "pt"]}, **bearer(user, "write"))
    assert response.status_code == 200, response.content
    body = response.json()
    assert body["nationalities"] == ["PT", "BR"]
    assert body["nationality_scope"] == "eu", "derived from the countries"
    user.profile.refresh_from_db()
    assert user.profile.nationality_scope == "", "and not stored"
    cleared = patch(
        client, {"nationalities": [], "nationality_scope": "other"}, **bearer(user, "write")
    ).json()
    assert cleared["nationalities"] == [] and cleared["nationality_scope"] == "other"
    assert client.get("/api/v1/profile", **bearer(user)).json()["nationality_scope"] == "other"


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("nationalities", ["ZZ"]),
        ("nationalities", "PT"),
        ("nationalities", [1]),
        ("nationalities", ["PT"] * 11),
        ("nationalities", [code for code, _p, _n in phones.COUNTRIES][:11]),
        ("nationality_scope", "european"),
    ],
)
def test_the_api_refuses_what_the_page_would_and_names_the_field(client, user, field, value):
    holding(user, "PT")
    response = patch(client, {field: value}, **bearer(user, "write"))
    assert response.status_code == 422, response.content
    assert field in json.dumps(response.json())
    user.profile.refresh_from_db()
    assert user.profile.nationalities == ["PT"]


def test_the_prints_of_a_cv_carry_the_switch(client, user):
    cv = a_cv(user)
    body = client.get(f"/api/v1/cvs/{cv.pk}", **bearer(user)).json()
    assert body["prints"]["nationality"] is False
    response = client.patch(
        f"/api/v1/cvs/{cv.pk}",
        data=json.dumps({"prints": {"nationality": True}}),
        content_type="application/json",
        **bearer(user, "write"),
    )
    assert response.status_code == 200, response.content
    cv.refresh_from_db()
    assert cv.show_nationality is True


def test_nothing_here_is_found_by_search(user):
    holding(user, "PT", "BR")
    for query in ("Portugal", "Brazil"):
        assert search(user, query) == []
