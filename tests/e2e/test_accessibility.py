"""Every page, checked against WCAG 2.2 A and AA by axe-core, in a real browser.

Usable by everyone is one of Postulo's stated commitments, and what is not checked drifts.
axe-core is injected into each page the suite visits and asked for violations at levels A
and AA; any violation fails the test and is printed with the rule, the impact, the offending
markup and the help page, so the fix is a minute away rather than a search.

axe covers what a machine can check — names, roles, contrast, structure, focus order
markers. It does not replace using the pages with a keyboard and a screen reader, which
the accessibility programme (#41) schedules by hand.

**What this suite is, and is not.** It is a regression net for the things a machine can
measure. It is not evidence that a page is good: it reported nothing at all, in both
themes, on a sign-in page that turned out to be rendered with no styling whatsoever —
correct markup, associated labels, ample contrast, and nothing a person could use. Looking
at the pages remains the other half of the job.

The list below was written by hand, which meant a page added later was simply not on it and
nothing said so. `tests/test_page_coverage.py` now walks the URL resolver and fails unless
every pattern is either named here or excused in writing, so the list can no longer drift
behind the application.
"""

import datetime as dt
from pathlib import Path

import pytest
from playwright.sync_api import Page, expect

from .conftest import EMAIL, PASSWORD

pytestmark = pytest.mark.e2e

AXE = Path(__file__).resolve().parents[2] / "node_modules" / "axe-core" / "axe.min.js"
EUROPASS = Path(__file__).resolve().parents[1] / "data" / "europass.xml"
TAGS = ["wcag2a", "wcag2aa", "wcag21a", "wcag21aa", "wcag22aa", "best-practice"]

#: Rules deliberately not enforced, with the reason. Keep this list short and honest.
IGNORED_RULES = {
    # The theme switch and the account menu are landmarks-free by design; axe's "region"
    # best practice wants every element in a landmark, and the header is one.
}


@pytest.fixture(scope="session")
def axe_source() -> str:
    if not AXE.is_file():
        pytest.skip("axe-core is not installed; run npm ci")
    return AXE.read_text(encoding="utf-8")


def violations_on(page: Page, axe_source: str) -> list[dict]:
    # Through the DevTools protocol rather than a `<script>` element: the page's content
    # security policy allows no inline script, and since #232 the suite runs under it.
    session = page.context.new_cdp_session(page)
    session.send("Runtime.evaluate", {"expression": axe_source})
    session.detach()
    results = page.evaluate(
        """async (tags) => {
            const results = await axe.run(document, {
                runOnly: { type: "tag", values: tags },
                resultTypes: ["violations"],
            });
            return results.violations;
        }""",
        TAGS,
    )
    return [v for v in results if v["id"] not in IGNORED_RULES]


def describe(url: str, violations: list[dict]) -> str:
    lines = [f"{url}: {len(violations)} accessibility violation(s)"]
    for violation in violations:
        head = f"  [{violation['impact']}] {violation['id']}: {violation['help']}"
        lines.append(f"{head} ({violation['helpUrl']})")
        for node in violation["nodes"][:3]:
            target = ", ".join(node.get("target", []))
            lines.append(f"      {target}")
            lines.append(f"      {node['html'][:160]}")
            if node.get("failureSummary"):
                lines.append("      " + node["failureSummary"].replace("\n", " ")[:220])
    return "\n".join(lines)


@pytest.fixture
def furnished(applicant):
    """Enough of everything for every page to have content, not only empty states."""
    from django.core.files.base import ContentFile
    from django.utils import timezone

    from postulo.applications.models import (
        Application,
        EventKind,
        InterviewKind,
        Reminder,
        Status,
    )
    from postulo.applications.services import change_status, schedule_interview
    from postulo.jobs.models import Company, Contact, Industry, JobPosting
    from postulo.resume.models import Experience

    company = Company.objects.create(owner=applicant, name="Aperture Science", location="Cambridge")
    company.industries.set(Industry.named(applicant, ["Research"]))
    Contact.objects.create(owner=applicant, company=company, name="Cave Johnson", role="CEO")
    JobPosting.objects.create(owner=applicant, company=company, title="Undecided Role")
    posting = JobPosting.objects.create(owner=applicant, company=company, title="Test Engineer")
    application = Application.objects.create(owner=applicant, posting=posting, status=Status.DRAFT)
    change_status(application, Status.APPLIED, occurred_at=timezone.now() - dt.timedelta(days=30))
    reminder = Reminder.objects.create(
        owner=applicant, application=application, summary="Chase", due_at=timezone.now()
    )
    schedule_interview(
        application, kind=InterviewKind.VIDEO, starts_at=timezone.now() + dt.timedelta(days=2)
    )
    experience = Experience.objects.create(
        owner=applicant,
        organisation="Weyland-Yutani",
        role="Backend engineer",
        location="Lisbon",
        start_date=dt.date(2019, 1, 1),
        summary="Kept the services up.",
        highlights="Cut deploy time.",
    )
    # Everything below exists so the walk can open the *object* pages -- a CV's own page, a
    # letter's, an upload's, a tag's. Until #167 the walk visited the lists and never a row,
    # so axe and the reflow check had never seen any of them, and the coverage test counted
    # them as checked because a hand-written tuple said so.
    from django.contrib.contenttypes.models import ContentType

    from postulo.applications.models import Tag
    from postulo.documents.models import CV, CoverLetter, CVItem, UploadedDocument
    from postulo.jobs.models import Capture
    from postulo.plugins.models import Connection

    cv = CV.objects.create(owner=applicant, name="Backend engineer")
    # One entry, because an empty CV's page is the state that was already being checked and
    # the row with its buttons is the thing that runs off a phone.
    CVItem.objects.create(
        owner=applicant,
        cv=cv,
        content_type=ContentType.objects.get_for_model(Experience),
        object_id=experience.pk,
    )
    letter = CoverLetter.objects.create(
        owner=applicant,
        name="To Aperture",
        body="Dear Aperture, I would like to help.",
    )
    upload = UploadedDocument.objects.create(
        owner=applicant,
        title="Reference",
        file=ContentFile(b"a reference", name="reference.txt"),
    )
    # The same CV sent twice with a rewrite in between, so that the comparison has lines of
    # all three kinds on it -- added, removed, and what did not change -- and so that the
    # CV's page and the application's documents each have a version with one before it,
    # which is the state their *Compare with previous* link exists in (#236). Written
    # straight to the table: what is being looked at is the pages, not the renderer.
    from postulo.documents.models import RenderedDocument

    sent_as = "Test Engineer at Aperture Science"
    words = (
        "Alex Morgan\nDjango developer\n\nExperience\n\n{role}\nWeyland-Yutani · Lisbon\n"
        "January 2019 – present\nKept the services up.\n- Cut deploy time.\n{last}"
    )
    render = None
    for days_ago, role, last in (
        (14, "Backend engineer", "- Wrote the runbooks."),
        (1, "Senior backend engineer", "- Ran the on-call rota."),
    ):
        render = RenderedDocument(
            owner=applicant,
            title="Alex Morgan — CV",
            kind="cv",
            source=cv,
            application=application,
            sent_to=sent_as,
            language="en-GB",
            source_text="<html></html>",
            plain_text=words.format(role=role, last=last),
            checksum=f"{days_ago:064d}",
            rendered_at=timezone.now() - dt.timedelta(days=days_ago),
        )
        render.file.save("alex-morgan-cv.pdf", ContentFile(b"%PDF-1.7 sent"), save=True)
    # Coloured and with an icon, so that axe reads a tag as it is drawn rather than as
    # the grey default -- the tones are where a contrast failure would hide (#285).
    tag = Tag.objects.create(
        owner=applicant, name="Remote", slug="remote", colour="violet", icon="home"
    )
    # A capture waiting for review, which is the state its page exists for.
    # Its employer read where the person's own corrections showed it on this site before, and
    # the place that did it remembered: so the review screen is walked with its mark on a
    # field and the line above the form, and Settings -> Capture with a site listed (#267).
    capture = Capture.objects.create(
        owner=applicant,
        url="https://jobs.example.org/42",
        source_name="schema.org",
        data={"title": "Research Engineer", "company_name": "Black Mesa"},
        learning={
            "hints": [{"field": "company_name", "place": {"id": "employer"}, "outcome": "used"}]
        },
    )
    from postulo.jobs.models import FieldHint

    FieldHint.objects.create(
        owner=applicant, host="jobs.example.org", field="company_name", place={"id": "employer"}
    )
    # And what it kept of the page it was read from (#256): the source and a picture, so
    # the page that shows them is walked with both on it -- the box the picture scrolls
    # in, the source as text, and the buttons under each -- and the two settings pages
    # are walked with their switches on rather than locked.
    _keep_the_page_of(capture)
    # What arrived about the listing before and beside its application (#270): a message
    # from somebody recorded, a file somebody forwarded, and the same advert captured again
    # from another board -- so both pages that draw a listing's history, the listing's own
    # and the application's, are walked with every sort of entry on them. And a listing at
    # the waiting capture's address, so its review screen offers to add it to a history.
    from postulo.jobs.history import bind_capture, record_listing_event

    record_listing_event(
        posting,
        kind="message",
        summary="The counsellor sent the advert",
        body="Worth a look.\nThey close on Friday.",
        contact=company.contacts.first(),
    )
    record_listing_event(posting, kind="document", summary="The full description", artefact=upload)
    bind_capture(
        Capture.objects.create(
            owner=applicant,
            url="https://boards.example/test-engineer",
            source_name="schema.org",
            data={"title": "Test Engineer", "company_name": "Aperture Science"},
        ),
        posting,
    )
    JobPosting.objects.filter(owner=applicant, title="Undecided Role").update(
        url="https://jobs.example.org/42"
    )
    interview = application.interviews.first()
    # An offer with every field, so the comparison page and the card have something to
    # draw in both themes (#237).
    from postulo.applications.services import record_offer

    offer = record_offer(
        application,
        base_amount=65000,
        currency="EUR",
        period="year",
        variable_pay="10% on target",
        benefits="Pension",
        location="Two days in the office",
        holidays=25,
        answer_by=timezone.localdate() + dt.timedelta(days=7),
    )
    # A connection row, so its own pages exist. Nothing is configured behind it and nothing
    # here contacts anything: the pages being checked are a form and a confirmation.
    connection = Connection.objects.create(
        owner=applicant, kind="notifier", plugin="email", label="My email"
    )
    # A suggestion a plugin filed that nothing has matched to an application: the state in
    # which the accept form carries a select, which #167 measured at 45 pixels over the edge
    # of a phone in English and the walk had never reached. Filed through the service, as a
    # plugin would, with the body, context and dates a mailbox reader actually sends.
    from postulo.applications import suggestions

    suggestion, _ = suggestions.suggest(
        applicant,
        source="imap",
        external_id="<interview-42@blackmesa.test>",
        kind=EventKind.EMAIL_RECEIVED,
        summary="Interview invitation from Black Mesa",
        body="We would like to invite you to an interview next week.",
        suggested_status=Status.INTERVIEWING,
        proposed_dates=(dt.date(2026, 9, 21), dt.date(2026, 9, 22)),
        context={"From": "jobs@blackmesa.test", "Subject": "Interview invitation"},
    )

    # A finished piece of slow work, so the page a press lands on is walked with something
    # on it rather than as an empty spinner (#247).
    from postulo.core.models import Errand, ErrandState

    errand = Errand.objects.create(
        owner=applicant,
        kind="export",
        state=ErrandState.DONE,
        outcome={"message": "Your data is ready to download.", "url": "/settings/export/"},
        finished_at=timezone.now(),
    )

    # A second record of the same company and of the same person, so the notice that says
    # so is on the pages it is drawn on and the page that merges them has something to
    # show: what moves, what the kept record takes, and where the two differ (#239).
    from postulo.jobs.models import CompanyIdentifier, Department

    duplicate = Company.objects.create(
        owner=applicant,
        name="Aperture Science Ltd",
        location="Cleveland",
        website="https://aperture.example",
        notes="The one from the fair.",
    )
    CompanyIdentifier.objects.create(
        owner=applicant, company=duplicate, scheme="wikidata", value="Q95"
    )
    Department.objects.create(owner=applicant, company=duplicate, name="Testing")
    twin = Contact.objects.create(
        owner=applicant,
        company=duplicate,
        name="Cave Johnson",
        role="Founder",
        email="cave@aperture.example",
    )
    # And an application that ended, with where it had got to and why, that somebody
    # referred the person to and that went through an agency: the state in which its page
    # says all four, and the two widgets that count them have something to count (#239).
    agency = Company.objects.create(owner=applicant, name="Hays")
    ended = Application.objects.create(
        owner=applicant,
        posting=JobPosting.objects.create(
            owner=applicant, company=duplicate, title="Enrichment Centre Supervisor"
        ),
        status=Status.DRAFT,
        referred_by=twin,
        through_agency=agency,
    )
    change_status(ended, Status.APPLIED, occurred_at=timezone.now() - dt.timedelta(days=20))
    change_status(ended, Status.INTERVIEWING, occurred_at=timezone.now() - dt.timedelta(days=9))
    change_status(ended, Status.REJECTED, note="Under the range they advertised.", end_reason="pay")

    applicant.is_staff = True
    applicant.is_superuser = True
    applicant.save()
    return {
        "application": application,
        "ended": ended,
        "duplicate": duplicate,
        "twin": twin,
        "errand": errand,
        "company": company,
        "applicant": applicant,
        "experience": experience,
        "contact": company.contacts.first(),
        "industry": company.industries.first(),
        "posting": posting,
        "cv": cv,
        "cv_item": cv.items.first(),
        "letter": letter,
        "upload": upload,
        # The later of the two versions: the one with something to be compared with.
        "render": render,
        "tag": tag,
        "capture": capture,
        "interview": interview,
        "reminder": reminder,
        "offer": offer,
        "connection": connection,
        "suggestion": suggestion,
    }


def sign_in(page: Page, base: str) -> None:
    page.goto(f"{base}/accounts/login/")
    page.locator("input[name=login]").fill(EMAIL)
    page.locator("input[name=password]").fill(PASSWORD)
    page.locator("form").get_by_role("button", name="Sign In", exact=True).click()
    expect(page).to_have_url(f"{base}/")


#: A picture a browser will actually draw: one pixel, which the page stretches to the
#: width of its column. What is being looked at is the box around it, not the picture.
_ONE_PIXEL = (
    "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mNkYAAAAAYAAjCB0C8AAAAASUVORK5CYII="
)


def _keep_the_page_of(capture) -> None:
    """Switch keeping on for the instance and the person, and keep a page beside a capture."""
    import base64
    import io

    from postulo.core import site
    from postulo.core.models import SiteSettings
    from postulo.jobs import pages

    SiteSettings.objects.update_or_create(
        pk=1, defaults={"capture_keep_source": True, "capture_keep_rendering": True}
    )
    site.forget_current()
    profile = capture.owner.profile
    profile.keep_page_source = True
    profile.keep_page_rendering = True
    profile.save(update_fields=["keep_page_source", "keep_page_rendering"])

    picture = base64.b64decode(_ONE_PIXEL)
    pages.keep_source(
        capture,
        "<!doctype html><html><head><title>Research Engineer</title></head>"
        "<body><h1>Research Engineer</h1><p>Black Mesa is hiring.</p>"
        "<script>document.title = 'never run'</script></body></html>",
    )
    pages.attach_rendering(
        capture, io.BytesIO(picture), content_type="image/png", length=len(picture)
    )


def signed_in_paths(a, c, me, entry=None, recovery_link: str = "", things=None) -> list[str]:
    """Every address the signed-in walk visits, given an application, a company, an account.

    Hoisted out of the test that grew it so other suites can walk the same list rather than
    keep a second one that drifts. `tests/test_page_coverage.py` holds it to the resolver.

    ``entry`` is one career entry, for the pages that need one to exist.

    ``recovery_link`` stands in for a real token, so `walked_url_names` can build the list
    for the resolver without a database behind it. The walk itself passes nothing and gets
    a live one.

    ``things`` is the rest of what `furnished` made -- a CV, a letter, an upload, a tag and
    the others -- for the *object* pages. Until #167 the walk opened every list and no row,
    so axe and the reflow check had never seen a CV's own page or a letter's. A plain object
    with `pk` stands in when only resolution is wanted, so nothing here may touch a field.
    """
    it = things or {}
    cv = it.get("cv", a)
    cv_item = it.get("cv_item", a)
    letter = it.get("letter", a)
    upload = it.get("upload", a)
    render = it.get("render", a)
    tag = it.get("tag", a)
    capture = it.get("capture", a)
    contact = it.get("contact", a)
    industry = it.get("industry", a)
    posting = it.get("posting", a)
    interview = it.get("interview", a)
    reminder = it.get("reminder", a)
    offer = it.get("offer", a)
    connection = it.get("connection", a)
    errand = it.get("errand", a)
    ended = it.get("ended", a)
    duplicate = it.get("duplicate", c)
    twin = it.get("twin", contact)
    return [
        "/",
        "/listings/",
        # Sorted and on a tab that is not the usual one, so the walk sees the header row,
        # the bulk bar and the state column with rows under them (#160).
        "/listings/?state=all&sort=title",
        "/listings/new/",
        "/applications/",
        "/applications/?company=aperture&sort=applied",
        "/applications/?view=board",
        # Folded to one column, the others strips (#315).
        "/applications/?view=board&status=applied",
        "/applications/report/",
        "/applications/report/?period=weeks&weeks=8",
        f"/applications/{a.pk}/",
        f"/applications/{a.pk}/edit/",
        f"/applications/{a.pk}/interviews/new/",
        "/applications/interviews/",
        "/applications/reminders/new/",
        # The form as a day on the calendar opens it, with that day in it (#316).
        "/applications/reminders/new/?on=2026-10-03&next=/applications/calendar/",
        "/applications/calendar/",
        "/applications/calendar/?view=week",
        "/applications/calendar/?view=day",
        "/applications/calendar/?view=agenda",
        # What the reminders page was: the agenda narrowed to them (#316).
        "/applications/calendar/?view=agenda&kinds=reminder",
        "/applications/new/",
        "/applications/tags/",
        "/jobs/companies/",
        "/jobs/companies/map/",
        "/jobs/companies/new/",
        f"/jobs/companies/{c.pk}/",
        f"/jobs/companies/{c.pk}/edit/",
        "/jobs/industries/",
        "/jobs/captures/",
        # The page a button that sends work off lands on, finished (#247).
        f"/working/{errand.pk}/",
        "/jobs/postings/new/",
        "/documents/cvs/",
        "/documents/cvs/new/",
        "/documents/letters/",
        "/documents/files/",
        "/documents/files/new/",
        "/documents/sent/",
        f"/documents/applications/{a.pk}/documents/",
        "/career/",
        "/career/experience/new/",
        "/career/preview/",
        "/search/?q=engineer",
        "/accounts/profile/",
        "/accounts/invitations/",
        "/accounts/2fa/",
        "/settings/",
        "/settings/appearance/",
        "/settings/accessibility/",
        "/?arrange=1",
        "/settings/language/",
        "/settings/account/",
        # What a person's captures keep of the page they were read from (#256).
        "/settings/capture/",
        "/settings/connections/",
        "/settings/plugins/",
        "/settings/connections/add/",
        "/capture-tokens/",
        "/export/",
        "/import/",
        "/accounts/delete/",
        "/server/overview/",
        "/server/people/",
        f"/server/people/{me.pk}/plugins/",
        "/server/sign-in/",
        "/server/email/",
        "/server/plugins/",
        # The identifier registry's settings as a page: what its dialog on Plugins falls
        # back to with scripts off, a box of JSON and what a scheme can say (#311).
        "/server/plugins/identifiers/",
        "/server/capture/",
        "/server/defaults/",
        "/server/data-protection/",
        "/server/record-of-processing/",
        # The gallery: every component on one page, which is the point of walking it
        # here -- a component seen only inside a feature is one axe never sees whole (#292).
        "/server/design/",
        "/accounts/password/change/",
        # The pages allauth renders, which now sit inside Postulo's own layout. They
        # passed here throughout while being entirely unstyled, which is the limit of
        # what a machine can tell you; tests/test_allauth_layout.py checks the rest.
        "/accounts/email/",
        "/accounts/2fa/totp/activate/",
        "/accounts/social/connections/",
        "/accounts/logout/",
        # Everything below was reachable and unchecked until the coverage test asked.
        "/applications/suggestions/",
        f"/applications/{a.pk}/delete/",
        "/applications/tags/new/",
        "/documents/letters/new/",
        "/documents/cvs/new/",
        "/jobs/contacts/new/",
        "/jobs/industries/new/",
        "/jobs/captures/new/",
        "/jobs/postings/new/",
        "/career/education/new/",
        # The skill box, with the list htmx fills as somebody types beside it (#266).
        "/career/skill/new/",
        "/career/preview/",
        "/career/import/",
        # One person's own record as a file: what is in it, the link to it, and the box
        # that reads one back (#181). The half that comes after a file has a test below.
        "/career/file/",
        "/server/logs/",
        "/accounts/invitations/new/",
        f"/server/people/{me.pk}/recovery/",
        # The two halves of a recovery link, walked in order: the first sets the ticket in
        # the session and redirects, the second is the form it lands on. Only fetched, never
        # submitted, so nobody's password changes half way through the walk (#103).
        f"/accounts/recover/{recovery_link or _a_recovery_link_for(me)}/",
        "/accounts/recover/",
        # The object pages, none of which anything had opened before #167. Delete pages are
        # confirmation forms: fetched and never submitted, like the recovery link above.
        f"/documents/cvs/{cv.pk}/",
        f"/documents/cvs/{cv.pk}/edit/",
        f"/documents/cvs/{cv.pk}/preview/",
        # *Copy as plain text*, which is a page so that it works with no script, and what
        # changed between two versions of one document (#236). The drafts and the files
        # beside them are downloads, and a download has no page to look at.
        f"/documents/cvs/{cv.pk}/text/",
        f"/documents/sent/{render.pk}/compare/",
        f"/documents/cvs/{cv.pk}/delete/",
        f"/documents/cv-entries/{cv_item.pk}/edit/",
        f"/documents/cv-entries/{cv_item.pk}/delete/",
        f"/documents/letters/{letter.pk}/",
        f"/documents/letters/{letter.pk}/edit/",
        f"/documents/letters/{letter.pk}/preview/",
        f"/documents/letters/{letter.pk}/delete/",
        f"/documents/files/{upload.pk}/edit/",
        f"/documents/files/{upload.pk}/delete/",
        f"/documents/applications/{a.pk}/send/",
        f"/applications/interviews/{interview.pk}/edit/",
        f"/applications/tags/{tag.pk}/edit/",
        f"/applications/tags/{tag.pk}/delete/",
        f"/applications/reminders/{reminder.pk}/edit/",
        f"/applications/reminders/{reminder.pk}/delete/",
        "/applications/offers/",
        f"/applications/{a.pk}/offers/new/",
        f"/applications/offers/{offer.pk}/edit/",
        f"/applications/offers/{offer.pk}/delete/",
        f"/jobs/captures/{capture.pk}/review/",
        # What the capture kept of its page, and the question asked before it is thrown
        # away: fetched and never submitted, like every other confirmation here (#256).
        f"/jobs/captures/{capture.pk}/page/",
        f"/jobs/captures/{capture.pk}/page/forget/",
        f"/jobs/captures/{capture.pk}/page/forget/?what=rendering",
        f"/jobs/companies/{c.pk}/delete/",
        # An application that ended, saying where it had got to and why, who referred the
        # person and through which agency (#239).
        f"/applications/{ended.pk}/",
        f"/applications/{ended.pk}/edit/",
        # Merging two records of one company, and of one person: the page that asks which
        # one, and the page that says what would move. Fetched and never submitted, like
        # every confirmation on this walk (#239).
        f"/jobs/companies/{c.pk}/merge/",
        f"/jobs/companies/{c.pk}/merge/?with={duplicate.pk}",
        f"/jobs/contacts/{contact.pk}/merge/",
        f"/jobs/contacts/{contact.pk}/merge/?with={twin.pk}",
        f"/jobs/contacts/{contact.pk}/edit/",
        f"/jobs/contacts/{contact.pk}/delete/",
        f"/jobs/industries/{industry.pk}/edit/",
        f"/jobs/industries/{industry.pk}/delete/",
        f"/jobs/postings/{posting.pk}/",
        f"/jobs/postings/{posting.pk}/edit/",
        f"/jobs/postings/{posting.pk}/delete/",
        f"/listings/{posting.pk}/apply/",
        f"/server/people/{me.pk}/username/",
        f"/server/people/{me.pk}/delete/",
        # A connection Postulo can offer without one being configured: the kind and the
        # plugin name are in the path, so the form exists whether or not anything is set up.
        "/settings/connections/add/notifier/email/",
        # The one made for a machine (#240): a password field, a help line each, and the
        # three events a human notifier keeps off.
        "/settings/connections/add/notifier/webhook/",
        f"/settings/connections/{connection.pk}/",
        f"/settings/connections/{connection.pk}/delete/",
        *(
            [
                f"/career/experience/{entry.pk}/edit/",
                f"/career/experience/{entry.pk}/delete/",
                # Both halves: the list of languages, and the form for one of them.
                f"/career/experience/{entry.pk}/languages/",
                f"/career/experience/{entry.pk}/languages/?language=fr-FR",
            ]
            if entry is not None
            else []
        ),
    ]


#: The pages somebody sees before signing in. Hoisted beside the signed-in walk so that
#: `walked_url_names` reads the same list the test visits, rather than a copy of it.
SIGNED_OUT_PATHS: tuple[str, ...] = (
    "/accounts/login/",
    "/accounts/password/reset/",
    "/accounts/login/code/",
    "/",
)


def _offer_signing_in_by_code() -> None:
    from postulo.core.models import SiteSettings

    SiteSettings.objects.update_or_create(
        pk=1, defaults={"email_sign_in": True, "mail_failures": 0}
    )


def _a_recovery_link_for(person) -> str:
    """A live token, so the form the link leads to can be looked at like any other page."""
    from postulo.accounts import recovery

    _link, token = recovery.issue(person, by=person)
    return token


@pytest.mark.parametrize("scheme", ["light", "dark"])
def test_the_entrance_pages_have_no_violations(live_server, page: Page, axe_source, db, scheme):
    page.emulate_media(color_scheme=scheme)
    base = live_server.url
    # Signing in by code is an administrator's decision and is off by default (#153), so the
    # page only exists once somebody has said yes. Switched on here so the walk can look at
    # it like any other entrance page.
    _offer_signing_in_by_code()
    failures = []
    for path in SIGNED_OUT_PATHS:
        page.goto(f"{base}{path}")
        found = violations_on(page, axe_source)
        if found:
            failures.append(describe(f"{path} ({scheme})", found))
    assert not failures, "\n\n".join(failures)


#: Paths the walk visits but axe does not judge, and why. **Not a general escape hatch** --
#: #167 says violations found on newly-walked pages get fixed rather than excluded, and the
#: other thirty were. These two are different in kind.
#:
#: A preview is not a page. `CVPreviewView` returns *"the CV as HTML, exactly as the PDF
#: renderer will see it"*, so what axe is reading is a print document: the same markup
#: WeasyPrint turns into a PDF. It reports `landmark-one-main`, `region` and, for a letter,
#: `page-has-heading-one` -- rules about the shape of an application page. Satisfying them
#: means putting a `<main>` into a CV theme, which would change every PDF Postulo produces
#: in order to answer a question nobody asked of a printed page.
#:
#: They stay in the walk, so the reflow and target-size checks still read them: a CV preview
#: running off the side of a phone would be a real fault, and this exempts one tool rather
#: than the page.
AXE_EXEMPT_SUFFIXES: tuple[str, ...] = ("/preview/",)


def axe_should_read(path: str) -> bool:
    return not path.endswith(AXE_EXEMPT_SUFFIXES)


@pytest.mark.parametrize("scheme", ["light", "dark"])
def test_every_signed_in_page_has_no_violations(
    live_server, page: Page, axe_source, furnished, scheme
):
    page.emulate_media(color_scheme=scheme)
    base = live_server.url
    sign_in(page, base)
    a = furnished["application"]
    c = furnished["company"]
    me = furnished["applicant"]
    paths = signed_in_paths(a, c, me, furnished["experience"], things=furnished)
    failures = []
    for path in paths:
        if not axe_should_read(path):
            continue
        page.goto(f"{base}{path}")
        if "reauthenticate" in page.url:
            page.locator("input[name=password]").fill(PASSWORD)
            page.locator("form").get_by_role("button").first.click()
            page.goto(f"{base}{path}")
        found = violations_on(page, axe_source)
        if found:
            failures.append(describe(f"{path} ({scheme})", found))
    assert not failures, "\n\n".join(failures)


@pytest.mark.parametrize("scheme", ["light", "dark"])
def test_the_company_form_in_two_columns_has_no_violations(
    live_server, page: Page, axe_source, furnished, scheme
):
    """The walk above runs at the default window, 1280 pixels, where the company form is one
    column. From `2xl` it is two, the details beside the identifiers (#210), and that state
    exists only in a wide window, so it is looked at in one."""
    page.emulate_media(color_scheme=scheme)
    page.set_viewport_size({"width": 2560, "height": 1200})
    base = live_server.url
    sign_in(page, base)
    failures = []
    for path in ("/jobs/companies/new/", f"/jobs/companies/{furnished['company'].pk}/edit/"):
        page.goto(f"{base}{path}")
        details = page.locator("main form .card").first.bounding_box()
        identifiers = page.locator("main form [data-identifiers]").bounding_box()
        assert details["y"] == identifiers["y"], f"{path} is not in two columns at 2560"
        found = violations_on(page, axe_source)
        if found:
            failures.append(describe(f"{path} at 2560 ({scheme})", found))
    assert not failures, "\n\n".join(failures)


@pytest.mark.parametrize("scheme", ["light", "dark"])
def test_the_contact_and_capture_forms_in_two_columns_have_no_violations(
    live_server, page: Page, axe_source, furnished, scheme
):
    """The same for the two forms that followed the company form (#320): a contact's
    details beside its numbers and links, and the capture form beside what it does. Each
    is two columns only in a wide window, which the walk never opens."""
    page.emulate_media(color_scheme=scheme)
    page.set_viewport_size({"width": 2560, "height": 1200})
    base = live_server.url
    sign_in(page, base)
    pairs = {
        "/jobs/contacts/new/": ("main form .card", "main form [data-contact-rows]"),
        f"/jobs/contacts/{furnished['contact'].pk}/edit/": (
            "main form .card",
            "main form [data-contact-rows]",
        ),
        "/jobs/captures/new/": ("main form[method=post]", "main [data-capture-help]"),
    }
    failures = []
    for path, (first, second) in pairs.items():
        page.goto(f"{base}{path}")
        one = page.locator(first).first.bounding_box()
        other = page.locator(second).bounding_box()
        assert one["y"] == other["y"], f"{path} is not in two columns at 2560"
        found = violations_on(page, axe_source)
        if found:
            failures.append(describe(f"{path} at 2560 ({scheme})", found))
    assert not failures, "\n\n".join(failures)


@pytest.mark.parametrize("scheme", ["light", "dark"])
def test_the_europass_review_page_has_no_violations(
    live_server, page: Page, axe_source, applicant, scheme
):
    """The half of the import page a GET cannot reach.

    The review state only exists after a file has been read, so walking addresses never
    sees it. It is also the half with the content: counts, five CEFR levels per language,
    and the two buttons that decide whether any of it is written.
    """
    page.emulate_media(color_scheme=scheme)
    base = live_server.url
    sign_in(page, base)

    page.goto(f"{base}/career/import/")
    page.locator("input[type=file]").set_input_files(str(EUROPASS))
    page.get_by_role("button", name="Read it").click()

    expect(page.get_by_role("heading", name="What is in the file")).to_be_visible()
    found = violations_on(page, axe_source)
    assert not found, describe(f"/career/import/ review ({scheme})", found)


def a_candidate_file(path: Path) -> Path:
    """A file with one of everything the review can say about a row, written to ``path``.

    Read into `furnished`'s account, which holds a name and one role: that role is *already
    in your record*, the one beside it *will be added*, the name is *kept*, a role with no
    title *cannot be added*, and a second copy of the new one is *in the file twice*.
    """
    import json

    from postulo.core import export

    new = {
        "id": 2,
        "organisation": "Aperture Science",
        "role": "Test subject",
        "start_date": "2016-05-01",
        "end_date": "2018-12-31",
    }
    document = {
        "postulo": {
            "candidate_format": export.CANDIDATE_FORMAT,
            "version": "0.5.0",
            "exported_at": "2026-09-28T10:00:00+00:00",
        },
        "account": {
            "first_name": "Alexandra",
            "profile": {
                "headline": "Backend engineer",
                "phone_numbers": [{"kind": "mobile", "number": "+351912345678"}],
                "postal_addresses": [
                    {"kind": "home", "street": "Rua do Exemplo 1", "country": "PT"}
                ],
                "web_links": [{"kind": "website", "url": "https://alex.example.org"}],
            },
            "identifiers": [{"scheme": "orcid", "value": "0000-0002-1825-0097"}],
        },
        "resume": {
            "experience": [
                {
                    "id": 1,
                    "organisation": "Weyland-Yutani",
                    "role": "Backend engineer",
                    "start_date": "2019-01-01",
                    "summary": "Something else.",
                },
                new,
                {**new, "id": 3},
                {"id": 4, "organisation": "Black Mesa", "start_date": "soon"},
            ],
            "skill_groups": [{"id": 1, "name": "Languages"}],
            "skills": [{"id": 1, "name": "Python", "group_id": 1}],
            "languages": [{"id": 1, "name": "português", "proficiency": "native"}],
            "translations": [
                {
                    "section": "experience",
                    "ref": 2,
                    "language": "fr-FR",
                    "field": "role",
                    "text": "Sujet de test",
                }
            ],
        },
        "companies": [],
    }
    path.write_text(json.dumps(document), encoding="utf-8")
    return path


@pytest.mark.parametrize("scheme", ["light", "dark"])
def test_the_candidate_file_review_has_no_violations(
    live_server, page: Page, axe_source, furnished, tmp_path, scheme
):
    """The half of the page a GET cannot reach, as the Europass review is (#181).

    It is the half with the content: a table for every part of the file, a row for each
    thing in it, and beside each what adding the file would do. Read with one of every
    outcome on it, because a colour or a weight that says which is which is what axe is
    there to look at.
    """
    page.emulate_media(color_scheme=scheme)
    base = live_server.url
    sign_in(page, base)

    page.goto(f"{base}/career/file/")
    page.locator("input[type=file]").set_input_files(
        str(a_candidate_file(tmp_path / "candidate.json"))
    )
    page.get_by_role("button", name="Read the file").click()

    expect(page.get_by_role("heading", name="What is in the file")).to_be_visible()
    for words in (
        "Will be added",
        "Already in your record",
        "Yours is kept",
        "In the file twice",
        "Cannot be added",
    ):
        expect(page.get_by_text(words).first).to_be_visible()
    found = violations_on(page, axe_source)
    assert not found, describe(f"/career/file/ review ({scheme})", found)


@pytest.mark.parametrize("scheme", ["light", "dark"])
def test_a_dashboard_with_every_widget_on_it_has_no_violations(
    live_server, page: Page, axe_source, furnished, scheme
):
    """Every widget at once, which no walk of addresses can reach.

    The default dashboard shows seven of them. The rest — the funnel bars, the two tables,
    the figures — used to be on the Insights page and were checked there; when that page
    became widgets, nothing was looking at them any more. This looks at all of them, in
    both themes, on one page.
    """
    from postulo.core import widgets

    profile = furnished["applicant"].profile
    profile.dashboard_widgets = [w.key for w in widgets.all_widgets()]
    profile.save(update_fields=["dashboard_widgets"])

    page.emulate_media(color_scheme=scheme)
    base = live_server.url
    sign_in(page, base)
    page.goto(f"{base}/")

    for widget in widgets.all_widgets():
        expect(page.locator(f'[data-widget="{widget.key}"]')).to_have_count(1)
    found = violations_on(page, axe_source)
    assert not found, describe(f"/ with every widget ({scheme})", found)


@pytest.mark.parametrize("scheme", ["light", "dark"])
def test_the_dashboard_can_be_arranged_from_the_keyboard(
    live_server, page: Page, axe_source, furnished, scheme
):
    """Arranging is a form, on purpose: it works with the keyboard and with scripts off."""
    page.emulate_media(color_scheme=scheme)
    base = live_server.url
    sign_in(page, base)
    page.goto(f"{base}/?arrange=1")

    page.get_by_role("button", name="Take Gone quiet off").click()
    expect(page.get_by_role("button", name="Add Gone quiet")).to_be_visible()

    page.goto(f"{base}/")
    expect(page.locator('[data-widget="gone_quiet"]')).to_have_count(0)


@pytest.mark.parametrize("scheme", ["light", "dark"])
def test_the_error_pages_have_no_violations(live_server, page: Page, db, axe_source, scheme):
    """The pages somebody is on when they are already lost, and the last to be looked at.

    404 is reached by asking for something that is not there. 500 is rendered directly:
    provoking a real one would need a view that raises, and what is being checked is the
    template rather than the machinery that reaches it.
    """
    page.emulate_media(color_scheme=scheme)
    failures = []

    page.goto(f"{live_server.url}/no-such-page-exists-here/")
    found = violations_on(page, axe_source)
    if found:
        failures.append(describe(f"404 ({scheme})", found))

    from django.template.loader import render_to_string

    page.set_content(render_to_string("500.html"))
    found = violations_on(page, axe_source)
    if found:
        failures.append(describe(f"500 ({scheme})", found))

    assert not failures, "\n\n".join(failures)


def test_the_skip_link_and_keyboard_reach_the_main_content(live_server, page: Page, furnished):
    base = live_server.url
    sign_in(page, base)
    page.goto(f"{base}/applications/")
    page.keyboard.press("Tab")
    skip = page.get_by_role("link", name="Skip to content")
    expect(skip).to_be_focused()
    skip.press("Enter")
    expect(page.locator("main")).to_be_focused()
    # Every menu opens and closes from the keyboard.
    page.locator("summary", has_text="Columns").focus()
    page.keyboard.press("Enter")
    expect(page.locator("details[data-menu][open]")).to_have_count(1)
    page.keyboard.press("Escape")
    expect(page.locator("details[data-menu][open]")).to_have_count(0)


#: Which URL patterns the walk above reaches, **resolved from the walk itself** rather than
#: written down beside it. `tests/test_page_coverage.py` compares this with every pattern
#: the resolver knows, and until #167 the comparison was against a hand-written tuple that
#: had drifted: it claimed 104 names and the walk reached 69, so thirty-five pages were
#: counted as checked and never opened -- every CV page and every letter page past the
#: list among them.
#:
#: A claim cannot get ahead of the walk now, because it is the same list read twice.


class _Stand_in:
    """Something with a `pk`, for building a path the resolver only has to match.

    The paths are resolved, never fetched, so nothing behind them has to exist. Using real
    rows would mean a database for a test whose whole point is that it runs in milliseconds
    without one.
    """

    pk = 1


def walked_url_names() -> frozenset[str]:
    """Every URL pattern the signed-in and signed-out walks actually reach.

    Resolution rather than string matching, so a path with a query string, a redirect
    target or an unusual converter is counted as what it really is.
    """
    from django.urls import Resolver404, resolve

    stand_in = _Stand_in()
    paths = [
        *signed_in_paths(
            stand_in,
            stand_in,
            stand_in,
            stand_in,
            recovery_link="x" * 32,
            # Every object page gets the same stand-in: resolution reads the path, never
            # the row, so one object with a `pk` answers for all of them.
            things=dict.fromkeys(
                (
                    "cv",
                    "cv_item",
                    "letter",
                    "upload",
                    "render",
                    "tag",
                    "capture",
                    "contact",
                    "industry",
                    "posting",
                    "interview",
                    "reminder",
                    "offer",
                    "connection",
                ),
                stand_in,
            ),
        ),
        *SIGNED_OUT_PATHS,
    ]
    found = set()
    for path in paths:
        try:
            found.add(resolve(path.split("?", 1)[0]).view_name)
        except Resolver404:
            # A path the walk visits that resolves to nothing is a broken walk, and
            # `test_every_path_the_walk_visits_resolves` is what says so. Skipped here so
            # that failure is reported once, in the test written for it.
            continue
    return frozenset(found)


#: Kept as a name for the readers that already import it.
VISITED_URL_NAMES: frozenset[str] = walked_url_names()
