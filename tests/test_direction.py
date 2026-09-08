"""Right-to-left: the direction Postulo declares, and the layout that has to follow it.

Postulo offers no right-to-left language yet — #70 brings the first. That is precisely why
this exists: the hook in ``base.html`` was written years before anything exercised it, and
what the interface does under ``dir="rtl"`` was unknown rather than known-good. The
pseudo-locale here is a real language tag activated on a profile; no catalogue is needed,
because the words are not what is being tested. English text under ``dir="rtl"`` is easier
to read while checking that the boxes flipped, not harder.
"""

import re
from pathlib import Path

import pytest
from django.urls import reverse

from postulo.core import languages

pytestmark = pytest.mark.django_db

TEMPLATES = Path(__file__).resolve().parents[1] / "src" / "postulo" / "templates"


@pytest.fixture
def company(user):
    """A Latin name, which is the whole point: it stays Latin in an Arabic line."""
    from postulo.jobs.models import Company

    return Company.objects.create(owner=user, name="Aperture Science")


def reading_right_to_left(user, code="ar"):
    """Put this account into a right-to-left language. No catalogue, on purpose."""
    profile = user.profile
    profile.language = code
    profile.save(update_fields=["language"])
    return profile


# ------------------------------------------------------------ which languages


@pytest.mark.parametrize("code", ["ar", "he", "fa", "ur", "ps", "yi", "ckb", "dv"])
def test_the_languages_written_right_to_left(code):
    assert languages.is_rtl(code)
    assert languages.direction(code) == "rtl"


@pytest.mark.parametrize("code", ["en-gb", "pt-pt", "el", "mt", "ga", "hu", "bg"])
def test_everything_postulo_speaks_today_is_left_to_right(code):
    assert not languages.is_rtl(code)
    assert languages.direction(code) == "ltr"


def test_a_region_does_not_change_the_direction():
    """Direction belongs to the script. No region of Arabic is written the other way."""
    assert languages.is_rtl("ar-EG")
    assert languages.is_rtl("ar_SA")
    assert not languages.is_rtl("en-AE")


def test_a_language_nobody_has_heard_of_gets_a_direction_anyway():
    """Always one of the two: dir="" is not the same as no attribute in every engine."""
    assert languages.direction("") == "ltr"
    assert languages.direction("zz-quux") == "ltr"


def test_every_language_offered_today_has_a_direction():
    for code, _name in languages.LANGUAGES:
        assert languages.direction(code) in {"ltr", "rtl"}


# ------------------------------------------------------------------- the page


def test_the_page_declares_the_direction_of_the_language_it_is_in(client, user):
    reading_right_to_left(user)
    client.force_login(user)

    html = client.get(reverse("core:home")).content.decode()

    assert 'dir="rtl"' in html
    assert 'lang="ar"' in html


def test_a_left_to_right_language_still_says_so(client, user):
    reading_right_to_left(user, code="pt-pt")
    client.force_login(user)

    html = client.get(reverse("core:home")).content.decode()

    assert 'dir="ltr"' in html


def test_the_direction_comes_from_postulo_rather_than_from_django(client, user):
    """Django's LANGUAGES_BIDI knows the languages Django ships. #43 goes past them.

    One list that both the interface and a rendered document read is one place to add a
    language, and one answer when they are asked the same question.
    """
    reading_right_to_left(user, code="ckb")  # Sorani: Django has no LANG_INFO for it
    client.force_login(user)

    assert 'dir="rtl"' in client.get(reverse("core:home")).content.decode()


# -------------------------------------------------------------- the documents


def test_a_document_is_laid_out_for_the_language_it_is_written_in(user):
    """Not for whoever is looking at it: the PDF goes to somebody else entirely."""
    from postulo.documents.models import CV
    from postulo.documents.rendering import document_direction, render_cv_html

    reading_right_to_left(user)
    cv = CV.objects.create(owner=user, name="Main", language="en-gb")

    assert document_direction(cv) == "ltr"
    assert 'dir="ltr"' in render_cv_html(cv)


def test_a_document_written_right_to_left_says_so(user):
    from postulo.documents.models import CV
    from postulo.documents.rendering import render_cv_html

    cv = CV.objects.create(owner=user, name="Main", language="ar")

    html = render_cv_html(cv)
    assert 'lang="ar"' in html and 'dir="rtl"' in html


def test_a_letter_carries_the_direction_too(user):
    from postulo.documents.models import CoverLetter
    from postulo.documents.rendering import render_letter_html

    letter = CoverLetter.objects.create(
        owner=user, name="Speculative", subject="Hello", body="Text", language="he"
    )

    assert 'dir="rtl"' in render_letter_html(letter)


def test_a_document_with_no_language_of_its_own_follows_its_owner(user):
    from postulo.documents.models import CV
    from postulo.documents.rendering import document_direction

    reading_right_to_left(user)
    cv = CV.objects.create(owner=user, name="Main", language="")

    assert document_direction(cv) == "rtl"


# ------------------------------------------------------ what is isolated, and why


def test_latin_text_inside_a_line_is_isolated(client, user, company):
    """A Latin name in an Arabic line needs isolating or the punctuation jumps.

    The company name sits beside a separator on the dashboard's Coming up widget. Without
    <bdi>, the bidirectional algorithm resolves that separator against the paragraph and
    throws it to the other end of the line.
    """
    import datetime as dt

    from django.utils import timezone

    from postulo.applications.models import Application, InterviewKind, Status
    from postulo.applications.services import schedule_interview
    from postulo.jobs.models import JobPosting

    posting = JobPosting.objects.create(owner=user, company=company, title="Engineer")
    application = Application.objects.create(owner=user, posting=posting, status=Status.APPLIED)
    schedule_interview(
        application, kind=InterviewKind.VIDEO, starts_at=timezone.now() + dt.timedelta(days=2)
    )
    profile = reading_right_to_left(user)
    profile.dashboard_widgets = ["upcoming_interviews"]
    profile.save(update_fields=["dashboard_widgets"])
    client.force_login(user)

    html = client.get(reverse("core:home")).content.decode()

    assert "<bdi>Aperture Science</bdi>" in html


def test_machine_text_is_isolated_by_the_stylesheet_rather_than_by_markup():
    """Forty <code> spans, one rule. A package name is never in the reader's script."""
    css = (
        Path(__file__).resolve().parents[1] / "src" / "postulo" / "static" / "css" / "app.css"
    ).read_text(encoding="utf-8")

    assert re.search(r"code[^{]*\{[^}]*unicode-bidi:\s*isolate", css)


def test_the_skip_link_and_the_menus_are_anchored_logically():
    """The three places that positioned themselves against an edge by name."""
    base = (TEMPLATES / "base.html").read_text(encoding="utf-8")
    columns = (TEMPLATES / "partials" / "table" / "columns.html").read_text(encoding="utf-8")

    assert "end-0" in base and "right-0" not in base
    assert "end-0" in columns and "right-0" not in columns


def test_the_timeline_rule_is_on_the_reading_edge():
    """The line down the left of the event log is down the reading-start edge now."""
    detail = (TEMPLATES / "applications" / "application_detail.html").read_text(encoding="utf-8")

    assert "border-s" in detail and "ps-6" in detail
    assert "-start-1.5" in detail
