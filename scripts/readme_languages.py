"""Writes the README's *In your language* table from the language registry (#937).

    uv run python scripts/readme_languages.py           # rewrite the section
    uv run python scripts/readme_languages.py --check   # fail if it is out of date

The rows are the languages somebody has begun translating, in the order of their own names;
the flag is the SVG the language picker draws, picked by ``FLAG_COUNTRIES``; the sentence is
``core.tagline.TAGLINE`` as the language's own catalogue has it. Nothing here is typed by
hand, and ``tests/test_readme.py`` fails when the README drifts from what this writes.
"""

from __future__ import annotations

import html
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO / "src"))

from postulo.core import languages, messages_tool  # noqa: E402

README = REPO / "README.md"
LOCALE = REPO / "src" / "postulo" / "locale"
START = "<!-- languages:start (written by scripts/readme_languages.py; do not edit by hand) -->"
END = "<!-- languages:end -->"
WEBLATE = "https://translate.tiagoagueda.com/projects/postulo/"
#: `core.tagline.TAGLINE`, repeated because importing it needs Django settings; a test
#: (tests/test_readme.py) fails if the two differ.
SOURCE_SENTENCE = (
    "Postulo is a self-hosted job application manager: your applications, CVs and cover "
    "letters on your own server, with no paid features."
)


def _catalogue(code: str):
    path = LOCALE / code.replace("-", "_") / "LC_MESSAGES" / "django.po"
    if not path.is_file():
        return None
    return messages_tool.parse(path.read_text(encoding="utf-8"))


def _sentence(code: str) -> str | None:
    """The language's sentence, or None while it is untranslated or still a draft."""
    if code == languages.SOURCE:
        return SOURCE_SENTENCE
    catalogue = _catalogue(code)
    message = catalogue.messages.get((None, SOURCE_SENTENCE)) if catalogue else None
    if message is None or not message.translated or message.draft:
        return None
    return message.msgstr[0]


def _begun(code: str) -> bool:
    """Whether any string of the catalogue is translated (a scaffold is not listed)."""
    if code == languages.SOURCE:
        return True
    catalogue = _catalogue(code)
    return catalogue is not None and any(
        m.translated and not m.draft for m in catalogue.messages.values()
    )


def rows() -> list[tuple[str, str, str]]:
    found = []
    for code, name in languages.NATIVE_NAMES.items():
        if _begun(code):
            found.append((name.casefold(), code, name))
    return [(code, name, _sentence(code) or "") for _, code, name in sorted(found)]


def section() -> str:
    table = rows()
    lines = [
        START,
        "",
        f"Postulo speaks **{len(table)} languages**, each named here as its speakers write it.",
        "",
        "| | Language | What Postulo is |",
        "| --- | --- | --- |",
    ]
    for code, name, sentence in table:
        country = languages.flag_country(code).lower()
        flag = (
            f'<img src="src/postulo/static/flags/{country}.svg" width="20" height="15" alt="">'
            if country and (REPO / "src/postulo/static/flags" / f"{country}.svg").is_file()
            else ""
        )
        direction = languages.direction(code)
        label = f'<span lang="{code}" dir="{direction}">{html.escape(name)}</span>'
        text = (
            f'<span lang="{code}" dir="{direction}">{html.escape(sentence)}</span>'
            if sentence
            else "—"
        )
        lines.append(f"| {flag} | **{label}** | {text} |")
    lines += ["", END]
    return "\n".join(lines)


def render(text: str) -> str:
    start, end = text.index(START), text.index(END) + len(END)
    return text[:start] + section() + text[end:]


def main(argv: list[str]) -> int:
    current = README.read_text(encoding="utf-8")
    wanted = render(current)
    if "--check" in argv:
        if wanted != current:
            print("README.md's language table is out of date: run scripts/readme_languages.py")
            return 1
        return 0
    README.write_text(wanted, encoding="utf-8", newline="\n")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
