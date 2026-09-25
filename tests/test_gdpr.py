"""The data-protection side (#297): what the instance keeps on other people, and the duties.

The export answers "what do you have on me?"; the erasure deletes one contact and says what
it removed; the retention policy is a dry run, never a janitor; the record of processing is
drawn from the registry at read time; the notice is the operator's words or nothing.
"""

import datetime as dt
import json

import pytest
from django.contrib.auth import get_user_model
from django.contrib.messages import get_messages
from django.urls import reverse
from django.utils import timezone

from postulo.applications.models import Application
from postulo.core import gdpr
from postulo.core.models import SiteSettings, WebLink
from postulo.jobs.models import Company, Contact, JobPosting
from postulo.plugins.gdpr import GDPR
from postulo.plugins.models import Connection, PluginPolicy

pytestmark = pytest.mark.django_db

User = get_user_model()
PASSWORD = "a-fairly-long-password-42"


@pytest.fixture
def admin(db):
    return User.objects.create_user(
        email="gdpr-admin@example.org",
        password=PASSWORD,
        username="gdpr-admin",
        is_staff=True,
        is_superuser=True,
    )


def make_company(user, name="Aperture"):
    return Company.objects.create(owner=user, name=name)


def make_contact(user, company=None, **fields):
    defaults = {
        "owner": user,
        "name": "Cave Johnson",
        "role": "Recruiter",
        "email": "cave@aperture.example",
        "notes": "Met at the fair.",
    }
    defaults.update(fields)
    return Contact.objects.create(company=company, **defaults)


def add_link(contact, owner, url="https://aperture.example", kind=WebLink.Kind.WEBSITE):
    return WebLink.objects.create(owner=owner, holder=contact, kind=kind, label="Site", url=url)


def make_application(user, company, contact):
    posting = JobPosting.objects.create(owner=user, company=company, title="Engineer")
    return Application.objects.create(owner=user, posting=posting, contact=contact)


# --------------------------------------------------------------------- the export


def test_the_document_carries_everything_on_one_person(user):
    company = make_company(user)
    contact = make_contact(user, company)
    add_link(contact, user)

    document = gdpr.contact_document(contact)

    assert document["document"] == gdpr.DOCUMENT_NAME
    assert document["version"] == gdpr.DOCUMENT_VERSION
    assert document["contact"]["name"] == contact.name
    assert document["contact"]["role"] == contact.role
    assert document["contact"]["email"] == contact.email
    assert document["contact"]["notes"] == contact.notes
    assert document["contact"]["company"] == company.name
    assert [row["url"] for row in document["web_links"]] == ["https://aperture.example"]
    assert document["phone_numbers"] == []
    assert document["postal_addresses"] == []


def test_the_document_follows_the_archive_discipline_for_plugins(user):
    contact = make_contact(user)

    plugins = gdpr.contact_document(contact)["plugins"]

    assert isinstance(plugins["carried"], dict)
    assert "not_carried" not in plugins, "every plugin Postulo ships carries its own rows"


# ---------------------------------------------------------------------- the erasure


def test_erasure_deletes_and_says_what_it_removed(user):
    company = make_company(user)
    contact = make_contact(user, company)
    link = add_link(contact, user)
    application = make_application(user, company, contact)

    report = gdpr.erase_contact(contact)

    assert not Contact.objects.filter(pk=contact.pk).exists()
    assert not WebLink.objects.filter(pk=link.pk).exists()
    application.refresh_from_db()
    assert application.contact_id is None, "the history stays, without its main contact"
    assert report.name == contact.name
    assert report.deleted["web_links"] == 1
    assert report.unlinked["applications"] == 1
    assert "is gone" in report.summary()
    assert "application kept, without its main contact" in report.summary()


def test_erasure_says_when_it_left_nothing(user):
    contact = make_contact(user)

    report = gdpr.erase_contact(contact)

    assert not Contact.objects.filter(pk=contact.pk).exists()
    assert report.summary() == "Nothing was left to remove."


def test_switching_the_feature_off_deletes_nothing(user):
    """Off is not an erasure: it keeps every row and merely stops offering the pages."""
    contact = make_contact(user)
    PluginPolicy.objects.create(plugin=GDPR, person=None, state=PluginPolicy.State.FORCED_OFF)

    assert gdpr.is_offered(user) is False
    assert Contact.objects.filter(pk=contact.pk).exists()


# --------------------------------------------------------------------- the retention


def test_a_retention_without_a_limit_touches_nothing(user):
    make_contact(user)

    assert gdpr.retention_dry_run() == {"days": None, "cutoff": None, "contacts": []}


def test_the_dry_run_lists_only_what_is_older_than_the_line(user):
    settings = SiteSettings.get()
    settings.retention_days = 30
    settings.save()

    company = make_company(user)
    old = make_contact(user, company, name="An old contact")
    add_link(old, user)
    Contact.objects.filter(pk=old.pk).update(created_at=timezone.now() - dt.timedelta(days=60))
    make_contact(user, name="A new contact")

    report = gdpr.retention_dry_run()

    assert report["days"] == 30
    assert [row["name"] for row in report["contacts"]] == ["An old contact"]
    row = report["contacts"][0]
    assert row["company"] == company.name
    assert row["would_remove"]["web_links"] == 1
    assert Contact.objects.filter(pk=old.pk).exists(), "the dry run deletes nothing"


def test_the_dry_run_counts_what_the_erasures_would_keep(user):
    settings = SiteSettings.get()
    settings.retention_days = 30
    settings.save()

    company = make_company(user)
    old = make_contact(user, company)
    make_application(user, company, old)
    Contact.objects.filter(pk=old.pk).update(created_at=timezone.now() - dt.timedelta(days=45))

    row = gdpr.retention_dry_run()["contacts"][0]

    assert row["would_remove"]["applications_unlinked"] == 1, (
        "the application is kept, the way an erasure keeps it; the report says so"
    )


# --------------------------------------------------------- the record of processing


def test_the_record_is_drawn_from_the_registry_and_the_connections(user):
    record = gdpr.record_of_processing()

    names = [purpose["name"] for purpose in record["purposes"]]
    assert GDPR in names, "the gdpr plugin is a purpose, like every installed one"
    assert record["recipients"] == []

    connection = Connection.objects.create(
        owner=user, kind="store", plugin="a-plugin", label="The outbox"
    )
    recipients = gdpr.record_of_processing()["recipients"]
    assert [row["plugin"] for row in recipients] == ["a-plugin"]
    assert recipients[0]["kind"] == "store"
    assert recipients[0]["label"] == connection.label


# ----------------------------------------------------------------------- the notice


def test_the_notice_is_the_operators_words_and_nothing_is_invented(user):
    assert gdpr.notice() == "", "an instance with no notice to give does not invent one"

    settings = SiteSettings.get()
    settings.privacy_notice = "This instance keeps what you record, for as long as you say."
    settings.save()

    assert gdpr.notice() == "This instance keeps what you record, for as long as you say."


def test_the_notice_shows_where_the_site_promises_to_keep_another_person(client, user):
    client.force_login(user)
    assert "This instance keeps" not in client.get(reverse("jobs:contact_create")).content.decode()

    settings = SiteSettings.get()
    settings.privacy_notice = "This instance keeps what you record, for as long as you say."
    settings.save()

    html = client.get(reverse("jobs:contact_create")).content.decode()
    assert "This instance keeps what you record" in html

    PluginPolicy.objects.create(plugin=GDPR, person=None, state=PluginPolicy.State.FORCED_OFF)
    assert (
        "This instance keeps what you record"
        not in client.get(reverse("jobs:contact_create")).content.decode()
    ), "off is not a promise the site keeps any more"


# -------------------------------------------------------------- policy and offering


def test_is_offered_follows_the_policy(user):
    assert gdpr.is_offered(user) is True

    PluginPolicy.objects.create(plugin=GDPR, person=None, state=PluginPolicy.State.FORCED_OFF)
    assert gdpr.is_offered(user) is False

    PluginPolicy.objects.create(plugin=GDPR, person=user, state=PluginPolicy.State.FORCED_ON)
    assert gdpr.is_offered(user) is True, "a person's decision beats the instance default"


# ------------------------------------------------------------------------ the pages


def test_the_export_is_a_document_that_leaves_the_site(client, user):
    contact = make_contact(user, make_company(user))
    client.force_login(user)

    response = client.get(reverse("jobs:contact_export", args=[contact.pk]))

    assert response.status_code == 200
    assert response["Content-Type"] == "application/json"
    assert response["Content-Disposition"] == (
        f'attachment; filename="postulo-contact-{contact.pk}.json"'
    )
    assert json.loads(response.content)["contact"]["name"] == contact.name


def test_the_export_is_only_the_owners_and_only_while_offered(client, user, other_user):
    contact = make_contact(user, make_company(user))

    client.force_login(other_user)
    assert client.get(reverse("jobs:contact_export", args=[contact.pk])).status_code == 404
    client.logout()
    assert client.get(reverse("jobs:contact_export", args=[contact.pk])).status_code == 302

    PluginPolicy.objects.create(plugin=GDPR, person=None, state=PluginPolicy.State.FORCED_OFF)
    client.force_login(user)
    assert client.get(reverse("jobs:contact_export", args=[contact.pk])).status_code == 404


def test_deleting_a_contact_says_what_went(client, user):
    company = make_company(user)
    contact = make_contact(user, company)
    add_link(contact, user)
    make_application(user, company, contact)
    client.force_login(user)

    response = client.post(reverse("jobs:contact_delete", args=[contact.pk]))

    assert response.status_code == 302
    assert not Contact.objects.filter(pk=contact.pk).exists()
    messages = " ".join(str(m) for m in get_messages(response.wsgi_request))
    assert "is gone" in messages
    assert "application kept, without its main contact" in messages


def test_deleting_without_the_feature_is_the_plain_delete(client, user):
    contact = make_contact(user, make_company(user))
    add_link(contact, user)
    PluginPolicy.objects.create(plugin=GDPR, person=None, state=PluginPolicy.State.FORCED_OFF)
    client.force_login(user)

    response = client.post(reverse("jobs:contact_delete", args=[contact.pk]))

    assert response.status_code == 302
    assert not Contact.objects.filter(pk=contact.pk).exists()
    messages = " ".join(str(m) for m in get_messages(response.wsgi_request))
    assert "is gone" not in messages


def test_the_data_protection_page_saves_the_policy(client, admin):
    client.force_login(admin)
    response = client.get(reverse("server:data_protection"))
    assert response.status_code == 200
    assert "Run the dry run" in response.content.decode()

    client.post(
        reverse("server:data_protection"),
        {"retention_days": "90", "privacy_notice": "The operator's own words."},
    )

    settings = SiteSettings.get()
    assert settings.retention_days == 90
    assert settings.privacy_notice == "The operator's own words."


def test_the_dry_run_shows_what_would_be_touched_and_saves_nothing(client, admin, user):
    settings = SiteSettings.get()
    settings.retention_days = 30
    settings.save()
    old = make_contact(user, make_company(user), name="An old contact")
    Contact.objects.filter(pk=old.pk).update(created_at=timezone.now() - dt.timedelta(days=45))
    client.force_login(admin)

    response = client.post(
        reverse("server:data_protection"), {"retention_days": "1", "dry_run": "1"}
    )

    assert response.status_code == 200
    html = response.content.decode()
    assert "An old contact" in html
    assert "Nothing was removed" in html
    assert Contact.objects.filter(pk=old.pk).exists(), "the dry run deletes nothing"
    assert SiteSettings.get().retention_days == 30, "and the button does not spend the form"


def test_the_record_page_is_drawn_at_render_time(client, admin, user):
    client.force_login(admin)
    html = client.get(reverse("server:record_of_processing")).content.decode()
    assert "Record of processing" in html
    assert "No connections" in html

    Connection.objects.create(owner=user, kind="store", plugin="a-plugin", label="The outbox")
    html = client.get(reverse("server:record_of_processing")).content.decode()
    assert "a-plugin" in html
    assert "The outbox" in html


def test_everyone_but_the_administrator_is_kept_from_the_pages(client, user):
    client.force_login(user)
    assert client.get(reverse("server:data_protection")).status_code == 403
    assert client.get(reverse("server:record_of_processing")).status_code == 403
