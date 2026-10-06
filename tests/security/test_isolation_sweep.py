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
import itertools
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
        "administrators only, on an invitation rather than an owned record; a member's GET "
        "and POST are refused in tests/security/test_staff_only.py"
    ),
    "server:person_active": (
        "administrators only, on an account; a member's GET and POST are refused in "
        "tests/security/test_staff_only.py"
    ),
    "server:person_admin": (
        "administrators only, on an account; a member's GET and POST are refused in "
        "tests/security/test_staff_only.py"
    ),
    "server:person_delete": (
        "administrators only, on an account; a member's GET and POST are refused in "
        "tests/security/test_staff_only.py"
    ),
    "server:person_plugins": (
        "administrators only, on an account; a member's GET and POST are refused in "
        "tests/security/test_staff_only.py"
    ),
    "server:person_recovery": (
        "administrators only, on an account; a member's GET and POST are refused in "
        "tests/security/test_staff_only.py"
    ),
    "server:person_username": (
        "administrators only, on an account; a member's GET and POST are refused in "
        "tests/security/test_staff_only.py"
    ),
    "server:backup_delete": (
        "administrators only, and a file named in the address rather than an owned record; "
        "tests/security/test_backups_page.py"
    ),
    "server:backup_download": (
        "administrators only, and a file named in the address rather than an owned record; "
        "tests/security/test_backups_page.py"
    ),
    "server:backup_restore": (
        "administrators only, and a file named in the address rather than an owned record; "
        "tests/security/test_backups_page.py"
    ),
    "server:backup_verify": (
        "administrators only, and a file named in the address rather than an owned record; "
        "tests/security/test_backups_page.py"
    ),
    "connections:create": "a plugin kind and name in the address, and no record",
    "connections:logo": (
        "a plugin name, and no record; who may see which logo is tests/test_plugin_logos.py"
    ),
    "core:help_topic": (
        "a help topic's name in the address, and no record: the same words for everybody "
        "signed in; tests/test_help_topics.py tries an unknown one and a hostile way back"
    ),
    "core:table_settings": "a table's name in the address, and the settings are the caller's",
    "core:table_views": "a table's name in the address, and the views are the caller's",
    "resume:item_create": "a section's name in the address, and no record",
    "settings:save_field": (
        "a section and a field in the address, and no record: it always writes the "
        "signed-in person's own profile; tests/security/test_save_as_you_go.py asks for "
        "another account's and for a field no form lists (#656)"
    ),
}


# ------------------------------------------------------------- the other person's records


_serial = itertools.count(1)


def _named(text: str) -> str:
    """A name no other record of the same person has, for the kinds that must be unique."""
    return f"{text} {next(_serial)}"


PNG = b"\x89PNG\r\n\x1a\n" + b"\0" * 32


def company(owner):
    from postulo.jobs.models import Company

    return Company.objects.create(owner=owner, name=_named("Somebody else's company"))


def logo_kept(owner):
    """A company with its logo stored, so that a 404 is the lookup refusing and not a file
    that was never there (as `kept_page` does)."""
    from postulo.jobs import logos

    made = company(owner)
    logos.store(made, ContentFile(PNG), source="upload")
    return made


def pictured(owner):
    """Somebody else's profile, with a picture stored for the same reason."""
    from postulo.accounts import avatars

    avatars.store(owner.profile, avatars.ProfilePicture.UPLOAD, ContentFile(PNG))
    return owner


def errand(owner):
    from postulo.core.models import Errand

    return Errand.objects.create(owner=owner, kind="export")


def import_errand(owner):
    from postulo.core.models import Errand, ErrandState

    return Errand.objects.create(
        owner=owner, kind="csv_import", state=ErrandState.DONE, outcome={"filename": "h.csv"}
    )


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


def offer(owner):
    from postulo.applications.models import Offer

    return Offer.objects.create(owner=owner, application=application(owner), currency="EUR")


def suggestion(owner):
    from postulo.applications.models import Suggestion

    return Suggestion.objects.create(
        owner=owner, application=application(owner), source="test", summary="Theirs"
    )


def tag(owner):
    from postulo.applications.models import Tag

    return Tag.objects.create(owner=owner, name=_named("Their tag"))


def contact(owner):
    from postulo.jobs.models import Contact

    return Contact.objects.create(owner=owner, company=company(owner), name="Their contact")


def industry(owner):
    from postulo.jobs.models import Industry

    return Industry.objects.create(owner=owner, name=_named("Their industry"))


def capture(owner):
    from postulo.jobs.models import Capture, CaptureStatus

    return Capture.objects.create(
        owner=owner,
        url="https://example.org/theirs",
        data={"title": "Their capture"},
        status=CaptureStatus.PENDING,
    )


def kept_page(owner):
    """Somebody else's capture, with the page it was read from kept beside it (#256).

    With real files behind it, so that a 404 is the lookup refusing and not a file that
    was never there. The rows are made directly: whether the other person had switched
    keeping on is not what is being asked.
    """
    from postulo.jobs.models import CapturedPage

    theirs = capture(owner)
    CapturedPage.objects.create(
        owner=owner,
        capture=theirs,
        source=ContentFile(b"\x1f\x8b theirs", name="theirs.txt.gz"),
        rendering=ContentFile(b"\x89PNG\r\n\x1a\n theirs", name="theirs.png"),
        rendering_type="image/png",
    )
    return theirs


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


def number(owner):
    """A telephone number on somebody else's *Your details* (#303)."""
    from postulo.core.models import PhoneNumber

    return PhoneNumber.objects.create(owner=owner, holder=owner.profile, number="+351912345678")


def web_link(owner):
    from postulo.core.models import WebLink

    return WebLink.objects.create(
        owner=owner, holder=owner.profile, kind=WebLink.Kind.SOCIAL, url="https://example.org"
    )


def messaging_handle(owner):
    from postulo.core.models import MessagingHandle

    return MessagingHandle.objects.create(
        owner=owner, holder=owner.profile, service="matrix", handle="@them:example.org"
    )


def postal_address(owner):
    from postulo.core.models import PostalAddress

    return PostalAddress.objects.create(
        owner=owner, holder=owner.profile, street="Their street 1", country="PT"
    )


def person_identifier(owner):
    from postulo.accounts.models import PersonIdentifier

    return PersonIdentifier.objects.create(profile=owner.profile, scheme="wikidata", value="Q95")


def pk_of(make: Callable, **extra) -> Callable:
    return lambda owner: {"pk": make(owner).pk, **extra}


#: For every pattern that names a record: how to make it for the other person, as the
#: keyword arguments `reverse()` needs.
FACTORIES: dict[str, Callable] = {
    # accounts: a picture is somebody's own, or an administrator's to see
    "accounts:avatar": lambda owner: {"pk": pictured(owner).pk},
    # a row taken off *Your details* at once, one address per kind of row (#303)
    "accounts:remove_number": pk_of(number),
    "accounts:remove_link": pk_of(web_link),
    "accounts:remove_messaging": pk_of(messaging_handle),
    "accounts:remove_address": pk_of(postal_address),
    "accounts:remove_identifier": pk_of(person_identifier),
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
    "applications:offer_create": pk_of(application),
    "applications:offer_update": pk_of(offer),
    "applications:offer_delete": pk_of(offer),
    "applications:reminder_complete": pk_of(reminder),
    "applications:reminder_delete": pk_of(reminder),
    "applications:reminder_later": pk_of(reminder),
    "applications:reminder_update": pk_of(reminder),
    "applications:suggestion_action": pk_of(suggestion, action="decline"),
    "applications:tag_delete": pk_of(tag),
    "applications:tag_update": pk_of(tag),
    # core: a piece of slow work, and the archive it produced (#247)
    "core:errand": pk_of(errand),
    "core:errand_state": pk_of(errand),
    "core:export_archive": pk_of(export_archive),
    "core:import_csv_done": pk_of(import_errand),
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
    # A format the registry holds, so that the 404 is the record's and not the format's.
    "documents:cv_download": pk_of(cv, format="txt"),
    "documents:cv_draft": pk_of(cv),
    "documents:cv_export": pk_of(cv),
    "documents:cv_preview": pk_of(cv),
    "documents:cv_text": pk_of(cv),
    "documents:cv_update": pk_of(cv),
    "documents:cv_item_delete": pk_of(cv_item),
    "documents:cv_item_move": pk_of(cv_item, direction="up"),
    "documents:cv_item_update": pk_of(cv_item),
    "documents:letter_delete": pk_of(letter),
    "documents:letter_detail": pk_of(letter),
    "documents:letter_draft": pk_of(letter),
    # A format the registry holds, so that the 404 is the record's and not the format's.
    "documents:letter_download": pk_of(letter, format="txt"),
    "documents:letter_preview": pk_of(letter),
    "documents:letter_update": pk_of(letter),
    "documents:rendered_archive": pk_of(rendered),
    "documents:rendered_compare": pk_of(rendered),
    "documents:rendered_download": pk_of(rendered),
    "documents:upload_archive": pk_of(upload),
    "documents:upload_delete": pk_of(upload),
    "documents:upload_download": pk_of(upload),
    "documents:upload_update": pk_of(upload),
    # jobs
    "jobs:capture_delete": pk_of(capture),
    "jobs:capture_discard": pk_of(capture),
    "jobs:capture_restore": pk_of(capture),
    # What a capture kept of its page (#256): the page that shows it, the two files, and
    # the two things that can be done to them.
    "jobs:capture_page": pk_of(kept_page),
    "jobs:capture_page_draw": pk_of(kept_page),
    "jobs:capture_page_forget": pk_of(kept_page),
    "jobs:capture_page_rendering": pk_of(kept_page),
    "jobs:capture_page_source": pk_of(kept_page),
    "jobs:capture_review": pk_of(capture),
    # Adding somebody else's capture to a listing's history (#270). The listing it names
    # is in the body; tests/security/test_listing_history.py asks for somebody else's there.
    "jobs:capture_bind": pk_of(capture),
    "jobs:company_cell": pk_of(company, column="name"),
    "jobs:company_delete": pk_of(company),
    "jobs:company_detail": pk_of(company),
    "jobs:company_logo": pk_of(logo_kept),
    "jobs:company_logo_action": pk_of(company, action="find"),
    # The record that would be kept, in the address. The one merged into it is named in
    # the query, and tests/test_merging.py asks for somebody else's there as well (#239).
    "jobs:company_merge": pk_of(company),
    "jobs:company_update": pk_of(company),
    "jobs:contact_delete": pk_of(contact),
    "jobs:contact_export": pk_of(contact),
    "jobs:contact_vcard": pk_of(contact),
    "jobs:company_vcard": pk_of(company),
    "jobs:company_contacts_vcard": pk_of(company),
    "jobs:contact_merge": pk_of(contact),
    "jobs:contact_update": pk_of(contact),
    "jobs:industry_delete": pk_of(industry),
    "jobs:industry_update": pk_of(industry),
    "jobs:posting_delete": pk_of(posting),
    "jobs:posting_detail": pk_of(posting),
    "jobs:posting_update": pk_of(posting),
    "listings:apply": pk_of(posting),
    "listings:cell": pk_of(posting, column="title"),
    "listings:discard": pk_of(posting),
    "listings:event_create": pk_of(posting),
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
    # Telling somebody else's CV what to print (#308).
    "postulo-api:patch_cv": ("patch", pk_of(cv), {"show_contact_details": False}),
    "postulo-api:get_letter": ("get", pk_of(letter), {}),
    "postulo-api:get_interview": ("get", pk_of(interview), {}),
    "postulo-api:change_interview": ("patch", pk_of(interview), {"notes": "x"}),
    "postulo-api:record_outcome": ("post", pk_of(interview), {"outcome": "done"}),
    "postulo-api:interview_calendar": ("get", pk_of(interview), {}),
    "postulo-api:get_offer": ("get", pk_of(offer), {}),
    "postulo-api:get_listing": ("get", pk_of(posting), {}),
    # An entry for somebody else's listing's history (#270).
    "postulo-api:add_listing_event": ("post", pk_of(posting), {"summary": "x"}),
    "postulo-api:apply": ("post", pk_of(posting), {}),
    "postulo-api:discard": ("post", pk_of(posting), {}),
    "postulo-api:restore": ("post", pk_of(posting), {}),
    "postulo-api:shortlist": ("post", pk_of(posting), {}),
    "postulo-api:complete_reminder": ("post", pk_of(reminder), {}),
    "postulo-api:change_reminder": ("patch", pk_of(reminder), {"summary": "x"}),
    "postulo-api:delete_reminder": ("delete", pk_of(reminder), {}),
    "postulo-api:document_download": ("get", pk_of(upload, source="upload"), {}),
    # The same route for a sent document, the most sensitive file there is (#422). A
    # second key, since a route is keyed by name; the part after `#` is not the name.
    "postulo-api:document_download#rendered": (
        "get",
        pk_of(rendered, source="rendered"),
        {},
    ),
    # Sending a rendering for somebody else's capture (#256). The lookup comes before
    # anything about the upload is looked at, so what the body is does not matter here.
    "postulo-api:attach_rendering": ("put", pk_of(kept_page), {}),
}

#: Somebody else's framework: allauth's pages are keyed by its own records and its own
#: tests, and the admin is checked as a whole in tests/security/test_admin_exposure.py.
NOT_OURS = ("admin:", "account_", "mfa_", "openid_connect_", "socialaccount_")


def route_of(key: str) -> str:
    """The route a key names: `name#variant` is the same route tried another way."""
    return key.split("#")[0]


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
    swept = set(FACTORIES) | {route_of(key) for key in API}
    undecided = sorted(names - swept - set(EXCUSED))
    assert not undecided, (
        "These addresses name a record and nobody has said what happens when it is "
        f"somebody else's. Add each to FACTORIES or API, or to EXCUSED with a reason: {undecided}"
    )


def test_nothing_is_listed_that_no_longer_exists():
    names = set(patterns_with_arguments())
    listed = set(FACTORIES) | {route_of(key) for key in API} | set(EXCUSED)
    stale = sorted(listed - names)
    assert not stale, f"listed here and gone from the resolver: {stale}"


def test_nothing_is_both_swept_and_excused():
    both = sorted((set(FACTORIES) | {route_of(key) for key in API}) & set(EXCUSED))
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
    url = reverse(route_of(name), kwargs=factory(other_user))

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


# ---------------------------------------------------------- ids in a body (#422)

#: A record that does not exist, for the answer the other person's has to equal.
MISSING = 2_000_000_000


def _stamp(days: int = 3) -> str:
    return (timezone.now() + dt.timedelta(days=days)).isoformat()


def contact_at(owner, at_company):
    """A contact of ``owner``'s filed under somebody else's company.

    The only one that tests the owner and not the company: a contact at the other person's
    own company is refused by the company check whatever the owner check does.
    """
    from postulo.jobs.models import Contact

    return Contact.objects.create(owner=owner, company=at_company, name="Their contact")


def _interview_post(user, other, theirs):
    mine = application(user)
    body = {"application_id": mine.pk, "starts_at": _stamp(), "kind": "phone", "remind": False}
    return (
        "postulo-api:add_interview",
        {},
        "post",
        {**body, "contact_ids": [theirs(mine, other)]},
        {**body, "contact_ids": [MISSING]},
    )


def _interview_patch(user, other, theirs):
    mine = interview(user)
    return (
        "postulo-api:change_interview",
        {"pk": mine.pk},
        "patch",
        {"contact_ids": [theirs(mine.application, other)]},
        {"contact_ids": [MISSING]},
    )


def _at_another_company(_application, other):
    return contact(other).pk


def _at_this_company(application_of_user, other):
    return contact_at(other, application_of_user.posting.company).pk


def _interview_application(user, other):
    base = {"starts_at": _stamp(), "kind": "phone", "remind": False}
    return (
        "postulo-api:add_interview",
        {},
        "post",
        {**base, "application_id": application(other).pk},
        {**base, "application_id": MISSING},
    )


def _reminder_post(user, other):
    base = {"summary": "x", "due_at": _stamp()}
    return (
        "postulo-api:add_reminder",
        {},
        "post",
        {**base, "application_id": application(other).pk},
        {**base, "application_id": MISSING},
    )


def _reminder_patch(user, other):
    mine = reminder(user)
    return (
        "postulo-api:change_reminder",
        {"pk": mine.pk},
        "patch",
        {"application_id": application(other).pk},
        {"application_id": MISSING},
    )


#: Where an id arrives in a body and not in an address. Each builds
#: `(route, kwargs, method, body_with_theirs, body_with_a_missing_one)`; the two answers
#: have to be the same, and nothing may have been made or changed by either.
BODIES: dict[str, Callable] = {
    "interview application_id (POST)": _interview_application,
    "interview contact_ids (POST), a contact at another company": (
        lambda user, other: _interview_post(user, other, _at_another_company)
    ),
    "interview contact_ids (POST), a contact filed under this company": (
        lambda user, other: _interview_post(user, other, _at_this_company)
    ),
    "interview contact_ids (PATCH), a contact at another company": (
        lambda user, other: _interview_patch(user, other, _at_another_company)
    ),
    "interview contact_ids (PATCH), a contact filed under this company": (
        lambda user, other: _interview_patch(user, other, _at_this_company)
    ),
    "reminder application_id (POST)": _reminder_post,
    "reminder application_id (PATCH)": _reminder_patch,
}


def _state() -> tuple:
    """Everything a body could have made or changed."""
    from postulo.applications.models import Interview, Reminder

    return (
        sorted(Interview.objects.values_list("pk", "application_id", "notes")),
        sorted(
            (i.pk, tuple(sorted(i.contacts.values_list("pk", flat=True))))
            for i in Interview.objects.all()
        ),
        sorted(Reminder.objects.values_list("pk", "application_id", "summary")),
    )


@pytest.mark.parametrize("label", sorted(BODIES))
def test_somebody_elses_id_in_a_body_is_answered_as_a_missing_one(client, user, other_user, label):
    from postulo.api.models import ApiToken

    _record, raw = ApiToken.issue(user, "Sweep", scopes=("read", "write"))
    route, kwargs, method, theirs, missing = BODIES[label](user, other_user)
    url = reverse(route, kwargs=kwargs)

    def ask(body):
        before = _state()
        response = getattr(client, method)(
            url,
            data=json.dumps(body),
            content_type="application/json",
            HTTP_AUTHORIZATION=f"Bearer {raw}",
        )
        assert _state() == before, f"{label}: {response.status_code} changed something"
        # Every answer carries the id of its own request; that is the one thing that differs.
        answer = json.loads(response.content)
        answer.pop("request_id", None)
        return response.status_code, answer

    for_a_missing_one = ask(missing)
    assert for_a_missing_one[0] in (404, 422), f"{label}: the control is not a refusal"
    assert ask(theirs) == for_a_missing_one, (
        f"{label}: somebody else's record is answered differently from one that does not exist"
    )


# ----------------------------------------------------------- choices in a form (#422)


def department(owner, at):
    from postulo.jobs.models import Department

    return Department.objects.create(owner=owner, company=at, name="A team")


def form_table(user, mine=None) -> dict:
    """Every `OwnerScopedModelForm`, and the ways to build it for ``user``, who has ``mine``."""
    from postulo.applications import forms as applications
    from postulo.documents import forms as documents
    from postulo.jobs import forms as jobs
    from postulo.resume import forms as resume

    mine = mine or application(user)
    return {
        applications.ApplicationForm: [{"user": user}, {"user": user, "instance": mine}],
        applications.ReminderForm: [{"user": user}],
        applications.InterviewForm: [
            {"user": user, "application": mine},
            {"user": user, "instance": interview(user)},
        ],
        applications.TagForm: [{"user": user}],
        applications.OfferForm: [{"user": user}],
        documents.CVForm: [{"user": user}],
        documents.CVItemForm: [{"user": user}],
        documents.CoverLetterForm: [{"user": user}],
        documents.UploadedDocumentForm: [{"user": user}],
        jobs.CompanyForm: [{"user": user}, {"user": user, "instance": mine.posting.company}],
        jobs.ContactForm: [{"user": user}],
        jobs.IndustryForm: [{"user": user, "instance": industry(user)}],
        jobs.JobPostingForm: [{"user": user}],
        jobs.ListingEventForm: [{"user": user}],
        resume.CertificationForm: [{"user": user}],
        resume.DrivingLicenceForm: [{"user": user}],
        resume.EducationForm: [{"user": user}],
        resume.ExperienceForm: [{"user": user}],
        resume.HonourForm: [{"user": user}],
        resume.LanguageSkillForm: [{"user": user}],
        resume.LinkForm: [{"user": user}],
        resume.ProjectForm: [{"user": user}],
        resume.PublicationForm: [{"user": user}],
        resume.SkillForm: [{"user": user}],
        resume.SkillGroupForm: [{"user": user}],
    }


def _subclasses(base):
    for sub in base.__subclasses__():
        yield sub
        yield from _subclasses(sub)


#: The related fields the sweep has to have met: a picker is taken away where there is
#: nothing to choose, and that must not turn the sweep into one over nothing.
OFFERED = {
    ("ApplicationForm", "contact"),
    ("ApplicationForm", "referred_by"),
    ("ApplicationForm", "through_agency"),
    ("ApplicationForm", "tags"),
    ("ApplicationForm", "department"),
    ("CompanyForm", "parent"),
    ("CompanyForm", "industries"),
    ("ContactForm", "company"),
    ("IndustryForm", "merge_into"),
    ("InterviewForm", "contacts"),
    ("JobPostingForm", "company"),
    ("ListingEventForm", "contact"),
    ("ListingEventForm", "document"),
    ("ReminderForm", "application"),
}


def test_every_owner_scoped_form_is_in_the_form_sweep(user):
    from postulo.jobs.forms import OwnerScopedModelForm

    listed = set(form_table(user))  # imports every app's forms
    # `ResumeItemForm` is a base with no model of its own, and so no fields to narrow.
    concrete = {c for c in _subclasses(OwnerScopedModelForm) if getattr(c._meta, "model", None)}
    unlisted = sorted(concrete - listed, key=lambda c: c.__name__)
    assert not unlisted, f"add these to `form_table()`: {unlisted}"


def test_no_form_offers_a_choice_that_is_somebody_elses(user, other_user):
    from django import forms as django_forms

    # The other person has records of every kind, some of them filed under this person's
    # own company: a form that narrowed by company and not by owner would offer those.
    mine = application(user)
    own_company = mine.posting.company
    department(user, own_company)
    contact(user)
    tag(user)
    industry(user)
    upload(user)
    theirs = application(other_user)
    department(other_user, own_company)
    contact_at(other_user, own_company)
    theirs.contact = contact_at(other_user, theirs.posting.company)
    theirs.save()
    for make in (tag, industry, upload, contact, company, posting):
        make(other_user)

    offered: set[tuple[str, str]] = set()
    for form_class, calls in form_table(user, mine).items():
        for kwargs in calls:
            form = form_class(**kwargs)
            for name, field in form.fields.items():
                if not isinstance(field, django_forms.ModelChoiceField):
                    continue
                model = field.queryset.model
                if not hasattr(model, "owner"):
                    continue
                offered.add((form_class.__name__, name))
                strangers = field.queryset.exclude(owner=user)
                assert not strangers.exists(), (
                    f"{form_class.__name__}.{name} offers {model.__name__} rows that belong "
                    f"to somebody else: {list(strangers)}"
                )

    never_met = OFFERED - offered
    assert not never_met, f"the sweep never met these fields, so it proved nothing: {never_met}"


# ------------------------------------------------------------------- publications (#687)


@pytest.mark.parametrize("name", ["item_update", "item_delete", "item_languages"])
def test_somebody_elses_publication_is_not_found(client, user, other_user, name):
    from postulo.resume.models import Publication

    theirs = Publication.objects.create(owner=other_user, title="Theirs", authors="Other, A.")
    client.force_login(user)
    url = reverse(f"resume:{name}", args=["publication", theirs.pk])

    assert client.get(url).status_code == 404
    assert client.post(url).status_code == 404
    move = reverse("resume:item_move", args=["publication", theirs.pk, "up"])
    assert client.post(move).status_code == 404
    assert Publication.objects.filter(pk=theirs.pk).exists()


def test_somebody_elses_publication_is_on_no_page_of_mine(client, user, other_user):
    from postulo.core import search
    from postulo.resume.models import Publication

    Publication.objects.create(owner=other_user, title="Zebra studies", authors="Other, A.")
    client.force_login(user)

    assert "Zebra studies" not in client.get(reverse("resume:overview")).content.decode()
    assert "Zebra studies" not in client.get(reverse("resume:preview")).content.decode()
    assert search.search(user, "Zebra") == []


# ---------------------------------------------------------- driving licences (#691)


@pytest.mark.parametrize("name", ["item_update", "item_delete", "item_languages"])
def test_somebody_elses_driving_licence_is_not_found(client, user, other_user, name):
    from postulo.resume.models import DrivingLicence

    theirs = DrivingLicence.objects.create(owner=other_user, country="PT", categories=["B"])
    client.force_login(user)
    url = reverse(f"resume:{name}", args=["driving-licence", theirs.pk])

    assert client.get(url).status_code == 404
    assert client.post(url).status_code == 404
    move = reverse("resume:item_move", args=["driving-licence", theirs.pk, "up"])
    assert client.post(move).status_code == 404
    assert DrivingLicence.objects.filter(pk=theirs.pk).exists()


def test_somebody_elses_driving_licence_is_on_no_page_of_mine(client, user, other_user):
    from postulo.resume.models import DrivingLicence

    DrivingLicence.objects.create(owner=other_user, country="NZ", categories=["DE"])
    client.force_login(user)

    assert "DE (New Zealand)" not in client.get(reverse("resume:overview")).content.decode()
    assert "DE (New Zealand)" not in client.get(reverse("resume:preview")).content.decode()


# --------------------------------------------------- honours and awards (#693)


@pytest.mark.parametrize("name", ["item_update", "item_delete", "item_languages"])
def test_somebody_elses_honour_is_not_found(client, user, other_user, name):
    from postulo.resume.models import Honour

    theirs = Honour.objects.create(owner=other_user, title="Theirs")
    client.force_login(user)
    url = reverse(f"resume:{name}", args=["honour", theirs.pk])

    assert client.get(url).status_code == 404
    assert client.post(url).status_code == 404
    move = reverse("resume:item_move", args=["honour", theirs.pk, "up"])
    assert client.post(move).status_code == 404
    assert Honour.objects.filter(pk=theirs.pk).exists()


def test_somebody_elses_honour_is_on_no_page_of_mine(client, user, other_user):
    from postulo.core import search
    from postulo.resume.models import Honour

    Honour.objects.create(owner=other_user, title="Zebra prize", awarded_by="Quokka Society")
    client.force_login(user)

    assert "Zebra prize" not in client.get(reverse("resume:overview")).content.decode()
    assert "Zebra prize" not in client.get(reverse("resume:preview")).content.decode()
    assert search.search(user, "Zebra") == []
    assert search.search(user, "Quokka") == []
