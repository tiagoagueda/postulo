"""The widgets core owns: the ones that point somewhere rather than compute something."""

from __future__ import annotations

from django.utils.translation import gettext_lazy as _

from .widgets import Sources, Widget, register


def _nothing(sources: Sources) -> dict:
    return {}


register(
    Widget(
        key="shortcuts",
        label=_("Shortcuts"),
        blurb=_("Links to the places a job search goes back to: the board, CVs, companies."),
        template="widgets/shortcuts.html",
        context=_nothing,
        width="half",
        group=_("What needs doing"),
        default_order=70,
    )
)
