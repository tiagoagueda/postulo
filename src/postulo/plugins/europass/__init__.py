"""Europass, as a plugin.

The reader was already shaped like one — ``read()`` decides which of the two formats it has
and dispatches, which is the ``can_handle`` / ``read`` split sources have used since the
beginning. This is the wrapper that says so, so that the import page asks the registry what
it can read instead of naming Europass, and so that the next format somebody wants is a
plugin rather than a patch to a view.

**One importer, two formats**, because that is what the code is: `read_xml` and `read_json`
fill in the same :class:`~postulo.resume.importing.Record`, and `read()` already tells them
apart. Two plugins would be one thing described twice.

Writing stays in core and deliberately: an importer turns bytes into a record and does
not touch the database, and ``postulo.resume.importing`` takes it from there. That is what
keeps an importer — including one somebody else writes — from needing ownership scoping of
its own, and it is why this package can be one at all (#129).
"""

from __future__ import annotations

from django.utils.translation import gettext_lazy as _

from postulo.plugins.api import declares, shipped

from . import reader


@declares(
    shipped(
        name="europass",
        label="Europass",
        kind="importer",
        description=_(
            "The European CV format: the XML the Europass editor produces and the JSON "
            "europass.europa.eu exports."
        ),
    )
)
class EuropassImporter:
    """Reads the XML the Europass CV editor produces and the JSON europass.europa.eu exports."""

    def can_handle(self, data: bytes, filename: str = "") -> bool:
        """Whether this looks like one of the two Europass formats.

        On the shape of the file rather than on its name. A Europass export arrives called
        all sorts of things, and somebody who renamed it has not thereby changed what it is.
        """
        head = data[:4096].lstrip(b"\xef\xbb\xbf").lstrip()
        if not head.startswith((b"<", b"{")):
            return False
        # Either name is enough, and a file need not be valid to be recognised. A
        # truncated Europass export is still plainly a Europass export, and saying "not
        # readable XML" is far more use to whoever exported it than "nothing here reads
        # that". `can_handle` answers *what is this*; `read` answers *is it any good*.
        #
        # Both names, because the two readers accept two shapes: the XML keeps
        # `LearnerInfo` under a `SkillsPassport` root, and the JSON has it either there or
        # handed over on its own.
        return b"SkillsPassport" in data or b"LearnerInfo" in data

    def read(self, data: bytes) -> reader.Record:
        return reader.read(data)
