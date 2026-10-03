"""The icon an option of a select draws beside its words (#301).

An ``<option>`` holds words and nothing else, in every browser, so a native select cannot
draw a picture in its list. Since #301 every select has a control of its own, built beside
it by ``app.js``, whose options are elements; what each of them draws is said by the
server, as data on the ``<option>``:

- ``data-flag`` is the address of a flag, for a language or a country. Static files are
  served under a content hash, so there is no pattern a script could build one from.
- ``data-icon`` is the name of an icon out of the Lucide set Postulo ships, for a kind: a
  telephone number's, a postal address's, an identifier's, the service a web link is on,
  an application's status. A generic icon, never somebody's mark (``TRADEMARKS.md``).

An icon is an inline ``<svg>``, drawn in the colour of the words beside it, so unlike a
flag it has no address to point an image at; and a script may not build one from a string,
by the same rule that keeps every other script-built string off the page. So the server
draws each icon a list can show, once, into a ``<template data-option-icons>`` beside the
select, with the ``{% icon %}`` tag that draws every other icon, and the script copies the
one an option names. With the script blocked the template is inert and the select is the
native one, without pictures, as it was.

`OptionIcons` is the widget's half: it puts ``data-icon`` on the options and the template
after the select. A select written out in a template says ``data-icon`` on its own options
and draws the template once for the page with ``{% option_icons %}``, which is what the
board does for two hundred cards that share one set of statuses.
"""

from __future__ import annotations

import re
from collections.abc import Iterable, Mapping
from pathlib import Path

from django import forms

#: Where `npm run sync:icons` puts the Lucide icons listed in ``assets/icons.txt``.
ICON_DIR = Path(__file__).resolve().parents[1] / "static" / "icons"

_NAME = re.compile(r"[a-z0-9][a-z0-9-]*")


def shipped(name: str) -> bool:
    """Whether Postulo ships an icon of this name.

    A plugin names icons -- a link service does, an identifier scheme does -- and a name it
    got wrong must cost a picture, not a page: ``{% icon %}`` raises on a name it does not
    have, which is right for a template somebody wrote and wrong for data.
    """
    return bool(name) and bool(_NAME.fullmatch(name)) and (ICON_DIR / f"{name}.svg").is_file()


def distinct(names: Iterable[str]) -> list[str]:
    """The icons to draw for a list: each one once, in the order first asked for, and only
    the ones Postulo ships."""
    return [name for name in dict.fromkeys(names) if shipped(name)]


class OptionIcons:
    """Mixin for a `forms.Select` whose options each name an icon.

    A subclass says which icon goes with a value in `option_icon`, or writes ``data-icon``
    on the option itself in ``create_option``; either way the widget is drawn with the
    icons it names in a ``<template>`` after it. An option whose icon Postulo does not
    ship carries none.
    """

    template_name = "partials/select_with_icons.html"

    def option_icon(self, value: str) -> str:
        """The icon for the option of this value, or nothing."""
        return ""

    def get_context(self, name, value, attrs):
        context = super().get_context(name, value, attrs)
        named: list[str] = []
        for _group, options, _index in context["widget"]["optgroups"]:
            for option in options:
                held = option["value"]
                written = option["attrs"].get("data-icon")
                icon = written or self.option_icon("" if held is None else str(held))
                if not icon or not shipped(icon):
                    option["attrs"].pop("data-icon", None)
                    continue
                if not written:
                    # Before `selected`: what an option is, then the state it is in. One
                    # the widget wrote itself stays where it wrote it.
                    option["attrs"] = {"data-icon": icon, **option["attrs"]}
                named.append(icon)
        context["widget"]["option_icons"] = distinct(named)
        return context


class IconSelect(OptionIcons, forms.Select):
    """A select whose options draw an icon each, from a table of value to icon name."""

    def __init__(self, attrs=None, choices=(), *, icons: Mapping[str, str] | None = None):
        super().__init__(attrs, choices)
        self.icons = dict(icons or {})

    def option_icon(self, value: str) -> str:
        return self.icons.get(value, "")
