"""Writes ``.github/README.md``, the README GitHub shows on the read-only mirror (#938).

    uv run python scripts/github_readme.py           # rewrite the file
    uv run python scripts/github_readme.py --check   # fail if it is out of date

GitHub looks in ``.github/`` before the repository root; Forgejo looks at the root first. So
this file changes only what GitHub shows. It is the root README with a notice on top, and
with every relative link made absolute on Forgejo -- GitHub resolves them from ``.github/``,
where they would break, and absolute ones also send readers to the official repository.
"""

from __future__ import annotations

import re
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
README = REPO / "README.md"
MIRROR = REPO / ".github" / "README.md"

FORGEJO = "https://source.tiagoagueda.com/postulo/postulo"
NOTICE = (
    "> **This is a read-only mirror.** Postulo is developed at "
    f"[source.tiagoagueda.com/postulo/postulo]({FORGEJO}). Please open issues, suggestions "
    "and pull requests there.\n\n"
)
HEADER = "<!-- Generated from ../README.md by scripts/github_readme.py; do not edit by hand. -->\n"

#: Markdown `](target)` and HTML `src="…"` / `href="…"`; the group is the target.
_MARKDOWN = re.compile(r"(\]\()([^)\s]+)")
_HTML = re.compile(r'((?:src|href)=")([^"]+)')


def _absolute(target: str, attribute: str) -> str:
    if re.match(r"([a-z][a-z0-9+.-]*:|//|#)", target, re.I):
        return target
    path = target.removeprefix("./")
    # Pictures are fetched raw; everything else is a page on Forgejo.
    is_image = attribute == "src" or path.lower().endswith(
        (".png", ".jpg", ".jpeg", ".gif", ".svg", ".webp")
    )
    return f"{FORGEJO}/{'raw' if is_image else 'src'}/branch/main/{path}"


def render(text: str) -> str:
    text = _MARKDOWN.sub(lambda m: m[1] + _absolute(m[2], "href"), text)
    text = _HTML.sub(lambda m: m[1] + _absolute(m[2], m[1][:3]), text)
    return HEADER + NOTICE + text


def main(argv: list[str]) -> int:
    wanted = render(README.read_text(encoding="utf-8"))
    if "--check" in argv:
        current = MIRROR.read_text(encoding="utf-8") if MIRROR.is_file() else ""
        if current != wanted:
            print(".github/README.md is out of date: run scripts/github_readme.py")
            return 1
        return 0
    MIRROR.write_text(wanted, encoding="utf-8", newline="\n")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
