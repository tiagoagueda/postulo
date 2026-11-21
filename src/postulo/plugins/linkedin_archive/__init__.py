"""The career half of a LinkedIn data archive, read in (#1260).

LinkedIn gives every member a copy of their own data (*Settings & Privacy → Data privacy →
Get a copy of your data*): a ZIP of CSV files. This reads the career's files out of it --
or one of them uploaded on its own -- into a career record, shown on the review before
anything is written. Nothing is fetched from LinkedIn and no page is read: it is the
person's own file, from the service's own export.

What it never does: unpack the archive to disk, follow a member's path, or open a member it
does not know by name. Messages, connections, invitations and the rest are named on the
review and not opened (`archive`). Bundled, standard library only, on by default as Europass
is: it contacts nothing and does nothing until somebody chooses a file. No logo: the name is
LinkedIn's mark, so the label says the format in words.
"""

from __future__ import annotations

from django.utils.translation import gettext_lazy as _
from django.utils.translation import ngettext

from postulo.plugins.api import ImportRefused, declares, refuse_unreadable, shipped

from . import archive, career, sheets

#: How many unopened files the review names before it gives a count of the rest.
NAMED_AT_MOST = 40

#: What each reason a known file was not read is, in words.
REFUSED = {
    archive.TOO_LARGE: _("%(name)s is larger than 2 MB unpacked, so it was not read."),
    archive.ENCRYPTED: _("%(name)s is encrypted, so it was not read."),
    archive.DAMAGED: _("%(name)s could not be unpacked, so it was not read."),
    archive.TWICE: _("%(name)s is in the archive twice; only the first was read."),
}
UNREADABLE = {
    "encoding": _("%(name)s is not UTF-8 text, so it was not read."),
    "csv": _("%(name)s is not a readable CSV file, so it was not read."),
    "header": _(
        "%(name)s does not start with the columns Postulo knows for it, so it was not read: "
        "LinkedIn may have renamed them."
    ),
}


def _loose_kind(data: bytes) -> str | None:
    """Which career file a CSV uploaded on its own is, by its header row; else ``None``."""
    if data.lstrip(b"\xef\xbb\xbf").lstrip()[:1] in (b"{", b"<", b"%"):
        return None
    # The first line only, so a character cut at the end of the slice does not matter.
    first = data[:8192].decode("utf-8-sig", errors="ignore").splitlines()[:1]
    if not first:
        return None
    import csv

    try:
        row = next(csv.reader(first))
    except (csv.Error, StopIteration):
        return None
    for key in career.LOOSE:
        kind = career.KINDS[key]
        if sheets.header_matches(row, kind.columns, kind.required) and len(row) >= 3:
            return key
    return None


@declares(
    shipped(
        name="linkedin-archive",
        label=_("LinkedIn data archive import"),
        kind="importer",
        parent="file-formats",
        categories=("files",),
        icon="file-up",
        description=_(
            "The career in the copy of your data LinkedIn sends you: the ZIP as it came, or "
            "one of its CSV files. Ask LinkedIn for the categories you want (profile, "
            "positions, education, skills and so on) rather than everything: the archive "
            "arrives in minutes and stays under the 5 MB an import may be. Messages, "
            "connections and the rest are named and never opened."
        ),
    )
)
class LinkedInArchiveImporter:
    """Reads the career files of a LinkedIn data archive, and nothing else in it."""

    #: What the import page's file box offers to choose from.
    accepts = (".zip", ".csv", "application/zip", "text/csv")

    def can_handle(self, data: bytes, filename: str = "") -> bool:
        """A ZIP with ``Profile.csv`` or ``Positions.csv`` in it, or one career CSV."""
        if archive.is_zip(data):
            names = archive.names_in(data)
            if names is None:
                return False
            return bool({"profile.csv", "positions.csv"} & names) and not (
                archive.NOT_AN_EXPORT & names
            )
        return _loose_kind(data) is not None

    def too_large(self, filename: str = "") -> str:
        """What to do about an archive over the import's 5 MB, said with the refusal (D4).

        Only for a ZIP, or a file named as LinkedIn names its archive: a PDF that is too big
        has nothing to do with LinkedIn.
        """
        name = (filename or "").lower()
        if not (name.endswith(".zip") or "linkedin" in name):
            return ""
        return str(
            _(
                "A LinkedIn archive with everything in it is often that large. Ask LinkedIn "
                "for a copy of only the categories you want here (profile, positions, "
                "education, skills, languages, certifications, projects, honours, courses, "
                "publications, volunteering), which also arrives in minutes."
            )
        )

    def read(self, data: bytes):
        from postulo.plugins.api import Record

        refuse_unreadable(data)
        record = Record(source="linkedin-archive")
        reading = career.Reading(record)
        if archive.is_zip(data):
            try:
                opened = archive.open_known(data, frozenset(career.KINDS))
            except Exception as error:
                raise ImportRefused(
                    _(
                        "That ZIP file could not be opened, so it was not read. Download the "
                        "archive from LinkedIn again and upload it as it came."
                    )
                ) from error
        else:
            key = _loose_kind(data)
            if key is None:
                raise ImportRefused(
                    _("That CSV file is not one of the career files of a LinkedIn archive.")
                )
            opened = archive.Opened(members={key: data}, names={key: career.KINDS[key].name})

        for name, reason in opened.refused:
            record.skipped.append(str(REFUSED[reason] % {"name": name}))
        read = []
        for key in career.KINDS:
            if key not in opened.members:
                continue
            kind = career.KINDS[key]
            try:
                sheet = sheets.read(opened.members[key], kind.columns, kind.required)
            except sheets.Unreadable as error:
                record.skipped.append(str(UNREADABLE[str(error)] % {"name": opened.names[key]}))
                continue
            read.append(opened.names[key])
            if sheet.unknown:
                record.skipped.append(
                    str(
                        _(
                            "%(name)s has columns Postulo does not know, which were not read: "
                            "%(columns)s."
                        )
                        % {"name": opened.names[key], "columns": ", ".join(sheet.unknown[:20])}
                    )
                )
            if sheet.cut_short:
                record.skipped.append(
                    str(
                        ngettext(
                            "%(name)s has more than %(count)s row; only the first was read.",
                            "%(name)s has more than %(count)s rows; only the first were read.",
                            sheets.MAX_ROWS,
                        )
                        % {"name": opened.names[key], "count": sheets.MAX_ROWS}
                    )
                )
            career.read_sheet(reading, key, sheet)
        _say(record, reading, opened, read)
        return record


def _say(record, reading: career.Reading, opened: archive.Opened, read: list[str]) -> None:
    """The sentences of the review: what was read, and everything that was not."""
    record.source_label = str(
        ngettext(
            "Read as a LinkedIn data archive: %(count)s file was read, %(files)s.",
            "Read as a LinkedIn data archive: %(count)s files were read, %(files)s.",
            len(read),
        )
        % {"count": len(read), "files": ", ".join(read)}
        if read
        else _("Read as a LinkedIn data archive, and none of its career files was readable.")
    )
    if record.memberships and any(
        name.lower() in ("volunteering.csv", "organizations.csv") for name in read
    ):
        # Postulo has no section of its own for volunteering (#1260, decision D5).
        record.notes.append(
            str(
                _(
                    "Volunteering and organisations were read as memberships, since a career "
                    "record in Postulo has no section of its own for volunteering."
                )
            )
        )
    for language in record.languages:
        if language["stated"]:
            record.notes.append(
                str(
                    _(
                        "LinkedIn's levels for a language are not CEFR levels, so no level is "
                        "claimed for one except native: the phrase is shown beside it."
                    )
                )
            )
            break
    for file, columns in reading.unmapped.items():
        record.skipped.append(
            str(
                _(
                    "%(name)s: these have no place in a career record in Postulo and were not "
                    "read: %(columns)s."
                )
                % {"name": file, "columns": ", ".join(columns)}
            )
        )
    if reading.bad_dates:
        record.skipped.append(
            str(
                _("These dates are not ones Postulo reads, so they were left out: %(dates)s.")
                % {"dates": "; ".join(reading.bad_dates[:20])}
            )
        )
    if any(name.lower() == "connections.csv" for name in opened.unopened):
        # Other people's names, employers and addresses: never opened, not even to count
        # them, since a count needs every row (decision D3).
        record.skipped.append(
            str(
                _(
                    "Connections.csv holds other people's details, so it was not opened. "
                    "Add the people you need as contacts yourself."
                )
            )
        )
    unopened = [name for name in opened.unopened if name.lower() != "connections.csv"]
    if unopened:
        named = ", ".join(unopened[:NAMED_AT_MOST])
        rest = len(unopened) - NAMED_AT_MOST
        record.skipped.append(
            str(
                _("These files were in the archive and were not read: %(names)s.")
                % {"names": named}
                if rest <= 0
                else ngettext(
                    "These files were in the archive and were not read: %(names)s, and %(count)s "
                    "more.",
                    "These files were in the archive and were not read: %(names)s, and %(count)s "
                    "more.",
                    rest,
                )
                % {"names": named, "count": rest}
            )
        )
    if opened.cut_short:
        record.skipped.append(
            str(
                ngettext(
                    "The archive holds more than %(count)s file; only the first was looked at.",
                    "The archive holds more than %(count)s files; only the first were looked at.",
                    archive.MAX_ENTRIES,
                )
                % {"count": archive.MAX_ENTRIES}
            )
        )
