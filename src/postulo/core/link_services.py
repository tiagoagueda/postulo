"""Which service an address on the web is on, and what an address there looks like (#305).

A web link used to be an address and a free name, and nothing more: *the kind is the sort
of thing, never the host*, because a list of brand names in a model ages every time
somebody's Forgejo instance or the next network appears. That reason still holds, and it is
why the list is not in the model. It is a registry, supplied by plugins the way the
identifier schemes are (`core.identifiers`), and it always has an **Other**: a free name
and the web-address check alone, so nobody is refused an address because the list does not
know it.

A service says five things about itself:

- which **kind** of link it is -- a social profile, a code repository, a website -- and so
  which block of the page offers it;
- the **hosts** it answers on, or that it lives on **any host** (a Mastodon server, a
  Forgejo instance), in which case it is recognised by shape alone;
- the **pattern** an address's path has there;
- an **icon**, out of the Lucide set Postulo ships;
- an **example**, for the sentence that refuses an address.

**Nothing here touches the network**, for the reason the identifier schemes give and the
one ``WebLink`` gives: a service says what an address looks like, and never asks anybody
whether the address answers.

**The pattern reads the path, percent-decoded, and nothing else.** The host is checked
apart from it, so ``www.``, a country's subdomain (``pt.linkedin.com``) and a mobile one
(``m.facebook.com``) need no pattern of their own; the query string and the fragment are
never read, so a tracking parameter refuses nobody; and a name with an accent passes
whether the browser copied it as ``joão`` or as ``jo%C3%A3o`` (#638).

**An address is a service's only where there is nothing to disagree about.** A service's
name on a row is a promise about where the link goes, and what a link with no name is
called by is read out of its path. So three things are never a named service's, whatever
the pattern says, and each is *Other* -- or refused, where the service was chosen by hand:

- an address whose authority holds a backslash, or anything before an ``@``. Python reads
  ``https://evil.example\\@linkedin.com/in/x`` as LinkedIn's; a browser reads the backslash
  as a slash and goes to ``evil.example``. And no profile's address carries a name to sign
  in with, which is how one host is dressed as another;
- a path that holds a control character once it is decoded (``%00``, ``%1B``): none is part
  of anybody's name, and it would reach the page in what the link is called;
- an address longer than a link can be (`MAX_ADDRESS_LENGTH`).

**A pattern has to read an address once.** It is run on whatever somebody pastes, in their
request, and nothing times it out: the length of an address is the only bound there is. A
repetition inside a repetition -- ``(a+)+``, ``(?:[^/]+/?)+`` -- takes time that doubles
with every character, and five hundred of them is a worker that never comes back. Postulo's
own patterns are held to this by a test; a plugin's are its author's to keep.
"""

from __future__ import annotations

import logging
import re
from dataclasses import dataclass, field
from pathlib import Path
from urllib.parse import unquote, urlsplit

from django.core.exceptions import ValidationError
from django.utils.translation import gettext_lazy as _

from .addresses import _CONTROL

logger = logging.getLogger(__name__)

#: The three kinds of link, as ``WebLink.Kind`` stores them. Written out again here so that
#: this module needs no model: a plugin's table is read before anything touches a database.
SOCIAL = "social"
REPOSITORY = "repository"
WEBSITE = "website"
KINDS = (SOCIAL, REPOSITORY, WEBSITE)

#: What a form posts for *Other*. It is never stored: a row with no service **is** Other,
#: so a row written before services existed needs nothing done to it to stay what it was.
OTHER = "other"

#: How long a service's key may be. The column has to hold it, and the keys are short words.
MAX_KEY_LENGTH = 40

#: How long an address may be: what ``WebLink.url`` holds, and what the page, the API and
#: an archive each hold an address to. It is also the whole of what bounds the time a
#: pattern can take, so it is said again where a pattern is run (`Service._match`), and an
#: address handed over by something that did not measure it is not read at all.
MAX_ADDRESS_LENGTH = 500

_KEY = re.compile(r"[a-z0-9][a-z0-9-]*")

#: Where the icons a service may name live: the Lucide set listed in ``assets/icons.txt``.
ICON_DIR = Path(__file__).resolve().parent.parent / "static" / "icons"

#: The icon a kind's services take when one names none, or names one Postulo does not ship.
#: No brand marks, by the rule in ``TRADEMARKS.md``, which #301 decided to keep.
KIND_ICONS = {SOCIAL: "user", REPOSITORY: "git-branch", WEBSITE: "globe"}

#: The icon for *Other*, whatever the kind: an address on the web, and no more is known.
OTHER_ICON = "globe"


@dataclass(frozen=True)
class Service:
    """One place a web link can be: LinkedIn, a Mastodon server, somebody's Forgejo."""

    #: What a row stores. Lower-case letters, digits and hyphens; never ``other``.
    key: str
    #: Its name, as its owner writes it. A brand is not translated; a description is.
    label: str
    #: Which block offers it: one of `KINDS`.
    kind: str
    #: What the path of an address there looks like, matched whole against the path once it
    #: is percent-decoded. A group named ``handle`` is what the row is called by; without
    #: one it is the whole path. Compiled over text, not bytes, and written to read an
    #: address once: no repetition inside a repetition, because nothing times it out.
    pattern: re.Pattern[str]
    #: The hosts it answers on. A subdomain of one counts as that host.
    hosts: tuple[str, ...] = ()
    #: Whether it also lives on hosts nobody can list -- a federated network, software
    #: people run themselves. An address anywhere is then accepted on its shape, and
    #: ``hosts`` is only where it is recognised *without being asked*: a shape alone is
    #: never enough to guess from, or every unknown forge would be called a Forgejo.
    any_host: bool = False
    #: A Lucide icon Postulo ships (``assets/icons.txt``). Blank, or a name Postulo does
    #: not have, draws the kind's own.
    icon: str = ""
    #: An address there, for the sentence that refuses one.
    example: str = ""
    #: Who provides this service, for a page that lists them. Empty for Postulo's own.
    provider: str = field(default="")

    def __post_init__(self) -> None:
        # Whatever a plugin wrote them as -- a `LinkKind`, a list -- they are read as a
        # plain word and a tuple of lower-case hosts from here on.
        object.__setattr__(self, "kind", str(getattr(self.kind, "value", self.kind)))
        hosts = (self.hosts,) if isinstance(self.hosts, str) else tuple(self.hosts or ())
        object.__setattr__(self, "hosts", tuple(str(host).lower().strip(".") for host in hosts))

    def hosted(self, url: str) -> bool:
        """Whether ``url`` is on one of this service's own hosts, or a subdomain of one."""
        host = _host(url)
        return any(host == known or host.endswith("." + known) for known in self.hosts)

    def _match(self, url: str) -> re.Match[str] | None:
        url = url or ""
        if len(url) > MAX_ADDRESS_LENGTH:
            return None
        try:
            parts = urlsplit(url)
        except ValueError:  # a bracketed host that is not an address
            return None
        if parts.scheme.lower() not in ("http", "https") or not _host(url):
            return None
        # A browser reads a backslash in the authority as a slash, and so reads another
        # host than `urlsplit` does; and what stands before an `@` is a name to sign in
        # with, which is how one host is dressed as another. Neither is anybody's profile.
        if "\\" in parts.netloc or "@" in parts.netloc:
            return None
        if not self.any_host and not self.hosted(url):
            return None
        path = unquote(parts.path) or "/"
        if _CONTROL.search(path):
            # Nobody's name, and it would be on the page in what the link is called.
            return None
        return self.pattern.fullmatch(path)

    def accepts(self, url: str) -> bool:
        """Whether ``url`` is an address on this service: its host, and its shape."""
        return self._match(url) is not None

    def handle(self, url: str) -> str:
        """What the address is called by there: the name, the owner and the project.

        On a host of the service's own that is the handle alone. Anywhere else -- another
        Mastodon server, a GitLab somebody runs -- the host goes in front of it, because
        there the host is half of who somebody is.
        """
        match = self._match(url)
        if match is None:
            return ""
        named = match.groupdict().get("handle")
        handle = (named if named is not None else match.group(0)).strip("/")
        if self.any_host and not self.hosted(url):
            host = _host(url)
            host = host[4:] if host.startswith("www.") else host
            return f"{host}/{handle}" if handle else host
        return handle

    @property
    def icon_name(self) -> str:
        """The icon to draw: the service's own where Postulo ships it, else the kind's."""
        return self.icon if icon_exists(self.icon) else KIND_ICONS.get(self.kind, OTHER_ICON)


def _host(url: str) -> str:
    try:
        host = urlsplit(url or "").hostname or ""
    except ValueError:  # a bracketed host that is not an address
        return ""
    return host.lower().rstrip(".")


def icon_exists(name: str) -> bool:
    """Whether Postulo ships an icon of this name. A plugin names icons, and a name it got
    wrong must cost a picture, not a page."""
    return bool(name) and bool(_KEY.fullmatch(name)) and (ICON_DIR / f"{name}.svg").is_file()


# ------------------------------------------------------------------ the registry
#
# The services themselves live in plugins. Postulo owns the *table* -- `WebLink` is a core
# model, migrated by core, and a service owns no rows -- and a plugin contributes the
# vocabulary: a key, a name, a kind, hosts, a pattern, an icon. The list Postulo ships is a
# plugin like any other (`postulo.plugins.link_services`), and a package installed beside
# it adds its own through the `postulo.link_services` entry-point group.


def _usable(service) -> bool:
    """Whether something a plugin handed over can be a service at all.

    Checked here, once, rather than trusted everywhere it is read: a key that will not fit
    the column, a kind no block offers, or a service with nowhere to live would each fail
    later, on somebody's page. So would a pattern compiled over bytes, on every save of an
    address it was asked about, and an icon that is not a name, on every drawing of the
    block that offers it.

    **Whether a pattern is slow is not checked**, because it cannot be from here: see the
    module's last paragraph.
    """
    return (
        isinstance(service, Service)
        and isinstance(service.key, str)
        and bool(_KEY.fullmatch(service.key))
        and len(service.key) <= MAX_KEY_LENGTH
        and service.key != OTHER
        and service.kind in KINDS
        and isinstance(service.pattern, re.Pattern)
        and isinstance(service.pattern.pattern, str)
        and isinstance(service.icon, str)
        and (bool(service.hosts) or service.any_host)
    )


#: The plugins the registry was last read from, and what it came to. See `registry`.
_merged: tuple[list, dict[str, Service]] | None = None


def registry() -> dict[str, Service]:
    """Every service this instance knows, by key, from every installed plugin.

    **The plugins Postulo ships are read first**, so a key of theirs is theirs: a package
    installed beside them adds services and cannot quietly change what ``linkedin`` means
    on every row already stored. Between two packages the first to claim a key keeps it,
    and the second is logged and left out -- the rule document themes follow. *Shipped* is
    an object of exactly a class Postulo registered, and never of one built on it: a
    package that inherits from `LinkServices` is a package installed beside it.

    **Read once per list of plugins.** A row asks this several times -- for its choices, its
    icon, its name -- and a table of rows many times over, so the merged list is kept beside
    the very plugin objects it was read from and built again when they are not the same
    objects any more. `plugins.registry` already decides when that is (an install, a
    removal, a switch), so there is nothing here for anybody to remember to clear, and a
    service that cannot be used is logged when it is found rather than on every page.
    """
    global _merged

    from postulo.plugins import registry as plugin_registry

    installed = plugin_registry.plugins("link-service")
    if (
        _merged is not None
        and len(_merged[0]) == len(installed)
        and all(was is now for was, now in zip(_merged[0], installed, strict=True))
    ):
        return dict(_merged[1])
    shipped = tuple(plugin_registry.builtins().get("link-service", ()))
    found: dict[str, Service] = {}
    for plugin in sorted(installed, key=lambda item: type(item) not in shipped):
        try:
            offered = list(getattr(plugin, "services", ()) or ())
        except Exception:
            # Somebody else's table, read on somebody's page: one that will not be read
            # costs its own services and nobody else's.
            logger.exception("Plugin %r's link services could not be read", plugin)
            continue
        for service in offered:
            if not _usable(service):
                logger.warning(
                    "Plugin %r offered a link service that cannot be used: %r",
                    getattr(plugin, "name", plugin),
                    service,
                )
            elif service.key in found:
                logger.warning(
                    "Link service %r was offered twice; the first one keeps it", service.key
                )
            else:
                found[service.key] = service
    _merged = (installed, found)
    return dict(found)


def services_for(kind: str) -> dict[str, Service]:
    """The services a block of this kind offers, in registration order."""
    return {key: service for key, service in registry().items() if service.kind == kind}


def find(key: str, kind: str = "") -> Service | None:
    """One service by key, or nothing -- and nothing if it is not of ``kind``.

    Nothing as well for a key no installed plugin knows: a row whose service came from a
    plugin since removed reads as *Other*, and keeps the key for the day it comes back.
    """
    service = registry().get(key or "")
    if service is None or (kind and service.kind != kind):
        return None
    return service


def label_for(key: str, kind: str = "") -> str:
    """A service's name in words; *Other* for a blank key and for one nothing recognises."""
    service = find(key, kind)
    return str(service.label) if service is not None else str(_("Other"))


def icon_for(key: str, kind: str = "") -> str:
    """The icon a row draws: its service's, or the globe for *Other*."""
    service = find(key, kind)
    return service.icon_name if service is not None else OTHER_ICON


def guess(url: str, kind: str) -> Service | None:
    """The service of this kind whose own host the address is on, if it has its shape.

    By host and never by shape alone. An address on a known host that does not have the
    shape -- LinkedIn's front page, a single post -- is left as *Other*, which is what it
    was before there were services: guessing must never be how an address comes to be
    refused.
    """
    for service in services_for(kind).values():
        if service.hosted(url) and service.accepts(url):
            return service
    return None


def says_only_the_service(label: str, service: Service) -> bool:
    """Whether a name somebody typed is just the service's own: "LinkedIn", "github"."""
    typed = " ".join((label or "").split()).casefold()
    return typed in ("", str(service.label).casefold(), service.key.casefold())


def settle(kind: str, chosen: str, url: str, label: str = "") -> tuple[str, str]:
    """The service and the name a link is stored with, or `ValidationError`.

    ``chosen`` is what was asked for: a service's key, `OTHER`, or nothing at all.

    - **A named service** has to be one this kind offers, and the address has to be one of
      its addresses. The name is dropped: the service is the name.
    - **Other** takes any web address and keeps the name.
    - **Nothing chosen** is somebody pasting an address without saying what it is -- a new
      row left as it was drawn, a file, an older client. The service is picked from the
      host where one matches, so nobody has to choose LinkedIn and then paste a LinkedIn
      address. A name of its own keeps the link *Other*: a name only means something
      there, and picking a service would cost the link the name.
    """
    chosen = (chosen or "").strip()
    label = (label or "").strip()
    if chosen == OTHER:
        return "", label
    if not chosen:
        guessed = guess(url, kind)
        if guessed is None or not says_only_the_service(label, guessed):
            return "", label
        return guessed.key, ""
    service = find(chosen, kind)
    if service is None:
        raise ValidationError(
            _("That is not a service this kind of link can be on."), code="service"
        )
    if not service.accepts(url):
        raise ValidationError(refusal(service), code="address")
    return service.key, ""


def refusal(service: Service) -> str:
    """Why an address is not one of this service's, saying what one looks like."""
    if service.example:
        return _(
            "That does not look like an address on %(service)s. One looks like "
            "%(example)s; choose “Other” to list an address of another shape."
        ) % {"service": service.label, "example": service.example}
    return _(
        "That does not look like an address on %(service)s. Choose “Other” to list an "
        "address of another shape."
    ) % {"service": service.label}


def display(key: str, kind: str, url: str) -> str:
    """What a link with no name is called where its service is known: the service's name
    and the handle, "GitHub alex/thing". Empty for *Other*, which is called by its host."""
    service = find(key, kind)
    if service is None:
        return ""
    handle = service.handle(url)
    return f"{service.label} {handle}" if handle else str(service.label)
