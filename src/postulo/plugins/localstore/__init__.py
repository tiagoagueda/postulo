"""The store every document is in: this instance's own private media.

A plugin in shape only. It takes no connection, appears on no form and cannot be switched
off; it exists so that the code writing a file and the code copying it to a Paperless or a
WebDAV share speak one contract instead of two, and so that the built-in is not the special
case every other store has to be described against.

It is a package of its own for the same reason every plugin Postulo ships is (#129): a rule
that a plugin is self-contained is worth nothing while the plugin Postulo writes itself is
exempt from it.
"""

from __future__ import annotations

from django.utils.translation import gettext_lazy as _

from postulo.documents.stores import download_path
from postulo.plugins.api import (
    DocumentMetadata,
    ExternalRef,
    FieldSpec,
    TestResult,
    declares,
    shipped,
)


@declares(
    shipped(
        name="local",
        label=_("This instance"),
        kind="store",
        description=_(
            "The private media directory on this server, where every document is kept "
            "whatever else it is also copied to."
        ),
    )
)
class LocalStore:
    """Private media on this instance: the store every document is in, always.

    It is a plugin in shape only. It takes no connection, appears on no form and cannot
    be removed; it exists so that the code writing a file and the code copying it
    elsewhere speak the same contract.
    """

    #: Not offered under Settings → Connections: it needs nothing from anyone.
    needs_connection = False

    def config_fields(self) -> list[FieldSpec]:
        return []

    def test(self, config: dict) -> TestResult:
        return TestResult(True, str(_("Files are kept under this instance's private media.")))

    def put(self, document, file, metadata: DocumentMetadata, config: dict, user) -> ExternalRef:
        document.file.save(metadata.filename, file, save=False)
        return ExternalRef(store=self.name, id=document.file.name, url=download_path(document))
