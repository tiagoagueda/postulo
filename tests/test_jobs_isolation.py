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
    # The reminders are the calendar's agenda narrowed to them (#316), overdue ones and all.
    page = client.get(reverse("applications:calendar"), {"view": "agenda", "kinds": "reminder"})
    reminders = page.context["page"]
    assert not reminders.events and not reminders.overdue and not reminders.further


def test_a_duplicate_company_name_is_refused_for_one_account_only(db, user, other_user):
    """Two people may each keep their own record of the same employer."""
    from postulo.jobs.forms import CompanyForm

    Company.objects.create(owner=user, name="Aperture Science")

    mine = CompanyForm(data={"name": "aperture science"}, user=user)
    theirs = CompanyForm(data={"name": "Aperture Science"}, user=other_user)

    assert not mine.is_valid(), "matching loosely stops Acme, acme and ACME piling up"
    assert theirs.is_valid()


# ------------------------------------------------------------ messaging handles (#682)


def test_another_accounts_handles_are_not_found_not_offered_and_not_touched(
    client, user, their_data, other_user
):
    """A contact's handles are the account's own: the page of somebody else's contact is a
    404, and a row of theirs named in a post to one of mine is neither read nor changed."""
    from postulo.core.models import MessagingHandle

    theirs = MessagingHandle.objects.create(
        owner=other_user,
        holder=their_data["contact"],
        service="matrix",
        handle="@secret:umbrella.example",
        is_primary=True,
    )
    mine = Contact.objects.create(
        owner=user, company=Company.objects.create(owner=user, name="Mine"), name="Mine"
    )
    client.force_login(user)

    assert (
        client.get(reverse("jobs:contact_update", args=[their_data["contact"].pk])).status_code
        == 404
    )
    assert (
        client.post(
            reverse("jobs:contact_update", args=[their_data["contact"].pk]),
            {"name": "Taken", "messaging-TOTAL_FORMS": "0", "messaging-INITIAL_FORMS": "0"},
        ).status_code
        == 404
    )
    page = client.get(reverse("jobs:contact_update", args=[mine.pk])).content.decode()
    assert "@secret:umbrella.example" not in page

    response = client.post(
        reverse("jobs:contact_update", args=[mine.pk]),
        {
            "name": "Mine",
            "role": "",
            "company": mine.company_id,
            "email": "",
            "notes": "",
            "messaging-TOTAL_FORMS": "1",
            "messaging-INITIAL_FORMS": "1",
            "messaging-MIN_NUM_FORMS": "0",
            "messaging-MAX_NUM_FORMS": "1000",
            "messaging-0-id": str(theirs.pk),
            "messaging-0-service": "matrix",
            "messaging-0-handle": "@taken-over:mine.example",
            "messaging-0-DELETE": "on",
        },
    )

    assert response.status_code in (200, 302)
    theirs.refresh_from_db()
    assert (theirs.handle, theirs.owner, theirs.object_id) == (
        "@secret:umbrella.example",
        other_user,
        their_data["contact"].pk,
    )
    assert not mine.messaging_handles.exists(), "nothing of theirs was moved to mine either"


def test_another_accounts_company_is_neither_offered_nor_linked_by_the_career(
    client, user, other_user
):
    """The career form suggests and links only the person's own companies (#683)."""
    from postulo.jobs.models import Company
    from postulo.resume.forms import ExperienceForm

    theirs = Company.objects.create(owner=other_user, name="Umbrella Corp", notes="Secret")
    assert "Umbrella Corp" not in ExperienceForm(user=user).datalists["company-suggestions"]

    client.force_login(user)
    response = client.post(
        reverse("resume:item_create", args=["experience"]),
        {"role": "Engineer", "organisation": "Umbrella Corp", "start_date": "2020-01-01"},
    )
    assert response.status_code == 302
    from postulo.resume.models import Experience

    entry = Experience.objects.get(owner=user)
    assert entry.company is not None and entry.company != theirs
    assert entry.company.owner == user
    theirs.refresh_from_db()
    assert not theirs.career_entries.exists()
    page = client.get(reverse("resume:item_update", args=["experience", entry.pk]))
    assert "Secret" not in page.content.decode()


def test_another_accounts_company_is_neither_offered_nor_linked_by_an_education_entry(
    client, user, other_user
):
    """The education form suggests and links only the person's own companies (#685)."""
    from postulo.jobs.models import Company, Industry
    from postulo.resume.forms import EducationForm
    from postulo.resume.models import Education

    theirs = Company.objects.create(owner=other_user, name="Miskatonic", notes="Secret")
    theirs.industries.set(Industry.named(other_user, ["Education"]))
    assert "Miskatonic" not in EducationForm(user=user).datalists["school-suggestions"]

    client.force_login(user)
    response = client.post(
        reverse("resume:item_create", args=["education"]),
        {"qualification": "BSc", "institution": "Miskatonic"},
    )
    assert response.status_code == 302
    entry = Education.objects.get(owner=user)
    assert entry.company is not None and entry.company != theirs
    assert entry.company.owner == user
    assert not theirs.education_entries.exists()
    assert theirs.industries.count() == 1
    page = client.get(reverse("resume:item_update", args=["education", entry.pk]))
    assert "Secret" not in page.content.decode()


def test_another_accounts_company_is_neither_offered_nor_linked_as_an_issuer(
    client, user, other_user
):
    """The certification form suggests and links only the person's own companies (#686)."""
    from postulo.jobs.models import Company
    from postulo.resume.forms import CertificationForm
    from postulo.resume.models import Certification

    theirs = Company.objects.create(owner=other_user, name="Umbrella Academy", notes="Secret")
    assert "Umbrella Academy" not in CertificationForm(user=user).datalists["issuer-suggestions"]

    client.force_login(user)
    response = client.post(
        reverse("resume:item_create", args=["certification"]),
        {"name": "CKA", "issuer": "Umbrella Academy"},
    )
    assert response.status_code == 302
    entry = Certification.objects.get(owner=user)
    assert entry.company is not None and entry.company != theirs
    assert entry.company.owner == user
    assert not theirs.certifications.exists()
    page = client.get(reverse("resume:item_update", args=["certification", entry.pk]))
    assert "Secret" not in page.content.decode()
