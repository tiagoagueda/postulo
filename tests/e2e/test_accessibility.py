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
    from django.utils import timezone

    from postulo.applications.models import Application, InterviewKind, Reminder, Status
    from postulo.applications.services import change_status, schedule_interview
    from postulo.jobs.models import Company, Contact, Industry, JobPosting

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
    applicant.is_staff = True
    applicant.is_superuser = True
    applicant.save()
    return {"application": application, "company": company}


def sign_in(page: Page, base: str) -> None:
    page.goto(f"{base}/accounts/login/")
    page.locator("input[name=login]").fill(EMAIL)
    page.locator("input[name=password]").fill(PASSWORD)
    page.locator("form").get_by_role("button", name="Sign In", exact=True).click()
    expect(page).to_have_url(f"{base}/")


@pytest.mark.parametrize("scheme", ["light", "dark"])
def test_the_entrance_pages_have_no_violations(live_server, page: Page, axe_source, db, scheme):
    page.emulate_media(color_scheme=scheme)
    base = live_server.url
    failures = []
    for path in ("/accounts/login/", "/accounts/password/reset/", "/"):
        page.goto(f"{base}{path}")
        found = violations_on(page, axe_source)
        if found:
            failures.append(describe(f"{path} ({scheme})", found))
    assert not failures, "\n\n".join(failures)


@pytest.mark.parametrize("scheme", ["light", "dark"])
def test_every_signed_in_page_has_no_violations(
    live_server, page: Page, axe_source, furnished, scheme
):
    page.emulate_media(color_scheme=scheme)
    base = live_server.url
    sign_in(page, base)
    a = furnished["application"]
    c = furnished["company"]
    paths = [
        "/",
        "/listings/",
        "/listings/new/",
        "/applications/",
        "/applications/?company=aperture&sort=applied",
        "/applications/board/",
        f"/applications/{a.pk}/",
        f"/applications/{a.pk}/edit/",
        f"/applications/{a.pk}/interviews/new/",
        "/applications/interviews/",
        "/applications/reminders/",
        "/applications/reminders/new/",
        "/applications/insights/",
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
        "/settings/language/",
        "/settings/account/",
        "/settings/connections/",
        "/settings/connections/add/",
        "/capture-tokens/",
        "/export/",
        "/import/",
        "/accounts/delete/",
        "/server/overview/",
        "/server/people/",
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
    ]
    failures = []
    for path in paths:
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


#: Which URL patterns the lists above reach, so `tests/test_page_coverage.py` can tell
#: what is covered from what nobody has decided about. Names rather than addresses,
#: because an address changes and a name is what the rest of the code refers to.
VISITED_URL_NAMES: tuple[str, ...] = (
    "core:home",
    "core:search",
    "core:export",
    "core:import_csv",
    "listings:list",
    "listings:create",
    "listings:apply",
    "applications:list",
    "applications:board",
    "applications:detail",
    "applications:create",
    "applications:update",
    "applications:delete",
    "applications:insights",
    "applications:interview_list",
    "applications:interview_create",
    "applications:interview_update",
    "applications:interview_outcome",
    "applications:reminder_list",
    "applications:reminder_create",
    "applications:suggestion_list",
    "applications:tag_list",
    "applications:tag_create",
    "applications:tag_update",
    "applications:tag_delete",
    "jobs:company_list",
    "jobs:company_create",
    "jobs:company_detail",
    "jobs:company_update",
    "jobs:company_delete",
    "jobs:contact_create",
    "jobs:contact_update",
    "jobs:contact_delete",
    "jobs:industry_list",
    "jobs:industry_create",
    "jobs:industry_update",
    "jobs:industry_delete",
    "jobs:capture_list",
    "jobs:capture_create",
    "jobs:capture_review",
    "jobs:posting_create",
    "jobs:posting_detail",
    "jobs:posting_update",
    "jobs:posting_delete",
    "documents:cv_list",
    "documents:cv_create",
    "documents:cv_detail",
    "documents:cv_update",
    "documents:cv_delete",
    "documents:cv_preview",
    "documents:cv_add_items",
    "documents:cv_item_update",
    "documents:cv_item_delete",
    "documents:letter_list",
    "documents:letter_create",
    "documents:letter_detail",
    "documents:letter_update",
    "documents:letter_delete",
    "documents:letter_preview",
    "documents:upload_list",
    "documents:upload_create",
    "documents:upload_update",
    "documents:upload_delete",
    "documents:rendered_list",
    "documents:application_documents",
    "documents:send",
    "resume:overview",
    "resume:item_create",
    "resume:item_update",
    "resume:item_delete",
    "resume:preview",
    "resume:europass_import",
    "accounts:profile",
    "accounts:delete",
    "accounts:invite_list",
    "accounts:invite_create",
    "settings:appearance",
    "settings:locale",
    "settings:account",
    "connections:list",
    "connections:pick",
    "connections:create",
    "connections:edit",
    "connections:delete",
    "api:token_list",
    "server:overview",
    "server:people",
    "server:person_username",
    "server:person_delete",
    "server:signin",
    "server:email",
    "server:plugins",
    "server:capture",
    "server:defaults",
    "server:logs",
)
