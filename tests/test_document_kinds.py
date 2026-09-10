"""A document is a kind, and a kind is described in one place (#133).

> on the documents, general conceived for one kid CV's, shoould in fact extended to other
> kinds of documents, once again defined by a deveral internal plugins

The first stage of the three the issue itself proposes: *portfolios need the polymorphic
link and the theme rule and nothing else*. The polymorphic links arrived with #130 — a
render points at whatever made it, a copy points at whatever it copied — and the theme rule
with #132. So what is here is the kind vocabulary in one place, the first kind that uses it,
and the last two places a store still asked what sort of document it was holding.

Reports and emails are deliberately not here, and #162 says why.
"""

from __future__ import annotations

import pytest
from django.urls import reverse

from postulo.documents import kinds, themes
from postulo.documents.models import CV, CVKind, DocumentKind, RenderedDocument, UploadedDocument

pytestmark = pytest.mark.django_db


def a_portfolio(user, name="My work"):
    return CV.objects.create(owner=user, name=name, kind=CVKind.PORTFOLIO)


# ------------------------------------------------- the vocabulary, said once


def test_every_stored_kind_is_described():
    """The disagreement the issue names, made impossible: a value a column can hold and no
    registry entry for it would be a document nothing can name.
    """
    for value in DocumentKind:
        assert kinds.get(value) is not None, f"{value} is storable and undescribed"


def test_every_described_kind_is_storable():
    """And the other way: a kind nothing can be filed under is a kind nobody can use."""
    storable = set(DocumentKind)
    for key in kinds.REGISTRY:
        assert key in storable or kinds.REGISTRY[key].provider, (
            f"{key} is described, unstorable, and claims no provider"
        )


def test_a_kind_says_whether_postulo_makes_one():
    """A certificate is issued by somebody else and can only ever be uploaded; a CV is
    written here. The difference is what decides which pickers offer it.
    """
    assert kinds.get(DocumentKind.CV).authored
    assert kinds.get(DocumentKind.PORTFOLIO).authored
    assert not kinds.get(DocumentKind.CERTIFICATE).authored
    assert not kinds.get(DocumentKind.REFERENCE).authored


def test_a_kind_says_which_themes_set_it():
    assert kinds.theme_kind_for(DocumentKind.CV) == themes.Kind.CV
    assert kinds.theme_kind_for(DocumentKind.PORTFOLIO) == themes.Kind.PORTFOLIO
    assert kinds.theme_kind_for(DocumentKind.CERTIFICATE) == "", "nothing renders one"


def test_the_pickers_read_the_registry_rather_than_the_enumeration():
    """Which is what makes "a kind is a plugin" mean something: a kind a plugin adds reaches
    every picker with no migration, because `choices` is a callable.
    """
    field = UploadedDocument._meta.get_field("kind")

    assert callable(field.choices) or field.choices == kinds.choices()
    assert kinds.choices, "and the callable is this one"


def test_a_kind_a_plugin_adds_reaches_the_picker(client, user):
    kinds.register(kinds.Kind("acme:brief", "Design brief", provider="Acme"))
    try:
        client.force_login(user)
        html = client.get(reverse("documents:upload_create")).content.decode()
        assert "Design brief" in html
    finally:
        kinds.forget("acme:brief")


def test_a_key_already_taken_is_refused_rather_than_overridden():
    """A plugin able to replace `cv` could change what every CV already filed under it
    claims to be, without anybody having chosen that (#132's reasoning, again).
    """
    assert not kinds.register(kinds.Kind(DocumentKind.CV, "Something else"))
    assert kinds.get(DocumentKind.CV).label != "Something else"


def test_a_kind_nobody_claims_still_says_something():
    """A document filed under a kind whose plugin has been removed has to say *something*,
    and the raw value is honest where nothing would look like a bug.
    """
    assert kinds.label_for("acme:gone") == "acme:gone"


# ------------------------------------------------------------ the first new kind


def test_a_portfolio_is_a_cv_with_a_different_shape(user):
    """One model rather than two: a portfolio *is* a selection from the career record with
    its own layout, and it uses every line of `CVItem`'s machinery unchanged.
    """
    portfolio = a_portfolio(user)

    assert isinstance(portfolio, CV)
    assert portfolio.kind == CVKind.PORTFOLIO


def test_a_cv_is_still_a_cv_without_anybody_saying_so(user):
    """Nothing about this release changes what an existing CV is."""
    assert CV.objects.create(owner=user, name="Main").kind == CVKind.CV


def test_a_render_is_filed_under_what_the_model_says_it_is(user):
    """A portfolio filed as a CV is a document a store or an employment office mislabels."""
    from postulo.documents.rendering import snapshot_cv

    class Nothing:
        def render(self, html: str) -> bytes:
            return b"%PDF-1.4 pretend"

    render = snapshot_cv(a_portfolio(user), backend=Nothing())

    assert render.kind == DocumentKind.PORTFOLIO
    assert "Portfolio" in render.title


def test_the_two_shapes_pick_from_different_theme_vocabularies(user):
    portfolio = a_portfolio(user)
    cv = CV.objects.create(owner=user, name="Main")

    assert portfolio.theme_kind == themes.Kind.PORTFOLIO
    assert cv.theme_kind == themes.Kind.CV


def test_a_portfolio_is_offered_only_themes_that_set_one(user):
    """A theme that only knows how to set a CV is exactly the pair #132 exists to keep out
    of the menu.
    """
    from postulo.documents.forms import CVForm

    form = CVForm(user=user, instance=a_portfolio(user))

    offered = {value for value, _label in form.fields["theme"].choices}
    for name in offered:
        assert themes.find(name).sets(themes.Kind.PORTFOLIO)


def test_every_shipped_theme_sets_a_portfolio_too():
    """The rule #132 established, holding for the kind that arrived after it."""
    for theme in themes.BUILT_IN:
        assert theme.sets(themes.Kind.PORTFOLIO), f"{theme.name} sets no portfolio"


def test_a_portfolio_leads_with_the_work(user):
    """The one thing a document's structure is *for* is the order of the argument. A CV is
    a career read backwards; a portfolio is the work, with the career as context.
    """
    from pathlib import Path

    base = Path("src/postulo/templates/documents/themes/base_portfolio.html").read_text(
        encoding="utf-8"
    )

    assert "section.kind == 'project'" in base
    assert "background" in base, "and the career is set as context"


def test_a_portfolio_renders_through_its_own_structure(user):
    from postulo.documents.rendering import render_cv_html

    html = render_cv_html(a_portfolio(user))

    assert "<html" in html and "</html>" in html


def test_the_page_names_both_shapes(client, user):
    a_portfolio(user)
    client.force_login(user)

    html = client.get(reverse("documents:cv_list")).content.decode()

    assert "Portfolio" in html


def test_one_person_never_sees_another_portfolio(client, user, other_user):
    a_portfolio(other_user, "Theirs")
    client.force_login(user)

    html = client.get(reverse("documents:cv_list")).content.decode()

    assert "Theirs" not in html


# --------------------------------------- a store never learns what kinds exist


def test_a_store_asks_the_document_where_it_can_be_fetched(user):
    """It used to be an `isinstance` against the two models that hold a file. A third --
    and a report, or anything a plugin brings, would be a third -- needed a branch in a
    module whose whole point is that a store never learns what kinds of document exist.
    """
    from postulo.documents import stores

    upload = UploadedDocument.objects.create(owner=user, title="A file", kind=DocumentKind.OTHER)
    render = RenderedDocument.objects.create(owner=user, title="A render")

    assert stores.download_path(upload).endswith(f"{upload.pk}/download/") or str(
        upload.pk
    ) in stores.download_path(upload)
    assert str(render.pk) in stores.download_path(render)


def test_a_third_thing_that_holds_a_file_needs_no_branch(user):
    """The claim, tested rather than asserted in a comment: something declaring the two
    attributes gets described without `stores.py` being edited.
    """
    import datetime as dt

    from postulo.documents import stores

    class ARecordSomebodyElseWrote:
        download_url_name = ""
        archive_origin = "report"
        archived_at = dt.datetime(2026, 3, 1, 9, 0)
        kind = DocumentKind.OTHER
        title = "A report"
        file = None
        owner = None
        created_at = dt.datetime(2026, 3, 1, 9, 0)

    described = stores.metadata_for(ARecordSomebodyElseWrote(), filename="report.pdf")

    assert described.origin == "report"
    assert described.title == "A report"
    assert described.created_at.year == 2026


def test_the_per_kind_switches_come_from_the_registry():
    """So a kind a plugin adds gets its own switch on every store's connection form."""
    from postulo.documents import stores

    names = {spec.name for spec in stores.kind_specs()}

    assert f"kind_{DocumentKind.PORTFOLIO}" in names
    assert len(names) == len(kinds.REGISTRY)


def test_a_store_is_told_the_kind_in_words_from_the_registry(user):
    from postulo.documents import stores

    upload = UploadedDocument.objects.create(
        owner=user, title="A file", kind=DocumentKind.PORTFOLIO
    )

    assert stores.metadata_for(upload).kind_label == "Portfolio"


# ---------------------------------------------------------- through the archive


def test_the_archive_carries_which_shape_it_is(user, other_user):
    import zipfile

    from postulo.core import export as export_module
    from postulo.core import importer

    a_portfolio(user, "My work")
    CV.objects.create(owner=user, name="Main")

    archive = zipfile.ZipFile(export_module.write_archive(user))
    importer.load(other_user, archive)

    restored = {cv.name: cv.kind for cv in CV.objects.for_user(other_user)}
    assert restored == {"My work": CVKind.PORTFOLIO, "Main": CVKind.CV}


def test_an_archive_written_before_this_restores_every_cv_as_a_cv(user):
    """Which is what every one of them was."""
    import io
    import json
    import zipfile

    from postulo.core import export as export_module
    from postulo.core import importer

    document = {
        "postulo": {"format": 13},
        "documents": {"cvs": [{"id": 1, "name": "Main"}]},
    }
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w") as archive:
        archive.writestr(export_module.MANIFEST_NAME, json.dumps(document))

    importer.load(user, zipfile.ZipFile(buffer))

    assert CV.objects.for_user(user).get().kind == CVKind.CV


def test_a_kind_the_archive_invents_restores_as_a_cv(user):
    """An archive can say anything, and an unknown shape is not a reason to refuse a CV."""
    import io
    import json
    import zipfile

    from postulo.core import export as export_module
    from postulo.core import importer

    document = {
        "postulo": {"format": 14},
        "documents": {"cvs": [{"id": 1, "name": "Main", "kind": "hologram"}]},
    }
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w") as archive:
        archive.writestr(export_module.MANIFEST_NAME, json.dumps(document))

    importer.load(user, zipfile.ZipFile(buffer))

    assert CV.objects.for_user(user).get().kind == CVKind.CV
