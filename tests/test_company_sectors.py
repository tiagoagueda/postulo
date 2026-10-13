"""Sectors above industries: NACE sections over divisions, and the ESCO bridge (#897).

A company's classification is two levels. The *sector* is the NACE section, 22 of them and
closed; the *industry* is a division or the person's own word, as it always was. The sector
is the industry's, read off the name where it can be and chosen by hand where it cannot; a
company's sectors are its industries'. ESCO's companion correspondence links an occupation to
the divisions it is employed in, and that orders suggestions both ways.
"""

from __future__ import annotations

import importlib
import io
import json
import zipfile

import pytest
from django.apps import apps
from django.urls import reverse

from postulo.api.models import ApiToken
from postulo.applications import analytics
from postulo.applications.models import Application, Status
from postulo.applications.services import change_status
from postulo.core import export as export_module
from postulo.core import importer
from postulo.jobs import esco, industries
from postulo.jobs.forms import CompanyForm, IndustryForm, JobPostingForm
from postulo.jobs.management.commands import fetch_esco
from postulo.jobs.models import Company, Industry, JobPosting

pytestmark = pytest.mark.django_db


def company_with(user, name, *fields):
    company = Company.objects.create(owner=user, name=name)
    company.industries.set(Industry.named(user, fields))
    return company


# ------------------------------------------------------------------- the sections


def test_there_are_twenty_two_sections_named_in_sentence_case():
    named = industries.sections("en")
    assert [letter for letter, _name in named] == list("ABCDEFGHIJKLMNOPQRSTUV")
    assert dict(named)["Q"] == "Education"
    assert dict(named)["P"] == "Public administration and defence; compulsory social security"
    assert all(name == name[:1].upper() + name[1:] and not name.isupper() for _l, name in named)


def test_a_german_name_keeps_the_capital_on_every_noun():
    assert industries.section_name("R", "de") == "Gesundheits- und Sozialwesen"
    assert industries.section_name("N", "de") == (
        "Erbringung von wissenschaftlichen und technischen Dienstleistungen"
    )
    assert "IT-Beratung" in industries.section_name("K", "de")


def test_a_french_name_loses_the_soft_hyphen_eurostat_put_in_it():
    name = industries.section_name("K", "fr")
    assert "\xad" not in name and "service d’information" in name


def test_a_language_eurostat_does_not_publish_reads_the_english_name():
    assert industries.section_name("Q", "uk") == "Education"


def test_an_unknown_section_has_no_name():
    assert industries.section_name("Z") == "" and industries.section_name("") == ""
    assert not industries.is_section("Z") and industries.is_section("K")


def test_every_short_name_has_a_section():
    for starter in industries.STARTER_INDUSTRIES:
        english = industries._english(starter)
        assert industries.is_section(industries.STARTER_SECTIONS[english]), english
    assert set(industries.STARTER_SECTIONS) == {
        industries._english(s) for s in industries.STARTER_INDUSTRIES
    }


def test_the_sections_of_some_divisions():
    assert industries.sections_for(["62", "63", "85", "zz", ""]) == ["K", "Q"]


def test_what_is_offered_carries_its_section_and_puts_the_postings_divisions_first():
    offered = industries.offered(exclude=["Software"], language="en", first=["85"])
    assert offered[0] == ("Education", "Q"), "the division the postings point at, first"
    by_name = dict(offered)
    assert "Software" not in by_name, "already the person's"
    assert by_name["Banking"] == "L"
    assert by_name[industries.name_for("62", "en")] == "K"
    assert len(offered) == len({name.casefold() for name, _section in offered})


# ------------------------------------------------------------------- the industry


def test_an_industry_takes_its_divisions_section(user):
    (industry,) = Industry.named(user, [industries.name_for("62", "en")])
    assert (industry.code, industry.section) == ("62", "K")
    assert not industry.sector_by_hand


def test_a_short_name_takes_the_section_it_implies(user):
    (industry,) = Industry.named(user, ["Public sector"])
    assert (industry.code, industry.section) == ("", "P")


def test_a_word_of_ones_own_has_no_sector_until_one_is_chosen(user):
    (industry,) = Industry.named(user, ["Fintech"])
    assert industry.section == ""
    assert industry.look == industries.PLAIN_LOOK, "and draws plainly"

    industry.section = "L"
    industry.save()
    industry.refresh_from_db()
    assert industry.section == "L" and industry.sector_by_hand
    assert industry.look == industries.LOOKS["L"]


def test_a_division_overrides_a_sector_chosen_by_hand(user):
    (industry,) = Industry.named(user, ["Fintech"])
    industry.section = "L"
    industry.save()
    industry.name = industries.name_for("85", "en")
    industry.save()
    assert (industry.code, industry.section) == ("85", "Q"), "the two never disagree"


def test_a_sector_that_is_not_a_section_is_not_kept(user):
    (industry,) = Industry.named(user, ["Fintech"])
    industry.section = "Z"
    industry.save()
    assert industry.section == ""


def test_named_files_new_words_under_a_sector_and_leaves_known_ones(user):
    (known,) = Industry.named(user, ["Fintech"])
    made = Industry.named(user, ["Fintech", "Cleantech"], section="D")
    assert made[0].pk == known.pk and made[0].section == "", "a known one keeps its own"
    assert made[1].section == "D"


def test_the_migration_fills_the_sections_from_the_codes_and_the_short_names(user):
    rows = [
        Industry.objects.create(owner=user, name=industries.name_for("85", "en"), slug="a"),
        Industry.objects.create(owner=user, name="Banking", slug="b"),
        Industry.objects.create(owner=user, name="Fintech", slug="c"),
    ]
    Industry.objects.filter(pk__in=[r.pk for r in rows]).update(section="")

    migration = importlib.import_module("postulo.jobs.migrations.0035_industry_section")
    migration.fill_sections(apps, None)

    assert [Industry.objects.get(pk=r.pk).section for r in rows] == ["Q", "L", ""]


def test_a_companys_sectors_are_its_industries(user):
    company = company_with(user, "Aperture", "Software", "Banking", "Fintech")
    assert company.sectors == ["K", "L"]
    assert company.sector_names == [industries.section_name("K"), industries.section_name("L")]


# ------------------------------------------------------------------- the forms


def test_the_company_form_offers_the_sectors_above_the_industries(user):
    form = CompanyForm(user=user)
    fields = list(form.fields)
    assert fields.index("sector") < fields.index("industries")
    choices = form.fields["sector"].choices
    assert choices[0][0] == "" and len(choices) == 23
    assert "data-industry-sector" in form.fields["sector"].widget.attrs


def test_the_company_form_starts_on_the_one_sector_a_company_is_in(user):
    company = company_with(user, "Aperture", "Software", "Telecommunications")
    assert CompanyForm(user=user, instance=company).fields["sector"].initial == "K"
    company.industries.add(*Industry.named(user, ["Banking"]))
    assert CompanyForm(user=user, instance=company).fields["sector"].initial is None


def test_a_new_word_typed_with_a_sector_chosen_is_filed_under_it(user):
    form = CompanyForm(
        data={
            "name": "Aperture",
            "kind": "employer",
            "sector": "N",
            "new_industries": "Portal research, Banking",
        },
        user=user,
    )
    assert form.is_valid(), form.errors
    form.instance.owner = user
    company = form.save()
    held = {i.name: i.section for i in company.industries.all()}
    assert held == {"Portal research": "N", "Banking": "L"}, "a short name keeps its own"


def test_the_company_page_marks_each_suggestion_and_box_with_its_sector(client, user):
    company_with(user, "Black Mesa", "Software")
    client.force_login(user)
    html = client.get(reverse("jobs:company_create")).content.decode()
    assert "data-industry-sector" in html
    assert '<option value="Banking" data-section="L">' in html
    assert 'data-section="K"' in html.split("data-labels-existing", 1)[1].split("</div>", 1)[0]


def test_a_company_with_no_industries_is_offered_the_divisions_its_listings_point_at(
    user, monkeypatch
):
    company = Company.objects.create(owner=user, name="Aperture")
    JobPosting.objects.create(owner=user, company=company, title="A")
    JobPosting.objects.filter(company=company).update(isco_code="2512")
    monkeypatch.setattr(esco, "divisions_for", lambda group: frozenset({"62", "85"}))

    form = CompanyForm(user=user, instance=company)
    assert form.divisions_from_postings() == ["62", "85"]
    assert [section for _name, section in form.suggestions[:2]] == ["K", "Q"]

    company.industries.add(*Industry.named(user, ["Fintech"]))
    assert CompanyForm(user=user, instance=company).divisions_from_postings() == []


def test_the_industry_form_chooses_a_sector_for_a_word_of_ones_own(user):
    (fintech,) = Industry.named(user, ["Fintech"])
    form = IndustryForm(data={"name": "Fintech", "section": "L"}, instance=fintech, user=user)
    assert form.is_valid(), form.errors
    assert form.save().section == "L"


def test_the_industry_form_says_a_divisions_sector_is_set_by_it(user):
    (education,) = Industry.named(user, [industries.name_for("85", "en")])
    form = IndustryForm(instance=education, user=user)
    assert form.fields["section"].disabled
    assert "85" in str(form.fields["section"].help_text)


def test_a_listings_title_suggestions_put_the_companys_sectors_first(user, monkeypatch):
    company = company_with(user, "Aperture", "Banking")
    posting = JobPosting.objects.create(owner=user, company=company, title="Role")
    monkeypatch.setattr(
        esco, "unit_groups", lambda language="": [("1111", "Legislators"), ("2412", "Advisers")]
    )
    monkeypatch.setattr(
        esco, "divisions_for", lambda group: frozenset({"64"} if group == "2412" else {"84"})
    )

    lists = JobPostingForm(user=user, instance=posting).datalists
    assert lists["title-suggestions"] == ["Advisers", "Legislators"], "finance first"
    assert esco.suggestions() == ["Legislators", "Advisers"], "and nothing left out"


# ------------------------------------------------------------------- the pages


def test_the_industries_page_is_grouped_by_sector_the_ones_with_none_last(client, user):
    company_with(user, "Aperture", "Software", "Fintech", "Banking")
    client.force_login(user)
    html = client.get(reverse("jobs:industry_list")).content.decode()
    computing, finance, none = (html.index(f'data-sector="{s}"') for s in ("K", "L", ""))
    assert computing < finance < none
    assert html.index("Software") > computing and html.index("Fintech") > none


def test_the_industries_page_finds_a_sector_by_its_name(client, user):
    company_with(user, "Aperture", "Software", "Banking")
    client.force_login(user)
    html = client.get(reverse("jobs:industry_list"), {"q": "financial and insurance"}).content
    assert b"Banking" in html and b"Software" not in html


def test_the_company_table_shows_and_narrows_by_sector(client, user):
    company_with(user, "Aperture", "Software", "Information technology")
    company_with(user, "Black Mesa", "Banking")
    client.force_login(user)
    url = reverse("jobs:company_list")

    html = client.get(url).content.decode()
    assert "data-sectors" in html and industries.section_name("K") in html
    narrowed = client.get(url, {"sector": "K"}).content.decode()
    assert "Aperture" in narrowed and "Black Mesa" not in narrowed
    assert narrowed.count(">Aperture<") == 1, "a company in two industries of K is one row"


def test_the_insights_roll_up_to_sectors_with_the_industries_beneath(user):
    company = company_with(user, "Aperture", "Software", "Information technology", "Banking")
    posting = JobPosting.objects.create(owner=user, company=company, title="Role")
    application = Application.objects.create(owner=user, posting=posting, status=Status.DRAFT)
    change_status(application, Status.APPLIED)

    found = analytics.build(user)
    by_code = {row.code: row for row in found.sectors}
    assert set(by_code) == {"K", "L"}
    assert by_code["K"].applied == 1, "once in a sector, however many of its industries"
    assert {child.name for child in by_code["K"].children} == {"Software", "Information technology"}


# ------------------------------------------------------------------- archive and API


def test_the_archive_writes_a_sector_only_where_it_was_chosen_by_hand(user, other_user):
    company = company_with(user, "Aperture", "Software", "Fintech", "Portal research")
    Industry.objects.filter(owner=user, name="Fintech").update(section="L")

    document = export_module.build_document(user)
    entry = next(c for c in document["companies"] if c["name"] == "Aperture")
    assert sorted(entry["industries"], key=str) == sorted(
        ["Portal research", "Software", {"name": "Fintech", "sector": "L"}], key=str
    )
    assert export_module.FORMAT_VERSION >= 70

    importer.load(other_user, zipfile.ZipFile(export_module.write_archive(user)))
    held = {i.name: i.section for i in Industry.objects.filter(owner=other_user)}
    assert held == {"Software": "K", "Fintech": "L", "Portal research": ""}
    assert company.pk


def test_an_archive_with_names_only_still_restores(user, other_user):
    company_with(user, "Aperture", "Banking", "Fintech")
    buffer = io.BytesIO()
    with (
        zipfile.ZipFile(export_module.write_archive(user)) as source,
        zipfile.ZipFile(buffer, "w") as target,
    ):
        for item in source.infolist():
            data = source.read(item)
            if item.filename.endswith(".json"):
                document = json.loads(data)
                for entry in document.get("companies", []):
                    entry["industries"] = [
                        e["name"] if isinstance(e, dict) else e for e in entry["industries"]
                    ]
                    entry["industries"].append({"name": "Odd", "sector": "Z"})
                data = json.dumps(document).encode()
            target.writestr(item, data)
    importer.load(other_user, zipfile.ZipFile(io.BytesIO(buffer.getvalue())), force=True)
    held = {i.name: i.section for i in Industry.objects.filter(owner=other_user)}
    assert held == {"Banking": "L", "Fintech": "", "Odd": ""}, "a sector that is no section"


def test_the_api_says_a_companys_sectors(client, user):
    company_with(user, "Aperture", "Software", "Banking")
    _record, raw = ApiToken.issue(user, "Agent", scopes=("read",))
    company = Company.objects.get(owner=user)
    body = client.get(f"/api/v1/companies/{company.pk}", HTTP_AUTHORIZATION=f"Bearer {raw}").json()
    assert body["sectors"] == ["K", "L"] and sorted(body["industries"]) == ["Banking", "Software"]


# ------------------------------------------------------------------- the ESCO bridge


def test_an_occupations_nace_codes_are_cut_to_their_divisions():
    concept = {
        "_links": {
            "hasNACECode": [
                {"code": "59.11", "uri": "http://data.europa.eu/ux2/nace2.1/5911"},
                {"code": "62", "uri": "http://data.europa.eu/ux2/nace2.1/62"},
                {"uri": "http://data.europa.eu/ux2/nace2.1/6312"},
                {"code": "?"},
            ]
        }
    }
    assert fetch_esco.nace_divisions(concept) == {"59", "62", "63"}
    assert fetch_esco.nace_divisions({"_links": {}}) == set()
    assert fetch_esco.nace_divisions({"_links": {"hasNACECode": {"code": "85.4"}}}) == {"85"}


def test_the_divisions_of_a_unit_group_are_its_occupations_together(monkeypatch):
    esco.forget()
    document = {
        **esco.ABSENT,
        "occupations": {
            "2512.1": {"isco": "2512", "names": {"en": "a"}, "nace": ["62"]},
            "2512.2": {"isco": "2512", "names": {"en": "b"}, "nace": ["63", "62"]},
            "2511.1": {"isco": "2511", "names": {"en": "c"}},
        },
    }
    monkeypatch.setattr(esco, "classification", lambda: document)
    try:
        assert esco.divisions_for("2512") == {"62", "63"}
        assert esco.divisions_for("2511") == frozenset(), "a file from before the bridge"
        assert esco.divisions_for("9999") == frozenset()
    finally:
        esco._divisions_by_group.cache_clear()
