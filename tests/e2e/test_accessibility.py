"""Every page, checked against WCAG 2.2 A and AA by axe-core, in a real browser.

Usable by everyone is one of Postulo's stated commitments, and what is not checked drifts.
axe-core is injected into each page the suite visits and asked for violations at levels A
and AA; any violation fails the test and is printed with the rule, the impact, the offending
markup and the help page, so the fix is a minute away rather than a search.

axe covers what a machine can check — names, roles, contrast, structure, focus order
markers. It does not replace using the pages with a keyboard and a screen reader, which
the accessibility programme (#41) schedules by hand.
"""

import datetime as dt
from pathlib import Path

import pytest
from playwright.sync_api import Page, expect

from .conftest import EMAIL, PASSWORD

pytestmark = pytest.mark.e2e

AXE = Path(__file__).resolve().parents[2] / "node_modules" / "axe-core" / "axe.min.js"
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
