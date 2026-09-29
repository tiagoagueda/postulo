"""Two versions of one document can be compared (#236).

The model has said since it was written that the text is kept because a PDF is "impossible
to diff", and nothing diffed anything. What a CV kept was its whole themed page, stylesheet
included, which is the right thing to keep and the wrong thing to read.

Held here: that a CV's snapshot keeps its words beside its markup; that the comparison says
what changed in lines, with more than a colour; that it says so where there is nothing to
compare rather than comparing markup; and that it is linked from the three pages somebody
is on when they ask.
"""

from __future__ import annotations

import datetime
import hashlib
import re

import pytest
from django.contrib.contenttypes.models import ContentType
from django.core.files.base import ContentFile
from django.urls import reverse
from django.utils import timezone

from postulo.applications.models import Application, Status
from postulo.documents import comparing, rendering
from postulo.documents.models import CV, CoverLetter, CVItem, DocumentKind, RenderedDocument
from postulo.jobs.models import Company, JobPosting
from postulo.resume.models import Experience

pytestmark = pytest.mark.django_db


class Drawing:
    """A renderer whose PDF depends on the page it was given, as a real one's does."""

    name = "drawing"

    def is_available(self) -> bool:
        return True

    def render(self, html: str) -> bytes:
        return b"%PDF-1.7 " + hashlib.sha256(html.encode()).hexdigest().encode()


@pytest.fixture
def experience(user):
    return Experience.objects.create(
        owner=user,
        organisation="Aperture Science",
        role="Senior Engineer",
        location="Cambridge",
        start_date=datetime.date(2021, 3, 1),
        summary="Kept the portal up.",
        highlights="Cut deploy time from 40 minutes to 4.\nMentored three engineers.",
    )


@pytest.fixture
def cv(user, experience):
    variant = CV.objects.create(owner=user, name="Backend EN", headline="Backend engineer")
    CVItem.objects.create(
        owner=user,
        cv=variant,
        content_type=ContentType.objects.get_for_model(Experience),
        object_id=experience.pk,
        order=0,
    )
    return variant


@pytest.fixture
def application(user):
    company = Company.objects.create(owner=user, name="Black Mesa")
    posting = JobPosting.objects.create(owner=user, company=company, title="Research Engineer")
    return Application.objects.create(owner=user, posting=posting, status=Status.APPLIED)


@pytest.fixture
def two_versions(cv, experience, application):
    """The CV as it was sent, and as it was sent again after a rewrite."""
    earlier = rendering.snapshot_cv(cv, application=application, backend=Drawing())
    experience.role = "Principal Engineer"
    experience.highlights = "Cut deploy time from 40 minutes to 4.\nRan the on-call rota."
    experience.save()
    later = rendering.snapshot_cv(cv, application=application, backend=Drawing())
    return earlier, later


def a_snapshot(user, source=None, *, text="", plain="", kind=DocumentKind.CV, when=None):
    document = RenderedDocument(
        owner=user,
        title="A document",
        kind=kind,
        source=source,
        source_text=text,
        plain_text=plain,
        checksum="x",
    )
    if when is not None:
        document.rendered_at = when
    document.file.save("sent.pdf", ContentFile(b"%PDF-1.7 x"), save=False)
    document.save()
    return document


# -------------------------------------------------- what a snapshot keeps of a CV


def test_a_cv_snapshot_keeps_its_words_beside_its_markup(cv):
    document = rendering.snapshot_cv(cv, backend=Drawing())

    assert "<style" in document.source_text, "what was rendered is still what is kept"
    text = document.plain_text
    assert "Senior Engineer" in text and "Aperture Science · Cambridge" in text
    assert "- Cut deploy time from 40 minutes to 4." in text
    assert "<" not in text and "{" not in text, "and these are words, not a page"


def test_the_words_are_frozen_with_the_snapshot(cv, experience):
    document = rendering.snapshot_cv(cv, backend=Drawing())

    experience.role = "Completely Different Title"
    experience.save()
    document.refresh_from_db()

    assert "Senior Engineer" in document.plain_text
    assert "Completely Different Title" not in document.plain_text


def test_a_letter_is_compared_by_the_text_it_already_kept(user, application):
    letter = CoverLetter.objects.create(owner=user, name="General", body="Dear {{ company }},")
    document = rendering.snapshot_letter(letter, application=application, backend=Drawing())

    assert document.plain_text == "", "said once, in `source_text`, and not again"
    assert document.text_to_compare == "Dear Black Mesa,"


def test_a_cv_frozen_before_its_words_were_kept_has_none_to_compare(user, cv):
    """Markup is not offered in their place."""
    old = a_snapshot(user, cv, text="<html><style>p { color: red }</style><p>Words</p></html>")

    assert old.text_to_compare == ""


def test_a_report_has_none_either(user):
    assert a_snapshot(user, text="<html>r</html>", kind=DocumentKind.REPORT).text_to_compare == ""


# ------------------------------------------------------------ which one came before


def test_the_previous_version_is_the_one_before_it_of_the_same_document(user, cv):
    now = timezone.now()
    other = CV.objects.create(owner=user, name="Frontend")
    first = a_snapshot(user, cv, when=now - datetime.timedelta(days=3))
    a_snapshot(user, other, when=now - datetime.timedelta(days=2))
    second = a_snapshot(user, cv, when=now - datetime.timedelta(days=1))
    third = a_snapshot(user, cv, when=now)

    assert third.previous() == second
    assert second.previous() == first, "not the other CV's, which was sent in between"
    assert first.previous() is None


def test_two_made_in_the_same_instant_are_still_in_an_order(user, cv):
    now = timezone.now()
    first = a_snapshot(user, cv, when=now)
    second = a_snapshot(user, cv, when=now)

    assert second.previous() == first and first.previous() is None


def test_a_cv_and_a_letter_with_the_same_number_are_not_each_others(user, cv):
    """A generic link is a kind and a number, and the number alone matches both."""
    letter = CoverLetter.objects.create(id=cv.pk, owner=user, name="General", body="Dear them,")
    now = timezone.now()
    a_snapshot(user, letter, kind=DocumentKind.COVER_LETTER, when=now - datetime.timedelta(days=1))

    assert a_snapshot(user, cv, when=now).previous() is None


def test_a_snapshot_whose_source_is_gone_has_nothing_before_it(user, cv):
    now = timezone.now()
    a_snapshot(user, cv, when=now - datetime.timedelta(days=1))
    later = a_snapshot(user, cv, when=now)
    cv.delete()
    later.refresh_from_db()

    assert later.source is None and later.previous() is None


# ----------------------------------------------------------------- what changed


def test_a_changed_line_is_one_removed_and_one_added():
    found = comparing.compare("Alex Morgan\nSenior Engineer", "Alex Morgan\nPrincipal Engineer")

    assert (found.added, found.removed) == (1, 1) and found.differs
    (passage,) = found.passages
    assert [(line.change, line.text) for line in passage.lines] == [
        ("same", "Alex Morgan"),
        ("removed", "Senior Engineer"),
        ("added", "Principal Engineer"),
    ], "what went, then what came"


def test_the_same_words_do_not_differ():
    found = comparing.compare("One\nTwo", "One\nTwo")

    assert not found.differs and found.passages == ()


def test_blank_lines_are_where_a_section_ends_and_are_not_compared():
    found = comparing.compare("One\nTwo", "One\n\n\n  Two  \n")

    assert not found.differs


def test_what_is_unchanged_is_left_out_and_counted():
    before = "\n".join(f"line {number}" for number in range(1, 21))
    after = before.replace("line 10", "line ten")

    found = comparing.compare(before, after)

    (passage,) = found.passages
    assert [line.text for line in passage.lines] == [
        "line 8",
        "line 9",
        "line 10",
        "line ten",
        "line 11",
        "line 12",
    ]
    assert passage.skipped == 7, "lines 1 to 7, above"
    assert found.skipped_after == 8, "lines 13 to 20, below"


def test_two_changes_far_apart_are_two_passages():
    before = "\n".join(f"line {number}" for number in range(1, 21))
    after = before.replace("line 2\n", "line two\n").replace("line 18", "line eighteen")

    found = comparing.compare(before, after)

    assert len(found.passages) == 2
    assert found.passages[0].skipped == 0 and found.passages[1].skipped == 11
    assert found.skipped_after == 0
    assert (found.added, found.removed) == (2, 2)


def test_a_list_item_is_not_drawn_with_the_sign_that_means_removed():
    """The text file writes a hyphen in front of one, and on this page a minus in front of
    a line says it went. Only where the hyphen is known to be a bullet."""
    found = comparing.compare("- Mentored three.", "- Ran the rota.", bullet="- ")
    (passage,) = found.passages
    assert [line.text for line in passage.lines] == ["• Mentored three.", "• Ran the rota."]

    typed = comparing.compare("- as I said,", "- as I wrote,")
    (passage,) = typed.passages
    assert [line.text for line in passage.lines] == ["- as I said,", "- as I wrote,"]


def test_a_line_that_recurs_is_still_a_line():
    """`difflib` would call it junk by default, and on a CV it is an employer's name."""
    before = "\n".join(["Aperture Science"] * 300 + ["Engineer"])
    after = "\n".join(["Aperture Science"] * 300 + ["Principal Engineer"])

    found = comparing.compare(before, after)

    assert (found.added, found.removed) == (1, 1)


# ------------------------------------------------------------------- the page


def compare_page(client, document) -> str:
    response = client.get(reverse("documents:rendered_compare", args=[document.pk]))
    assert response.status_code == 200
    return response.content.decode()


def test_the_page_says_what_changed(client, user, two_versions):
    _earlier, later = two_versions
    client.force_login(user)

    page = compare_page(client, later)

    assert "Principal Engineer" in page and "Senior Engineer" in page
    assert "• Ran the on-call rota." in page and "• Mentored three engineers." in page
    assert "- Ran the on-call rota." not in page, "a hyphen there would read as *removed*"
    assert "2 lines added" in page and "2 lines removed" in page
    # What did not change is there to place what did, and no further than that.
    assert "Cut deploy time from 40 minutes to 4." in page


def test_a_change_is_said_by_more_than_a_colour(client, user, two_versions):
    """A sign everybody sees, an edge forced colours keep, and a word for a screen reader."""
    _earlier, later = two_versions
    client.force_login(user)

    page = compare_page(client, later)

    added = re.search(r'<li class="([^"]*)"\s+data-change="added">(.*?)</li>', page, re.S)
    removed = re.search(
        r'<li class="([^"]*)"\s+data-change="removed">\s*<span[^>]*>−</span>\s*<del', page, re.S
    )
    assert added and removed
    assert "border-s-4" in added.group(1) and "border-solid" in added.group(1)
    assert "border-s-4" in removed.group(1) and "border-dashed" in removed.group(1)
    assert ">+</span>" in page and ">−</span>" in page
    assert '<span class="sr-only">Added: </span>' in page
    assert '<span class="sr-only">Removed: </span>' in page
    assert "<ins" in page and "<del" in page


def test_the_page_carries_no_style_of_its_own(client, user, two_versions):
    """`style-src 'self'` refuses both, which is what rules out `difflib.HtmlDiff`."""
    _earlier, later = two_versions
    client.force_login(user)

    page = compare_page(client, later)
    main = page[page.index("<main") : page.index("</main>")]

    assert "style=" not in main and "<style" not in main
    assert "<table" not in main and "nowrap" not in main, "a list, which a phone can read"


def test_a_line_is_read_out_in_the_language_its_version_declared(client, user, cv, experience):
    """The same CV sent in French last month and in English today."""
    cv.language = "fr-fr"
    cv.save(update_fields=["language"])
    rendering.snapshot_cv(cv, backend=Drawing())
    cv.language = "en-gb"
    cv.save(update_fields=["language"])
    later = rendering.snapshot_cv(cv, backend=Drawing())
    client.force_login(user)

    page = compare_page(client, later)

    assert re.search(r'<del[^>]*>.*?<bdi lang="fr-fr">mars 2021 – ', page, re.S)
    assert re.search(r'<ins[^>]*>.*?<bdi lang="en-gb">March 2021 – ', page, re.S)


def test_the_page_names_the_two_it_is_comparing(client, user, two_versions):
    earlier, later = two_versions
    client.force_login(user)

    page = compare_page(client, later)

    assert "This version" in page and "The version before it" in page
    assert reverse("documents:rendered_download", args=[later.pk]) in page
    assert reverse("documents:rendered_download", args=[earlier.pk]) in page
    assert "Research Engineer at Black Mesa" in page, "and where each of them went"
    assert 'aria-labelledby="download-this-version this-version"' in page


def test_the_first_version_has_nothing_to_be_compared_with(client, user, cv):
    only = rendering.snapshot_cv(cv, backend=Drawing())
    client.force_login(user)

    page = compare_page(client, only)

    assert "Nothing was sent from the same document before this" in page
    assert "The version before it" not in page


def test_an_older_snapshot_is_not_compared_by_its_markup(client, user, cv):
    """The page says so, and both can still be downloaded."""
    now = timezone.now()
    old = a_snapshot(
        user,
        cv,
        text="<html><style>p { margin: 0 }</style><p>Senior Engineer</p></html>",
        when=now - datetime.timedelta(days=30),
    )
    later = a_snapshot(user, cv, text="<html>x</html>", plain="Principal Engineer", when=now)
    client.force_login(user)

    page = compare_page(client, later)

    assert "cannot be compared line by line" in page
    assert "margin: 0" not in page and "data-change" not in page
    assert reverse("documents:rendered_download", args=[old.pk]) in page


def test_the_same_words_in_another_theme_are_said_to_be_the_same(client, user, cv):
    rendering.snapshot_cv(cv, backend=Drawing())
    cv.theme = "classic"
    cv.save(update_fields=["theme"])
    later = rendering.snapshot_cv(cv, backend=Drawing())
    assert RenderedDocument.objects.count() == 2, "a change of theme is a new version"
    client.force_login(user)

    page = compare_page(client, later)

    assert "The words are the same in both." in page
    assert "data-change" not in page


def test_two_letters_are_compared_too(client, user, application):
    letter = CoverLetter.objects.create(
        owner=user, name="General", body="Dear {{ company }},\n\nI would like the job."
    )
    rendering.snapshot_letter(letter, application=application, backend=Drawing())
    letter.body = "Dear {{ company }},\n\nI would like the job very much."
    letter.save(update_fields=["body"])
    later = rendering.snapshot_letter(letter, application=application, backend=Drawing())
    client.force_login(user)

    page = compare_page(client, later)

    assert "I would like the job very much." in page
    assert "1 line added" in page and "1 line removed" in page


def test_somebody_elses_version_is_not_found(client, other_user, two_versions):
    _earlier, later = two_versions
    client.force_login(other_user)

    response = client.get(reverse("documents:rendered_compare", args=[later.pk]))

    assert response.status_code == 404


def test_what_somebody_typed_is_never_markup_on_the_page(client, user, cv, experience):
    rendering.snapshot_cv(cv, backend=Drawing())
    experience.role = '<script>alert("x")</script>'
    experience.save()
    later = rendering.snapshot_cv(cv, backend=Drawing())
    client.force_login(user)

    page = compare_page(client, later)

    assert "<script>alert" not in page
    assert "&lt;script&gt;alert(&quot;x&quot;)&lt;/script&gt;" in page


# --------------------------------------------------------------- where it is linked


def test_the_cvs_page_links_each_version_but_the_first(client, user, two_versions, cv):
    earlier, later = two_versions
    client.force_login(user)

    page = client.get(cv.get_absolute_url()).content.decode()

    assert "Compare with previous" in page
    assert reverse("documents:rendered_compare", args=[later.pk]) in page
    assert reverse("documents:rendered_compare", args=[earlier.pk]) not in page


def test_a_version_past_the_end_of_the_list_still_counts_as_earlier(client, user, cv):
    """Ten are shown, and the tenth is not the oldest because it is the last one drawn."""
    from postulo.documents import views

    now = timezone.now()
    snapshots = [
        a_snapshot(user, cv, when=now - datetime.timedelta(days=number))
        for number in range(views.VERSIONS_SHOWN + 1)
    ]
    client.force_login(user)

    page = client.get(cv.get_absolute_url()).content.decode()

    tenth, eleventh = snapshots[-2], snapshots[-1]
    assert reverse("documents:rendered_compare", args=[tenth.pk]) in page
    assert reverse("documents:rendered_download", args=[eleventh.pk]) not in page


def test_a_letters_page_lists_its_versions_now(client, user, application):
    """The view had been handing them to a page that never drew them."""
    letter = CoverLetter.objects.create(owner=user, name="General", body="Dear {{ company }},")
    rendering.snapshot_letter(letter, application=application, backend=Drawing())
    later = rendering.snapshot_letter(letter, application=application, backend=Drawing())
    client.force_login(user)

    page = client.get(letter.get_absolute_url()).content.decode()

    assert "Versions you have sent" in page
    assert reverse("documents:rendered_compare", args=[later.pk]) in page


def test_the_applications_documents_link_it(client, user, two_versions, application):
    earlier, later = two_versions
    client.force_login(user)

    page = client.get(
        reverse("documents:application_documents", args=[application.pk])
    ).content.decode()

    assert reverse("documents:rendered_compare", args=[later.pk]) in page
    assert reverse("documents:rendered_compare", args=[earlier.pk]) not in page


def test_every_link_in_a_list_of_versions_is_named_by_its_version(client, user, two_versions, cv):
    """Ten *Download*s are ten controls saying one thing (#227)."""
    client.force_login(user)

    page = client.get(cv.get_absolute_url()).content.decode()

    for name in ("Download", "Compare with previous"):
        assert re.search(rf'{name}<span class="sr-only">: [^<]+</span>', page), name


def test_a_version_that_went_nowhere_says_so(client, user, cv):
    rendering.snapshot_cv(cv, backend=Drawing())
    client.force_login(user)

    assert "Exported on its own" in client.get(cv.get_absolute_url()).content.decode()


# --------------------------------------------------------------------- the archive


def test_the_words_travel_in_the_archive_and_an_older_one_has_none(user, other_user, cv):
    import json
    import zipfile
    from io import BytesIO

    from postulo.core import export, importer

    rendering.snapshot_cv(cv, backend=Drawing())

    document = export.build_document(user)
    # 19 is the format that added the words (#236); the number itself is pinned in
    # test_phone_verification.py, where a change to it is written down.
    assert document["postulo"]["format"] >= 19
    assert "Senior Engineer" in document["documents"]["sent"][0]["plain_text"]

    importer.load(other_user, zipfile.ZipFile(export.write_archive(user)))
    restored = RenderedDocument.objects.for_user(other_user).get()
    assert "Senior Engineer" in restored.plain_text

    # The same archive as format 18 wrote it: no such key, and it still restores. Forced,
    # because the account is no longer empty: it holds what the first import put there.
    RenderedDocument.objects.for_user(other_user).delete()
    document["postulo"]["format"] = 18
    document["documents"]["sent"][0].pop("plain_text")
    buffer = BytesIO()
    with zipfile.ZipFile(buffer, "w") as archive:
        archive.writestr("postulo.json", json.dumps(document, default=str))
    buffer.seek(0)

    importer.load(other_user, zipfile.ZipFile(buffer), force=True)

    assert RenderedDocument.objects.for_user(other_user).get().plain_text == ""
