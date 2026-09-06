"""Industries: a per-person vocabulary, several per company."""

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
from postulo.jobs import industries as starter
from postulo.jobs.forms import CompanyForm
from postulo.jobs.models import Company, Industry, JobPosting

pytestmark = pytest.mark.django_db


def company_with(user, name, *fields, **extra):
    company = Company.objects.create(owner=user, name=name, **extra)
    company.industries.set(Industry.named(user, fields))
    return company


# ------------------------------------------------------------------- vocabulary


def test_names_are_matched_by_slug_and_kept_in_order(user):
    first = Industry.named(user, ["Software", "Finance"])
    again = Industry.named(user, ["software", " FINANCE ", "Software", "", "Insurance"])
    assert [i.name for i in first] == ["Software", "Finance"]
    assert [i.pk for i in again[:2]] == [i.pk for i in first], "one industry, whatever the capitals"
    assert [i.name for i in again] == ["Software", "Finance", "Insurance"]
    assert Industry.objects.for_user(user).count() == 3


def test_the_vocabulary_is_per_person(user, other_user):
    mine = Industry.named(user, ["Software"])[0]
    theirs = Industry.named(other_user, ["Software"])[0]
    assert mine.pk != theirs.pk and mine.slug == theirs.slug
    assert list(Industry.objects.for_user(other_user)) == [theirs]


def test_a_typed_list_splits_on_the_usual_separators():
    assert Industry.split("Software, Insurance; Banking / Retail") == [
        "Software",
        "Insurance",
        "Banking",
        "Retail",
    ]
    assert Industry.split("") == []


def test_the_starter_list_is_a_suggestion_minus_what_you_have():
    names = starter.suggestions(exclude=["software", "Finance"])
    assert "Software" not in names and "Finance" not in names and "Insurance" in names
    assert len(names) == len(starter.STARTER_INDUSTRIES) - 2


# ------------------------------------------------------------------------ forms


def test_the_company_form_offers_your_industries_and_takes_new_ones(client, user):
    Industry.named(user, ["Software"])
    client.force_login(user)
    page = client.get(reverse("jobs:company_create")).content.decode()
    assert 'name="industries"' in page and "Software" in page
    assert 'list="industry-suggestions"' in page and "Insurance" in page, "starter suggestions"

    software = Industry.objects.get(owner=user, slug="software")
    response = client.post(
        reverse("jobs:company_create"),
        {
            "name": "Aperture Science",
            "industries": [software.pk],
            "new_industries": "Research, software, Robotics",
        },
    )
    assert response.status_code == 302
    company = Company.objects.get(owner=user, name="Aperture Science")
    assert sorted(i.name for i in company.industries.all()) == ["Research", "Robotics", "Software"]
    assert Industry.objects.for_user(user).count() == 3, "software was already there"


def test_editing_keeps_the_ticked_ones_and_drops_the_rest(client, user):
    company = company_with(user, "Aperture Science", "Software", "Research")
    research = Industry.objects.get(owner=user, slug="research")
    client.force_login(user)
    response = client.post(
        reverse("jobs:company_update", args=[company.pk]),
        {"name": "Aperture Science", "industries": [research.pk], "new_industries": ""},
    )
    assert response.status_code == 302
    assert [i.name for i in company.industries.all()] == ["Research"]


def test_the_form_never_offers_another_persons_industries(user, other_user, rf):
    Industry.named(other_user, ["Theirs"])
    Industry.named(user, ["Mine"])
    form = CompanyForm(user=user)
    assert [i.name for i in form.fields["industries"].queryset] == ["Mine"]
    assert "Theirs" not in form.suggestions


# ------------------------------------------------------------- where they show


def test_the_company_page_and_table_show_every_industry(client, user):
    company_with(user, "Aperture Science", "Software", "Research")
    company_with(user, "Black Mesa", "Energy")
    client.force_login(user)

    page = client.get(reverse("jobs:company_detail", args=[1])).content.decode()
    assert "data-industries" in page and "Software" in page and "Research" in page

    table = client.get(reverse("jobs:company_list")).content.decode()
    assert "Software, Research" in table or "Research, Software" in table

    narrowed = client.get(reverse("jobs:company_list"), {"industry": "soft"})
    assert [c.name for c in narrowed.context["companies"]] == ["Aperture Science"]
    both = client.get(reverse("jobs:company_list"), {"industry": "e"})
    assert [c.name for c in both.context["companies"]] == ["Aperture Science", "Black Mesa"], (
        "a company matching in two industries is still one row"
    )

    searched = client.get(reverse("jobs:company_list"), {"q": "energy"})
    assert [c.name for c in searched.context["companies"]] == ["Black Mesa"]


# ---------------------------------------------------------------- the vocabulary page


def test_the_industries_page_lists_counts_renames_and_merges(client, user):
    company_with(user, "Aperture Science", "Fintech")
    company_with(user, "Black Mesa", "FinTech ")  # the same slug, so the same industry
    company_with(user, "Initech", "Financial technology")
    fintech = Industry.objects.get(owner=user, slug="fintech")
    long_form = Industry.objects.get(owner=user, slug="financial-technology")
    client.force_login(user)

    page = client.get(reverse("jobs:industry_list")).content.decode()
    assert "Fintech" in page and "2 companies" in page

    response = client.post(reverse("jobs:industry_update", args=[fintech.pk]), {"name": "Fin-tech"})
    assert response.status_code == 302
    fintech.refresh_from_db()
    assert fintech.name == "Fin-tech" and fintech.slug == "fin-tech"

    response = client.post(
        reverse("jobs:industry_update", args=[long_form.pk]),
        {"name": "Financial technology", "merge_into": fintech.pk},
    )
    assert response.status_code == 302
    assert not Industry.objects.filter(pk=long_form.pk).exists()
    assert Company.objects.get(owner=user, name="Initech").industries.get() == fintech
    assert Industry.objects.for_user(user).count() == 1

    response = client.post(reverse("jobs:industry_create"), {"name": "fin-tech"})
    assert response.status_code == 200 and "already have that industry" in response.content.decode()

    response = client.post(reverse("jobs:industry_delete", args=[fintech.pk]))
    assert response.status_code == 302
    assert Industry.objects.for_user(user).count() == 0
    assert Company.objects.for_user(user).count() == 3, "deleting a word deletes no company"


def test_another_persons_industry_is_not_there(client, user, other_user):
    theirs = Industry.named(other_user, ["Theirs"])[0]
    client.force_login(user)
    assert client.get(reverse("jobs:industry_update", args=[theirs.pk])).status_code == 404
    assert client.post(reverse("jobs:industry_delete", args=[theirs.pk])).status_code == 404
    assert "Theirs" not in client.get(reverse("jobs:industry_list")).content.decode()


# ----------------------------------------------------------------------- export


def test_industries_travel_as_a_list_and_the_old_string_still_imports(user, other_user):
    company_with(user, "Aperture Science", "Software", "Research")
    document = export_module.build_document(user)
    exported = document["companies"][0]
    assert exported["industries"] == ["Software", "Research"] or exported["industries"] == [
        "Research",
        "Software",
    ]
    assert "industry" not in exported

    archive = zipfile.ZipFile(export_module.write_archive(user))
    importer.load(other_user, archive)
    restored = Company.objects.get(owner=other_user)
    assert sorted(i.name for i in restored.industries.all()) == ["Research", "Software"]
    assert Industry.objects.for_user(other_user).count() == 2

    # A format 2 archive: one string, as people typed it.
    document["postulo"]["format"] = 2
    document["companies"][0].pop("industries")
    document["companies"][0]["industry"] = "Banking, Insurance"
    document["companies"][0]["name"] = "Old Style Ltd"
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w") as archive:
        archive.writestr("postulo.json", json.dumps(document, default=str))
    importer.load(other_user, zipfile.ZipFile(io.BytesIO(buffer.getvalue())), force=True)
    old_style = Company.objects.get(owner=other_user, name="Old Style Ltd")
    assert sorted(i.name for i in old_style.industries.all()) == ["Banking", "Insurance"]


def test_the_migration_turns_every_string_into_the_owners_words(user, other_user):
    migration = importlib.import_module("postulo.jobs.migrations.0004_industries")
    mine = Company.objects.create(owner=user, name="Aperture Science")
    theirs = Company.objects.create(owner=other_user, name="Black Mesa")

    # The column is gone from the model, so play the old rows through the shape the
    # migration reads: a model with a manager whose rows carry the old string.
    class FakeCompanies:
        def __init__(self, rows):
            self.rows = rows

        def exclude(self, **kwargs):
            return self

        def iterator(self):
            return iter(self.rows)

    class Row:
        def __init__(self, company, industry):
            self.owner_id = company.owner_id
            self.industry = industry
            self.industries = company.industries

    class FakeCompanyModel:
        objects = FakeCompanies(
            [Row(mine, "Software, Insurance; software"), Row(theirs, "Software")]
        )

    class FakeApps:
        def get_model(self, app, name):
            return Industry if name == "Industry" else FakeCompanyModel

    migration.industries_from_strings(FakeApps(), None)
    assert sorted(i.name for i in mine.industries.all()) == ["Insurance", "Software"]
    assert [i.name for i in theirs.industries.all()] == ["Software"]
    assert Industry.objects.for_user(user).count() == 2
    assert Industry.objects.for_user(other_user).count() == 1
    assert apps.get_model("jobs", "Industry") is Industry


# --------------------------------------------------------------------------- API


def test_the_api_reads_and_writes_industries_as_names(client, user):
    _record, raw = ApiToken.issue(user, "Agent", scopes=("read", "write"))
    headers = {"HTTP_AUTHORIZATION": f"Bearer {raw}"}

    response = client.post(
        "/api/v1/companies",
        data=json.dumps({"name": "Aperture Science", "industries": ["Software", "Research"]}),
        content_type="application/json",
        **headers,
    )
    assert response.status_code == 201
    assert response.json()["industries"] == ["Research", "Software"], "listed alphabetically"

    company = Company.objects.get(owner=user)
    response = client.patch(
        f"/api/v1/companies/{company.pk}",
        data=json.dumps({"industries": ["software"]}),
        content_type="application/json",
        **headers,
    )
    assert response.json()["industries"] == ["Software"], "replaced, and matched by slug"

    listed = client.get("/api/v1/companies?q=softw", **headers).json()
    assert [c["name"] for c in listed["items"]] == ["Aperture Science"]


# ---------------------------------------------------------------------- insights


def test_insights_count_an_application_under_each_of_its_companys_industries(user):
    aperture = company_with(user, "Aperture Science", "Software", "Research")
    black_mesa = company_with(user, "Black Mesa")
    for company, title in [(aperture, "Role A"), (aperture, "Role B"), (black_mesa, "Role C")]:
        posting = JobPosting.objects.create(owner=user, company=company, title=title)
        application = Application.objects.create(owner=user, posting=posting, status=Status.DRAFT)
        change_status(application, Status.APPLIED)
        if title == "Role A":
            change_status(application, Status.INTERVIEWING)

    rows = {row.name: row for row in analytics.build(user).industries}
    assert rows["Software"].applied == 2 and rows["Research"].applied == 2
    assert rows["Software"].interviewed == 1
    assert rows["Not recorded"].applied == 1


def test_the_industry_table_is_a_dashboard_widget(client, user):
    aperture = company_with(user, "Aperture Science", "Software")
    posting = JobPosting.objects.create(owner=user, company=aperture, title="Role")
    application = Application.objects.create(owner=user, posting=posting, status=Status.DRAFT)
    change_status(application, Status.APPLIED)
    profile = user.profile
    profile.dashboard_widgets = ["industries"]
    profile.save(update_fields=["dashboard_widgets"])
    client.force_login(user)
    body = client.get(reverse("core:home")).content.decode()
    assert "data-by-industry" in body and "By industry" in body
