"""Derive Postulo's served brand images from the vector mark.

The mark is a layered paper-cut ``P``. It lives as vectors in ``assets/brand/``, and
everything the application serves is generated from them by this script and committed --
the same arrangement as the stylesheet and the icon set, so an instance runs without
needing the tooling that produced them.

    uv run python scripts/brand.py          # write the served copies and the images
    uv run python scripts/brand.py --check  # fail if the mark changed without them

Rendering needs the browser the browser tests use (``uv run playwright install
chromium``); the check needs nothing but Python.

What it writes into ``src/postulo/static/brand/``:

- **The served vectors**, ``logo.svg`` and its pride and animated companions: each source
  file minified, framed on the mark, and stripped of what a browser does not draw.
- **The PNGs**, every one rendered from the served ``logo.svg`` (or ``logo-pride.svg``):
  the tab's favicons for a browser that does not take an SVG icon, the Apple and manifest
  icons, which those platforms want as PNG, and the header's fallback.

Five decisions worth knowing:

**The PNGs are drawn by Chromium.** The mark's depth is seven shadow filters and seven
gradients, and the renderer has to draw them as a browser does, because the vector is what
a browser shows and the PNG is what it shows instead. WeasyPrint, which Postulo already
has, draws SVG filters only as far as ``feOffset`` and ``feBlend``: no blur, so no shadow,
and only into a PDF. Chromium is already here for the browser tests, and it is the engine
most people see the vector in. ``resvg`` drew the same picture to the eye and would have
been one more package (#1238).

**One raster at 1024 pixels, then scaled.** The served vector is drawn once at 1024 on a
side -- twice the largest image derived -- trimmed to the mark, and the sizes are made
from that the way they were made from the old raster, with LANCZOS. A 16-pixel favicon
drawn by downscaling a large picture is cleaner than one rasterised at 16.

**The served copy is framed on the mark.** The drawing sits in a 1254-unit square with a
margin all round; served as it is, a 28-pixel header logo would spend a third of its
pixels on nothing. The frame is measured from the 1024 drawing, so it follows the mark
when the mark changes.

**``--check`` compares recorded digests rather than rebuilding.** Rebuilding and comparing
bytes fails on any machine but the one that last ran the script: a renderer and Pillow's
platform wheels round differently, so the pixels differ by one here and there, and CI once
reported the mark as out of date on every run. A check whose answer depends on which
machine asked is not a check. So ``brand.json`` records what was built and what it was
built from, and ``--check`` answers the question worth asking -- *were these files made
from these sources?* -- and trusts that whoever ran the script ran it, exactly as the
committed stylesheet does.

**The Apple touch icon gets a solid background.** iOS composites a transparent icon onto
black, which turns a navy mark into a dark rectangle. Giving it the page's own off-white
means it looks the same on a home screen as it does in a tab.
"""

from __future__ import annotations

import base64
import hashlib
import io
import json
import re
import sys
from pathlib import Path
from xml.etree import ElementTree as ET

ROOT = Path(__file__).resolve().parent.parent
ASSETS = ROOT / "assets" / "brand"
OUT = ROOT / "src" / "postulo" / "static" / "brand"

#: The served vectors, each from its source. The first is the mark everything is framed on
#: and the usual PNGs are rendered from; the pride PNGs come from the second.
SERVED = {
    "logo.svg": "postulo.svg",
    "logo-pride.svg": "postulo_pride.svg",
    # The animated mark a person can switch on (#1238): the light on each sheet turning and
    # returning. Drift rather than swell: a twentieth of the size (swell spells out every
    # frame of every edge), and at 28 pixels a bending edge is under a pixel while a
    # turning light still reads.
    "logo-drift.svg": "postulo_drift.svg",
    "logo-pride-drift.svg": "postulo_pride_drift.svg",
}

#: What was built and what it was built from. Beside the sources rather than in `static/`,
#: because it is a record for the repository and not a file to serve.
MANIFEST = ASSETS / "brand.json"

#: What the page sits on, so the Apple icon matches the rest rather than sitting on black.
APPLE_BACKGROUND = (248, 250, 252, 255)

#: The side of the one raster each set of PNGs is scaled from.
MASTER = 1024

#: name -> (served vector, size, padding as a fraction of the side, background)
DERIVED = {
    # The tab, for a browser that does not take the SVG icon. Small enough that the mark
    # needs the whole square.
    "favicon-16.png": ("logo.svg", 16, 0.0, None),
    "favicon-32.png": ("logo.svg", 32, 0.0, None),
    "favicon-48.png": ("logo.svg", 48, 0.0, None),
    # A home screen. Padded, because every platform rounds the corners off one.
    "apple-touch-icon.png": ("logo.svg", 180, 0.12, APPLE_BACKGROUND),
    # The manifest's two, for a phone that installs it.
    "icon-192.png": ("logo.svg", 192, 0.08, None),
    "icon-512.png": ("logo.svg", 512, 0.08, None),
    # The same, for a launcher that crops every icon to its own shape (#895). Opaque, and
    # padded until the mark sits inside the central circle of 40% radius that every
    # launcher keeps; one file serving both purposes would be cropped into the mark.
    "icon-maskable-512.png": ("logo.svg", 512, 0.2, APPLE_BACKGROUND),
    # The header's fallback behind the vector, and the one the README shows.
    "logo-64.png": ("logo.svg", 64, 0.0, None),
    "logo-256.png": ("logo.svg", 256, 0.0, None),
    # June's (#1238): the tab and the header only. A home-screen icon installed in June
    # would stay a pride icon all year, so the Apple and manifest icons are the usual ones.
    "favicon-pride-16.png": ("logo-pride.svg", 16, 0.0, None),
    "favicon-pride-32.png": ("logo-pride.svg", 32, 0.0, None),
    "favicon-pride-48.png": ("logo-pride.svg", 48, 0.0, None),
    "logo-pride-64.png": ("logo-pride.svg", 64, 0.0, None),
}

SVG = "http://www.w3.org/2000/svg"
XLINK = "http://www.w3.org/1999/xlink"

#: Elements and attributes a served copy may hold. Anything else -- a script, an external
#: reference, a foreignObject, an editor's notes -- is not drawn and is not served.
KEPT_ELEMENTS = {
    "svg", "defs", "g", "path", "use", "clipPath", "linearGradient", "radialGradient",
    "stop", "filter", "feGaussianBlur", "feOffset", "feComponentTransfer", "feFuncA",
    "feFlood", "feComposite", "feMerge", "feMergeNode", "animate",
}  # fmt: skip
#: The root's own size: the page that shows the mark says how large, from the viewBox's shape.
DROPPED_ROOT_ATTRIBUTES = {"version", "width", "height"}


def _number(match: re.Match) -> str:
    """One decimal, no trailing zero: a tenth of a unit is a fortieth of a pixel at 512."""
    text = f"{float(match.group(0)):.1f}".rstrip("0").rstrip(".")
    return "0" if text == "-0" else text


def _compact_path(d: str) -> str:
    """A path's numbers to one decimal, and the spaces a parser does not need."""
    d = re.sub(r"-?\d+\.\d+", _number, d)
    d = re.sub(r"\s*([MLCZmlcz])\s*", r"\1", d)
    return re.sub(r"\s*,\s*", ",", d).strip()


def served(source: Path, frame: tuple[float, float, float] | None) -> bytes:
    """``source`` minified for serving, its viewBox the square ``frame`` (x, y, side)."""
    from defusedxml import ElementTree

    root = ElementTree.parse(source).getroot()
    for parent in list(root.iter()):
        for child in list(parent):
            local = child.tag.rsplit("}", 1)[-1]
            if not child.tag.startswith(f"{{{SVG}}}") or local not in KEPT_ELEMENTS:
                parent.remove(child)
    for element in root.iter():
        for name in list(element.attrib):
            local = name.rsplit("}", 1)[-1]
            namespaced = name.startswith("{")
            if namespaced and name != f"{{{XLINK}}}href":
                del element.attrib[name]
            elif element is root and local in DROPPED_ROOT_ATTRIBUTES:
                del element.attrib[name]
            elif local.lower().startswith("on"):
                del element.attrib[name]
        # `href` alone: every browser that draws this file reads it without the prefix.
        if f"{{{XLINK}}}href" in element.attrib:
            target = element.attrib.pop(f"{{{XLINK}}}href")
            element.attrib.setdefault("href", target)
        # Only a reference inside the file: nothing served reaches outside it.
        if not element.attrib.get("href", "#").startswith("#"):
            del element.attrib["href"]
        if "d" in element.attrib:
            element.attrib["d"] = _compact_path(element.attrib["d"])
        if element.tag == f"{{{SVG}}}animate" and element.get("attributeName") == "d":
            element.attrib["values"] = ";".join(
                _compact_path(v) for v in element.attrib["values"].split(";")
            )
        element.text = element.text.strip() if element.text and element.text.strip() else None
        element.tail = None
    if frame is not None:
        x, y, side = frame
        root.set("viewBox", f"{x:g} {y:g} {side:g} {side:g}")
    ET.register_namespace("", SVG)
    text = ET.tostring(root, encoding="unicode", short_empty_elements=True)
    # One line, ended as every text file in the repository is, so the hooks leave it alone.
    return text.replace(" />", "/>").encode("utf-8") + b"\n"


def draw(browser, svg: bytes, size: int):
    """``svg`` as Chromium draws it in an ``<img>``, ``size`` pixels square, transparent."""
    from PIL import Image

    page = browser.new_page(viewport={"width": size, "height": size}, device_scale_factor=1)
    try:
        uri = "data:image/svg+xml;base64," + base64.b64encode(svg).decode("ascii")
        page.set_content(
            '<html><body style="margin:0;background:transparent">'
            f'<img src="{uri}" width="{size}" height="{size}" style="display:block"></body></html>'
        )
        page.wait_for_function("document.images[0].complete && document.images[0].naturalWidth")
        shot = page.screenshot(omit_background=True)
    finally:
        page.close()
    return Image.open(io.BytesIO(shot)).convert("RGBA")


def frame_of(browser) -> tuple[float, float, float]:
    """The square, in the drawing's own units, that holds the mark and no more margin.

    Measured from the source drawn at 1024: the box around every pixel that is not
    transparent, widened to a square about its centre and rounded outward to whole units.
    """
    from defusedxml import ElementTree

    source = ASSETS / SERVED["logo.svg"]
    x0, y0, width, height = (
        float(v) for v in ElementTree.parse(source).getroot().get("viewBox").split()
    )
    left, top, right, bottom = draw(browser, source.read_bytes(), MASTER).getbbox()
    unit_x, unit_y = width / MASTER, height / MASTER
    left, right = x0 + left * unit_x, x0 + right * unit_x
    top, bottom = y0 + top * unit_y, y0 + bottom * unit_y
    side = float(int(max(right - left, bottom - top)) + 2)
    return (
        float(int((left + right) / 2 - side / 2)),
        float(int((top + bottom) / 2 - side / 2)),
        side,
    )


def rendered(source, size: int, padding: float, background):
    """The mark, trimmed and centred in a square of ``size``."""
    from PIL import Image

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
    from playwright.sync_api import sync_playwright

    produced: dict[str, bytes] = {}
    with sync_playwright() as playwright:
        browser = playwright.chromium.launch()
        try:
            frame = frame_of(browser)
            for name, source in SERVED.items():
                produced[name] = served(ASSETS / source, frame)
            masters = {
                name: draw(browser, produced[name], MASTER)
                for name in {vector for vector, *_ in DERIVED.values()}
            }
        finally:
            browser.close()

    for name, (vector, size, padding, background) in DERIVED.items():
        buffer = io.BytesIO()
        rendered(masters[vector], size, padding, background).save(
            buffer, format="PNG", optimize=True
        )
        produced[name] = buffer.getvalue()

    # One .ico holding the three small sizes, for anything that still asks for /favicon.ico.
    buffer = io.BytesIO()
    rendered(masters["logo.svg"], 48, 0.0, None).save(
        buffer, format="ICO", sizes=[(16, 16), (32, 32), (48, 48)]
    )
    produced["favicon.ico"] = buffer.getvalue()
    return produced


def digest(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def sources() -> dict[str, str]:
    """Each source vector's digest: a change to any of them is a change to the mark."""
    return {name: digest((ASSETS / name).read_bytes()) for name in sorted(set(SERVED.values()))}


def spec() -> dict:
    """The recipe, so a change to a size or a padding counts as a change too.

    Digests alone answer "were these built from these sources", and would miss somebody
    editing the tables above without re-running the script. This is the cheap half of what
    comparing bytes used to give, without the half that depended on which machine asked.
    """
    # Through JSON and back, so that what is compared is what was written: a tuple in
    # the table comes back as a list, and a spec that differs only in that is not a
    # difference anybody meant.
    return json.loads(
        json.dumps(
            {
                "master": MASTER,
                "served": dict(sorted(SERVED.items())),
                "derived": dict(sorted(DERIVED.items())),
            }
        )
    )


def check() -> int:
    """Fail if the mark changed without its derivatives, or a derivative was edited."""
    if not MANIFEST.is_file():
        print(f"No record at {MANIFEST}; run `uv run python scripts/brand.py`", file=sys.stderr)
        return 1
    recorded = json.loads(MANIFEST.read_text(encoding="utf-8"))

    if recorded.get("sources") != sources():
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

    print(f"{len(recorded.get('derived', {}))} brand files current.")
    return 0


def main() -> int:
    missing = [name for name in sorted(set(SERVED.values())) if not (ASSETS / name).is_file()]
    if missing:
        print(f"No source at {', '.join(missing)} in {ASSETS}", file=sys.stderr)
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
                "sources": sources(),
                "spec": spec(),
                "derived": {name: digest((OUT / name).read_bytes()) for name in sorted(produced)},
            },
            indent=2,
        )
        + "\n",
        encoding="utf-8",
        newline="\n",
    )

    print(f"{len(produced)} brand files written; {len(changed)} changed.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
