"""Cross-account isolation for everything M2 added.

M1 proved the foundation keeps accounts apart. This proves each new model and view
actually sits on that foundation, which is the part that is easy to forget when adding
the sixth model.
"""

import pytest
from django.urls import reverse

from postulo.applications.models import Application, Reminder, Status
from postulo.core.models import Tag
from postulo.jobs.models import Company, Contact, JobPosting


@pytest.fixture
def their_data(db, other_user):
    """A complete little world belonging to somebody else."""
    company = Company.objects.create(owner=other_user, name="Umbrella Corporation")
    contact = Contact.objects.create(owner=other_user, company=company, name="Their Recruiter")
    posting = JobPosting.objects.create(owner=other_user, company=company, title="Their Role")
    application = Application.objects.create(
        owner=other_user, posting=posting, status=Status.INTERVIEWING
    )
    return {
        "company": company,
        "contact": contact,
        "posting": posting,
        "application": application,
        "tag": Tag.objects.create(owner=other_user, name="Their Tag"),
        "reminder": Reminder.objects.create(
            owner=other_user,
            application=application,
            summary="Their reminder",
            due_at="2026-01-01T09:00Z",
        ),
    }


@pytest.mark.parametrize(
    "model_path,key",
    [
        ("postulo.jobs.models.Company", "company"),
        ("postulo.jobs.models.Contact", "contact"),
        ("postulo.jobs.models.JobPosting", "posting"),
        ("postulo.applications.models.Application", "application"),
        ("postulo.applications.models.Reminder", "reminder"),
        ("postulo.core.models.Tag", "tag"),
    ],
)
def test_querysets_never_cross_accounts(user, their_data, model_path, key):
    module_name, class_name = model_path.rsplit(".", 1)
    model = getattr(__import__(module_name, fromlist=[class_name]), class_name)

    assert model.objects.count() >= 1, "the other account's row exists"
    assert not model.objects.for_user(user).exists(), "but not for this one"


@pytest.mark.parametrize(
    "url_name,key",
    [
        ("applications:detail", "application"),
        ("applications:update", "application"),
        ("applications:delete", "application"),
        ("jobs:company_detail", "company"),
        ("jobs:company_update", "company"),
        ("jobs:posting_detail", "posting"),
        ("jobs:posting_update", "posting"),
        ("jobs:contact_update", "contact"),
        ("applications:tag_update", "tag"),
    ],
)
def test_another_accounts_page_is_not_found(client, user, their_data, url_name, key):
    """404 rather than 403: confirming the record exists would itself be a disclosure."""
    client.force_login(user)
    response = client.get(reverse(url_name, args=[their_data[key].pk]))

    assert response.status_code == 404


def test_another_accounts_status_cannot_be_changed(client, user, their_data):
    client.force_login(user)
    application = their_data["application"]

    response = client.post(
        reverse("applications:status", args=[application.pk]), {"status": Status.REJECTED}
    )
    application.refresh_from_db()

    assert response.status_code == 404
    assert application.status == Status.INTERVIEWING


def test_another_accounts_timeline_cannot_be_written_to(client, user, their_data):
    client.force_login(user)
    application = their_data["application"]

    response = client.post(
        reverse("applications:event_create", args=[application.pk]),
        {"kind": "note", "occurred_at": "2026-09-01T10:00", "summary": "Intruding"},
    )

    assert response.status_code == 404
    assert not application.events.filter(summary="Intruding").exists()


def test_another_accounts_reminder_cannot_be_completed(client, user, their_data):
    client.force_login(user)
    reminder = their_data["reminder"]

    response = client.post(reverse("applications:reminder_complete", args=[reminder.pk]))
    reminder.refresh_from_db()

    assert response.status_code == 404
    assert not reminder.is_done


def test_forms_never_offer_another_accounts_records(client, user, their_data):
    """A select box populated from the whole table is a disclosure of its own."""
    from postulo.applications.forms import ApplicationIntakeForm, ReminderForm
    from postulo.jobs.forms import ContactForm, JobPostingForm

    assert their_data["tag"] not in ApplicationIntakeForm(user=user).fields["tags"].queryset
    assert their_data["company"] not in ContactForm(user=user).fields["company"].queryset
    assert their_data["company"] not in JobPostingForm(user=user).fields["company"].queryset
    assert their_data["application"] not in ReminderForm(user=user).fields["application"].queryset


def test_lists_show_nothing_belonging_to_anyone_else(client, user, their_data):
    client.force_login(user)

    assert len(client.get(reverse("applications:list")).context["applications"]) == 0
    assert len(client.get(reverse("jobs:company_list")).context["companies"]) == 0
    assert len(client.get(reverse("applications:reminder_list")).context["reminders"]) == 0


def test_a_duplicate_company_name_is_refused_for_one_account_only(db, user, other_user):
    """Two people may each keep their own record of the same employer."""
    from postulo.jobs.forms import CompanyForm

    Company.objects.create(owner=user, name="Aperture Science")

    mine = CompanyForm(data={"name": "aperture science"}, user=user)
    theirs = CompanyForm(data={"name": "Aperture Science"}, user=other_user)

    assert not mine.is_valid(), "matching loosely stops Acme, acme and ACME piling up"
    assert theirs.is_valid()
