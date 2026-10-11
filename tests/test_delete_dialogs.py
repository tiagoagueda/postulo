"""Delete asks over the object's own page, in a dialog, from the same facts as the page (#854)."""

from __future__ import annotations

import re

import pytest
from django.urls import reverse

from postulo.applications.models import Application, Status
from postulo.jobs.models import Company, JobPosting
from postulo.jobs.views import CompanyDeleteView, PostingDeleteView

pytestmark = pytest.mark.django_db


@pytest.fixture
def world(client, user):
    company = Company.objects.create(owner=user, name="Aperture Science")
    posting = JobPosting.objects.create(owner=user, company=company, title="Test Engineer")
    application = Application.objects.create(owner=user, posting=posting, status=Status.APPLIED)
    client.force_login(user)
    return {"company": company, "posting": posting, "application": application}


CASES = (
    ("company", "jobs:company_detail", "jobs:company_delete", "delete-company"),
    ("posting", "jobs:posting_detail", "jobs:posting_delete", "delete-posting"),
    ("application", "applications:detail", "applications:delete", "delete-application"),
)


def dialog_of(html: str, identifier: str) -> str:
    return re.search(rf'<dialog[^>]*id="{identifier}".*?</dialog>', html, re.S)[0]


@pytest.mark.parametrize(("key", "detail", "delete", "identifier"), CASES)
def test_the_button_opens_a_dialog_that_posts_to_the_delete_view(
    client, world, key, detail, delete, identifier
):
    obj = world[key]
    html = client.get(reverse(detail, args=[obj.pk])).content.decode()

    assert f'popovertarget="{identifier}"' in html, "a button that opens it with no script"
    assert reverse(delete, args=[obj.pk]) not in re.sub(
        r"<dialog.*?</dialog>", "", html, flags=re.S
    ), "no link to the page any more: the dialog is the way"
    dialog = dialog_of(html, identifier)
    assert 'role="alertdialog"' in dialog
    assert f'action="{reverse(delete, args=[obj.pk])}"' in dialog
    assert 'data-variant="destructive"' in dialog, "and a button that answers yes"
    assert "csrfmiddlewaretoken" in dialog


def test_the_dialog_counts_what_goes_with_the_company_as_the_page_does(client, world):
    company = world["company"]
    detail = client.get(reverse("jobs:company_detail", args=[company.pk])).content.decode()
    page = client.get(reverse("jobs:company_delete", args=[company.pk])).content.decode()

    def lines(html: str) -> list[str]:
        block = re.search(r"data-consequences[^>]*>(.*?)</ul>", html, re.S)[1]
        return [" ".join(t.split()) for t in re.findall(r"<li>(.*?)</li>", block, re.S)]

    assert lines(dialog_of(detail, "delete-company")) == lines(page)
    assert lines(page), "there is something to count"


def test_the_facts_are_the_delete_views_own(client, world, rf, user):
    from postulo.core import deletions

    request = rf.get("/")
    request.user = user
    facts = deletions.facts_for(PostingDeleteView, request, world["posting"])

    assert set(facts) <= set(deletions.KEYS)
    assert facts["consequences"], "the application goes with its listing"
    assert deletions.facts_for(CompanyDeleteView, request, world["company"])["consequences"]


@pytest.mark.parametrize(("key", "detail", "delete", "identifier"), CASES)
def test_confirming_in_the_dialog_deletes_and_the_page_is_still_there_for_a_link(
    client, world, key, detail, delete, identifier
):
    obj = world[key]
    assert client.get(reverse(delete, args=[obj.pk])).status_code == 200, "the page still answers"

    response = client.post(reverse(delete, args=[obj.pk]))

    assert response.status_code == 302
    assert not type(obj).objects.filter(pk=obj.pk).exists()


def test_somebody_elses_object_has_no_dialog_and_no_page(client, world, other_user):
    foreign = Company.objects.create(owner=other_user, name="Black Mesa")

    assert client.get(reverse("jobs:company_detail", args=[foreign.pk])).status_code == 404
    assert client.get(reverse("jobs:company_delete", args=[foreign.pk])).status_code == 404
