"""What assistive technology is told (#276): context, purpose, and the text a hover reveals.

axe can see none of this. Every control here has a name and every link has text; what was
missing was which row an action belongs to, what a field is for, and what a tooltip said.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest
from django.urls import reverse

pytestmark = pytest.mark.django_db

TEMPLATES = Path(__file__).resolve().parents[1] / "src" / "postulo" / "templates"


def test_row_actions_say_which_row(client, user):
    """A links list read "Edit, Edit, Edit, Delete, Delete, Delete"; the bulk checkbox had
    said "Select Aperture Science" since #134, and the actions now do the same."""
    from postulo.applications.models import Tag

    Tag.objects.create(owner=user, name="Marketing")
    client.force_login(user)
    html = client.get(reverse("applications:tag_list")).content.decode()
    assert 'Edit<span class="sr-only">: <bdi>Marketing</bdi></span>' in html
    assert 'Delete<span class="sr-only">: <bdi>Marketing</bdi></span>' in html


def test_the_notes_column_shows_two_lines_rather_than_a_tooltip():
    """The column is chosen per person, so the row's template is read rather than a page."""
    row = (TEMPLATES / "jobs" / "partials" / "company_row.html").read_text(encoding="utf-8")
    notes = row[row.index("column.key == 'notes'") : row.index("column.key == 'last_activity'")]
    cell = notes[notes.index("<td") : notes.index(">", notes.index("<td")) + 1]
    assert "line-clamp-2" in notes
    assert "title=" not in cell and "truncate" not in cell


def test_the_invitation_link_is_whole_and_marked_for_a_copy_button(client, user):
    """On the page that made it, since #232; the list no longer has it to show."""
    user.is_staff = True
    user.save()
    client.force_login(user)
    response = client.post(
        reverse("accounts:invite_create"), {"email": "new@example.org", "note": ""}
    )
    html = response.content.decode()
    tag = re.search(r"<code[^>]*data-copy-source[^>]*>", html)
    assert tag, "the invitation link is not marked for a copy button"
    assert "truncate" not in tag.group(0) and "wrap-anywhere" in tag.group(0)
    assert 'data-copy-label="Copy link"' in tag.group(0)


def test_the_fields_about_the_person_say_what_they_are_for(client, user):
    """SC 1.3.5: the given and family name, the username and the parts of the person's own
    postal address carry the autocomplete token that names them."""
    # The sign-up page shows no form while registration is closed, which it is here, so the
    # form is rendered on its own; the page draws it through `<c-field>`, widget attributes
    # and all, when it is open.
    from postulo.accounts.forms import SignupForm

    form = SignupForm()
    assert 'autocomplete="given-name"' in str(form["first_name"])
    assert 'autocomplete="family-name"' in str(form["last_name"])

    client.force_login(user)
    account = client.get(reverse("settings:account")).content.decode()
    assert 'autocomplete="username"' in account

    profile = client.get(reverse("accounts:profile")).content.decode()
    for token in (
        "street-address",
        "postal-code",
        "address-level2",
        "address-level1",
        "country-name",
    ):
        assert f'autocomplete="{token}"' in profile, token


def test_a_company_s_fields_are_about_somebody_else(client, user):
    client.force_login(user)
    html = client.get(reverse("jobs:company_create")).content.decode()
    assert "given-name" not in html and "street-address" not in html


def test_every_link_that_opens_a_new_tab_says_so():
    """Four did not, while the rest said *external* or spelled it out."""
    silent = []
    for path in sorted(TEMPLATES.rglob("*.html")):
        text = path.read_text(encoding="utf-8")
        for match in re.finditer(r'<(a|form|button)\b[^>]*target="_blank"[^>]*>', text):
            tag = match.group(0)
            closing = "</form>" if match.group(1) == "form" else "</a>"
            after = text[match.end() : text.find(closing, match.end())]
            if "external" in tag or "(opens in a new tab)" in after:
                continue
            silent.append(f"{path.name}: {tag[:70]}")
    assert not silent, silent


def test_nothing_on_a_page_is_uppercased_by_the_stylesheet():
    """Twelve-pixel uppercase letter-spaced text is the hardest on the page for low-vision
    and dyslexic readers, and VoiceOver can spell it out letter by letter."""
    found = [
        path.name
        for path in sorted(TEMPLATES.rglob("*.html"))
        if "uppercase" in path.read_text(encoding="utf-8")
        and "themes" not in path.parts
        and path.name != "report_print.html"
    ]
    assert not found, found


def test_a_relative_date_carries_the_absolute_one(client, user):
    from django.utils import timezone

    from postulo.applications.models import Application, Status
    from postulo.jobs.models import Company, JobPosting

    company = Company.objects.create(owner=user, name="Aperture Science")
    posting = JobPosting.objects.create(owner=user, company=company, title="Tester")
    Application.objects.create(
        owner=user, posting=posting, status=Status.APPLIED, applied_at=timezone.now()
    )
    client.force_login(user)
    html = client.get(reverse("applications:list") + "?view=board").content.decode()
    assert re.search(r'<time class="text-xs text-ink-400" datetime="\d{4}-\d\d-\d\d">', html)
