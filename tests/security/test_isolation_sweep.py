"""Every address with a record's id in it, asked for with somebody else's record (#232).

`docs/THREAT-MODEL.md` promises that "the test suite sweeps every view and queryset" for
ownership. It did not: `tests/test_ownership.py` proves the mixin against a test model, and
isolation is otherwise tested app by app, so a view added tomorrow with a pk in its address
could ship untested and nothing would say so. This is the sweep. It walks the URL resolver,
as `tests/test_page_coverage.py` does, and for every pattern that names a record makes that
record for another account and asks for it as this one -- GET and POST on a page, the
method an API route declares -- and insists on a 404. Never a 403: that confirms the record
exists, which is itself a disclosure.

A pattern with an argument has to be in `FACTORIES`, which says how to make the other
person's record, or in `EXCUSED` with a reason. Adding a page without deciding fails here.
"""

from __future__ import annotations

import datetime as dt
import json
from collections.abc import Callable

import pytest
from django.contrib.contenttypes.models import ContentType
from django.core.files.base import ContentFile
from django.urls import get_resolver, reverse
from django.utils import timezone

pytestmark = pytest.mark.django_db

#: Patterns that name no owned record, and why. Keep this honest.
EXCUSED: dict[str, str] = {
    "accounts:invite_accept": (
        "a token rather than an id, and nobody owns an invitation; tests/test_invites.py "
        "tries an unknown one and an expired one"
    ),
    "accounts:recovery_open": (
        "a token rather than an id; tests/test_recovery_links.py tries a spent one, a "
        "revoked one and a made-up one"
    ),
    "accounts:invite_revoke": (
        "administrators only, on an invitation rather than an owned record; the gate is "
        "tests/test_invites.py"
    ),
    "server:person_active": "administrators only, on an account; tests/test_server_settings.py",
    "server:person_admin": "administrators only, on an account; tests/test_server_settings.py",
    "server:person_delete": "administrators only, on an account; tests/test_server_settings.py",
    "server:person_plugins": "administrators only, on an account; tests/test_server_settings.py",
    "server:person_recovery": "administrators only, on an account; tests/test_server_settings.py",
    "server:person_username": "administrators only, on an account; tests/test_server_settings.py",
    "connections:create": "a plugin kind and name in the address, and no record",
    "connections:logo": (
        "a plugin name, and no record; who may see which logo is tests/test_plugin_logos.py"
    ),
    "core:table_settings": "a table's name in the address, and the settings are the caller's",
    "core:table_views": "a table's name in the address, and the views are the caller's",
    "resume:item_create": "a section's name in the address, and no record",
}


# ------------------------------------------------------------- the other person's records


def company(owner):
    from postulo.jobs.models import Company

    return Company.objects.create(owner=owner, name="Somebody else's company")


def errand(owner):
    from postulo.core.models import Errand

    return Errand.objects.create(owner=owner, kind="export")


def export_archive(owner):
    from django.core.files.base import ContentFile
    from django.utils import timezone

    from postulo.core.models import ExportArchive

    archive = ExportArchive(
        owner=owner,
        filename="theirs.zip",
        size=3,
        expires_at=timezone.now() + dt.timedelta(hours=1),
    )
    archive.file.save("theirs.zip", ContentFile(b"zip"), save=True)
    return archive


def posting(owner):
    from postulo.jobs.models import JobPosting

    return JobPosting.objects.create(owner=owner, company=company(owner), title="Their role")


def application(owner):
    from postulo.applications.models import Application, Status

    return Application.objects.create(owner=owner, posting=posting(owner), status=Status.APPLIED)


def interview(owner):
    from postulo.applications.models import Interview, InterviewKind

    starts = timezone.now() + dt.timedelta(days=2)
    return Interview.objects.create(
        owner=owner,
        application=application(owner),
        kind=InterviewKind.PHONE,
        starts_at=starts,
        ends_at=starts + dt.timedelta(hours=1),
    )


def reminder(owner):
    from postulo.applications.models import Reminder

    return Reminder.objects.create(
        owner=owner, application=application(owner), summary="Theirs", due_at=timezone.now()
    )


def suggestion(owner):
    from postulo.applications.models import Suggestion

    return Suggestion.objects.create(
        owner=owner, application=application(owner), source="test", summary="Theirs"
    )


def tag(owner):
    from postulo.applications.models import Tag

    return Tag.objects.create(owner=owner, name="Their tag")


def contact(owner):
    from postulo.jobs.models import Contact

    return Contact.objects.create(owner=owner, company=company(owner), name="Their contact")


def industry(owner):
    from postulo.jobs.models import Industry

    return Industry.objects.create(owner=owner, name="Their industry")


def capture(owner):
    from postulo.jobs.models import Capture, CaptureStatus

    return Capture.objects.create(
        owner=owner,
        url="https://example.org/theirs",
        data={"title": "Their capture"},
        status=CaptureStatus.PENDING,
    )


def cv(owner):
    from postulo.documents.models import CV

    return CV.objects.create(owner=owner, name="Their CV")


def experience(owner):
    from postulo.resume.models import Experience

    return Experience.objects.create(
        owner=owner,
        organisation="Their employer",
        role="Their role",
        start_date=dt.date(2020, 1, 1),
    )


def cv_item(owner):
    from postulo.documents.models import CVItem

    entry = experience(owner)
    return CVItem.objects.create(
        owner=owner,
        cv=cv(owner),
        content_type=ContentType.objects.get_for_model(type(entry)),
        object_id=entry.pk,
    )


def letter(owner):
    from postulo.documents.models import CoverLetter

    return CoverLetter.objects.create(owner=owner, name="Their letter", body="Dear them")


def upload(owner):
    from postulo.documents.models import UploadedDocument

    return UploadedDocument.objects.create(
        owner=owner, title="Their upload", file=ContentFile(b"%PDF-1.7 theirs", name="theirs.pdf")
    )


def rendered(owner):
    from postulo.documents.models import RenderedDocument

    return RenderedDocument.objects.create(
        owner=owner,
        title="Their sent CV",
        kind="cv",
        source=cv(owner),
        file=ContentFile(b"%PDF-1.7 theirs", name="sent.pdf"),
        checksum="theirs",
    )


def link(owner):
    from postulo.resume.models import Link

    return Link.objects.create(owner=owner, title="Their site", url="https://example.org", order=0)


def connection(owner):
    from postulo.plugins.models import Connection

    return Connection.objects.create(
        owner=owner, kind="notifier", plugin="email", label="Their mail", config={}
    )


def token(owner):
    from postulo.api.models import ApiToken

    return ApiToken.issue(owner, "Their agent")[0]


def pk_of(make: Callable, **extra) -> Callable:
    return lambda owner: {"pk": make(owner).pk, **extra}


#: For every pattern that names a record: how to make it for the other person, as the
#: keyword arguments `reverse()` needs.
FACTORIES: dict[str, Callable] = {
    # accounts: a picture is somebody's own, or an administrator's to see
    "accounts:avatar": lambda owner: {"pk": owner.pk},
    "api:token_revoke": pk_of(token),
    # applications
    "applications:delete": pk_of(application),
    "applications:detail": pk_of(application),
    "applications:event_create": pk_of(application),
    "applications:interview_create": pk_of(application),
    "applications:quiet_action": pk_of(application),
    "applications:status": pk_of(application),
    "applications:update": pk_of(application),
    "applications:interview_ics": pk_of(interview),
    "applications:interview_outcome": pk_of(interview),
    "applications:interview_update": pk_of(interview),
    "applications:reminder_complete": pk_of(reminder),
    "applications:reminder_delete": pk_of(reminder),
    "applications:reminder_later": pk_of(reminder),
    "applications:reminder_update": pk_of(reminder),
    "applications:suggestion_action": pk_of(suggestion, action="dismiss"),
    "applications:tag_delete": pk_of(tag),
    "applications:tag_update": pk_of(tag),
    # core: a piece of slow work, and the archive it produced (#247)
    "core:errand": pk_of(errand),
    "core:errand_state": pk_of(errand),
    "core:export_archive": pk_of(export_archive),
    # connections
    "connections:backfill": pk_of(connection),
    "connections:consent": pk_of(connection),
    "connections:delete": pk_of(connection),
    "connections:edit": pk_of(connection),
    "connections:sync_now": pk_of(connection),
    "connections:test": pk_of(connection),
    # documents
    "documents:application_documents": pk_of(application),
    "documents:send": pk_of(application),
    "documents:cv_add_items": pk_of(cv),
    "documents:cv_delete": pk_of(cv),
    "documents:cv_detail": pk_of(cv),
    "documents:cv_export": pk_of(cv),
    "documents:cv_preview": pk_of(cv),
    "documents:cv_update": pk_of(cv),
    "documents:cv_item_delete": pk_of(cv_item),
    "documents:cv_item_move": pk_of(cv_item, direction="up"),
    "documents:cv_item_update": pk_of(cv_item),
    "documents:letter_delete": pk_of(letter),
    "documents:letter_detail": pk_of(letter),
    "documents:letter_preview": pk_of(letter),
    "documents:letter_update": pk_of(letter),
    "documents:rendered_archive": pk_of(rendered),
    "documents:rendered_download": pk_of(rendered),
    "documents:upload_archive": pk_of(upload),
    "documents:upload_delete": pk_of(upload),
    "documents:upload_download": pk_of(upload),
    "documents:upload_update": pk_of(upload),
    # jobs
    "jobs:capture_discard": pk_of(capture),
    "jobs:capture_review": pk_of(capture),
    "jobs:company_cell": pk_of(company, column="name"),
    "jobs:company_delete": pk_of(company),
    "jobs:company_detail": pk_of(company),
    "jobs:company_logo": pk_of(company),
    "jobs:company_logo_action": pk_of(company, action="find"),
    "jobs:company_update": pk_of(company),
    "jobs:contact_delete": pk_of(contact),
    "jobs:contact_update": pk_of(contact),
    "jobs:industry_delete": pk_of(industry),
    "jobs:industry_update": pk_of(industry),
    "jobs:posting_delete": pk_of(posting),
    "jobs:posting_detail": pk_of(posting),
    "jobs:posting_update": pk_of(posting),
    "listings:apply": pk_of(posting),
    "listings:cell": pk_of(posting, column="title"),
    "listings:discard": pk_of(posting),
    "listings:restore": pk_of(posting),
    "listings:shortlist": pk_of(posting),
    # the career record
    "resume:item_delete": pk_of(experience, section="experience"),
    "resume:item_languages": pk_of(experience, section="experience"),
    "resume:item_move": pk_of(experience, section="experience", direction="up"),
    "resume:item_update": pk_of(experience, section="experience"),
    "resume:link_check": pk_of(link),
}

#: The JSON API: the same, with the method each route answers and a body that passes its
#: schema, so that the 404 comes from the lookup and not from validation.
API: dict[str, tuple[str, Callable, dict]] = {
    "postulo-api:get_company": ("get", pk_of(company), {}),
    "postulo-api:patch_company": ("patch", pk_of(company), {"notes": "x"}),
    "postulo-api:add_contact": ("post", pk_of(company), {"name": "x"}),
    "postulo-api:get_application": ("get", pk_of(application), {}),
    "postulo-api:add_event": ("post", pk_of(application), {"summary": "x"}),
    "postulo-api:set_status": ("post", pk_of(application), {"status": "applied"}),
    "postulo-api:get_cv": ("get", pk_of(cv), {}),
    "postulo-api:get_letter": ("get", pk_of(letter), {}),
    "postulo-api:get_interview": ("get", pk_of(interview), {}),
    "postulo-api:change_interview": ("patch", pk_of(interview), {"notes": "x"}),
    "postulo-api:record_outcome": ("post", pk_of(interview), {"outcome": "done"}),
    "postulo-api:interview_calendar": ("get", pk_of(interview), {}),
    "postulo-api:get_listing": ("get", pk_of(posting), {}),
    "postulo-api:apply": ("post", pk_of(posting), {}),
    "postulo-api:discard": ("post", pk_of(posting), {}),
    "postulo-api:restore": ("post", pk_of(posting), {}),
    "postulo-api:shortlist": ("post", pk_of(posting), {}),
    "postulo-api:complete_reminder": ("post", pk_of(reminder), {}),
    "postulo-api:change_reminder": ("patch", pk_of(reminder), {"summary": "x"}),
    "postulo-api:delete_reminder": ("delete", pk_of(reminder), {}),
    "postulo-api:document_download": ("get", pk_of(upload, source="upload"), {}),
}

#: Somebody else's framework: allauth's pages are keyed by its own records and its own
#: tests, and the admin is checked as a whole in tests/security/test_admin_exposure.py.
NOT_OURS = ("admin:", "account_", "mfa_", "openid_connect_", "socialaccount_")


def patterns_with_arguments() -> dict[str, str]:
    """Every named pattern whose address captures something, as `name -> route`."""

    def walk(resolver, prefix="", route=""):
        for pattern in resolver.url_patterns:
            text = str(pattern.pattern)
            if hasattr(pattern, "url_patterns"):
                namespace = pattern.namespace or ""
                yield from walk(
                    pattern, f"{prefix}{namespace}:" if namespace else prefix, route + text
                )
            elif pattern.name:
                yield f"{prefix}{pattern.name}", route + text

    return {
        name: route
        for name, route in walk(get_resolver())
        if "<" in route and not name.startswith(NOT_OURS)
    }


# ------------------------------------------------------------------ the bookkeeping


def test_every_address_that_names_a_record_is_swept_or_excused():
    names = set(patterns_with_arguments())
    undecided = sorted(names - set(FACTORIES) - set(API) - set(EXCUSED))
    assert not undecided, (
        "These addresses name a record and nobody has said what happens when it is "
        f"somebody else's. Add each to FACTORIES or API, or to EXCUSED with a reason: {undecided}"
    )


def test_nothing_is_listed_that_no_longer_exists():
    names = set(patterns_with_arguments())
    stale = sorted((set(FACTORIES) | set(API) | set(EXCUSED)) - names)
    assert not stale, f"listed here and gone from the resolver: {stale}"


def test_nothing_is_both_swept_and_excused():
    both = sorted((set(FACTORIES) | set(API)) & set(EXCUSED))
    assert not both, f"listed twice, which means one of them is wrong: {both}"


@pytest.mark.parametrize("name", sorted(EXCUSED))
def test_every_excuse_says_something(name):
    assert len(EXCUSED[name]) > 20, f"{name}: {EXCUSED[name]!r} is not a reason"


# ------------------------------------------------------------------------ the sweep


@pytest.mark.parametrize("name", sorted(FACTORIES))
def test_somebody_elses_record_is_not_found(client, user, other_user, name):
    """404 on GET and on POST, and never 403. A method the view does not answer is 405,
    which discloses nothing either."""
    url = reverse(name, kwargs=FACTORIES[name](other_user))
    client.force_login(user)

    for method in (client.get, client.post):
        response = method(url)
        assert response.status_code in (404, 405), (
            f"{name} {url}: {method.__name__.upper()} answered {response.status_code} "
            "for somebody else's record"
        )


@pytest.mark.parametrize("name", sorted(API))
def test_somebody_elses_record_is_not_found_through_the_api(client, user, other_user, name):
    from postulo.api.models import ApiToken

    method, factory, body = API[name]
    _record, raw = ApiToken.issue(
        user, "Sweep", scopes=("read", "write", "documents:read", "captures")
    )
    url = reverse(name, kwargs=factory(other_user))

    response = getattr(client, method)(
        url,
        data=json.dumps(body) if method != "get" else None,
        content_type="application/json",
        HTTP_AUTHORIZATION=f"Bearer {raw}",
    )
    assert response.status_code == 404, (
        f"{name} {url}: {method.upper()} answered {response.status_code} for somebody "
        f"else's record: {response.content[:200]!r}"
    )


def test_the_sweep_would_notice_a_leak(client, user, other_user):
    """The sweep is only worth having if a record that *is* reachable makes it fail: the
    same addresses, with the person's own record, are not 404."""
    url = reverse("jobs:company_detail", kwargs=FACTORIES["jobs:company_detail"](user))
    client.force_login(user)
    assert client.get(url).status_code == 200
