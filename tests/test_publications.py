"""A paper, a book, a chapter, a thesis: shaped like a BibTeX entry (#687).

What is held here: that the type decides which fields the form offers and never which are
kept; that a DOI, a citation key and a partial date are held to their shapes; that a
publication takes its place in the career page, the CV picker, the preview, the text and
Word files and both base themes, all saying the same line; that search finds one and
links to the section it is in; and that it travels in the archive and the candidate file.
"""

from __future__ import annotations

import io
import json
import re
import zipfile

import pytest
from django.contrib.contenttypes.models import ContentType
from django.urls import reverse
from django.utils.html import strip_tags

from postulo.core import export, importer, search
from postulo.documents import docx, rendering
from postulo.documents.models import CV, CVItem, CVKind
from postulo.resume import candidate, publications, translating
from postulo.resume.forms import PublicationForm
from postulo.resume.models import Link, Publication
from postulo.resume.registry import OVERVIEW_ORDER, SECTIONS

pytestmark = pytest.mark.django_db


def a_paper(owner, **changes) -> Publication:
    values = {
        "entry_type": "article",
        "title": "The Art of Computer Programming",
        "authors": "Knuth, Donald E.\nPatashnik, Oren",
        "container_title": "Journal of Careful Things",
        "volume": "12",
        "number": "3",
        "pages": "45–67",
        "date": "1974-05",
        "doi": "10.1000/182",
        **changes,
    }
    return Publication.objects.create(owner=owner, **values)


def form_for(user, **posted) -> PublicationForm:
    data = {"entry_type": "article", "title": "A title", **posted}
    return PublicationForm(data=data, user=user, instance=Publication(owner=user))


def put_on(cv, entry, order=0) -> CVItem:
    return CVItem.objects.create(
        owner=cv.owner,
        cv=cv,
        content_type=ContentType.objects.get_for_model(type(entry)),
        object_id=entry.pk,
        order=order,
    )


def a_file(entries) -> bytes:
    document = {
        "postulo": {"candidate_format": export.CANDIDATE_FORMAT},
        "resume": {"publications": list(entries)},
    }
    return json.dumps(document).encode()


def as_text(html: str) -> str:
    from html import unescape

    return " ".join(unescape(strip_tags(html)).split())


# ------------------------------------------------------------------- the table of types


def test_every_type_is_one_entry_in_one_table_and_the_menu_is_that_table():
    assert [key for key, _label in publications.CHOICES] == list(publications.TYPES)
    # BibTeX's thirteen without the `conference` alias, and biblatex's three beside them.
    bibtex = {
        "article",
        "book",
        "booklet",
        "inbook",
        "incollection",
        "inproceedings",
        "manual",
        "mastersthesis",
        "misc",
        "phdthesis",
        "proceedings",
        "techreport",
        "unpublished",
    }
    assert set(publications.TYPES) == bibtex | {"online", "dataset", "software"}
    assert "conference" not in publications.TYPES


@pytest.mark.parametrize("entry_type", list(publications.TYPES))
def test_each_type_offers_its_fields_and_hides_the_rest(user, entry_type):
    form = PublicationForm(
        initial={"entry_type": entry_type}, user=user, instance=Publication(entry_type=entry_type)
    )
    offered = {name for name, wrap in form.type_wrappers.items() if not wrap["hidden"]}
    # The type, the title and the date are on every form, and follow nothing.
    own = (set(publications.TYPES[entry_type].fields) | set(publications.COMMON)) - set(
        publications.ALWAYS
    )

    assert offered == own, entry_type
    # What is hidden is still on the form: hidden, not refused.
    assert set(form.type_wrappers) - offered <= set(form.fields)


def test_a_type_uses_what_bibtex_says_it_does():
    article = publications.TYPES["article"]
    assert set(publications.fields_for("article")) >= {"authors", "container_title", "pages"}
    assert "chapter" not in article.fields
    assert ("authors", "editors") in publications.TYPES["book"].required
    assert "institution" in publications.TYPES["phdthesis"].fields
    assert publications.TYPES["misc"].required == ()


def test_the_page_is_drawn_for_the_type_and_a_script_can_change_it(client, user):
    client.force_login(user)
    html = client.get(reverse("resume:item_create", args=["publication"])).content.decode()

    assert "data-type-select" in html
    # An article has no chapter, and so the chapter's box is there and hidden.
    chapter = re.search(
        r'<div data-type-field="([^"]*)"( hidden)?>\s*<div[^>]*>\s*<label[^>]*for="id_chapter"',
        html,
    )
    assert chapter and chapter.group(2), "a field the type does not use is drawn hidden"
    assert "article" not in chapter.group(1).split()
    assert "incollection" in chapter.group(1).split()
    pages = re.search(
        r'<div data-type-field="([^"]*)"( hidden)?>\s*<div[^>]*>\s*<label[^>]*for="id_pages"', html
    )
    assert pages and not pages.group(2)


def test_a_value_survives_a_type_change(client, user):
    client.force_login(user)
    paper = a_paper(user, chapter="4")
    url = reverse("resume:item_update", args=["publication", paper.pk])

    response = client.post(
        url,
        {
            "entry_type": "book",
            "title": paper.title,
            "authors": paper.authors,
            "container_title": paper.container_title,
            "volume": "12",
            "chapter": "4",
            "pages": "45–67",
            "date": "1974-05",
            "doi": paper.doi,
        },
    )
    assert response.status_code == 302
    paper.refresh_from_db()
    assert paper.entry_type == "book"
    # Fields a book does not use, still there; changed back, nothing was lost.
    assert (paper.container_title, paper.pages, paper.chapter) == (
        "Journal of Careful Things",
        "45–67",
        "4",
    )
    client.post(
        url, {"entry_type": "article", "title": paper.title, "chapter": "4", "pages": "45–67"}
    )
    paper.refresh_from_db()
    assert (paper.entry_type, paper.chapter) == ("article", "4")


def test_what_a_type_asks_for_is_a_hint_and_never_an_error(client, user):
    client.force_login(user)

    response = client.post(
        reverse("resume:item_create", args=["publication"]),
        {"entry_type": "article", "title": "Only a title"},
        follow=True,
    )

    assert Publication.objects.for_user(user).get().title == "Only a title"
    shown = [str(message) for message in response.context["messages"]]
    assert any(
        "usually also gives" in message and "authors" in message.lower() for message in shown
    )


def test_nothing_is_missing_from_a_type_that_asks_for_nothing():
    assert publications.missing_for("misc", {}.get) == []
    assert publications.missing_for("book", {"title": "T", "editors": "E"}.get) == [
        "publisher",
        "date",
    ]


# ------------------------------------------------------------ the shapes of a value


@pytest.mark.parametrize("doi", ["10.1000/182", "10.1038/nphys1170", "10.12345.6/a(b)c;d"])
def test_a_doi_is_held_to_its_shape(user, doi):
    assert form_for(user, doi=doi).is_valid()


@pytest.mark.parametrize("doi", ["182", "10.1/x", "11.1000/182", "10.1000/", "10.1000/a b"])
def test_a_doi_that_is_not_shaped_like_one_is_refused(user, doi):
    form = form_for(user, doi=doi)
    assert not form.is_valid()
    assert "doi" in form.errors


@pytest.mark.parametrize(
    "typed",
    ["https://doi.org/10.1000/182", "http://dx.doi.org/10.1000/182", "doi: 10.1000/182"],
)
def test_a_doi_is_stored_bare_and_linked_as_the_resolver(user, typed):
    form = form_for(user, doi=typed)
    assert form.is_valid(), form.errors
    paper = form.save()
    assert paper.doi == "10.1000/182"
    assert paper.doi_url == "https://doi.org/10.1000/182"


def test_nothing_here_asks_the_resolver(monkeypatch):
    """The DOI is a shape; resolving it is the one place #688 asks anybody."""
    import socket

    def refused(*args, **kwargs):  # pragma: no cover - the point is that it is not called
        raise AssertionError("a publication asked the network")

    monkeypatch.setattr(socket, "create_connection", refused)
    monkeypatch.setattr(socket, "getaddrinfo", refused)
    publications.validate_doi("10.1000/182")
    assert publications.citation_parts(Publication(title="T", doi="10.1000/182"))[1].startswith(
        "https://doi.org/"
    )


@pytest.mark.parametrize("key", ["knuth1974", "a-b_c:d.e+f/g", "X1"])
def test_a_citation_key_holds_ascii_letters_digits_and_a_few_marks(user, key):
    assert form_for(user, cite_key=key).is_valid()


@pytest.mark.parametrize("key", ["with space", "ação", "a{b}", "a,b", "a#b", "a'b", "a\\b"])
def test_a_citation_key_with_anything_else_is_refused(user, key):
    form = form_for(user, cite_key=key)
    assert not form.is_valid()
    assert "cite_key" in form.errors


def test_a_key_is_one_of_yours_at_most_and_another_persons_does_not_count(user, other_user):
    a_paper(user, cite_key="knuth1974")
    a_paper(other_user, cite_key="knuth1974")

    form = form_for(user, cite_key="knuth1974")

    assert not form.is_valid()
    assert "cite_key" in form.errors
    assert form_for(other_user, cite_key="other").is_valid()


def test_a_blank_key_is_made_from_the_author_the_year_and_a_title_word(user):
    first = a_paper(user)
    second = a_paper(user)

    assert first.cite_key == "knuth1974art"
    assert second.cite_key == "knuth1974arta", "a key is one of yours at most"
    assert re.fullmatch(publications.CITE_KEY, first.cite_key)


def test_a_key_is_made_of_ascii_whatever_the_names_are_written_in(user):
    paper = a_paper(user, authors="Águeda, Tiago", title="Ação e reação", date="2020")

    assert paper.cite_key == "agueda2020acao"


@pytest.mark.parametrize("date", ["", "2024", "2024-05", "2024-05-17", "2031"])
def test_a_date_is_a_year_a_month_or_a_day_and_may_be_forthcoming(user, date):
    assert form_for(user, date=date).is_valid()


@pytest.mark.parametrize(
    "date",
    ["24", "2024-5", "2024-13", "2024-02-30", "2024/05", "May 2024", "2024-05-17T10:00", "٢٠٢٤"],
)
def test_a_date_that_is_not_one_is_refused(user, date):
    form = form_for(user, date=date)
    assert not form.is_valid()
    assert "date" in form.errors


def test_the_date_is_read_the_way_a_date_of_birth_is():
    from postulo.core import personal

    assert personal.reduced_date_parts("2024-05") == (2024, 5, None)
    assert personal.reduced_date_parts("2024-02-30") is None
    assert personal.parse_birth_date("1990-03-12") == (1990, 3, 12)


def test_the_language_is_a_tag_or_nothing(user):
    assert form_for(user, language="pt-PT").is_valid()
    assert form_for(user, language="").is_valid()
    assert not form_for(user, language="not a language").is_valid()


def test_the_entry_type_is_one_of_the_table(user):
    assert not form_for(user, entry_type="conference").is_valid()
    assert not form_for(user, entry_type="<script>").is_valid()


# -------------------------------------------------------------- the career page


def test_publications_come_after_projects_on_the_career_page_and_have_their_anchor(client, user):
    order = list(OVERVIEW_ORDER)
    assert order.index("publication") == order.index("project") + 1
    a_paper(user)
    client.force_login(user)

    html = client.get(reverse("resume:overview")).content.decode()

    assert '<section id="section-publication">' in html
    assert "Knuth, Donald E." in html
    assert str(SECTIONS["publication"].plural) == "Publications"


def test_a_publication_is_added_and_edited_through_the_one_set_of_views(client, user):
    client.force_login(user)
    response = client.post(
        reverse("resume:item_create", args=["publication"]),
        {
            "entry_type": "phdthesis",
            "title": "A thesis",
            "authors": "Morgan, Alex",
            "institution": "Universidade de Lisboa",
            "date": "2021",
        },
    )
    assert response.status_code == 302
    paper = Publication.objects.for_user(user).get()
    assert paper.owner == user
    assert (
        client.get(reverse("resume:item_update", args=["publication", paper.pk])).status_code == 200
    )


def test_a_publication_translates_nothing():
    assert translating.fields_for(Publication) == ()
    paper = Publication(title="A title")
    assert translating.fields_for(paper) == ()


# --------------------------------------------------------------------- what a CV prints


@pytest.fixture
def cv(user):
    user.first_name, user.last_name = "Alex", "Morgan"
    user.save(update_fields=["first_name", "last_name"])
    variant = CV.objects.create(owner=user, name="Research", language="en-GB")
    put_on(variant, a_paper(user), 0)
    put_on(
        variant,
        a_paper(
            user,
            entry_type="book",
            title="A book",
            authors="",
            editors="Doe, J.",
            doi="",
            pages="",
            volume="",
            number="",
            container_title="",
            publisher="Press",
            date="2020",
        ),
        1,
    )
    put_on(variant, Link.objects.create(owner=user, title="Site", url="https://example.org/"), 2)
    return variant


NEUTRAL = (
    "Knuth, Donald E.; Patashnik, Oren. The Art of Computer Programming. "
    "Journal of Careful Things, vol. 12(3), pp. 45–67. 1974. https://doi.org/10.1000/182"
)


def test_the_line_is_authors_title_container_volume_pages_year_and_doi(cv):
    paper = Publication.objects.for_user(cv.owner).get(title="The Art of Computer Programming")

    assert paper.citation == NEUTRAL


def test_editors_stand_where_there_are_no_authors(cv):
    book = Publication.objects.for_user(cv.owner).get(title="A book")

    assert book.citation == "Doe, J. (ed.). A book. Press. 2020"


@pytest.mark.parametrize("kind", [CVKind.CV, CVKind.PORTFOLIO])
def test_both_base_themes_print_the_same_line(cv, kind):
    CV.objects.filter(pk=cv.pk).update(kind=kind)
    cv.refresh_from_db()

    html = rendering.render_cv_html(cv)

    assert "<h2>Publications</h2>" in html
    assert NEUTRAL in as_text(html)
    assert '<a href="https://doi.org/10.1000/182">' in html


def test_the_text_and_the_word_file_say_the_same_line(cv):
    text = rendering.cv_text(cv)
    assert NEUTRAL in text
    assert "Publications" in text

    parts = docx.parts_of(rendering.cv_outline(cv))
    assert NEUTRAL in as_text(parts["word/document.xml"])
    with zipfile.ZipFile(io.BytesIO(docx.write(rendering.cv_outline(cv)))) as archive:
        assert NEUTRAL in as_text(archive.read("word/document.xml").decode())


def test_the_preview_prints_it_too(client, user, cv):
    client.force_login(user)

    html = client.get(reverse("resume:preview")).content.decode()

    assert NEUTRAL.split(". https://")[0] in as_text(html)
    assert "https://doi.org/10.1000/182" in html


def test_a_cv_holding_a_publication_and_a_link_prints_both(cv):
    text = rendering.cv_text(cv)

    assert "Site" in text and "https://example.org/" in text
    assert NEUTRAL in text


def test_the_picker_offers_the_publications_and_adds_them(client, user):
    paper = a_paper(user)
    variant = CV.objects.create(owner=user, name="Research")
    client.force_login(user)

    page = client.get(reverse("documents:cv_detail", args=[variant.pk]))
    assert page.status_code == 200
    assert "add_publication" in page.content.decode()
    client.post(
        reverse("documents:cv_add_items", args=[variant.pk]),
        {"add_publication": [str(paper.pk)]},
    )

    assert variant.items.get().item == paper


def test_a_publication_never_prints_its_key_or_its_type_word(cv):
    text = rendering.cv_text(cv)

    assert "knuth1974art" not in text
    assert "article" not in text.lower()


def test_the_text_is_in_the_documents_language_not_the_readers(user):
    variant = CV.objects.create(owner=user, name="Pesquisa", language="pt-PT")
    put_on(variant, a_paper(user))

    text = rendering.cv_text(variant)

    assert "vol. 12(3)" in text or "pp." in text


# ---------------------------------------------------------------------------- search


def test_search_finds_a_publication_by_title_author_and_container(user):
    paper = a_paper(user)

    for query in ("Computer Programming", "Patashnik", "Careful Things"):
        groups = {group.kind: group for group in search.search(user, query)}
        hits = groups["career"].hits
        assert [hit.id for hit in hits] == [paper.pk], query
        assert hits[0].url.endswith("#section-publication")
        assert hits[0].title == paper.title


def test_search_never_finds_somebody_elses(user, other_user):
    a_paper(other_user)

    assert search.search(user, "Patashnik") == []


# ------------------------------------------------------------------- the archive


def test_the_archive_carries_the_publications_at_the_new_format(user):
    a_paper(user)

    buffer = export.write_archive(user)
    manifest = json.loads(zipfile.ZipFile(buffer).read(export.MANIFEST_NAME))

    assert manifest["postulo"]["format"] == export.FORMAT_VERSION >= 44
    rows = manifest["resume"]["publications"]
    assert rows[0]["title"] == "The Art of Computer Programming"
    assert rows[0]["entry_type"] == "article"
    assert rows[0]["cite_key"] == "knuth1974art"


def test_restore_brings_the_publications_back(user, other_user):
    original = a_paper(user)
    archive = zipfile.ZipFile(export.write_archive(user))

    importer.load(other_user, archive)

    restored = Publication.objects.for_user(other_user).get()
    for name in ("entry_type", "title", "authors", "container_title", "date", "doi", "cite_key"):
        assert getattr(restored, name) == getattr(original, name), name
    assert restored.owner == other_user and restored.pk != original.pk


def test_a_cv_holding_a_publication_and_a_link_keeps_both_on_restore(cv, other_user):
    archive = zipfile.ZipFile(export.write_archive(cv.owner))

    report = importer.load(other_user, archive)

    restored = CV.objects.for_user(other_user).get()
    kinds = sorted(item.content_type.model for item in restored.items.all())
    assert kinds == ["link", "publication", "publication"]
    assert not [line for line in report.skipped if "no such record" in line]
    assert {item.item.owner for item in restored.items.all()} == {other_user}


def test_a_file_cannot_put_what_a_page_would_refuse_into_a_restored_publication(user, other_user):
    a_paper(other_user, cite_key="taken")
    values = {
        "entry_type": "spaceship",
        "title": "T",
        "date": "next spring",
        "doi": "not a doi",
        "cite_key": "taken",
    }

    clean = publications.sanitise(values, {"taken"})

    assert clean["entry_type"] == "misc"
    assert (clean["date"], clean["doi"], clean["cite_key"]) == ("", "", "")


# -------------------------------------------------------------------- the candidate


def test_the_candidate_file_matches_on_the_doi_else_on_title_and_year(user):
    a_paper(user)
    base = {"entry_type": "article", "authors": "Someone"}

    def plan(*entries):
        return candidate.plan(user, candidate.read(a_file(entries)))

    def rows_of(drawn):
        return [
            row
            for section in drawn.sections
            if section.key == "publications"
            for row in section.rows
        ]

    same_doi = {**base, "title": "Another title altogether", "doi": "https://doi.org/10.1000/182"}
    same_title = {**base, "title": "the art of computer programming", "date": "1974"}
    other_year = {**base, "title": "The Art of Computer Programming", "date": "1999"}
    bad_doi = {**base, "title": "Fresh", "doi": "nope"}

    outcomes = [row.outcome for row in rows_of(plan(same_doi, same_title, other_year, bad_doi))]

    assert outcomes[0] == candidate.PRESENT, "the DOI says so, whatever the title says"
    assert outcomes[1] == candidate.PRESENT, "no DOI to go by, and the title and the year agree"
    assert outcomes[2] == candidate.ADD, "the same title in another year is another publication"
    assert outcomes[3] == candidate.REFUSED


def test_adding_from_a_candidate_file_makes_each_a_key_of_its_own(user):
    entries = [
        {"entry_type": "article", "title": "Same Words", "authors": "Doe, J.", "date": "2020"},
        {
            "entry_type": "article",
            "title": "Same Words Again",
            "authors": "Doe, J.",
            "date": "2020",
        },
    ]
    candidate.apply(user, candidate.read(a_file(entries)))

    keys = list(Publication.objects.for_user(user).values_list("cite_key", flat=True))
    assert len(keys) == 2 and len(set(keys)) == 2 and all(keys)
