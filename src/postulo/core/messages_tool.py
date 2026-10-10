"""Keep a project's translation catalogues current, and turn them into what Django reads.

Standard library and Django only — no GNU gettext on the machine. ``makemessages`` and
``compilemessages`` shell out to ``xgettext`` and ``msgfmt``, which a Windows laptop, a
slim container and most CI images do not have; this does the same work in Python so
that every contributor and every build can run it.

    uv run python scripts/messages.py extract          # refresh every .po from the source
    uv run python scripts/messages.py extract --check  # fail if a .po is out of date
    uv run python scripts/messages.py compile          # the .mo files Django loads, and status.json
    uv run python scripts/messages.py stats            # how far along each language is
    uv run python scripts/messages.py check            # placeholders and plural forms agree

**A plugin repository runs the same commands against itself** as ``postulo-messages``,
the console script this module is installed as, from the repository's root (#187). A
plugin is a project like Postulo's own -- a ``pyproject.toml``, a package under ``src/``,
a ``locale/`` inside it -- and the rule is the same on both sides of the plugin boundary.
:func:`use` points the module at a project; the script points it at the current directory.

**This tool writes slots, never translations (#706).** Every translation is made in
Weblate, which commits it back as a pull request; what this writes is the English, the
empty slot beside it, and the layout. So the layout is Weblate's own -- translate-toolkit's
at a line width of 65535: a value is split only after a newline, flags are sorted,
references name a file and not a line, and no header carries a date this tool sets -- and a
file either side writes is one the other leaves byte for byte as it found it
(``tests/test_po_roundtrip.py``).

One deliberate difference from ``msgfmt``: an entry flagged ``fuzzy`` -- a draft, written by
a machine and not yet read by a speaker, which Weblate shows as *needs editing* -- is
compiled, so a language is usable on day one, unless it would fail to format. Reviewing a
draft means saving it in Weblate as translated, which clears the flag. ``msgfmt`` users
need ``--use-fuzzy`` to see the same. The flag used to be called ``draft``; ``extract``
renames it.
"""

from __future__ import annotations

import argparse
import ast
import functools
import gettext
import io
import json
import re
import struct
import sys
import tokenize
import tomllib
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path

from postulo.core.languages import (
    LANGUAGES,
    NATIVE_NAMES,
    PLURAL_FORMS,
    SOURCE,
    find,
    nplurals,
)


@dataclass(frozen=True)
class Project:
    """The repository the tool is working in: its root, its package, and its name."""

    root: Path
    package: Path
    name: str
    #: ``project.license`` from ``pyproject.toml`` (an SPDX string, or the older table's
    #: ``text``); empty when the project declares none.
    licence: str = ""
    #: Where the project takes bug reports, from ``project.urls``; empty when it says nothing.
    issues_url: str = ""

    @property
    def locale(self) -> Path:
        return self.package / "locale"

    @property
    def is_postulo(self) -> bool:
        return self.name == "postulo"

    def licence_line(self) -> str:
        """The header sentence naming the licence the catalogues are under: the project's
        own, since a plugin's licence is its author's choice and not Postulo's (#417)."""
        if self.is_postulo:
            return "This file is distributed under the same license as Postulo (AGPL-3.0-or-later)."
        said = f" ({self.licence})" if self.licence else ""
        return f"This file is distributed under the same license as the {self.name} package{said}."

    def describe(self, subject: CatalogueSet) -> str:
        """What a catalogue's header calls its set: Postulo, one of its plugins, or the
        project itself when the tool is running in a plugin's repository."""
        if not self.is_postulo:
            return self.name
        return "Postulo" if subject.is_core else f"Postulo's {subject.name} plugin"


#: Set by `use`; `project()` fills it in from the current directory when nothing has.
PROJECT: Project | None = None


def use(root: Path | None = None) -> Project:
    """Point the tool at a repository: Postulo's own, or a plugin's.

    The package is read off ``pyproject.toml``: the project's name with hyphens as
    underscores, under ``src/``, which is the layout every official plugin already has
    and the one *Writing a plugin* describes. Nothing else is guessed.
    """
    global PROJECT
    root = Path(root or Path.cwd()).resolve()
    pyproject = root / "pyproject.toml"
    if not pyproject.is_file():
        raise SystemExit(f"{root} has no pyproject.toml; run this from a project's root.")
    meta = tomllib.loads(pyproject.read_text(encoding="utf-8")).get("project", {})
    name = meta.get("name")
    if not name:
        raise SystemExit(f"{pyproject} names no project.")
    package = root / "src" / name.replace("-", "_")
    if not package.is_dir():
        raise SystemExit(f"{package} is not there; the tool expects the package under src/.")
    PROJECT = Project(
        root=root,
        package=package,
        name=name,
        licence=_licence_of(meta),
        issues_url=_issues_url_of(meta),
    )
    return PROJECT


def _licence_of(meta: dict) -> str:
    licence = meta.get("license")
    if isinstance(licence, dict):
        licence = licence.get("text")
    return licence.strip() if isinstance(licence, str) else ""


def _issues_url_of(meta: dict) -> str:
    urls = meta.get("urls")
    if not isinstance(urls, dict):
        return ""
    by_label = {str(label).strip().lower(): url for label, url in urls.items()}
    for label in ("issues", "bug tracker", "bugs", "tracker", "issue tracker"):
        if isinstance(by_label.get(label), str):
            return by_label[label]
    return ""


def project() -> Project:
    return PROJECT if PROJECT is not None else use()


#: Functions whose string arguments are messages, and which argument is which.
#: (message index, plural index, context index)
CALLS: dict[str, tuple[int, int | None, int | None]] = {
    "_": (0, None, None),
    "gettext": (0, None, None),
    "gettext_lazy": (0, None, None),
    "gettext_noop": (0, None, None),
    "ngettext": (0, 1, None),
    "ngettext_lazy": (0, 1, None),
    "pgettext": (1, None, 0),
    "pgettext_lazy": (1, None, 0),
    "npgettext": (1, 2, 0),
    "npgettext_lazy": (1, 2, 0),
    # Aliases. `messages.py` reads the source rather than importing it, so it knows a call
    # by the name at the call site and by nothing else -- and three plugins import
    # `gettext_lazy as _lazy`, which meant their descriptions were never extracted and
    # never translatable, silently, for as long as they have existed (found in #149).
    "_lazy": (0, None, None),
}

SKIP_DIRS = {"migrations", "static", "locale", "__pycache__"}


@dataclass(frozen=True)
class CatalogueSet:
    """A directory of source, and the catalogues that hold the strings written in it.

    Postulo used to have exactly one, and *Writing a plugin* has always said a plugin's
    strings are never added to Postulo's catalogues -- a rule that was true of every
    third-party plugin and false of every plugin Postulo ships (#127). A plugin that
    carries its own catalogues is a second set, and everything below walks all of them so
    that moving strings out of core does not move them out of the coverage that keeps them
    translated.
    """

    #: What to call it in output: ``postulo`` for the core catalogues, otherwise the
    #: package's path inside the distribution, e.g. ``plugins/builtin``.
    name: str
    #: The directory whose source files this set claims.
    root: Path
    #: The ``locale/`` directory holding its ``.po`` files.
    locale: Path

    @property
    def is_core(self) -> bool:
        """The project's own set, as against one nested inside it. The name predates
        the tool running in a plugin's repository, where "core" is that plugin."""
        return self.root == project().package


def _within(path: Path, directory: Path) -> bool:
    return path == directory or directory in path.parents


def catalogue_sets() -> list[CatalogueSet]:
    """Postulo's own catalogues first, then every package carrying its own.

    Discovery is by the filesystem: a directory under the package with a ``locale/`` in it
    is a catalogue set. Nothing is listed here by name, so a plugin that moves its strings
    out of core needs no edit to this file -- which is the point, because #129 moves the
    built-ins out one at a time and each move should be one commit in one place.

    **Core is first, and the order is load-bearing.** It becomes the order of
    ``LOCALE_PATHS``, and Django merges those in ``reversed()`` order with each merge
    overriding the last -- so the first path wins a msgid two catalogues both define. Core
    winning is the safe direction: a plugin cannot quietly change a word in Postulo's own
    interface by translating the same English string differently.
    """
    sets = [CatalogueSet(project().name, project().package, project().locale)]
    for locale in sorted(project().package.rglob("locale")):
        if locale == project().locale or not locale.is_dir():
            continue
        if SKIP_DIRS & set(locale.relative_to(project().package).parts[:-1]):
            continue
        root = locale.parent
        sets.append(CatalogueSet(root.relative_to(project().package).as_posix(), root, locale))
    return sets


def core_set() -> CatalogueSet:
    return catalogue_sets()[0]


PLACEHOLDER = re.compile(r"%\((\w+)\)[sdifr]|%[sdifr%]|\{(\w*)\}")

#: What marks a draft. ``fuzzy`` is gettext's and Weblate's; ``draft`` is what this tool
#: wrote before Weblate held the translations, and ``merge`` renames it.
DRAFT = "fuzzy"
DRAFT_FLAGS = frozenset({DRAFT, "draft"})


# ------------------------------------------------------------------ the model


@dataclass
class Message:
    msgid: str
    plural: str | None = None
    context: str | None = None
    references: list[str] = field(default_factory=list)
    comments: list[str] = field(default_factory=list)  # extracted, "#."
    translator: list[str] = field(default_factory=list)  # "# "
    flags: list[str] = field(default_factory=list)
    msgstr: list[str] = field(default_factory=lambda: [""])

    @property
    def key(self) -> tuple[str | None, str]:
        return (self.context, self.msgid)

    @property
    def translated(self) -> bool:
        return all(form for form in self.msgstr)

    @property
    def draft(self) -> bool:
        """Written and not yet read by a speaker: ``fuzzy``, or ``draft`` as it was called."""
        return bool(DRAFT_FLAGS & set(self.flags))

    @property
    def python_format(self) -> bool:
        return bool(re.search(r"%(\(\w+\))?[sdifr]", self.msgid + (self.plural or "")))


@dataclass
class Catalogue:
    header: dict[str, str]
    messages: dict[tuple[str | None, str], Message]

    def ordered(self) -> list[Message]:
        return [self.messages[k] for k in sorted(self.messages, key=_sort_key)]


def _sort_key(key):
    return (key[1].casefold(), key[0] or "")


# --------------------------------------------------------------- extraction


def _strings_in(tokens: list[tokenize.TokenInfo], start: int) -> tuple[list[str | None], int]:
    """The positional arguments of a call whose ``(`` is at ``start``: literal strings
    or None for anything else, and the index just past the closing parenthesis."""
    args: list[str | None] = []
    current: list[str] = []
    literal = True
    depth = 0
    i = start
    while i < len(tokens):
        tok = tokens[i]
        if tok.type == tokenize.OP and tok.string in "([{":
            depth += 1
            if depth > 1:
                literal = False
        elif tok.type == tokenize.OP and tok.string in ")]}":
            depth -= 1
            if depth == 0:
                args.append("".join(current) if literal and current else None)
                return args, i + 1
        elif depth == 1 and tok.type == tokenize.OP and tok.string == ",":
            args.append("".join(current) if literal and current else None)
            current, literal = [], True
        elif depth == 1 and tok.type == tokenize.STRING:
            try:
                value = ast.literal_eval(tok.string)
            except (ValueError, SyntaxError):
                literal = False
                value = ""
            if isinstance(value, str):
                current.append(value)
            else:
                literal = False
        elif depth == 1 and tok.type not in (tokenize.NL, tokenize.NEWLINE, tokenize.COMMENT):
            literal = False
        i += 1
    return args, i


def extract_python(source: str, origin: str) -> list[Message]:
    """Messages in Python source (or in what ``templatize`` made of a template)."""
    found: list[Message] = []
    try:
        tokens = list(tokenize.generate_tokens(io.StringIO(source).readline))
    except (tokenize.TokenError, SyntaxError) as exc:  # pragma: no cover - broken source
        print(f"{origin}: cannot tokenise: {exc}", file=sys.stderr)
        return found
    i = 0
    while i < len(tokens) - 1:
        tok = tokens[i]
        nxt = tokens[i + 1]
        if (
            tok.type == tokenize.NAME
            and tok.string in CALLS
            and nxt.type == tokenize.OP
            and nxt.string == "("
            and not (i > 0 and tokens[i - 1].type == tokenize.OP and tokens[i - 1].string == ".")
        ):
            msg_index, plural_index, context_index = CALLS[tok.string]
            args, i = _strings_in(tokens, i + 1)
            msgid = args[msg_index] if len(args) > msg_index else None
            if msgid:
                # The file, not the line: a line number moves whenever anything above the
                # call does, which rewrote the reference beside a translation Weblate was
                # changing and made the two conflict on every rebase (#349).
                message = Message(msgid=msgid, references=[origin])
                if plural_index is not None and len(args) > plural_index and args[plural_index]:
                    message.plural = args[plural_index]
                if context_index is not None and len(args) > context_index:
                    message.context = args[context_index]
                found.append(message)
            continue
        i += 1
    return found


def extract_template(source: str, origin: str) -> list[Message]:
    """Messages in a Django template.

    ``templatize`` turns the template into Python-shaped text for xgettext, which does not
    care about indentation; Python's tokeniser does, so every line is flushed left first.
    Nothing in that text depends on indentation — it is only calls and filler.
    """
    from django.utils.translation.template import templatize

    lines = templatize(source, origin).splitlines()
    flattened = "\n".join(line.lstrip() for line in lines)
    return extract_python(flattened, origin)


def sources(subject: CatalogueSet | None = None) -> list[Path]:
    """Every file whose strings belong to ``subject``, defaulting to Postulo's own.

    A file inside a nested set belongs to that one, not to this: core claims the whole
    package *except* the packages that carry their own catalogues.
    """
    subject = subject or core_set()
    nested = [s.root for s in catalogue_sets() if s.root != subject.root]
    files = []
    for path in sorted(subject.root.rglob("*")):
        if not path.is_file() or path.suffix not in (".py", ".html", ".txt"):
            continue
        if SKIP_DIRS & set(path.relative_to(subject.root).parts[:-1]):
            continue
        if any(_within(path, other) for other in nested if _within(other, subject.root)):
            continue
        files.append(path)
    return files


def extract_all(subject: CatalogueSet | None = None) -> dict[tuple[str | None, str], Message]:
    """Every message in one set's source, merged by (context, msgid)."""
    import django
    from django.conf import settings

    if not settings.configured:
        settings.configure(USE_I18N=True)
        django.setup()

    merged: dict[tuple[str | None, str], Message] = {}
    for path in sources(subject):
        origin = path.relative_to(project().root).as_posix()
        text = path.read_text(encoding="utf-8")
        found = (
            extract_python(text, origin) if path.suffix == ".py" else extract_template(text, origin)
        )
        for message in found:
            existing = merged.get(message.key)
            if existing is None:
                merged[message.key] = message
            else:
                existing.references.extend(message.references)
                if message.plural and not existing.plural:
                    existing.plural = message.plural
    for message in merged.values():
        message.references = sorted(set(message.references), key=_reference_key)
        if message.python_format:
            message.flags = ["python-format"]
    return merged


def _reference_key(reference: str):
    path, _, line = reference.rpartition(":")
    if not line.isdigit():
        return (reference, 0)  # a file alone, which is what extraction writes now
    return (path, int(line))


# ------------------------------------------------------------------ .po files


_UNESCAPES = {"n": "\n", "t": "\t", "r": "\r", '"': '"', "\\": "\\"}


def _quote(text: str) -> str:
    escaped = (
        text.replace("\\", "\\\\")
        .replace('"', '\\"')
        .replace("\n", "\\n")
        .replace("\t", "\\t")
        .replace("\r", "\\r")
    )
    return f'"{escaped}"'


#: One segment of a value as translate-toolkit lays it out: up to and including a newline.
_SEGMENT = re.compile(r"[^\n]*\n|[^\n]+")


def _write_field(out: list[str], name: str, value: str) -> None:
    """A value as Weblate writes it at a line width of 65535: one line per newline.

    Split after each newline and nowhere else; a value of one segment stays on the keyword's
    line however long it is, and one of several starts with ``""``. That is exactly what
    translate-toolkit writes, so an entry Weblate re-saves comes back unchanged.
    """
    segments = _SEGMENT.findall(value)
    if len(segments) >= 2:
        out.append(f'{name} ""')
        out.extend(_quote(segment) for segment in segments)
    else:
        out.append(f"{name} {_quote(value)}")


def dump(catalogue: Catalogue, code: str, subject: CatalogueSet | None = None) -> str:
    subject = subject or core_set()
    current = project()
    what = current.describe(subject)
    out: list[str] = [
        f"# {NATIVE_NAMES.get(code, code)} translation of {what}.",
        f"# {current.licence_line()}",
        "#",
        'msgid ""',
        'msgstr ""',
    ]
    for key, value in catalogue.header.items():
        out.append(_quote(f"{key}: {value}\n"))
    for message in catalogue.ordered():
        out.append("")
        out.extend(f"# {line}" if line else "#" for line in message.translator)
        out.extend(f"#. {line}" for line in message.comments)
        for chunk in _chunks(message.references, 76):
            out.append("#: " + " ".join(chunk))
        if message.flags:
            # Sorted, because translate-toolkit sorts a flag line whenever it rewrites one.
            out.append("#, " + ", ".join(sorted(message.flags)))
        if message.context is not None:
            _write_field(out, "msgctxt", message.context)
        _write_field(out, "msgid", message.msgid)
        if message.plural is not None:
            _write_field(out, "msgid_plural", message.plural)
            for index, form in enumerate(message.msgstr):
                _write_field(out, f"msgstr[{index}]", form)
        else:
            _write_field(out, "msgstr", message.msgstr[0])
    return "\n".join(out) + "\n"


def _chunks(items: list[str], width: int):
    line: list[str] = []
    for item in items:
        if line and len(" ".join([*line, item])) > width:
            yield line
            line = []
        line.append(item)
    if line:
        yield line


def parse(text: str) -> Catalogue:
    """A .po file, well enough for what this tool writes and Poedit edits."""
    header: dict[str, str] = {}
    messages: dict[tuple[str | None, str], Message] = {}
    current = Message(msgid="")
    fields: dict[str, list[str]] = {}
    active: str | None = None
    saw_entry = False

    def flush():
        nonlocal current, fields, active, saw_entry
        if not saw_entry:
            return
        msgid = "".join(fields.get("msgid", []))
        context = "".join(fields["msgctxt"]) if "msgctxt" in fields else None
        plural = "".join(fields["msgid_plural"]) if "msgid_plural" in fields else None
        forms = sorted(k for k in fields if k.startswith("msgstr["))
        if forms:
            msgstr = ["".join(fields[k]) for k in forms]
        else:
            msgstr = ["".join(fields.get("msgstr", []))]
        if msgid == "" and context is None:
            for line in msgstr[0].split("\n"):
                if ":" in line:
                    name, _, value = line.partition(":")
                    header[name.strip()] = value.strip()
        else:
            current.msgid, current.context, current.plural, current.msgstr = (
                msgid,
                context,
                plural,
                msgstr,
            )
            messages[current.key] = current
        current = Message(msgid="")
        fields, active, saw_entry = {}, None, False

    for raw in text.splitlines():
        line = raw.strip()
        if not line:
            flush()
            continue
        if line.startswith("#~"):
            continue  # obsolete entries are not kept
        if line.startswith("#"):
            if saw_entry:
                flush()
            if line.startswith("#:"):
                current.references.extend(line[2:].split())
            elif line.startswith("#,"):
                current.flags.extend(f.strip() for f in line[2:].split(",") if f.strip())
            elif line.startswith("#."):
                current.comments.append(line[2:].strip())
            elif line.startswith("#|"):
                pass  # previous msgid: dropped
            else:
                current.translator.append(line[1:].strip())
            continue
        if line.startswith('"'):
            if active is not None:
                fields[active].append(_unquote(line))
            continue
        keyword, _, rest = line.partition(" ")
        active = keyword
        fields[keyword] = [_unquote(rest)] if rest else []
        saw_entry = True
    flush()
    return Catalogue(header=header, messages=messages)


def _unquote(chunk: str) -> str:
    chunk = chunk.strip()
    if chunk.startswith('"') and chunk.endswith('"'):
        chunk = chunk[1:-1]
    # One left-to-right pass: chained replaces misread an escaped backslash before n or t.
    return re.sub(r"\\(.)", lambda m: _UNESCAPES.get(m.group(1), m.group(0)), chunk)


def po_path(code: str, subject: CatalogueSet | None = None) -> Path:
    from django.utils.translation import to_locale

    # `to_locale` finds the directory for either spelling, which is how a gate keyed by
    # codes shrinks without failing: `fr-fr` names a catalogue here and is in no list a
    # test filters by. So the other spelling is refused, loudly (#337).
    listed = find(code)
    if listed and listed != code:
        raise ValueError(
            f"{code!r} is written {listed!r}: a language code is a BCP 47 tag in its canonical form"
        )
    subject = subject or core_set()
    return subject.locale / to_locale(code) / "LC_MESSAGES" / "django.po"


def translated_languages() -> list[str]:
    return [code for code, _name in LANGUAGES if code != SOURCE]


#: Header keys Weblate writes when a translation is saved, kept here as found.
WEBLATE_KEYS = ("PO-Revision-Date", "Last-Translator", "Language-Team", "X-Generator")


def header_for(code: str, existing: dict[str, str]) -> dict[str, str]:
    """The header, in a fixed order, owning only what is this project's to say.

    No ``POT-Creation-Date``: a date rewritten on every extraction is a line changed in
    every catalogue on every commit, next to the lines Weblate changes when it saves a
    translation, and that was a conflict on every rebase (#349). The keys Weblate writes are
    kept as found, and so is a ``Plural-Forms`` that says the same as ours in Weblate's
    spelling. Any other key a writer added is kept after these.
    """
    current = project()
    today = datetime.now(UTC).strftime("%Y-%m-%d %H:%M%z")
    ours = PLURAL_FORMS.get(code, "nplurals=2; plural=(n != 1);")
    found = existing.get("Plural-Forms", "")
    header = {
        "Project-Id-Version": "Postulo" if current.is_postulo else current.name,
        "Report-Msgid-Bugs-To": (
            "https://source.tiagoagueda.com/postulo/postulo/issues"
            if current.is_postulo
            else current.issues_url
        ),
        "PO-Revision-Date": existing.get("PO-Revision-Date", today),
        "Last-Translator": existing.get("Last-Translator", "Postulo contributors"),
        "Language-Team": existing.get("Language-Team", NATIVE_NAMES.get(code, code)),
        "Language": code.replace("-", "_") if "-" not in code else _django_locale(code),
        "MIME-Version": "1.0",
        "Content-Type": "text/plain; charset=UTF-8",
        "Content-Transfer-Encoding": "8bit",
        "Plural-Forms": found if found and same_plural_rule(found, ours) else ours,
        "X-Generator": existing.get(
            "X-Generator",
            "postulo scripts/messages.py" if current.is_postulo else "postulo-messages",
        ),
    }
    if not header["Report-Msgid-Bugs-To"]:
        del header["Report-Msgid-Bugs-To"]
    for key, value in existing.items():
        if key not in header and key not in ("POT-Creation-Date", "Report-Msgid-Bugs-To"):
            header[key] = value
    return header


def _plural_rule(header: str):
    """``(nplurals, function)`` for a ``Plural-Forms`` value, or None if it cannot be read."""
    number = re.search(r"nplurals\s*=\s*(\d+)", header)
    formula = re.search(r"plural\s*=\s*(.+?)\s*;?\s*$", header)
    if not number or not formula:
        return None
    try:
        return int(number.group(1)), gettext.c2py(formula.group(1))
    except ValueError:
        return None


@functools.cache
def same_plural_rule(one: str, other: str) -> bool:
    """Whether two ``Plural-Forms`` values say the same thing, however each is spelt.

    Weblate writes its own spelling of a rule -- ``n > 1`` for ``(n > 1)`` -- and a check that
    compared the text failed every catalogue it saved. The same number of forms, and the
    same form for every count up to a thousand, is the same rule.
    """
    a, b = _plural_rule(one), _plural_rule(other)
    if a is None or b is None or a[0] != b[0]:
        return False
    return all(a[1](n) == b[1](n) for n in range(1001))


def _django_locale(code: str) -> str:
    from django.utils.translation import to_locale

    return to_locale(code)


def merge(extracted: dict, existing: Catalogue | None, code: str) -> Catalogue:
    """The extracted messages, carrying over every translation the old catalogue had."""
    old = existing.messages if existing else {}
    messages: dict[tuple[str | None, str], Message] = {}
    forms = nplurals(code)
    for key, fresh in extracted.items():
        message = Message(
            msgid=fresh.msgid,
            plural=fresh.plural,
            context=fresh.context,
            references=list(fresh.references),
            comments=list(fresh.comments),
            flags=list(fresh.flags),
        )
        previous = old.get(key)
        if previous is not None:
            message.translator = list(previous.translator)
            for flag in previous.flags:
                # `draft` was this tool's word for what gettext and Weblate call `fuzzy`.
                flag = DRAFT if flag in DRAFT_FLAGS else flag
                if flag not in message.flags and flag != "python-format":
                    message.flags.append(flag)
            message.msgstr = list(previous.msgstr)
        message.flags.sort()
        wanted = forms if message.plural is not None else 1
        message.msgstr = (message.msgstr + [""] * wanted)[:wanted]
        messages[key] = message
    return Catalogue(
        header=header_for(code, existing.header if existing else {}), messages=messages
    )


def cmd_extract(check: bool) -> int:
    """Refresh every set, and say which set each count belongs to.

    Every set gets a catalogue for every language Postulo offers, whether or not the set
    has anything to say in it yet -- the same rule core follows. A set with no catalogue
    for a language would show English there, which is the documented behaviour for a third
    party and a regression for a plugin Postulo ships (#127).
    """
    stale: list[str] = []
    written: list[str] = []
    for subject in catalogue_sets():
        extracted = extract_all(subject)
        for code in translated_languages():
            path = po_path(code, subject)
            existing = parse(path.read_text(encoding="utf-8")) if path.exists() else None
            catalogue = merge(extracted, existing, code)
            text = dump(catalogue, code, subject)
            if check:
                current = path.read_text(encoding="utf-8") if path.exists() else ""
                if current != text:
                    stale.append(str(path.relative_to(project().root)))
                continue
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(text, encoding="utf-8", newline="\n")
        written.append(f"{subject.name}: {len(extracted)} messages")
    count = len(translated_languages())
    if check:
        if stale:
            print("Catalogues out of date; run scripts/messages.py extract:", *stale, sep="\n  ")
            return 1
        print(f"{count} catalogues current in each set; " + ", ".join(written))
        return 0
    print(f"{count} catalogues per set; " + ", ".join(written))
    return 0


# -------------------------------------------------------------------- .mo files


def compile_catalogue(catalogue: Catalogue, code: str | None = None) -> bytes:
    """A GNU .mo file: every entry with a translation, drafts included.

    A draft is compiled because a language is meant to be usable on the day it is drafted.
    Not a draft that would fail to format, though: a machine translation nobody has read
    may have lost a placeholder, and the page that formats it would raise. Given the
    language's ``code`` that is checked; a draft with a problem is left out and shows in
    English, and ``check`` names it.
    """
    code = code or _code_of(catalogue)
    entries: list[tuple[bytes, bytes]] = []
    header = "".join(f"{k}: {v}\n" for k, v in catalogue.header.items())
    entries.append((b"", header.encode()))
    for message in catalogue.messages.values():
        if not message.translated:
            continue
        if message.draft and code and message_problems(message, code):
            continue
        key = message.msgid
        if message.context is not None:
            key = f"{message.context}\x04{key}"
        if message.plural is not None:
            key = f"{key}\x00{message.plural}"
            value = "\x00".join(message.msgstr)
        else:
            value = message.msgstr[0]
        entries.append((key.encode(), value.encode()))
    entries.sort(key=lambda pair: pair[0])

    count = len(entries)
    header_size = 7 * 4
    ids_offset = header_size + 2 * count * 8
    ids_blob = b"".join(k + b"\0" for k, _ in entries)
    strs_offset = ids_offset + len(ids_blob)
    out = struct.pack("<7I", 0x950412DE, 0, count, header_size, header_size + count * 8, 0, 0)
    position = ids_offset
    for k, _ in entries:
        out += struct.pack("<2I", len(k), position)
        position += len(k) + 1
    position = strs_offset
    for _, v in entries:
        out += struct.pack("<2I", len(v), position)
        position += len(v) + 1
    out += ids_blob
    out += b"".join(v + b"\0" for _, v in entries)
    return out


def cmd_compile() -> int:
    """Every set, in one pass.

    A plugin that ships inside the image is compiled here rather than by a build step of
    its own: *Writing a plugin* tells a third party to ship its `.mo` files, and a built-in
    has no separate release to ship them in (#127).
    """
    written = 0
    for subject in catalogue_sets():
        for path in sorted(subject.locale.glob("*/LC_MESSAGES/django.po")):
            catalogue = parse(path.read_text(encoding="utf-8"))
            path.with_suffix(".mo").write_bytes(compile_catalogue(catalogue))
            written += 1
    print(f"{written} catalogues compiled.")
    if project().is_postulo:
        # Built with the .mo files rather than committed: every translation saved in
        # Weblate changes it, and a committed copy made every one of Weblate's pull
        # requests stale the moment it was opened (#349).
        status = project().locale / "status.json"
        status.write_text(_status_text(build_report()), encoding="utf-8", newline="\n")
        print(f"written {status.relative_to(project().root)}")
    return 0


def _code_of(catalogue: Catalogue) -> str | None:
    """The language a catalogue is in, from its ``Language`` header."""
    from django.utils.translation import to_language

    language = catalogue.header.get("Language", "")
    return find(to_language(language)) if language else None


# ----------------------------------------------------------------------- checks


def placeholders(text: str) -> list[str]:
    return sorted(m.group(0) for m in PLACEHOLDER.finditer(text) if m.group(0) != "%%")


@functools.cache
def counts_beyond_one(code: str) -> tuple[bool, ...]:
    """For each plural form of a language, whether its rule also selects a count of two or
    more.

    A form that only ever says *one* may spell the number out. A form that also counts
    twenty-one -- the Slavic, Baltic and Icelandic first forms, Slovene's at a hundred and
    one -- must carry the placeholder, or the page says "one application" at twenty-one,
    which nobody sees until somebody has twenty-one of something (#250).
    """
    header = PLURAL_FORMS.get(code, "nplurals=2; plural=(n != 1);")
    rule = gettext.c2py(header.split("plural=", 1)[1].strip().rstrip(";"))
    beyond = [False] * nplurals(code)
    for n in range(2, 1001):
        beyond[rule(n)] = True
    return tuple(beyond)


def _fails_to_format(source: str, form: str, singular: bool) -> str | None:
    """What Python's `%` operator says about a form, formatted the way the runtime will.

    The placeholder sets cannot see a stray `%`, nor a singular message that drops a
    positional placeholder; running the operator does, as `msgfmt --check-format` does
    (#497).
    """
    named = {m.group(1) for m in PLACEHOLDER.finditer(form) if m.group(1)}
    positional = [m.group(0) for m in PLACEHOLDER.finditer(form) if m.group(0)[1:2] in "sdifr"]
    expected = len([m for m in PLACEHOLDER.finditer(source) if m.group(0)[1:2] in "sdifr"])
    try:
        if named:
            form % dict.fromkeys(named, 1)
        else:
            form % ((1,) * (expected if singular else len(positional)))
    except (ValueError, TypeError, KeyError) as error:
        return f"{type(error).__name__}: {error}"
    return None


def problems_in(catalogue: Catalogue, code: str) -> list[str]:
    found: list[str] = []
    for message in catalogue.messages.values():
        found.extend(message_problems(message, code))
    return found


def message_problems(message: Message, code: str) -> list[str]:
    """What is wrong with one translated entry: its plural forms and its placeholders."""
    found: list[str] = []
    if not message.translated:
        return found
    forms = nplurals(code)
    beyond = counts_beyond_one(code)
    if message.plural is not None and len(message.msgstr) != forms:
        found.append(f"{message.msgid!r}: {len(message.msgstr)} forms, {code} has {forms}")
    sources_ = [message.msgid] if message.plural is None else [message.msgid, message.plural]
    expected = {p for s in sources_ for p in placeholders(s)}
    named = {p for p in expected if p.startswith("%(") or p.startswith("{")}
    python_format = "python-format" in message.flags or any(
        "%" in p for s_ in sources_ for p in placeholders(s_)
    )
    for index, form in enumerate(message.msgstr):
        got = set(placeholders(form))
        if python_format and (
            error := _fails_to_format(message.msgid, form, message.plural is None)
        ):
            found.append(f"{message.msgid!r} → {form!r}: would fail to format ({error})")
            continue
        counts_higher = index < len(beyond) and beyond[index]
        # A form that only ever says *one* may drop the count ("one application"); a
        # form that also counts higher must carry every named placeholder; and nothing
        # may be invented.
        if not got <= expected or (named and message.plural is None and got != expected):
            found.append(f"{message.msgid!r} → {form!r}: placeholders differ")
        elif message.plural is not None and named - got and counts_higher:
            found.append(
                f"{message.msgid!r} → {form!r}: form {index} also counts higher than one, "
                f"so it cannot drop {sorted(named - got)}"
            )
    return found


def cmd_check() -> int:
    """Fail on a problem in a reviewed entry; name the ones in drafts.

    A draft with a problem is a machine translation nobody has read: it is not compiled
    (`compile_catalogue`), so it cannot break a page, and Weblate shows the same failing
    check beside it for whoever reads it next. Failing the build on it would turn every one
    of Weblate's pull requests red for something only a speaker can fix.
    """
    failures = warnings = 0
    for subject in catalogue_sets():
        where = "" if subject.is_core else f"{subject.name} "
        for code in translated_languages():
            path = po_path(code, subject)
            if not path.exists():
                print(f"{where}{code}: no catalogue at {path.relative_to(project().root)}")
                failures += 1
                continue
            catalogue = parse(path.read_text(encoding="utf-8"))
            plural_forms = catalogue.header.get("Plural-Forms", "")
            if not same_plural_rule(plural_forms, PLURAL_FORMS.get(code, "")):
                print(f"{where}{code}: Plural-Forms is not the rule postulo.core.languages has")
                failures += 1
            for message in catalogue.messages.values():
                for problem in message_problems(message, code):
                    if message.draft:
                        print(f"{where}{code}: (draft, not compiled) {problem}")
                        warnings += 1
                    else:
                        print(f"{where}{code}: {problem}")
                        failures += 1
    if warnings:
        print(f"{warnings} draft(s) with a problem, left out of the .mo files")
    print("no problems" if not failures else f"{failures} problem(s)")
    return 1 if failures else 0


# ------------------------------------------------------------------------ stats


def stats_for(catalogue: Catalogue) -> dict[str, int]:
    """How far along one catalogue is.

    ``translated`` is every string with something written in each form, and ``drafts`` and
    ``reviewed`` divide all of those between them: a draft is flagged ``fuzzy`` (Weblate's
    *needs editing*), and a reviewed string is one a speaker saved in Weblate, which clears
    the flag (#312, #706).
    """
    total = len(catalogue.messages)
    translated = sum(1 for m in catalogue.messages.values() if m.translated)
    drafts = sum(1 for m in catalogue.messages.values() if m.translated and m.draft)
    return {
        "total": total,
        "translated": translated,
        "drafts": drafts,
        "reviewed": translated - drafts,
        "percent": round(100 * translated / total) if total else 0,
    }


def _as_the_reader_sees_it(catalogues: list[Catalogue]) -> Catalogue:
    """Every set's messages merged the way Django merges them: the first one wins.

    Two sets can hold the same English string, and the reader is shown one of them --
    Postulo's own, because its catalogue is read first. Counting both would report more
    strings than exist and would count the copy nobody sees.
    """
    merged: dict[tuple[str | None, str], Message] = {}
    for catalogue in catalogues:
        for key, message in catalogue.messages.items():
            merged.setdefault(key, message)
    return Catalogue(header={}, messages=merged)


def _status_text(report: dict[str, dict[str, int]]) -> str:
    return json.dumps(report, indent=2, ensure_ascii=False) + "\n"


def build_report() -> dict[str, dict[str, int]]:
    """How far along each language is, counting every set.

    Summed rather than reported per set, because the figure is shown to somebody choosing
    a language and "português is complete" has to mean the interface they will see, not
    the part of it that happens to live in core (#127).
    """
    report: dict[str, dict[str, int]] = {}
    # Found once: each call walks the source tree, and inside the loop that was once per
    # language, 68 walks for one answer (#724).
    subjects = catalogue_sets()
    for code in translated_languages():
        found = [
            parse(po_path(code, subject).read_text(encoding="utf-8"))
            for subject in subjects
            if po_path(code, subject).exists()
        ]
        if not found:
            continue
        report[code] = stats_for(_as_the_reader_sees_it(found))
    return report


def cmd_stats() -> int:
    """Print the report. ``compile`` writes it to ``locale/status.json`` for the picker."""
    report = build_report()
    width = max((len(NATIVE_NAMES[c]) for c in report), default=10)
    for code, row in report.items():
        state = f"{row['percent']:3d} %"
        if row["drafts"]:
            state += f"  ({row['drafts']} draft)"
        counts = f"{row['translated']:4}/{row['total']:<4}"
        print(f"{code:6} {NATIVE_NAMES[code]:{width}}  {counts} {state}")
    return 0


# ------------------------------------------------------------------- the guard

#: The committer Weblate writes as: the identity its environment gives it
#: (``WEBLATE_DEFAULT_COMMITER_EMAIL``), and its own default before that was set.
WEBLATE_COMMITTERS = frozenset({"weblate@tiagoagueda.com", "noreply@weblate.org"})

#: The trailer Weblate puts on every commit it makes, which survives a squash or a rebase
#: merge that rewrites the committer.
WEBLATE_TRAILER = re.compile(r"^Translate-URL: https://translate\.tiagoagueda\.com/", re.MULTILINE)

#: A commit carrying this may move a translation to a renamed string without marking it a
#: draft: an English edit that changes nothing a speaker would translate differently.
CARRY_TRAILER = re.compile(r"^L10n-Carry: keep-review\s*$", re.MULTILINE)


def _git(*args: str) -> str:
    import subprocess

    return subprocess.run(  # noqa: S603 - git, with arguments this module writes
        ["git", *args],  # noqa: S607 - whichever git the shell finds, as every hook does
        cwd=project().root,
        capture_output=True,
        check=True,
    ).stdout.decode("utf-8")


def _git_file(revision: str, path: str) -> str | None:
    import subprocess

    done = subprocess.run(  # noqa: S603 - git, with arguments this module writes
        ["git", "show", f"{revision}:{path}"],  # noqa: S607 - as above
        cwd=project().root,
        capture_output=True,
    )
    return done.stdout.decode("utf-8") if done.returncode == 0 else None


def from_weblate(committer: str, message: str, paths: list[str]) -> bool:
    """Whether a commit is one of Weblate's: by its committer, or by its trailer.

    The trailer alone counts only for a commit that touches nothing but catalogues: a
    squash or rebase merge of Weblate's pull request makes the maintainer its committer,
    and the trailer is what is left of where it came from. This catches a mistake, not an
    attacker -- whoever can push here can write any committer they like.
    """
    if committer.lower() in WEBLATE_COMMITTERS:
        return True
    return bool(WEBLATE_TRAILER.search(message)) and all(p.endswith(".po") for p in paths)


def translation_changes(before: str | None, after: str, *, carry_reviewed: bool) -> list[str]:
    """What a change to one catalogue does to its translations that only Weblate may do.

    Allowed: anything about the English, the references, the comments and the header; a
    new string with an empty slot; a string the source lost. Not allowed: writing a
    translation, changing or emptying one, and marking one a draft or a reviewed string.

    One exception, because a script that renames a string should not lose what speakers
    wrote for it: a new string may arrive with the exact translation of a string the same
    change removed. It arrives as a draft -- the English changed, so a speaker should read
    it again -- unless the commit says ``L10n-Carry: keep-review``.
    """
    new = parse(after)
    if before is None:
        return [
            f"{m.msgid!r}: a new catalogue arrives with a translation in it"
            for m in new.messages.values()
            if any(m.msgstr)
        ]
    old = parse(before)
    removed = {tuple(m.msgstr): m for key, m in old.messages.items() if key not in new.messages}
    found: list[str] = []
    for key, message in new.messages.items():
        previous = old.messages.get(key)
        if previous is not None:
            if previous.msgstr != message.msgstr:
                found.append(f"{message.msgid!r}: its translation was changed here")
            elif previous.draft != message.draft and any(message.msgstr):
                found.append(
                    f"{message.msgid!r}: marked {'a draft' if message.draft else 'reviewed'}"
                )
            continue
        if not any(message.msgstr):
            continue
        source = removed.get(tuple(message.msgstr))
        if source is None or not source.translated:
            found.append(f"{message.msgid!r}: a new string arrives with a translation")
        elif not message.draft and not carry_reviewed:
            found.append(
                f"{message.msgid!r}: carried from {source.msgid!r} without being marked a "
                "draft (or `L10n-Carry: keep-review`)"
            )
    return found


def _commits(base: str, head: str) -> list[str]:
    return _git("rev-list", "--reverse", "--no-merges", f"{base}..{head}").split()


def guard_commit(commit: str) -> list[str]:
    committer, _, message = _git("show", "-s", "--format=%ce%n%B", commit).partition("\n")
    paths = _git("diff-tree", "--no-commit-id", "--name-only", "-r", "--root", commit).split()
    if from_weblate(committer.strip(), message, paths):
        return []
    parent = f"{commit}^" if _git("rev-list", "--parents", "-n", "1", commit).split()[1:] else None
    carry = bool(CARRY_TRAILER.search(message))
    found = []
    for path in paths:
        if not path.endswith(".po"):
            continue
        after = _git_file(commit, path)
        if after is None:
            continue  # deleted
        before = _git_file(parent, path) if parent else None
        for problem in translation_changes(before, after, carry_reviewed=carry):
            found.append(f"{commit[:9]} {path}: {problem}")
    return found


def guard_staged() -> list[str]:
    paths = _git("diff", "--cached", "--name-only", "--diff-filter=AM").split()
    found = []
    for path in paths:
        if not path.endswith(".po"):
            continue
        after = _git_file("", path)  # ":path" is the index
        before = _git_file("HEAD", path)
        for problem in translation_changes(before, after or "", carry_reviewed=False):
            found.append(f"staged {path}: {problem}")
    return found


def cmd_guard(base: str | None, head: str, staged: bool) -> int:
    """Refuse translations that did not come from Weblate (#706).

    Every translation, correction and review is made in Weblate, which commits it back as
    a pull request. This reads every commit in ``base..head`` -- or, with ``--staged``,
    what is about to be committed -- and names each translation written anywhere else.
    """
    if staged:
        problems = guard_staged()
    else:
        if not base or set(base) == {"0"}:
            base = _git("merge-base", "origin/main", head).strip()
        else:
            base = _git("merge-base", base, head).strip()
        problems = [p for commit in _commits(base, head) for p in guard_commit(commit)]
    for problem in problems:
        print(problem)
    if problems:
        print(
            f"{len(problems)} translation(s) written outside Weblate. Translations are made "
            "at https://translate.tiagoagueda.com and come back as its pull request; see "
            "docs/TRANSLATING.md. Revert these and leave the slots empty."
        )
        return 1
    print("no translation written outside Weblate")
    return 0


def cmd_gate(codes: list[str]) -> int:
    """Every string, in every set, present in each of ``codes``: a draft counts (#347).

    Run on Weblate's pull request, which is what brings the machine drafts of a new string
    in, and before a release.
    """
    missing = 0
    for subject in catalogue_sets():
        for code in codes:
            path = po_path(code, subject)
            catalogue = parse(path.read_text(encoding="utf-8")) if path.exists() else None
            untranslated = (
                [m.msgid for m in catalogue.messages.values() if not m.translated]
                if catalogue
                else ["(no catalogue)"]
            )
            if untranslated:
                missing += len(untranslated)
                print(
                    f"{subject.name} {code}: {len(untranslated)} missing, e.g. {untranslated[:3]}"
                )
    print("complete" if not missing else f"{missing} string(s) missing")
    return 1 if missing else 0


# ------------------------------------------------------------------------- main


def main(argv: list[str] | None = None) -> int:
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")  # language names, on a Windows console
    parser = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    sub = parser.add_subparsers(dest="command", required=True)
    extract = sub.add_parser("extract", help="refresh the catalogues from the source")
    extract.add_argument(
        "--check", action="store_true", help="only report whether they are current"
    )
    sub.add_parser("compile", help="write the .mo files, and locale/status.json")
    sub.add_parser("check", help="placeholders and plural forms agree")
    sub.add_parser("stats", help="how far along each language is")
    guard = sub.add_parser("guard", help="refuse translations written outside Weblate")
    guard.add_argument("--base", help="the commit before the range (default: origin/main)")
    guard.add_argument("--head", default="HEAD", help="the last commit of the range")
    guard.add_argument("--staged", action="store_true", help="check what is staged instead")
    gate = sub.add_parser("gate", help="every string present in these languages")
    gate.add_argument("codes", nargs="+", metavar="CODE")
    args = parser.parse_args(argv)
    project()
    if args.command == "guard":
        return cmd_guard(args.base, args.head, args.staged)
    if args.command == "gate":
        return cmd_gate(args.codes)
    if args.command == "extract":
        return cmd_extract(args.check)
    if args.command == "compile":
        return cmd_compile()
    if args.command == "check":
        return cmd_check()
    return cmd_stats()
