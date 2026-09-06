"""Keep Postulo's translation catalogues current, and turn them into what Django reads.

Standard library and Django only — no GNU gettext on the machine. ``makemessages`` and
``compilemessages`` shell out to ``xgettext`` and ``msgfmt``, which a Windows laptop, a
slim container and most CI images do not have; this does the same work in Python so
that every contributor and every build can run it.

    uv run python scripts/messages.py extract          # refresh every .po from the source
    uv run python scripts/messages.py extract --check  # fail if a .po is out of date
    uv run python scripts/messages.py compile          # write the .mo files Django loads
    uv run python scripts/messages.py stats [--write]  # how far along each language is
    uv run python scripts/messages.py check            # placeholders and plural forms agree

One deliberate difference from ``msgfmt``: an entry flagged ``draft`` — a machine-assisted
translation nobody has reviewed yet — is compiled, so a language is usable on day one.
An entry flagged ``fuzzy`` is not, as everywhere else. Reviewing a draft means reading it
and deleting the flag.
"""

from __future__ import annotations

import argparse
import ast
import io
import json
import re
import struct
import sys
import tokenize
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
PACKAGE = REPO / "src" / "postulo"
LOCALE = PACKAGE / "locale"
sys.path.insert(0, str(REPO / "src"))

from postulo.core.languages import (  # noqa: E402
    LANGUAGES,
    NATIVE_NAMES,
    PLURAL_FORMS,
    SOURCE,
    nplurals,
)

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
}

SKIP_DIRS = {"migrations", "static", "locale", "__pycache__"}
PLACEHOLDER = re.compile(r"%\((\w+)\)[sdifr]|%[sdifr%]|\{(\w*)\}")


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
                message = Message(msgid=msgid, references=[f"{origin}:{tok.start[0]}"])
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


def sources() -> list[Path]:
    files = []
    for path in sorted(PACKAGE.rglob("*")):
        if not path.is_file() or path.suffix not in (".py", ".html", ".txt"):
            continue
        if SKIP_DIRS & set(path.relative_to(PACKAGE).parts[:-1]):
            continue
        files.append(path)
    return files


def extract_all() -> dict[tuple[str | None, str], Message]:
    """Every message in the source tree, merged by (context, msgid)."""
    import django
    from django.conf import settings

    if not settings.configured:
        settings.configure(USE_I18N=True)
        django.setup()

    merged: dict[tuple[str | None, str], Message] = {}
    for path in sources():
        origin = path.relative_to(REPO).as_posix()
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
    return (path, int(line) if line.isdigit() else 0)


# ------------------------------------------------------------------ .po files


def _quote(text: str) -> str:
    escaped = (
        text.replace("\\", "\\\\").replace('"', '\\"').replace("\n", "\\n").replace("\t", "\\t")
    )
    return f'"{escaped}"'


def _write_field(out: list[str], name: str, value: str) -> None:
    if "\n" in value.rstrip("\n") or len(value) > 72:
        out.append(f'{name} ""')
        parts = value.split("\n")
        for index, part in enumerate(parts):
            if index < len(parts) - 1:
                out.append(_quote(part + "\n"))
            elif part:
                out.append(_quote(part))
    else:
        out.append(f"{name} {_quote(value)}")


def dump(catalogue: Catalogue, code: str) -> str:
    out: list[str] = [
        f"# {NATIVE_NAMES.get(code, code)} translation of Postulo.",
        "# This file is distributed under the same license as Postulo (AGPL-3.0-or-later).",
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
            out.append("#, " + ", ".join(message.flags))
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
    return chunk.replace("\\n", "\n").replace("\\t", "\t").replace('\\"', '"').replace("\\\\", "\\")


def po_path(code: str) -> Path:
    from django.utils.translation import to_locale

    return LOCALE / to_locale(code) / "LC_MESSAGES" / "django.po"


def translated_languages() -> list[str]:
    return [code for code, _name in LANGUAGES if code != SOURCE]


def header_for(code: str, existing: dict[str, str]) -> dict[str, str]:
    now = datetime.now(UTC).strftime("%Y-%m-%d %H:%M%z")
    header = {
        "Project-Id-Version": "Postulo",
        "Report-Msgid-Bugs-To": "https://source.tiagoagueda.com/tiagoagueda/postulo/issues",
        "POT-Creation-Date": now,
        "PO-Revision-Date": existing.get("PO-Revision-Date", now),
        "Last-Translator": existing.get("Last-Translator", "Postulo contributors"),
        "Language-Team": existing.get("Language-Team", NATIVE_NAMES.get(code, code)),
        "Language": code.replace("-", "_") if "-" not in code else _django_locale(code),
        "MIME-Version": "1.0",
        "Content-Type": "text/plain; charset=UTF-8",
        "Content-Transfer-Encoding": "8bit",
        "Plural-Forms": PLURAL_FORMS.get(code, "nplurals=2; plural=(n != 1);"),
        "X-Generator": "postulo scripts/messages.py",
    }
    if "--check" in sys.argv:
        header["POT-Creation-Date"] = existing.get("POT-Creation-Date", now)
    return header


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
                if flag not in message.flags and flag != "python-format":
                    message.flags.append(flag)
            message.msgstr = list(previous.msgstr)
        wanted = forms if message.plural is not None else 1
        message.msgstr = (message.msgstr + [""] * wanted)[:wanted]
        messages[key] = message
    return Catalogue(
        header=header_for(code, existing.header if existing else {}), messages=messages
    )


def cmd_extract(check: bool) -> int:
    extracted = extract_all()
    stale: list[str] = []
    for code in translated_languages():
        path = po_path(code)
        existing = parse(path.read_text(encoding="utf-8")) if path.exists() else None
        catalogue = merge(extracted, existing, code)
        text = dump(catalogue, code)
        if check:
            current = path.read_text(encoding="utf-8") if path.exists() else ""
            if _without_dates(current) != _without_dates(text):
                stale.append(str(path.relative_to(REPO)))
            continue
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text, encoding="utf-8", newline="\n")
    if check:
        if stale:
            print("Catalogues out of date; run scripts/messages.py extract:", *stale, sep="\n  ")
            return 1
        print(f"{len(translated_languages())} catalogues current; {len(extracted)} messages.")
        return 0
    print(f"{len(extracted)} messages written to {len(translated_languages())} catalogues.")
    return 0


def _without_dates(text: str) -> str:
    return re.sub(r'"(POT-Creation|PO-Revision)-Date: [^"]*"', "", text)


# -------------------------------------------------------------------- .mo files


def compile_catalogue(catalogue: Catalogue) -> bytes:
    """A GNU .mo file: the entries with a translation, ``fuzzy`` ones left out."""
    entries: list[tuple[bytes, bytes]] = []
    header = "".join(f"{k}: {v}\n" for k, v in catalogue.header.items())
    entries.append((b"", header.encode()))
    for message in catalogue.messages.values():
        if "fuzzy" in message.flags or not message.translated:
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
    written = 0
    for path in sorted(LOCALE.glob("*/LC_MESSAGES/django.po")):
        catalogue = parse(path.read_text(encoding="utf-8"))
        path.with_suffix(".mo").write_bytes(compile_catalogue(catalogue))
        written += 1
    print(f"{written} catalogues compiled.")
    return 0


# ----------------------------------------------------------------------- checks


def placeholders(text: str) -> list[str]:
    return sorted(m.group(0) for m in PLACEHOLDER.finditer(text) if m.group(0) != "%%")


def problems_in(catalogue: Catalogue, code: str) -> list[str]:
    found: list[str] = []
    forms = nplurals(code)
    for message in catalogue.messages.values():
        if not message.translated:
            continue
        if message.plural is not None and len(message.msgstr) != forms:
            found.append(f"{message.msgid!r}: {len(message.msgstr)} forms, {code} has {forms}")
        sources_ = [message.msgid] if message.plural is None else [message.msgid, message.plural]
        expected = {p for s in sources_ for p in placeholders(s)}
        named = {p for p in expected if p.startswith("%(") or p.startswith("{")}
        for form in message.msgstr:
            got = set(placeholders(form))
            # A plural form may drop the count ("one application"); a named placeholder
            # must appear in every form, and nothing may be invented.
            if not got <= expected or (named and message.plural is None and got != expected):
                found.append(f"{message.msgid!r} → {form!r}: placeholders differ")
            elif message.plural is not None and named - got and form is message.msgstr[-1]:
                found.append(f"{message.msgid!r} → {form!r}: named placeholder missing")
    return found


def cmd_check() -> int:
    failures = 0
    for code in translated_languages():
        path = po_path(code)
        if not path.exists():
            print(f"{code}: no catalogue at {path.relative_to(REPO)}")
            failures += 1
            continue
        catalogue = parse(path.read_text(encoding="utf-8"))
        plural_forms = catalogue.header.get("Plural-Forms", "")
        if plural_forms != PLURAL_FORMS.get(code):
            print(f"{code}: Plural-Forms header differs from postulo.core.languages")
            failures += 1
        for problem in problems_in(catalogue, code):
            print(f"{code}: {problem}")
            failures += 1
    print("no problems" if not failures else f"{failures} problem(s)")
    return 1 if failures else 0


# ------------------------------------------------------------------------ stats


def stats_for(catalogue: Catalogue) -> dict[str, int]:
    total = len(catalogue.messages)
    translated = sum(1 for m in catalogue.messages.values() if m.translated)
    drafts = sum(1 for m in catalogue.messages.values() if m.translated and "draft" in m.flags)
    fuzzy = sum(1 for m in catalogue.messages.values() if "fuzzy" in m.flags)
    return {
        "total": total,
        "translated": translated,
        "drafts": drafts,
        "fuzzy": fuzzy,
        "reviewed": translated - drafts,
        "percent": round(100 * translated / total) if total else 0,
    }


def cmd_stats(write: bool) -> int:
    report: dict[str, dict[str, int]] = {}
    for code in translated_languages():
        path = po_path(code)
        if not path.exists():
            continue
        report[code] = stats_for(parse(path.read_text(encoding="utf-8")))
    width = max((len(NATIVE_NAMES[c]) for c in report), default=10)
    for code, row in report.items():
        state = f"{row['percent']:3d} %"
        if row["drafts"]:
            state += f"  ({row['drafts']} draft)"
        if row["fuzzy"]:
            state += f"  ({row['fuzzy']} fuzzy)"
        counts = f"{row['translated']:4}/{row['total']:<4}"
        print(f"{code:6} {NATIVE_NAMES[code]:{width}}  {counts} {state}")
    if write:
        (LOCALE / "status.json").write_text(
            json.dumps(report, indent=2, ensure_ascii=False) + "\n", encoding="utf-8", newline="\n"
        )
        print(f"written to {(LOCALE / 'status.json').relative_to(REPO)}")
    return 0


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
    sub.add_parser("compile", help="write the .mo files")
    sub.add_parser("check", help="placeholders and plural forms agree")
    stats = sub.add_parser("stats", help="how far along each language is")
    stats.add_argument("--write", action="store_true", help="also write locale/status.json")
    args = parser.parse_args(argv)
    if args.command == "extract":
        return cmd_extract(args.check)
    if args.command == "compile":
        return cmd_compile()
    if args.command == "check":
        return cmd_check()
    return cmd_stats(args.write)


if __name__ == "__main__":
    sys.exit(main())
