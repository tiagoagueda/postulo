"""A render points at whatever made it, and a copy at whatever was copied (#130).

> on the documents, general conceived for one kid CV's, shoould in fact extended to other
> kinds of documents

The first prerequisite: before a third kind of document can exist, the plumbing has to stop
naming its two kinds in foreign keys. `CVItem` decided this the other way one model over, and
its docstring is the argument — *six nullable foreign keys with a check constraint would say
the same thing less clearly, and would need widening every time a new kind of item is added.*

The tests that matter most here are the two `on_delete` behaviours, because they are not the
same one and a generic foreign key has neither. Deleting a CV must **not** delete the PDF an
employer received; deleting that PDF **must** delete the rows saying where its copies went.
Getting either backwards loses somebody's record of what they sent.
"""

from __future__ import annotations

import pytest
from django.core.files.base import ContentFile

from postulo.documents.models import (
    CV,
    CoverLetter,
    DocumentCopy,
    DocumentKind,
    RenderedDocument,
    UploadedDocument,
)

pytestmark = pytest.mark.django_db


def a_render(user, source=None) -> RenderedDocument:
    return RenderedDocument.objects.create(
        owner=user,
        title="Sent",
        kind=DocumentKind.CV,
        source=source,
        file=ContentFile(b"%PDF-1.7 sent", name="sent.pdf"),
    )


def a_copy(user, document) -> DocumentCopy:
    return DocumentCopy.objects.create(
        owner=user, store="shelf", label="My shelf", document=document
    )


# ------------------------------------------------------- one link, whatever the kind


def test_a_render_points_at_a_cv(user):
    cv = CV.objects.create(owner=user, name="Backend")

    render = a_render(user, cv)

    render.refresh_from_db()
    assert render.source == cv
    assert render.source_label == str(cv)


def test_a_render_points_at_a_cover_letter(user):
    letter = CoverLetter.objects.create(owner=user, name="To Aperture")

    render = a_render(user, letter)

    render.refresh_from_db()
    assert render.source == letter


def test_a_render_may_point_at_nothing(user):
    """A kind that produces nothing is still a kind, and a render whose source was deleted
    has none either. Every reader copes.
    """
    render = a_render(user)

    render.refresh_from_db()
    assert render.source is None
    assert render.source_label == ""


def test_a_copy_is_of_a_render_or_of_an_upload(user):
    render = a_render(user)
    upload = UploadedDocument.objects.create(
        owner=user, title="Diploma", file=ContentFile(b"scan", name="d.pdf")
    )

    assert a_copy(user, render).document == render
    assert a_copy(user, upload).document == upload


# ------------------------------------------------ the two on_delete behaviours


def test_deleting_a_cv_leaves_the_pdf_an_employer_received(user):
    """`SET_NULL`, written out. This is the whole point of `RenderedDocument`: what was
    sent stays exactly as it was sent, whatever happens to the draft it came from.
    """
    cv = CV.objects.create(owner=user, name="Backend")
    render = a_render(user, cv)

    cv.delete()

    render.refresh_from_db()
    assert RenderedDocument.objects.filter(pk=render.pk).exists(), "the PDF went with the CV"
    assert render.source is None
    assert render.source_type_id is None and render.source_id is None, "and nothing dangles"


def test_deleting_a_cover_letter_does_the_same(user):
    letter = CoverLetter.objects.create(owner=user, name="To Aperture")
    render = a_render(user, letter)

    letter.delete()

    render.refresh_from_db()
    assert render.source is None


def test_deleting_a_render_takes_its_copies_with_it(user):
    """`CASCADE`, and the other direction. A row saying where a copy of a deleted document
    went is a row about nothing.
    """
    render = a_render(user)
    copy = a_copy(user, render)

    render.delete()

    assert not DocumentCopy.objects.filter(pk=copy.pk).exists()


def test_deleting_an_upload_takes_its_copies_too(user):
    upload = UploadedDocument.objects.create(
        owner=user, title="Diploma", file=ContentFile(b"scan", name="d.pdf")
    )
    copy = a_copy(user, upload)

    upload.delete()

    assert not DocumentCopy.objects.filter(pk=copy.pk).exists()


# --------------------------------------------------------------- what is left over


def test_a_document_is_copied_once_per_connection(user):
    """The constraint that used to be two, one per column."""
    from django.db import IntegrityError, transaction

    from postulo.plugins.models import Connection

    connection = Connection.objects.create(
        owner=user, kind="store", plugin="shelf", label="Shelf", enabled=True
    )
    # Creating the render already offers it to every connected store, so the first copy is
    # the signal's rather than this test's — which is the behaviour worth colliding with.
    render = a_render(user)
    assert DocumentCopy.objects.filter(connection=connection).count() == 1

    with pytest.raises(IntegrityError), transaction.atomic():
        DocumentCopy.objects.create(
            owner=user, store="shelf", document=render, connection=connection
        )


def test_the_copies_of_many_documents_come_back_in_one_query(user, django_assert_num_queries):
    """A generic link has no join to `select_related`, which is the cost the issue warned
    about. A page listing documents with where their copies went pays two queries, not one
    per row.
    """
    from postulo.documents.archiving import copies_for

    documents = [a_render(user) for _ in range(3)]
    for document in documents:
        a_copy(user, document)

    with django_assert_num_queries(1):
        found = copies_for(documents)

    assert len(found) == 3


def test_adding_a_kind_needs_no_new_column(user):
    """The measure of this issue. Nothing in the two link fields names a kind of document,
    so the next one — a portfolio, an email, a report — is a package rather than two
    columns, an `isinstance` at every reader and a migration.
    """
    fields = {field.name for field in RenderedDocument._meta.get_fields()}
    copy_fields = {field.name for field in DocumentCopy._meta.get_fields()}

    assert "cv" not in fields and "cover_letter" not in fields
    assert "rendered" not in copy_fields and "upload" not in copy_fields
    assert {"source_type", "source_id"} <= fields
    assert {"document_type", "document_id"} <= copy_fields


def test_nothing_asks_isinstance_to_find_a_copy():
    """`archiving._lookup` used to, because the columns could not say it."""
    import inspect

    from postulo.documents import archiving

    source = inspect.getsource(archiving._lookup)
    # The code, not the docstring: that explains what it stopped doing, which is the point.
    code = source.split('"""')[2]

    assert "isinstance" not in code
    assert "ContentType" in code


def test_a_draft_still_lists_the_pdfs_made_from_it(user, client):
    """The reverse of the link, which the generic one took away without anybody noticing.

    ``related_name="renders"`` went with the two columns, and both detail pages ask for it —
    so the CV page and the letter page raised `AttributeError` for anyone who opened them.
    A `GenericRelation` would give the name back and a cascade with it, and the cascade is
    the one thing that must not happen here: deleting a CV must leave the PDF an employer
    received where it is. So the reverse is a query, and this is the test that says the pages
    open (#130).
    """
    from django.urls import reverse

    cv = CV.objects.create(owner=user, name="Backend")
    letter = CoverLetter.objects.create(owner=user, name="To Aperture", body="Dear …")
    render = a_render(user, cv)

    assert list(cv.renders.all()) == [render]
    assert list(letter.renders.all()) == []

    client.force_login(user)
    assert client.get(reverse("documents:cv_detail", args=[cv.pk])).status_code == 200
    assert client.get(reverse("documents:letter_detail", args=[letter.pk])).status_code == 200


# ------------------------------------------------------------------ and the archive


def test_the_archive_names_a_kind_rather_than_a_column(user):
    from postulo.core import export

    cv = CV.objects.create(owner=user, name="Backend")
    a_render(user, cv)

    document = export.build_document(user)
    sent = document["documents"]["sent"][0]

    assert sent["source_kind"] == "cv"
    assert sent["source_ref"] == cv.pk
    assert "cv_id" not in sent and "cover_letter_id" not in sent


def test_an_archive_written_before_this_still_restores(user):
    """Format 9 and earlier wrote `cv_id` or `cover_letter_id`; both shapes are read,
    because an archive made last month is still an archive.
    """
    from postulo.core.importer import _source_of

    cv = CV.objects.create(owner=user, name="Backend")
    letter = CoverLetter.objects.create(owner=user, name="To Aperture")
    cvs, letters = {7: cv}, {9: letter}

    old_cv = {"cv_id": 7, "cover_letter_id": None}
    old_letter = {"cv_id": None, "cover_letter_id": 9}
    new_shape = {"source_kind": "cv", "source_ref": 7}
    neither = {"cv_id": None, "cover_letter_id": None}

    assert _source_of(old_cv, cvs, letters) == cv
    assert _source_of(old_letter, cvs, letters) == letter
    assert _source_of(new_shape, cvs, letters) == cv
    assert _source_of(neither, cvs, letters) is None


def test_the_keys_are_taken_out_of_the_entry(user):
    """What is left goes straight to a model that has neither column."""
    from postulo.core.importer import _source_of

    entry = {"cv_id": 1, "cover_letter_id": None, "source_kind": "", "source_ref": None}

    _source_of(entry, {}, {})

    assert entry == {}
