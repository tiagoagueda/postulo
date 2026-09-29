"""Europass, as a plugin.

The reader was already shaped like one — ``read()`` decides which format it has and
dispatches, which is the ``can_handle`` / ``read`` split sources have used since the
beginning. This is the wrapper that says so, so that the import page asks the registry what
it can read instead of naming Europass, and so that the next format somebody wants is a
plugin rather than a patch to a view.

**One importer, every Europass format**, because that is what the code is: the PDF
europass.europa.eu gives you and the Candidate XML attached to it, and the SkillsPassport XML
and JSON of the service before it, all fill in the same
:class:`~postulo.resume.importing.Record`, and `read()` already tells them apart. Separate
plugins would be one thing described several times (#244).

Writing stays in core and deliberately: an importer turns bytes into a record and does
not touch the database, and ``postulo.resume.importing`` takes it from there. That is what
keeps an importer — including one somebody else writes — from needing ownership scoping of
its own, and it is why this package can be one at all (#129).
"""

from __future__ import annotations

import re

from django.utils.translation import gettext_lazy as _

from postulo.plugins.api import declares, shipped

from . import pdf, reader

#: A ``Candidate`` start tag, whatever prefix it was written with.
_CANDIDATE = re.compile(rb"<(?:[A-Za-z_][\w.-]*:)?Candidate[\s>/]")


@declares(
    shipped(
        name="europass",
        label="Europass",
        kind="importer",
        description=_(
            "The European CV format: the PDF europass.europa.eu gives you and the XML "
            "attached to it, and the older XML and JSON of the Europass service before it."
        ),
    )
)
class EuropassImporter:
    """Reads every Europass format: the PDF and its Candidate XML, and SkillsPassport."""

    def can_handle(self, data: bytes, filename: str = "") -> bool:
        """Whether this looks like one of the Europass formats.

        On the shape of the file rather than on its name. A Europass export arrives called
        all sorts of things, and somebody who renamed it has not thereby changed what it is.
        """
        # Any PDF. The one kind of PDF Postulo reads is Europass's, so Europass answers for
        # all of them and says why when it cannot read one -- "that PDF has nothing attached
        # to it" is something a person can act on, and "nothing here reads that file" is
        # not. An importer somebody installs is asked before this one, so a plugin for
        # another kind of PDF still gets its PDFs.
        if pdf.is_pdf(data):
            return True
        head = data[:4096].lstrip(b"\xef\xbb\xbf").lstrip()
        if not head.startswith((b"<", b"{")):
            return False
        # Any of the names is enough, and a file need not be valid to be recognised. A
        # truncated Europass export is still plainly a Europass export, and saying "not
        # readable XML" is far more use to whoever exported it than "nothing here reads
        # that". `can_handle` answers *what is this*; `read` answers *is it any good*.
        #
        # The old format keeps `LearnerInfo` under a `SkillsPassport` root, or hands it
        # over on its own; the current one is a `Candidate` in Europass's namespace, which
        # is what tells it from the HR Open Standards candidates other systems write.
        return (
            b"SkillsPassport" in data
            or b"LearnerInfo" in data
            or (b"www.europass.eu/" in data and _CANDIDATE.search(data) is not None)
        )

    def read(self, data: bytes) -> reader.Record:
        return reader.read(data)
