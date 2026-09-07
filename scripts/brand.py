"""Derive Postulo's served brand images from the one source file.

The mark is a layered paper-cut ``P``. It lives once, at ``assets/brand/postulo.png``, and
everything the application serves is generated from it by this script and committed — the
same arrangement as the stylesheet and the icon set, so an instance runs without needing
the tooling that produced them.

    uv run python scripts/brand.py          # write the derived images
    uv run python scripts/brand.py --check  # fail if the mark changed without them

Three decisions worth knowing:

**The source here is 1024 pixels square, not the designer's original.** The largest thing
derived from it is 512, so a 1024 master is twice what any output needs and no detail that
reaches a screen is lost. It also keeps the file under the repository's size limit, which
exists for good reasons. The full-resolution artwork belongs wherever it is being designed,
not in a source tree.

**The source is trimmed before anything is scaled.** It ships with transparent margins, and
a favicon made from the untrimmed square wastes a third of its 32 pixels on nothing, which
at that size is the difference between a recognisable mark and a smudge.

**``--check`` compares recorded digests rather than rebuilding.** It used to rebuild the
images and compare the bytes, which fails on any machine but the one that last ran the
script: Pillow's platform wheels take different paths through LANCZOS, so the resampled
pixels differ by one here and there. The three small icons came out identical and
everything from 64 pixels upward did not, and continuous integration had reported the mark
as out of date on every run since it was added. A check whose answer depends on which
machine asked is not a check. So `brand.json` records what was built and what it was built
from, and `--check` answers the question that is actually worth asking — *were these files
made from this source?* — rather than *does this machine round the same way as the last
one?* It trusts that whoever ran the script ran it, exactly as the committed stylesheet
does.

**The Apple touch icon gets a solid background.** iOS composites a transparent icon onto
black, which turns a navy mark into a dark rectangle. Giving it the page's own off-white
means it looks the same on a home screen as it does in a tab.
"""

from __future__ import annotations

import hashlib
import json
import sys
from pathlib import Path

from PIL import Image

ROOT = Path(__file__).resolve().parent.parent
SOURCE = ROOT / "assets" / "brand" / "postulo.png"
OUT = ROOT / "src" / "postulo" / "static" / "brand"

#: What was built and what it was built from. Beside the source rather than in `static/`,
#: because it is a record for the repository and not a file to serve.
MANIFEST = ROOT / "assets" / "brand" / "brand.json"

#: What the page sits on, so the Apple icon matches the rest rather than sitting on black.
APPLE_BACKGROUND = (248, 250, 252, 255)

#: name -> (size, padding as a fraction of the side, background)
DERIVED = {
    # The tab. Small enough that the mark needs the whole square.
    "favicon-16.png": (16, 0.0, None),
    "favicon-32.png": (32, 0.0, None),
    "favicon-48.png": (48, 0.0, None),
    # A home screen. Padded, because every platform rounds the corners off one.
    "apple-touch-icon.png": (180, 0.12, APPLE_BACKGROUND),
    # The manifest's two, for a phone that installs it.
    "icon-192.png": (192, 0.08, None),
    "icon-512.png": (512, 0.08, None),
    # The wordmark's companion in the header, and the one the README shows.
    "logo-64.png": (64, 0.0, None),
    "logo-256.png": (256, 0.0, None),
}


def rendered(source: Image.Image, size: int, padding: float, background) -> Image.Image:
    """The mark, trimmed and centred in a square of ``size``."""
    mark = source.crop(source.getbbox())
    inner = max(1, round(size * (1 - 2 * padding)))
    # Fit rather than fill: the mark is taller than it is wide and must not be stretched.
    scale = min(inner / mark.width, inner / mark.height)
    mark = mark.resize(
        (max(1, round(mark.width * scale)), max(1, round(mark.height * scale))),
        Image.Resampling.LANCZOS,
    )
    canvas = Image.new("RGBA", (size, size), background or (0, 0, 0, 0))
    canvas.alpha_composite(mark, ((size - mark.width) // 2, (size - mark.height) // 2))
    return canvas


def build() -> dict[str, bytes]:
    import io

    source = Image.open(SOURCE).convert("RGBA")
    produced: dict[str, bytes] = {}
    for name, (size, padding, background) in DERIVED.items():
        buffer = io.BytesIO()
        rendered(source, size, padding, background).save(buffer, format="PNG", optimize=True)
        produced[name] = buffer.getvalue()

    # One .ico holding the three small sizes, for anything that still asks for /favicon.ico.
    buffer = io.BytesIO()
    rendered(source, 48, 0.0, None).save(buffer, format="ICO", sizes=[(16, 16), (32, 32), (48, 48)])
    produced["favicon.ico"] = buffer.getvalue()
    return produced


def digest(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def spec() -> dict:
    """The recipe, so a change to a size or a padding counts as a change too.

    Digests alone answer "were these built from this source", and would miss somebody
    editing the table above without re-running the script. This is the cheap half of what
    comparing bytes used to give, without the half that depended on which machine asked.
    """
    # Through JSON and back, so that what is compared is what was written: a tuple in
    # the table comes back as a list, and a spec that differs only in that is not a
    # difference anybody meant.
    return json.loads(json.dumps(dict(sorted(DERIVED.items()))))


def check() -> int:
    """Fail if the mark changed without its derivatives, or a derivative was edited."""
    if not MANIFEST.is_file():
        print(f"No record at {MANIFEST}; run `uv run python scripts/brand.py`", file=sys.stderr)
        return 1
    recorded = json.loads(MANIFEST.read_text(encoding="utf-8"))

    if recorded.get("source") != digest(SOURCE.read_bytes()):
        print(
            "The mark has changed and its derivatives have not been rebuilt.",
            "Run `uv run python scripts/brand.py` and commit the result.",
            sep="\n",
            file=sys.stderr,
        )
        return 1

    if recorded.get("spec") != spec():
        print(
            "The sizes or the padding have changed and the images have not been rebuilt.",
            "Run `uv run python scripts/brand.py` and commit the result.",
            sep="\n",
            file=sys.stderr,
        )
        return 1

    wrong = []
    for name, expected in sorted(recorded.get("derived", {}).items()):
        path = OUT / name
        if not path.is_file():
            wrong.append(f"{name} is missing")
        elif digest(path.read_bytes()) != expected:
            wrong.append(f"{name} is not what the script wrote")
    if wrong:
        print("The derived images disagree with the record:", file=sys.stderr)
        for line in wrong:
            print(f"  {line}", file=sys.stderr)
        print("Run `uv run python scripts/brand.py` and commit the result.", file=sys.stderr)
        return 1

    print(f"{len(recorded.get('derived', {}))} brand images current.")
    return 0


def main() -> int:
    if not SOURCE.is_file():
        print(f"No source at {SOURCE}", file=sys.stderr)
        return 1

    if "--check" in sys.argv:
        return check()

    produced = build()
    changed = []
    for name, data in produced.items():
        path = OUT / name
        if not path.is_file() or path.read_bytes() != data:
            changed.append(name)
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_bytes(data)

    MANIFEST.parent.mkdir(parents=True, exist_ok=True)
    MANIFEST.write_text(
        json.dumps(
            {
                "source": digest(SOURCE.read_bytes()),
                "spec": spec(),
                "derived": {name: digest((OUT / name).read_bytes()) for name in sorted(produced)},
            },
            indent=2,
        )
        + "\n",
        encoding="utf-8",
        newline="\n",
    )

    print(f"{len(produced)} brand images written; {len(changed)} changed.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
