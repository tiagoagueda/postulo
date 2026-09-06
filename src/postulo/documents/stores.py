"""Where a document's bytes go: the store contract, and the local store that is built in.

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
"""

from __future__ import annotations

import mimetypes
from dataclasses import dataclass, field
from datetime import date, datetime
from typing import Protocol, runtime_checkable

from django.urls import reverse
from django.utils.translation import gettext_lazy as _

from postulo import __version__
from postulo.plugins.base import ConnectedPlugin, FieldSpec, TestResult

from .models import DocumentKind, RenderedDocument, UploadedDocument


@dataclass(frozen=True)
class DocumentMetadata:
    """What an archive needs to file a document without opening it.

    Everything here is a plain value, so a store never has to import Postulo's models to
    make sense of what it was given. ``kind`` is a :class:`DocumentKind` value; ``origin``
    says whether this is a render Postulo produced or a file the person uploaded.
    """

    kind: str
    kind_label: str
    origin: str  # "render" or "upload"
    title: str
    filename: str
    content_type: str
    created_at: datetime
    checksum: str = ""
    size: int = 0
    company: str = ""
    role: str = ""
    application_url: str = ""
    sent_on: date | None = None
    language: str = ""
    tags: tuple[str, ...] = field(default_factory=tuple)


@dataclass(frozen=True)
class ExternalRef:
    """Where a copy went: which store, its id there, and a link if the store has one."""

    store: str
    id: str
    url: str = ""


@runtime_checkable
class StorePlugin(ConnectedPlugin, Protocol):
    """A connected plugin that keeps a copy of a document somewhere.

    ``put`` receives the document, its open file, the metadata above, the connection's
    configuration and secrets together, and the person. It returns a reference, or
    ``None`` to say *not for me* — an archive for paperwork may decline a video, say —
    and raises on failure; Postulo retries later and shows the error on the document.
    ``delete`` and ``browse`` are optional and not yet called by the core.
    """

    def put(
        self, document, file, metadata: DocumentMetadata, config: dict, user
    ) -> ExternalRef | None: ...


# ---------------------------------------------------------------- the local store


class LocalStore:
    """Private media on this instance: the store every document is in, always.

    It is a plugin in shape only. It takes no connection, appears on no form and cannot
    be removed; it exists so that the code writing a file and the code copying it
    elsewhere speak the same contract.
    """

    name = "local"
    version = __version__
    kind = "store"
    label = _("This instance")
    #: Not offered under Settings → Connections: it needs nothing from anyone.
    needs_connection = False

    def config_fields(self) -> list[FieldSpec]:
        return []

    def test(self, config: dict) -> TestResult:
        return TestResult(True, str(_("Files are kept under this instance's private media.")))

    def put(self, document, file, metadata: DocumentMetadata, config: dict, user) -> ExternalRef:
        document.file.save(metadata.filename, file, save=False)
        return ExternalRef(store=self.name, id=document.file.name, url=download_path(document))


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
