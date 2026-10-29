"""Which paper a document is drawn on: the instance's, the person's, or one export's (#344).

The vocabulary is `paper`, a module of plain values the records read their columns' choices
from; this is the part that reads the records, and so sits above them. `paper` says why the
chain is the way it is.
"""

from __future__ import annotations

from django.utils.translation import gettext_lazy as _

from postulo.core import site

from .paper import DEFAULT, Paper, find, offered, restriction
from .themes import CannotRender


def instance_default() -> Paper:
    """What the administrator chose under *Server settings → Defaults*, or A4 portrait."""
    return find(site.current().default_paper) or DEFAULT


def personal_default(owner) -> Paper:
    """What this person chose under *Settings → Export*, or the instance's."""
    profile = getattr(owner, "profile", None) if owner is not None else None
    return find(getattr(profile, "paper", "")) or instance_default()


def for_file(owner, asked: str = "") -> Paper:
    """The paper of a file no theme sets -- a Word or an OpenDocument file.

    Any paper Postulo has, since nothing in a file of words is drawn for one; nothing asked,
    or something that is not a paper, is the person's default.
    """
    return find(asked) or personal_default(owner)


def for_document(theme_name: str, kind: str, owner, asked: str = "") -> Paper:
    """The paper a document set in ``theme_name`` is drawn on. Raises `CannotRender`.

    ``asked`` is what one export chose. A paper the theme does not print on is refused,
    since somebody picked it; nothing asked is the person's default, then the instance's,
    as far as the theme prints on it (see `paper`).
    """
    papers = offered(theme_name, kind)
    if asked:
        wanted = find(asked)
        if wanted is None or wanted not in papers:
            raise CannotRender(
                restriction(theme_name, kind)
                or str(_("Postulo does not print on that paper. Choose another."))
            )
        return wanted
    wished = personal_default(owner)
    if wished in papers:
        return wished
    return DEFAULT if DEFAULT in papers else papers[0]
