"""Which messaging service a handle is on, and what a handle there looks like (#682).

Somebody is reached on Signal, Matrix, Telegram or XMPP by a **handle**: an identifier the
service gave them, and not an address on the web. That is why this is not a fourth kind of
link (`core.link_services`, which validates a web address by host and path): ``xmpp:`` and
``matrix:`` are not web addresses, and Signal and Threema have no stable address a person
writes. It is a registry of its own, built the way that one is -- supplied by plugins, with
an **Other** that refuses nothing -- and kept apart from it.

A service says six things about itself:

- a **key**, which a row stores, and a **name** as its owner writes it;
- the **pattern** a handle has there, matched whole against the handle once it is normalised;
- a **normaliser**: what is stored for what was typed -- a leading ``@`` stripped, the case
  folded where the service folds it, a resource dropped;
- an **icon**, out of the Lucide set Postulo ships, and never a brand's mark;
- an **example**, for the sentence that refuses a handle.

**Nothing here touches the network**: a service says what a handle looks like, and never
asks anybody whether it exists.

**A pattern has to read a handle once.** It is run on whatever somebody types, in their
request, and nothing times it out: the length of a handle (`MAX_HANDLE_LENGTH`) is the only
bound there is, and a handle longer than that is not read at all. A repetition inside a
repetition -- ``(a+)+`` -- takes time that doubles with every character. Postulo's own
patterns are held to this by a test; a plugin's are its author's to keep.
"""

from __future__ import annotations

import logging
import re
import unicodedata
from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path

from django.core.exceptions import ValidationError
from django.utils.translation import gettext_lazy as _

logger = logging.getLogger(__name__)

#: What a form posts for *Other*. Never stored: a row with no service **is** Other.
OTHER = "other"

#: How long a service's key may be. The column has to hold it.
MAX_KEY_LENGTH = 40

#: How long a handle may be: what `MessagingHandle.handle` holds, and the whole of what
#: bounds the time a pattern can take. Matrix's identifiers are 255 bytes at most.
MAX_HANDLE_LENGTH = 255

#: How long the name under *Other* may be.
MAX_LABEL_LENGTH = 60

_KEY = re.compile(r"[a-z0-9][a-z0-9-]*")

#: Where the icons a service may name live: the Lucide set listed in ``assets/icons.txt``.
ICON_DIR = Path(__file__).resolve().parent.parent / "static" / "icons"

#: The icon every service takes when it names none, or names one Postulo does not ship.
DEFAULT_ICON = "at-sign"

#: The icon for *Other*.
OTHER_ICON = "at-sign"

_CONTROL = re.compile(r"[\x00-\x1f\x7f-\x9f]")


def tidy(text: str) -> str:
    """What every normaliser starts from: surrounding space gone, NFKC, one line."""
    return unicodedata.normalize("NFKC", " ".join((text or "").split()))


def strip_at(text: str) -> str:
    """A leading ``@`` is how a handle is written in running text and is not part of it."""
    text = tidy(text)
    return text[1:] if text.startswith("@") else text


def comparable(text: str) -> str:
    """A handle as two spellings of it agree: NFKC and case-folded."""
    return unicodedata.normalize("NFKC", text or "").casefold()


@dataclass(frozen=True)
class Service:
    """One place somebody can be reached: Matrix, Signal, an instance's own chat."""

    #: What a row stores. Lower-case letters, digits and hyphens; never ``other``.
    key: str
    #: Its name, as its owner writes it. A brand is not translated.
    label: str
    #: What a handle there looks like, matched whole against the normalised handle.
    #: Compiled over text, not bytes, and written to read a handle once: no repetition
    #: inside a repetition, because nothing times it out.
    pattern: re.Pattern[str]
    #: The stored form of what was typed. Defaults to `strip_at`.
    normaliser: Callable[[str], str] = strip_at
    #: A handle there, for the sentence that refuses one.
    example: str = ""
    #: A Lucide icon Postulo ships (``assets/icons.txt``). Blank, or a name Postulo does
    #: not have, draws the generic one.
    icon: str = ""
    #: Who provides this service, for a page that lists them. Empty for Postulo's own.
    provider: str = field(default="")

    def normalise(self, text: str) -> str:
        """The form a handle is stored in, or an empty string for something unreadable."""
        text = text or ""
        if len(text) > MAX_HANDLE_LENGTH * 4:
            return ""
        try:
            return str(self.normaliser(text))[: MAX_HANDLE_LENGTH + 1]
        except Exception:
            # Somebody else's function, run on somebody's request.
            logger.exception("Messaging service %r could not normalise a handle", self.key)
            return ""

    def accepts(self, handle: str) -> bool:
        """Whether ``handle`` -- already normalised -- is one on this service."""
        handle = handle or ""
        if not handle or len(handle) > MAX_HANDLE_LENGTH or _CONTROL.search(handle):
            return False
        return self.pattern.fullmatch(handle) is not None

    @property
    def icon_name(self) -> str:
        """The icon to draw: the service's own where Postulo ships it, else the generic."""
        return self.icon if icon_exists(self.icon) else DEFAULT_ICON


def icon_exists(name: str) -> bool:
    """Whether Postulo ships an icon of this name. A plugin names icons, and a name it
    got wrong must cost a picture, not a page."""
    return bool(name) and bool(_KEY.fullmatch(name)) and (ICON_DIR / f"{name}.svg").is_file()


# ------------------------------------------------------------------ the registry
#
# The services themselves live in plugins. Postulo owns the *table* -- `MessagingHandle`
# is a core model, migrated by core, and a service owns no rows -- and a plugin contributes
# the vocabulary. The list Postulo ships is a plugin like any other
# (`postulo.plugins.messaging_services`), and a package installed beside it adds its own
# through the `postulo.messaging_services` entry-point group.


def _usable(service) -> bool:
    """Whether something a plugin handed over can be a service at all.

    Checked once, here, rather than trusted everywhere it is read: a key that will not fit
    the column, a pattern compiled over bytes or a normaliser that cannot be called would
    each fail later, on somebody's page. **Whether a pattern is slow is not checked**,
    because it cannot be from here: see the module's last paragraph.
    """
    return (
        isinstance(service, Service)
        and isinstance(service.key, str)
        and bool(_KEY.fullmatch(service.key))
        and len(service.key) <= MAX_KEY_LENGTH
        and service.key != OTHER
        and isinstance(service.pattern, re.Pattern)
        and isinstance(service.pattern.pattern, str)
        and isinstance(service.icon, str)
        and callable(service.normaliser)
    )


_merged: tuple[list, dict[str, Service]] | None = None


def registry() -> dict[str, Service]:
    """Every service this instance knows, by key, from every installed plugin.

    **The plugins Postulo ships are read first**, so a key of theirs is theirs: a package
    installed beside them adds services and cannot quietly change what ``matrix`` means on
    every row already stored. Between two packages the first to claim a key keeps it, and
    the second is logged and left out. *Shipped* is an object of exactly a class Postulo
    registered, and never of one built on it.

    Read once per list of plugins, and built again when the plugin objects are not the
    same ones any more; `plugins.registry` decides when that is.
    """
    global _merged

    from postulo.plugins import registry as plugin_registry

    installed = plugin_registry.plugins("messaging-service")
    if (
        _merged is not None
        and len(_merged[0]) == len(installed)
        and all(was is now for was, now in zip(_merged[0], installed, strict=True))
    ):
        return dict(_merged[1])
    shipped = tuple(plugin_registry.builtins().get("messaging-service", ()))
    found: dict[str, Service] = {}
    for plugin in sorted(installed, key=lambda item: type(item) not in shipped):
        try:
            offered = list(getattr(plugin, "services", ()) or ())
        except Exception:
            logger.exception("Plugin %r's messaging services could not be read", plugin)
            continue
        for service in offered:
            if not _usable(service):
                logger.warning(
                    "Plugin %r offered a messaging service that cannot be used: %r",
                    getattr(plugin, "name", plugin),
                    service,
                )
            elif service.key in found:
                logger.warning(
                    "Messaging service %r was offered twice; the first one keeps it", service.key
                )
            else:
                found[service.key] = service
    _merged = (installed, found)
    return dict(found)


def find(key: str) -> Service | None:
    """One service by key, or nothing for *Other* and for a key no installed plugin knows:
    a row whose service came from a plugin since removed reads as *Other*, and keeps the
    key for the day it comes back."""
    return registry().get(key or "")


def label_for(key: str) -> str:
    """A service's name in words; *Other* for a blank key and for one nothing recognises."""
    service = find(key)
    return str(service.label) if service is not None else str(_("Other"))


def icon_for(key: str) -> str:
    """The icon a row draws: its service's, or the generic one for *Other*."""
    service = find(key)
    return service.icon_name if service is not None else OTHER_ICON


def refusal(service: Service) -> str:
    """Why a handle is not one of this service's, saying what one looks like."""
    if service.example:
        return _(
            "That does not look like a handle on %(service)s. One looks like "
            "%(example)s; choose “Other” to list a handle of another shape."
        ) % {"service": service.label, "example": service.example}
    return _(
        "That does not look like a handle on %(service)s. Choose “Other” to list a handle "
        "of another shape."
    ) % {"service": service.label}


def settle(chosen: str, handle: str, label: str = "") -> tuple[str, str, str]:
    """The service, the name and the handle a row is stored with, or `ValidationError`.

    ``chosen`` is what was asked for: a service's key, `OTHER`, or nothing at all.

    - **A named service** has to be one this instance offers, and the handle has to be one
      of its handles, once normalised. The name is dropped: the service is the name.
    - **Other** takes a name and any text up to `MAX_HANDLE_LENGTH` characters.
    - **Nothing chosen** is *Other*: a handle is not recognised by its shape alone, so no
      service is ever guessed for one.
    """
    chosen = (chosen or "").strip()
    label = " ".join((label or "").split())
    handle = tidy(handle)
    if not handle:
        raise ValidationError(_("A handle is needed."), code="handle")
    if chosen and chosen != OTHER:
        service = find(chosen)
        if service is None:
            raise ValidationError(_("That is not a service a handle can be on."), code="service")
        normal = service.normalise(handle)
        if not service.accepts(normal):
            raise ValidationError(refusal(service), code="handle")
        return service.key, "", normal
    if not label:
        raise ValidationError(
            _("Name the service this handle is on, or choose one from the list."), code="label"
        )
    if len(label) > MAX_LABEL_LENGTH:
        raise ValidationError(
            _("A name is at most %(max)s characters.") % {"max": MAX_LABEL_LENGTH},
            code="label",
        )
    if len(handle) > MAX_HANDLE_LENGTH or _CONTROL.search(handle):
        raise ValidationError(
            _("A handle is at most %(max)s characters, on one line.") % {"max": MAX_HANDLE_LENGTH},
            code="handle",
        )
    return "", label, handle
