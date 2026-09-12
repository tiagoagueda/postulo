"""Does every `aria-describedby` point at something that exists? (#114)

A control that says *I am invalid, and I am described by these two elements* is worse than
one that says nothing when the elements are not there: a screen reader gives the invalid
state and then has nothing to read, while the error sits on the page in red two lines below,
reachable to eyes and to nothing else. That was every form Postulo drew itself — Django
renders `aria-describedby="<id>_helptext <id>_error"` and was right to, and Postulo's field
partial rendered those two paragraphs without the ids. Four references on a company form
with one empty field, four of them dangling.

**axe cannot catch this.** It does not report a dangling `aria-describedby`, and it has no
way to know that a `<p>` below an input was meant to describe it. Resolving the ids against
the document is four lines, and it is the check worth keeping — which is what this is.

The forms are submitted empty on purpose. A valid form has no errors, so the half of the
bug that only appears once something has gone wrong would never be reached by walking pages.
"""

from __future__ import annotations

import pytest
from playwright.sync_api import Page

from .conftest import PASSWORD
from .test_accessibility import furnished, sign_in, signed_in_paths  # noqa: F401

pytestmark = pytest.mark.e2e

DANGLING = """() => {
  const bad = [];
  for (const el of document.querySelectorAll('[aria-describedby]')) {
    for (const id of el.getAttribute('aria-describedby').split(/\\s+/).filter(Boolean)) {
      if (!document.getElementById(id)) {
        bad.push({
          id,
          what: el.outerHTML.replace(/\\s+/g, ' ').slice(0, 110),
        });
      }
    }
  }
  // Two elements sharing an id is the other way this breaks: the reference resolves, and
  // resolves to whichever came first.
  const seen = new Map();
  const duplicated = [];
  for (const el of document.querySelectorAll('[id]')) {
    const count = (seen.get(el.id) || 0) + 1;
    seen.set(el.id, count);
    if (count === 2) duplicated.push(el.id);
  }
  return {dangling: bad, duplicated};
}"""

#: Forms worth submitting empty, and the field whose emptiness the server will object to.
#: One per shape Postulo draws: the ordinary partial, a formset, and a fieldset of choices.
FORMS_TO_BREAK = [
    ("/jobs/companies/new/", "form"),
    ("/applications/new/", "form"),
    ("/jobs/industries/new/", "form"),
    ("/documents/cvs/new/", "form"),
    ("/applications/tags/new/", "form"),
]


def submit(page: Page, selector: str) -> None:
    """Press the form's own Save, not the first submit button on the page.

    These pages carry more than one form -- a filter row, a column chooser inside a closed
    `<details>` -- so `.first` finds a button nobody can see and Playwright waits for it to
    become visible until it gives up.
    """
    page.locator(f"main {selector} button[type=submit]:visible").first.click()
    page.wait_for_load_state()


def dangling_on(page: Page) -> list[str]:
    result = page.evaluate(DANGLING)
    problems = [f"{row['id']} referenced by {row['what']}" for row in result["dangling"]]
    problems += [f"id {name!r} is on more than one element" for name in result["duplicated"]]
    return problems


def test_no_page_references_an_element_that_is_not_there(live_server, page: Page, furnished):  # noqa: F811
    base = live_server.url
    sign_in(page, base)

    failures: dict[str, str] = {}
    for path in signed_in_paths(
        furnished["application"],
        furnished["company"],
        furnished["applicant"],
        furnished["experience"],
        things=furnished,
    ):
        page.goto(f"{base}{path}")
        if "reauthenticate" in page.url:
            page.locator("input[name=password]").fill(PASSWORD)
            page.locator("form").get_by_role("button").first.click()
            page.goto(f"{base}{path}")
        for problem in dangling_on(page):
            failures.setdefault(problem, path)

    report = "\n".join(f"  {where}\n    {what}" for what, where in sorted(failures.items()))
    assert not failures, f"{len(failures)} broken reference(s):\n{report}"


@pytest.mark.parametrize("path,selector", FORMS_TO_BREAK)
def test_a_form_that_comes_back_with_errors_still_points_at_them(
    live_server,
    page: Page,
    furnished,  # noqa: F811
    path,
    selector,
):
    """The half a walk of pages cannot reach: what a form looks like after it is refused.

    `aria-describedby` only names an error element once there is an error, so a page that
    has never been submitted has nothing to dangle. Submitting empty is the cheapest way to
    make every required field object at once.
    """
    base = live_server.url
    sign_in(page, base)
    page.goto(f"{base}{path}")

    submit(page, selector)

    problems = dangling_on(page)
    assert not problems, f"{path} after an empty submit:\n" + "\n".join(f"  {p}" for p in problems)


@pytest.mark.parametrize("path,selector", [FORMS_TO_BREAK[0]])
def test_the_error_is_actually_reachable_from_the_field(
    live_server,
    page: Page,
    furnished,  # noqa: F811
    path,
    selector,
):
    """Not merely that the id resolves: that what it resolves to is the error text.

    A reference that lands on an empty element passes the check above and helps nobody.
    """
    base = live_server.url
    sign_in(page, base)
    page.goto(f"{base}{path}")
    submit(page, selector)

    described = page.evaluate("""() => {
      const field = document.querySelector('[aria-invalid="true"]');
      if (!field) return null;
      const ids = (field.getAttribute('aria-describedby') || '').split(/\\s+/).filter(Boolean);
      return ids.map(id => (document.getElementById(id) || {}).textContent || '').join(' ').trim();
    }""")

    assert described, "the invalid field describes nothing"
    assert len(described) > 5, f"the description is empty or near enough: {described!r}"
