"""What Postulo knows about the documents it stores, and where their bytes live.

Postulo keeps every file — a rendered CV, a rendered letter, a file a person uploaded —
under its own private media, and serves it through a permission check. That is the
**local store**, and it is the source of truth: rendering, serving, export and the review
of what was sent all work from it, with no network at all. A job search must not stop
because an archive server is down.

Other stores receive *copies*. A plugin registered in the ``postulo.stores`` group — a
Paperless, a WebDAV share, whatever someone writes — is handed each new document with
enough metadata to file it sensibly, and gives back a reference (its id there, a URL) that
Postulo keeps beside the document and carries in the export. The local store is expressed
through the same contract so that there is one code path and a plugin is not a special
case; it simply cannot be switched off.

The contract itself -- ``DocumentMetadata``, ``ExternalRef``, ``StorePlugin`` -- is part of
``postulo.plugins.api``, because it is what a store author writes against; and the local
store is ``postulo.plugins.localstore``, because it is a plugin and every plugin Postulo
ships is its own package (#129). What is left here is Postulo's side of the arrangement:
the metadata it assembles, and where a document can be downloaded from.
"""

from __future__ import annotations

import mimetypes

from django.urls import reverse

from postulo.plugins.api import DocumentMetadata, FieldSpec

from .models import DocumentKind, RenderedDocument, UploadedDocument


def download_path(document) -> str:
    if isinstance(document, RenderedDocument):
        return reverse("documents:rendered_download", args=[document.pk]) if document.pk else ""
    return reverse("documents:upload_download", args=[document.pk]) if document.pk else ""


# ---------------------------------------------------------------------- metadata


def metadata_for(document, *, filename: str = "") -> DocumentMetadata:
    """Describe a render or an upload for a store."""
    from postulo.notifications.base import absolute_url

    is_render = isinstance(document, RenderedDocument)
    name = filename or (document.file.name.rsplit("/", 1)[-1] if document.file else "")
    content_type = mimetypes.guess_type(name)[0] or "application/octet-stream"
    application = getattr(document, "application", None)
    company = role = application_url = ""
    if application is not None:
        posting = application.posting
        company = posting.company.name
        role = posting.title
        application_url = absolute_url(application.get_absolute_url())
    profile = getattr(document.owner, "profile", None)
    size = 0
    if document.file:
        try:
            size = document.file.size
        except (OSError, ValueError):
            size = 0
    when = document.rendered_at if is_render else document.created_at
    return DocumentMetadata(
        kind=document.kind,
        kind_label=str(DocumentKind(document.kind).label),
        origin="render" if is_render else "upload",
        title=document.title,
        filename=name,
        content_type=content_type,
        created_at=when,
        checksum=getattr(document, "checksum", "") or "",
        size=size,
        company=company,
        role=role,
        application_url=application_url,
        sent_on=when.date() if is_render and application is not None else None,
        language=getattr(profile, "language", "") or "",
        tags=("postulo", document.kind),
    )


# ------------------------------------------------------------ which kinds go where


def kind_specs() -> list[FieldSpec]:
    """The per-kind switches every store connection carries. All on by default."""
    return [
        FieldSpec(
            f"kind_{kind.value}", str(kind.label), type="boolean", required=False, default=True
        )
        for kind in DocumentKind
    ]


def wants_kind(config: dict, kind: str) -> bool:
    """Whether a store connection asked for documents of ``kind``. Unset means yes."""
    return bool(config.get(f"kind_{kind}", True))


def documents_of(user, kinds: set[str] | None = None):
    """Every render and upload of ``user``, optionally of the given kinds."""
    renders = RenderedDocument.objects.for_user(user)
    uploads = UploadedDocument.objects.for_user(user)
    if kinds is not None:
        renders = renders.filter(kind__in=kinds)
        uploads = uploads.filter(kind__in=kinds)
    return [*renders, *uploads]
