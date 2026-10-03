"""What every kind of external identifier knows about itself, including who it identifies.

A company has a Wikidata item or a legal-entity identifier; a person has an ORCID. The
machinery is the same for both: tidy what somebody pasted, say whether the result is
well-formed, and know where it links.

**The sentence this module used to open with was wrong in an interesting way.** It said the
things being identified have nothing in common, and kept the two registries in two files
that never had to agree. Three schemes identify people *and* organisations — ISNI says so
in its own definition, Wikidata has items for both, LinkedIn has profiles and company pages
— and the split had quietly picked a side for each. A researcher could not record their
Wikidata item; a university could not record its ISNI, which is the identifier an EU
application form asks an institution for (#109).

So a scheme now says **which subjects it identifies**, and three of them say both. That is
what makes "no company identifier appears on a person" a guarantee rather than a
convention: it used to rest on which module a form imported its choices from, and a
convention holds until somebody wires a form up differently. A subject that does not match
is refused at the model, by every route in.

Nothing here touches the network. The person typing an identifier knows what they typed,
and looking it up somewhere else is a deliberate act for another day. A plugin contributing
a scheme is not the loophole: a scheme says what a value should look like and where it
links, and that is the whole of what it may do.

**An instance adds schemes of its own (#311).** An administrator writes them, in JSON, on
*Server settings → Plugins*, and they join the registry after the ones Postulo ships: a
staff number, a national register Postulo has no scheme for. They are kept on the policy
row, which is Postulo's to store, and read by the registry plugin, which is handed the text
and touches no table. A key of Postulo's own is never theirs to redefine, and a row stored
under a scheme somebody has since deleted keeps its key and its value, shown as they are,
until the scheme is defined again. So does a row whose scheme was changed under it: a
stored row is never refused or lost by being looked at, it is marked where it is shown and
answers the day it is changed (`KeepsItsScheme`).
"""

from __future__ import annotations

import logging
import re
from collections.abc import Callable, Iterable, Mapping
from contextlib import contextmanager
from contextvars import ContextVar
from dataclasses import dataclass, field
from urllib.parse import quote, urlsplit

from django import forms
from django.core.exceptions import ValidationError
from django.db import models
from django.utils.deconstruct import deconstructible
from django.utils.functional import lazy
from django.utils.translation import gettext_lazy as _

from . import languages
from .addresses import _CONTROL, web_address
from .option_icons import OptionIcons

logger = logging.getLogger(__name__)

#: The two things an identifier can be *of*. Named rather than left as loose strings,
#: because every form, model and registry lookup keys on them.
PERSON = "person"
COMPANY = "company"
SUBJECTS = (PERSON, COMPANY)

#: The one kind a person or a company may hold more than once: a free slot with a name of
#: its own. The constraints on both identifier tables make the same exception.
OTHER = "other"

#: How long an identifier may be: what the ``value`` column of both tables holds. It is
#: also the whole of what a value checked against an instance's own pattern is bounded by,
#: so it is said again where such a pattern is run (`refuse_malformed`).
MAX_VALUE_LENGTH = 100

#: How long a link template may be, which is as long as a web link may (#305).
MAX_LINK_LENGTH = 500

#: Who provides a scheme an administrator defined on this instance (`Scheme.provider`).
DEFINED_HERE = "instance"

#: The icon a scheme draws when it names none, or names one Postulo does not ship: a card
#: with a name on it, which is what every identifier is.
ICON = "id-card"


@dataclass(frozen=True)
class Scheme:
    key: str
    label: str
    #: What a well-formed value looks like, applied after normalisation.
    pattern: re.Pattern[str]
    #: Which of `SUBJECTS` this scheme can identify. The matrix, and the reason there is
    #: one: ISNI identifies contributors *and* organisations, and a registry that could not
    #: say so had to pick one and lose the other.
    subjects: frozenset[str] = frozenset(SUBJECTS)
    #: Where the value links, with ``{value}`` standing for it; empty when there is nowhere.
    #: This is the address for an organisation, and for every scheme whose one address
    #: serves both.
    link: str = ""
    #: Where it links for a *person*, where that is somewhere else. A LinkedIn company page
    #: and a personal profile are different addresses for the same scheme, which is exactly
    #: the sort of thing one registry has to be able to say and two could not (#109).
    person_link: str = ""
    #: Shown beside the input.
    example: str = ""
    #: Path prefixes that identify a pasted URL of this scheme, so the slug can be lifted.
    url_paths: tuple[str, ...] = ()
    #: The hosts those URLs live on; an address elsewhere is not an identifier of this kind.
    hosts: tuple[str, ...] = ()
    #: How many path segments make up the value. OpenCorporates keeps its jurisdiction and
    #: its number either side of a slash, and both are the identifier.
    segments: int = 1
    #: Whether letters are folded to upper case, or to lower.
    upper: bool = False
    lower: bool = False
    #: What this scheme does to a value beyond the folding above — regrouping an ORCID's
    #: digits, separating a register's country from its number. Runs after the URL is
    #: lifted and the case is folded, so it sees something close to canonical already.
    tidy: Callable[[str], str] | None = None
    #: Whether the value passes its own check digits, where it has some. A scheme with a
    #: checksum never needs a lookup to catch a typo, which is the entire reason it has one.
    checksum: Callable[[str], bool] | None = None
    #: What to say when the checksum fails, in this scheme's own terms.
    checksum_message: object = ""
    #: Who provides this scheme, for a page that lists them. Empty for Postulo's own, and
    #: `DEFINED_HERE` for one an administrator defined on this instance (#311).
    provider: str = field(default="")
    #: Whether the value is percent-encoded where the link is built. Every scheme an
    #: instance defines is: its pattern is somebody's own, and whatever it lets through --
    #: a slash, a question mark, an ``@`` -- must stay inside the part of the address the
    #: template put it in, and never choose the host or the page (#311).
    quoted: bool = False
    #: The longest value the pattern is ever run on; nothing, for a pattern that was
    #: reviewed with the code. An instance's own carries `MAX_VALUE_LENGTH`.
    max_length: int = 0

    #: A Lucide icon Postulo ships (``assets/icons.txt``), drawn beside the scheme in a
    #: row's choice of kind (#301). Generic, never the scheme's own mark
    #: (``TRADEMARKS.md``). Blank, or a name Postulo does not have, draws `ICON`.
    icon: str = field(default="")

    @property
    def icon_name(self) -> str:
        """The icon to draw: the scheme's own where Postulo ships it, else the generic one."""
        from .option_icons import shipped

        return self.icon if shipped(self.icon) else ICON

    def identifies(self, subject: str) -> bool:
        return subject in self.subjects

    def url_for(self, value: str, subject: str = "") -> str:
        template = self.person_link if (subject == PERSON and self.person_link) else self.link
        if not template:
            return ""
        if self.quoted:
            written = quote(value, safe="")
            if _steps_out(template, written):
                return ""
            # Replaced, not formatted: `str.format` reads `{value.real}` as an attribute,
            # and a template is not a place for a value to be asked questions.
            return template.replace("{value}", written)
        return template.format(value=value)

    def normalise(self, raw: str) -> str:
        """Tidy ``raw`` into this scheme's canonical spelling.

        Pasting is the common case, and for most of these it is the whole address — so the
        value is lifted out of a URL of this scheme's own host, folded, then handed to
        whatever the scheme itself does about spacing and grouping.
        """
        value = (raw or "").strip()
        lifted = value_from_url(value, self, segments=self.segments)
        if lifted is not None:
            value = lifted
        if self.lower:
            value = value.lower()
        if self.upper:
            value = value.upper()
        return self.tidy(value) if self.tidy else value


def _steps_out(template: str, written: str) -> bool:
    """Whether a value, put where the template has it, makes a step of the path ``.`` or
    ``..``.

    Percent-encoding keeps a slash in a value a character of the value, and does nothing
    about a full stop, which needs no encoding: a value of ``..`` in
    ``https://example.org/staff/{value}/card`` is an address every browser reads as
    ``https://example.org/card``. The same host, and not the page the template names; so
    such a value has no link, as a value with no scheme has none. Only the path is looked
    at -- after a ``?`` or a ``#`` a full stop is a full stop -- and a step written
    ``%2e`` is one too, to a browser.
    """
    path = re.split(r"[?#]", template, maxsplit=1)[0]
    return any(
        "{value}" in step
        and step.replace("{value}", written).lower().replace("%2e", ".") in (".", "..")
        for step in path.split("/")
    )


def hosted_by(url: str, scheme: Scheme) -> bool:
    """Whether ``url`` is on one of the scheme's own hosts.

    Checked before anything is lifted out of a pasted address, so a link on somebody
    else's site cannot be read as an identifier of this kind.
    """
    host = (urlsplit(url).hostname or "").lower()
    return any(host == known or host.endswith("." + known) for known in scheme.hosts)


def value_from_url(url: str, scheme: Scheme, *, segments: int = 1) -> str | None:
    """The identifier inside a pasted URL, or nothing if it is not one of the scheme's."""
    if "://" not in url or not scheme.url_paths or not hosted_by(url, scheme):
        return None
    parts = urlsplit(url)
    # GLEIF keeps the record in the fragment; everyone else in the path.
    haystack = parts.path + ("#" + parts.fragment if parts.fragment else "")
    for prefix in scheme.url_paths:
        if prefix in haystack:
            tail = haystack.split(prefix, 1)[1].strip("/")
            return "/".join(tail.split("/")[:segments])
    return None


# ------------------------------------------------------------------ the registry
#
# The schemes themselves live in a plugin. Postulo owns the *tables* -- `PersonIdentifier`
# and `CompanyIdentifier` are core models, migrated by core -- and a plugin contributes the
# registry: a key, a label, a pattern, a checksum, a link and the subjects it applies to. A
# scheme owns no rows, which is exactly why this one can be a plugin where contact details
# could not (#109).
#
# The itch behind it is `register`: one generic scheme for every national company register
# -- SIRET, NIF, Companies House, KvK, Handelsregister -- each with its own format and its
# own checksum, none of which Postulo validates. A plugin per country could.


def shipped() -> dict[str, Scheme]:
    """The schemes the installed identifier plugins bring: Postulo's own.

    What is asked where the database must not be: when a module is imported, and inside a
    migration (`shipped_only`). The companies table builds its columns from these, at
    import, which is why a scheme an instance defines has no column there.

    Not cached here: `plugins.registry` already caches the plugin list, and a dictionary
    rebuilt from a handful of objects is cheaper than a cache somebody has to remember to
    clear when a plugin is switched on.
    """
    from postulo.plugins.registry import plugins

    found: dict[str, Scheme] = {}
    for plugin in plugins("identifier"):
        for scheme in getattr(plugin, "schemes", ()):
            found.setdefault(scheme.key, scheme)
    return found


def registry() -> dict[str, Scheme]:
    """Every scheme this instance knows: Postulo's own, then the ones it defined itself.

    In that order, and a key Postulo ships is never replaced: the definitions are refused a
    key of Postulo's when they are saved, and one that got past that -- a row written by
    hand, an archive from elsewhere -- still loses here.
    """
    found = shipped()
    for scheme in defined_here():
        found.setdefault(scheme.key, scheme)
    return found


# -------------------------------------------------- the schemes an instance defines (#311)
#
# The text is the policy row's (`SiteSettings.identifier_schemes`), and reading it is the
# registry plugin's: a plugin holds no rows, so Postulo hands it the text and is handed back
# the schemes that can be used and what is wrong with the rest.
#
# **Read once per text, in each process.** The registry is asked on most pages, several
# times a row. The policy row is already read once a request and memoised for it (#231), so
# the text costs no query of its own, and what it comes to is kept beside the very text it
# was read from: whoever's row holds other text reads it again, and nothing else does. The
# text is the key, so nothing has to be cleared -- but the *row* has to be read again for a
# change to be seen, and that is somebody's job in each kind of process. A web worker reads
# it at the start of every request (the middleware). The scheduler has no request and reads
# it at the top of every pass. The task worker has none either, and for a while read it
# once in its life: a CV rendered there kept the name a scheme had when the worker started,
# into the frozen copy of a sent document. It reads it at the top of every errand now
# (`core.errands.perform`). So a change made on the page is in force on the next request,
# the next pass and the next errand.
#
# **And read the same way wherever it came from.** What the page refuses is refused here
# too: the registry is built from what this reading accepts and from nothing else, so a
# row restored from a backup, or written past the form, is held to the same rules about
# patterns and keys as one typed into the page, and a scheme that breaks them is left out
# and logged.


@dataclass(frozen=True)
class Reading:
    """What an instance's own definitions come to.

    ``schemes`` are the ones that can be used, in the order they were written. ``problems``
    is everything wrong with the rest, each of which reads as a sentence and carries the
    ``line`` it was found on.
    """

    schemes: tuple = ()
    problems: tuple = ()


#: What answers with the policy row of the moment: `core.site.current`, once the
#: application has started. Handed over (`kept_on`) rather than imported, because the row's
#: module reaches the accounts' tables, and those are built from this module: an import
#: here, even one inside a function, would close that circle (#248). Until it is handed
#: over there is no row to ask, and the registry is Postulo's own schemes.
_policy_row: Callable[[], object] | None = None

#: Set while only Postulo's own schemes are to be asked. See `shipped_only`.
_shipped_only: ContextVar[bool] = ContextVar(
    "postulo_identifier_schemes_shipped_only", default=False
)

#: What the definitions were last read from in this process -- the text, and the keys that
#: were not the instance's to define when it was read -- and what that came to: the schemes
#: and the problems both, so that the page which shows the problems reads nothing twice.
_read: tuple[tuple[str, tuple[str, ...]], Reading] | None = None


def kept_on(policy_row: Callable[[], object]) -> None:
    """Say where the instance's own definitions are kept: what answers with the policy row.

    Called once, when the application starts (`CoreConfig.ready`), with `site.current`: the
    row as the request in flight already read it, so the definitions cost no query of
    their own.
    """
    global _policy_row
    _policy_row = policy_row


@contextmanager
def shipped_only():
    """Inside this block the registry is Postulo's own schemes, and no row is read.

    For a migration. One that tidies stored identifiers runs the rules of the day, and it
    may run before the column the definitions are kept in exists; asking for it there fails
    the query, and on PostgreSQL the transaction with it.
    """
    token = _shipped_only.set(True)
    try:
        yield
    finally:
        _shipped_only.reset(token)


def _reader(text: str, *, taken: tuple[str, ...]) -> Reading:
    """What the registry plugin makes of the text: the first installed that reads one."""
    from postulo.plugins.registry import plugins

    for plugin in plugins("identifier"):
        reader = getattr(plugin, "defined", None)
        if reader is not None:
            return reader(text, taken=taken)
    return Reading()


@dataclass(frozen=True)
class CannotBeDrawn:
    """A problem of the registry's own finding: a scheme the reading handed over whose
    words are not text. It reads as a sentence and carries a line, as the reader's do."""

    key: str
    line: int = 1
    column: int = 0

    def __str__(self) -> str:
        return _(
            "Scheme “%(key)s”: its name, its example or one of its links holds something "
            "that is not text and could not be drawn on a page, so the scheme is left out."
        ) % {"key": self.key.encode("ascii", "backslashreplace").decode("ascii")[:60]}


def _can_be_drawn(scheme: Scheme) -> bool:
    """Whether everything a page draws of a scheme can be drawn: whether it encodes.

    **Nothing the registry hands a page may be able to raise when it is drawn.** A scheme's
    name is in a menu on *Your details* and on every company's form, so one word of it
    that cannot be encoded -- half of a character, which JSON lets a text hold as an
    escape -- is a 500 for every person on the instance, while the page it was saved from
    goes on working (#311). The reader refuses such a word with its line; this is the same
    question asked again where the registry is built, of whatever the reading handed over,
    because the reading is a plugin's and the pages are everybody's.
    """
    try:
        for said in (scheme.key, scheme.label, scheme.example, scheme.link, scheme.person_link):
            str(said).encode("utf-8")
    except Exception:
        # Whatever it raised, a page would have raised it too.
        return False
    return True


def read_definitions(text: str) -> Reading:
    """Read an instance's own definitions: the schemes in them, and what is wrong with them.

    The registry plugin does the reading. Postulo's own keys are named to it, so that one
    of them in the text is a problem with a line number rather than a scheme left out. A
    scheme it hands over that could not be drawn is left out here, and is a problem too
    (`_can_be_drawn`).
    """
    reading = _reader(text or "", taken=tuple(shipped()))
    schemes, left_out = [], []
    for scheme in reading.schemes:
        if _can_be_drawn(scheme):
            schemes.append(scheme)
        else:
            left_out.append(CannotBeDrawn(str(scheme.key)))
    if not left_out:
        return reading
    return Reading(schemes=tuple(schemes), problems=(*reading.problems, *left_out))


def _kept_reading(text: str) -> Reading | None:
    """The reading already made of this text in this process, where there is one and the
    keys Postulo ships are what they were then."""
    kept = _read
    if kept is not None and kept[0] == (text, tuple(shipped())):
        return kept[1]
    return None


def what_is_kept() -> Reading:
    """What the definitions on the policy row come to: the schemes in use, and everything
    wrong with the rest. Read once for each text, in each process."""
    global _read

    if _shipped_only.get() or _policy_row is None:
        return Reading()
    row = _policy_row()
    reading = row.__dict__.get("_identifier_schemes_read")
    if reading is not None:
        return reading
    text = row.identifier_schemes or ""
    reading = _kept_reading(text)
    if reading is None:
        reading = read_definitions(text)
        for problem in reading.problems:
            # Once per text, here, and not on every page that asks.
            logger.warning("An identifier scheme defined on this instance is left out: %s", problem)
        reading = Reading(schemes=tuple(reading.schemes), problems=tuple(reading.problems))
        _read = ((text, tuple(shipped())), reading)
    # On the row as well, which is the request's own copy: the next question in the same
    # request compares nothing at all.
    row.__dict__["_identifier_schemes_read"] = reading
    return reading


def reading_of(text: str) -> Reading:
    """What a text comes to, without reading twice what was read once.

    For the page that shows the definitions. The text it shows is nearly always the text
    the policy row keeps, and reading a text is preparing every pattern in it: that one is
    the registry's own reading, made once. Any other text -- one somebody is trying to
    save -- is read here and not kept.
    """
    text = text or ""
    if _policy_row is not None and not _shipped_only.get():
        if (_policy_row().identifier_schemes or "") == text:
            return what_is_kept()
    return read_definitions(text)


def defined_here() -> tuple[Scheme, ...]:
    """The schemes this instance defined for itself, in the order they were written."""
    return what_is_kept().schemes


def _named(names: tuple[tuple[str, str], ...]) -> str:
    """One of a scheme's names: the reader's language, else British English, else the first."""
    said = dict(names)
    chosen = (
        languages.match(languages.current(), said)
        or languages.match(languages.SOURCE, said)
        or names[0][0]
    )
    return said[chosen]


_named_when_read = lazy(_named, str)


def in_languages(names: Mapping[str, str]):
    """A scheme's label that is written in several languages, as one that reads in the
    reader's.

    ``names`` maps a language tag to the name in it. Whoever reads the label is given the
    closest of them to the language their page is in (`languages.match`: ``pt-BR`` takes
    ``pt``, then ``pt-PT``), else the closest to British English, which is what Postulo
    itself is written in, else the first that was written. Decided when the label is
    drawn, as a translated string is, so one scheme serves every reader.

    `ValueError` where a name is not text that can be encoded. Which name a reader is shown
    is decided when the page is drawn, which is too late to find out that one of them
    cannot be: every one of them is asked here, once.
    """
    for tag, name in names.items():
        try:
            f"{tag}{name}".encode()
        except UnicodeError as exc:
            raise ValueError("A name that cannot be encoded is not a name.") from exc
    return _named_when_read(tuple(names.items()))


def link_template(raw: str) -> str:
    """A link an instance's own scheme may carry, or `ValueError` saying why not.

    A web address as every other one Postulo keeps is checked -- http or https, complete
    (`addresses.web_address`) -- holding ``{value}`` where the identifier goes. Three things
    more, because the address is drawn as a link for everybody and the value is somebody's:

    - ``{value}`` stands after the host: in the path, the query or the fragment. A value
      that chose the host would be a link to anywhere;
    - no other curly bracket, no backslash, nothing before an ``@``, no space and no
      control character. A browser reads a backslash as a slash and what stands before an
      ``@`` as a name to sign in with, so either moves the host from where it reads;
    - where the link is built the value is percent-encoded whole (`Scheme.quoted`), so a
      slash or a question mark in it stays a character of the value.
    """
    template = raw.strip() if isinstance(raw, str) else ""
    if len(template) > MAX_LINK_LENGTH:
        raise ValueError(
            str(_("A link is %(most)d characters long at most.") % {"most": MAX_LINK_LENGTH})
        )
    whole = _("A link is a complete web address that begins with http:// or https://.")
    try:
        template.encode()
    except UnicodeError as exc:
        # Half of a character, which a text can hold as an escape: not an address, and a
        # link that could not be drawn.
        raise ValueError(str(whole)) from exc
    if "{value}" not in template:
        raise ValueError(str(_("A link holds {value}, which is where the identifier goes.")))
    rest = template.replace("{value}", "")
    if "{" in rest or "}" in rest or "\\" in template or _CONTROL.search(template):
        raise ValueError(
            str(
                _(
                    "A link cannot hold a curly bracket other than {value}, a backslash, "
                    "or a line break."
                )
            )
        )
    if any(character.isspace() for character in template):
        raise ValueError(str(whole))
    try:
        parts = urlsplit(template)
    except ValueError as exc:
        raise ValueError(str(whole)) from exc
    if "{value}" in parts.netloc:
        raise ValueError(
            str(
                _(
                    "{value} goes after the host: in the path, after a question mark or "
                    "after a hash sign."
                )
            )
        )
    try:
        web_address(template.replace("{value}", "value"))
    except ValueError as exc:
        raise ValueError(str(whole)) from exc
    if not parts.hostname or "@" in parts.netloc:
        raise ValueError(str(whole))
    return template


def refuse_malformed(scheme: Scheme, value: str) -> None:
    """Raise `ValidationError` unless ``value`` has the scheme's shape and passes its check.

    The one place a value meets a pattern, for a person and for a company. A scheme that
    carries a `max_length` -- every one an instance defined -- has its pattern run on
    nothing longer: the pattern language reads a value once, and this is the bound on what
    there is to read.
    """
    too_long = bool(scheme.max_length) and len(value) > scheme.max_length
    if too_long or not scheme.pattern.match(value):
        if scheme.example:
            raise ValidationError(
                _("That does not look like a %(scheme)s identifier (for example %(example)s)."),
                code="format",
                params={"scheme": scheme.label, "example": scheme.example},
            )
        raise ValidationError(
            _("That does not look like a %(scheme)s identifier."),
            code="format",
            params={"scheme": scheme.label},
        )
    if scheme.checksum is not None and not scheme.checksum(value):
        raise ValidationError(scheme.checksum_message, code="checksum")


def schemes_for(subject: str) -> dict[str, Scheme]:
    """The schemes that identify ``subject``, in registration order."""
    return {key: scheme for key, scheme in registry().items() if scheme.identifies(subject)}


def find(key: str, subject: str = "") -> Scheme | None:
    """One scheme by key, or nothing — and nothing if it does not identify ``subject``."""
    scheme = registry().get(key)
    if scheme is None or (subject and not scheme.identifies(subject)):
        return None
    return scheme


def label_for(key: str, subject: str = "") -> str:
    """A scheme's name in words, or its key where nothing recognises it.

    What ``get_scheme_display()`` used to do. It cannot any more, and that is the point: a
    label out of ``choices`` can only name a scheme compiled into the model, so a scheme
    from a plugin would have shown as a bare key everywhere it appeared.
    """
    scheme = find(key, subject)
    return str(scheme.label) if scheme is not None else key


#: How long a scheme key may be. The column has to hold it, and the keys are short words.
MAX_KEY_LENGTH = 20


@deconstructible
class Identifies:
    """Field validator: this scheme exists, and identifies what this row is about.

    The requested guarantee, made by construction. It used to rest on which module a form
    imported its choices from -- a convention, and a convention holds until somebody wires a
    form up differently. Here it travels with the column, so an LEI on a person is refused
    wherever it arrives from (#109).
    """

    def __init__(self, subject: str) -> None:
        self.subject = subject

    def __call__(self, value: str) -> None:
        scheme = registry().get(value)
        if scheme is None:
            raise ValidationError(_("Unknown identifier scheme."), code="scheme")
        if not scheme.identifies(self.subject):
            raise ValidationError(
                _("%(scheme)s does not identify this kind of thing."),
                code="subject",
                params={"scheme": scheme.label},
            )

    def __eq__(self, other: object) -> bool:
        return isinstance(other, Identifies) and other.subject == self.subject

    def __hash__(self) -> int:
        return hash((type(self), self.subject))


def scheme_field(subject: str) -> models.CharField:
    """The column an identifier's scheme lives in.

    Not ``choices``, which is what it was. Choices are frozen into every migration that
    touches the field, so a scheme contributed by a plugin could never be one -- and the
    keys are the same strings they always were, so every existing row keeps its value.
    """
    return models.CharField(
        _("scheme"), max_length=MAX_KEY_LENGTH, validators=[Identifies(subject)]
    )


def require(key: str, subject: str) -> None:
    """Refuse a scheme that does not identify ``subject``. Raises `ValidationError`.

    Called from `save()` on both identifier models, because *refused at the model* has to
    mean every route in and not only the two that run `full_clean`.
    """
    Identifies(subject)(key)


# ------------------------------------------- a row whose scheme is not defined any more
#
# An administrator who deletes a scheme of the instance's own -- or takes people out of what
# it identifies -- deletes no identifier (#311). The rows stored under it stay as they are:
# shown under the key they were stored with, the value as it was typed, no link, which is
# how `label_for` and `url_for` have always answered for a key nothing recognises. Nothing
# is rewritten, so defining the scheme again brings every one of them back to life.
#
# What that needs is for such a row to go on being *saveable*. The page it sits on is saved
# for other reasons, a company is merged into another, an API client sends back the list it
# was given; each of those writes the row, and the model refuses a key the registry does not
# know. So a row that still holds the key it was stored with is let through, and only that:
# a new row cannot be given a key nobody defines, and a stored one cannot be moved to one.

# ----------------------------------------- a row its scheme would refuse if it were typed
#
# The other half of the same promise, and the one the review found missing (#311). A scheme
# can be *changed* as well as deleted: its pattern rewritten, a fold or a checksum added, or
# its key taken by a scheme Postulo starts to ship with another shape. The rows stored under
# it were right the day they were typed. Held to the scheme of today whenever they were
# looked at, they refused the page they sat on -- a first name could not be changed on *Your
# details* -- answered 422 to an API client that sent back the list it had been given, and
# were dropped by an import with nothing said. That is the loss deleting a scheme is
# forbidden to cause, by another road.
#
# So the rule the telephone numbers (#304) and the postal addresses (#306) follow: **a
# stored row is never refused or lost by being looked at.** A row whose scheme and value are
# the ones the table holds passes untouched, whatever its scheme says now, and answers the
# day either is changed. The page marks it -- *Kept as it was*, with what the scheme expects
# today -- and draws no link for it, because a link built from a value the scheme refuses
# leads wherever it leads.

_NOT_READ = object()


def said_against(key: str, value: str, subject: str = "") -> list[str]:
    """What a scheme, as it is today, says against a value as it stands: the sentences
    that would refuse it if it were typed. Empty where it would be accepted, and where
    there is nothing to hold it to: *Other*, and a key nothing defines for the subject.

    The value is asked as it is, not tidied first: a stored value is the scheme's own
    spelling or it is not, and one that today's scheme would rewrite is not.
    """
    scheme = find(key, subject)
    if scheme is None or not value:
        return []
    try:
        refuse_malformed(scheme, value)
    except ValidationError as refused:
        return [str(sentence) for sentence in refused.messages]
    return []


class KeepsItsScheme:
    """Model mixin for the two identifier tables: a stored row is not refused, or lost, by
    being looked at (#311).

    Two cases, and one rule. A row may keep a scheme that is **no longer defined**, and a
    row may keep a value its scheme **would refuse today**; either is saved as it is by
    whatever saves it, and is held to the registry of the day when its scheme or its value
    is changed.

    The model says which of `SUBJECTS` its rows are about in ``SUBJECT``. Before
    ``models.Model`` among the bases, so that `from_db` is this one.
    """

    SUBJECT = ""

    @classmethod
    def from_db(cls, db, field_names, values, **kwargs):
        # Whatever else Django hands a row as it is read is passed on as it came.
        row = super().from_db(db, field_names, values, **kwargs)
        row._as_stored = (
            row.__dict__.get("scheme", _NOT_READ),
            row.__dict__.get("value", _NOT_READ),
        )
        return row

    def as_stored(self) -> tuple[str | None, str | None]:
        """The scheme and the value the table holds for this row; nothing for a new one."""
        if self.pk is None:
            return None, None
        stored = getattr(self, "_as_stored", (_NOT_READ, _NOT_READ))
        if _NOT_READ in stored:
            # Made here rather than read, or read without the columns: ask, once.
            found = type(self)._base_manager.filter(pk=self.pk).values_list("scheme", "value")
            stored = self._as_stored = found.first() or (None, None)
        return stored

    def keeps_an_undefined_scheme(self) -> bool:
        """Whether this row is stored under a scheme nothing defines for its subject, and is
        being left under it."""
        if self.pk is None or find(self.scheme, self.SUBJECT) is not None:
            return False
        return self.as_stored()[0] == self.scheme

    def is_as_stored(self) -> bool:
        """Whether this row holds the scheme and the value the table holds for it: whether
        whatever is about to check it or save it is leaving the identifier alone."""
        return self.pk is not None and self.as_stored() == (self.scheme, self.value)

    @property
    def kept_as_it_was(self) -> list[str]:
        """What the row's scheme says today against the identifier **as it is kept**: the
        sentences a page draws beside a row that is left alone. Empty for a row not yet
        stored, and for one its scheme accepts."""
        scheme, value = self.as_stored()
        return said_against(scheme, value, self.SUBJECT) if scheme and value else []

    def link_for(self, url: str) -> str:
        """``url``, or nothing where the scheme of today refuses the value it was built
        from: a link to wherever a value of another shape happens to lead is not drawn."""
        return "" if url and said_against(self.scheme, self.value, self.SUBJECT) else url

    def clean_fields(self, exclude=None):
        if self.keeps_an_undefined_scheme():
            # The column's own validator asks the registry, and would refuse the key.
            exclude = {*(exclude or ()), "scheme"}
        super().clean_fields(exclude=exclude)

    def save(self, *args, **kwargs):
        """Refuse a scheme that does not identify this subject, by every route in.

        `full_clean` catches it for a form and an import; this catches it for
        ``objects.create`` and for anything a plugin writes directly. *No valid company
        identifier is shown on the user data* was the ask, and a guarantee that holds only
        where somebody remembered to validate is not one (#109).
        """
        if not self.keeps_an_undefined_scheme():
            require(self.scheme, self.SUBJECT)
        saved = super().save(*args, **kwargs)
        self._as_stored = (self.scheme, self.value)
        return saved


def as_restored(
    key: str, label: str = "", subject: str = "", value: str | None = None
) -> tuple[str, str]:
    """The scheme and the name an identifier out of a file is restored under (#311).

    An archive is one person's, and the schemes an instance defined for itself are the
    instance's: they travel in its backup, not in anybody's archive. So an archive may
    name a key this instance has never heard of -- or one it defined for something else:
    two instances are free to give ``member`` to a person and to a company. Such an
    identifier is restored as *Other*, named by the key it had -- the answer a web link's
    service gets (#305) -- which keeps the value, says what it was, and stores nothing
    under a key that means something else here or nothing at all.

    **And so is one whose value the scheme here refuses**, where ``value`` is given: the
    same key with another pattern on the instance the file came from, or a pattern changed
    since the file was written. An importer drops no identifier and stores none its scheme
    refuses, and *Other* is where a value with no shape to answer to belongs. The value is
    tidied first, as a typed one is: what is refused is what could not be typed here.

    A key of Postulo's own that does not identify the subject is left as it is, to be
    refused as it always was: an LEI on a person is not a kind this instance lacks.
    """
    if not isinstance(key, str) or not key.strip():
        return key, label
    known = registry().get(key.strip())
    elsewhere = known is None or (
        known.provider == DEFINED_HERE and bool(subject) and not known.identifies(subject)
    )
    asked = isinstance(value, str) and bool(value.strip())
    if not elsewhere and asked and (not subject or known.identifies(subject)):
        try:
            refuse_malformed(known, known.normalise(value))
        except ValidationError:
            elsewhere = known.key != OTHER
    if not elsewhere:
        return key, label
    return OTHER, (label if isinstance(label, str) and label.strip() else key.strip())[:60]


def offering(choices, row=None) -> list:
    """The kinds a row's menu offers: the registry's, and the row's own where it is stored
    under one that is no longer defined (#311).

    Without it the row could not be posted back, and the page it sits on could not be saved
    until the identifier was removed -- which is the loss deleting a scheme must not cause.
    The key stands for itself: there is no name left to call it by.
    """
    choices = list(choices)
    held = getattr(row, "scheme", "") if getattr(row, "pk", None) else ""
    if held and held not in {key for key, _label in choices}:
        choices.append((held, held))
    return choices


# ------------------------------------------------------------------ the rows on a form
#
# One identifier of each kind, Other excepted. Both tables have always held the rule, but the
# rows on *Your details* and on a company's form offered every kind on every row, and a
# second ORCID was found out only on saving -- on a company in words that did not say which
# kind, on a person in the constraint's own name. The rule is offered now as well as
# enforced, from here, for both forms (#307).


def _removing(row) -> bool:
    """Whether a row has *Remove* ticked, as posted or as drawn, or has gone already.

    A row posted from a copy of the page that is out of date, whose key is no longer in
    the table, holds no kind either (`core.formsets.RowsAlreadyGone`).
    """
    if getattr(row, "already_gone", False):
        return True
    return "DELETE" in row.fields and bool(row["DELETE"].value())


def _set_to(row) -> str:
    """The kind a row is set to, as posted or as drawn; empty while none is chosen."""
    return row["scheme"].value() or ""


class SchemeSelect(OptionIcons, forms.Select):
    """The choice of kind, with the kinds the other rows hold switched off (#307).

    ``<option disabled>`` needs no script, and a screen reader announces it as unavailable,
    so nothing more is said. The row's own kind is never switched off -- a row keeps what it
    has, even beside a duplicate about to be refused, or it could not be posted back -- and
    neither is Other, which may repeat.

    Each kind says its icon as well (#301, `OptionIcons`): the scheme's own, which is a
    generic one, drawn in the list of the control ``app.js`` builds beside the select.
    """

    def __init__(self, attrs=None, choices=()):
        super().__init__(attrs, choices)
        #: Asked when the select is drawn, for the kinds the other rows hold. The formset the
        #: row sits in answers; a row on its own has no neighbours.
        self.taken: Callable[[], Iterable[str]] = lambda: ()
        #: The schemes as they stood when the select was last drawn: asked once a drawing,
        #: not once an option.
        self._schemes: dict[str, Scheme] = {}

    def option_icon(self, value: str) -> str:
        scheme = self._schemes.get(value)
        return scheme.icon_name if scheme is not None else ""

    def get_context(self, name, value, attrs):
        self._schemes = registry()
        context = super().get_context(name, value, attrs)
        off = set(self.taken()) - set(context["widget"]["value"]) - {OTHER}
        for _group, options, _index in context["widget"]["optgroups"]:
            for option in options:
                if option["value"] in off:
                    option["attrs"]["disabled"] = True
        return context


class OneOfEachKind:
    """Formset mixin for identifier rows: one of each kind, offered and enforced (#307).

    Worked out from the rows as they stand, posted or drawn from the table, because the
    formset is the only thing that sees all of them.

    - **Offered.** Each row's choice switches off the kinds the other rows hold. A row
      marked for removal holds nothing, so its kind is free for the others.
    - **Enforced.** A duplicate is still refused, because two new rows typed in one go can
      pick the same kind, and a page with its script is not the only way in. The refusal
      names the kind and sits on the row that lost it. A row keeping the kind it was saved
      with has first claim, and so does every saved row on the kind it was saved with while
      it is being changed, because the table holds that kind until the row is written;
      between rows taking a kind anew, the first keeps it.
    - **Written in an order that works.** Rows marked for removal go first, so the kind one
      of them gives up can be taken by another row in the same save.

    The rows' form carries `IdentifierRow`, which is where the refusal is asked for.
    """

    def add_fields(self, form, index):
        super().add_fields(form, index)
        form.fields["scheme"].widget.taken = lambda: self.held(besides=form)
        form.one_of_each_kind = self

    def held(self, besides=None) -> set[str]:
        """The kinds the rows hold, apart from ``besides``'s; never Other."""
        kinds = {_set_to(row) for row in self.forms if row is not besides and not _removing(row)}
        return kinds - {"", OTHER}

    def refuse_taken(self, row, key: str) -> None:
        """Raise `ValidationError` if another row has first claim on ``key``."""
        if key == OTHER or key == row.initial.get("scheme") or _removing(row):
            return
        ahead = True
        for other in self.forms:
            if other is row:
                ahead = False
            elif not _removing(other) and (
                other.initial.get("scheme") == key or (ahead and _set_to(other) == key)
            ):
                raise ValidationError(
                    _("%(scheme)s is already listed on another row."),
                    code="taken",
                    params={"scheme": label_for(key)},
                )

    def save(self, commit=True):
        # Django writes the saved rows in the order they are listed, removing or changing
        # each in turn, so a row taking the kind of one removed further down would reach
        # the table while that kind was still in it. Removed first, they are passed over
        # afterwards the way Django passes over any row already gone.
        if commit:
            for row in self.deleted_forms:
                if row.instance.pk is not None:
                    self.delete_existing(row.instance)
        return super().save(commit=commit)


class IdentifierRow:
    """Form mixin for one row of a `OneOfEachKind` formset (#307).

    The table's one-of-each-kind constraint, checked from one row, sees only the table,
    where a row about to be removed is still sitting, so it would refuse the kind that the
    removal frees -- and on a person it said so in the constraint's own name. The formset
    sees every row, so a row in one swaps that check for the formset's, which names the
    kind. Everything else the table promises is still checked here, and a row outside a
    formset is checked exactly as Django would check it.
    """

    @property
    def kept_as_it_was(self) -> list[str]:
        """What the row's scheme says today against the identifier **as it is kept** (#311).

        The mark a template draws on a row that is left alone: an identifier stored before
        its scheme's pattern was changed, which is saved with the page as it is and answers
        the next time it is changed. Empty for a row being added, for one its scheme
        accepts, and for one whose kind or value was changed in this submission -- that one
        has answered, with its error beside it.
        """
        row = self.instance
        if row.pk is None:
            return []
        shown = (self["scheme"].value() or "", (self["value"].value() or "").strip())
        return row.kept_as_it_was if shown == row.as_stored() else []

    def validate_constraints(self):
        rows = getattr(self, "one_of_each_kind", None)
        scheme = self.cleaned_data.get("scheme")
        if rows is None or not scheme or scheme == OTHER:
            return super().validate_constraints()
        exclude = self._get_validation_exclusions()
        try:
            self.instance.validate_constraints(exclude=exclude | {"scheme"})
        except ValidationError as error:
            self._update_errors(error)
        if "scheme" not in exclude:
            try:
                rows.refuse_taken(self, scheme)
            except ValidationError as error:
                self.add_error("scheme", error)
