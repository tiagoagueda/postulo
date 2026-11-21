"""JSON Resume, read in (#1260).

Postulo has written a CV as JSON Resume since #1100 (`documents.jsonresume`, the sub-module
*JSON Resume export*); this is the other direction, an importer like Europass's. A file of
the open résumé format of jsonresume.org -- written by Postulo, by another tracker, by one of
the format's editors or by hand -- is read into a career record and shown on the review
before anything is written.

Bundled rather than in a repository of its own: its writer ships here, the two are tested as
a round trip, it uses nothing but the standard library and contacts nothing. On by default,
as Europass is: it does nothing until somebody chooses a file.
"""

from __future__ import annotations

from django.utils.translation import gettext_lazy as _

from postulo.plugins.api import declares, shipped

from . import reader


@declares(
    shipped(
        name="json-resume",
        # "import", as Europass's is, beside the sub-module that writes the format, which is
        # *JSON Resume export*: two switches, and the words say which is which (#911).
        label=_("JSON Resume import"),
        kind="importer",
        parent="file-formats",
        categories=("files",),
        icon="braces",
        description=_(
            "A CV in JSON Resume, the open résumé format of jsonresume.org: the .json file "
            "Postulo, another tracker or a résumé editor exports."
        ),
    )
)
class JsonResumeImporter:
    """Reads a JSON Resume file, any version of the schema from 1.0.0 to 1.7.1."""

    #: What the import page's file box offers to choose from.
    accepts = (".json", "application/json")

    def can_handle(self, data: bytes, filename: str = "") -> bool:
        """On the shape of the file, not its name: a ``basics``, the schema, or two sections."""
        return reader.recognises(data)

    def read(self, data: bytes):
        return reader.read(data)
