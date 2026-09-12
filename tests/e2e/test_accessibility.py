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
    page.add_script_tag(content=axe_source)
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
    Reminder.objects.create(
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
    tag = Tag.objects.create(owner=applicant, name="Remote", slug="remote")
    # A capture waiting for review, which is the state its page exists for.
    capture = Capture.objects.create(
        owner=applicant,
        url="https://jobs.example.org/42",
        source_name="schema.org",
        data={"title": "Research Engineer", "company_name": "Black Mesa"},
    )
    interview = application.interviews.first()
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

    applicant.is_staff = True
    applicant.is_superuser = True
    applicant.save()
    return {
        "application": application,
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
        "tag": tag,
        "capture": capture,
        "interview": interview,
        "connection": connection,
        "suggestion": suggestion,
    }


def sign_in(page: Page, base: str) -> None:
    page.goto(f"{base}/accounts/login/")
    page.locator("input[name=login]").fill(EMAIL)
    page.locator("input[name=password]").fill(PASSWORD)
    page.locator("form").get_by_role("button", name="Sign In", exact=True).click()
    expect(page).to_have_url(f"{base}/")


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
    tag = it.get("tag", a)
    capture = it.get("capture", a)
    contact = it.get("contact", a)
    industry = it.get("industry", a)
    posting = it.get("posting", a)
    interview = it.get("interview", a)
    connection = it.get("connection", a)
    return [
        "/",
        "/listings/",
        "/listings/new/",
        "/applications/",
        "/applications/?company=aperture&sort=applied",
        "/applications/board/",
        "/applications/report/",
        "/applications/report/?period=weeks&weeks=8",
        f"/applications/{a.pk}/",
        f"/applications/{a.pk}/edit/",
        f"/applications/{a.pk}/interviews/new/",
        "/applications/interviews/",
        "/applications/reminders/",
        "/applications/reminders/new/",
        "/applications/new/",
        "/applications/tags/",
        "/jobs/companies/",
        "/jobs/companies/new/",
        f"/jobs/companies/{c.pk}/",
        f"/jobs/companies/{c.pk}/edit/",
        "/jobs/industries/",
        "/jobs/captures/",
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
        "/settings/dashboard/",
        "/settings/language/",
        "/settings/account/",
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
        "/server/capture/",
        "/server/defaults/",
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
        "/career/preview/",
        "/career/import/",
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
        f"/jobs/captures/{capture.pk}/review/",
        f"/jobs/companies/{c.pk}/delete/",
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
        f"/settings/connections/{connection.pk}/",
        f"/settings/connections/{connection.pk}/delete/",
        *(
            [
                f"/career/experience/{entry.pk}/edit/",
                f"/career/experience/{entry.pk}/delete/",
                # Both halves: the list of languages, and the form for one of them.
                f"/career/experience/{entry.pk}/languages/",
                f"/career/experience/{entry.pk}/languages/?language=fr-fr",
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
    page.goto(f"{base}/settings/dashboard/")

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
                    "tag",
                    "capture",
                    "contact",
                    "industry",
                    "posting",
                    "interview",
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
