"""What Postulo will keep as a picture, said once for every picture it serves (#264, #265).

Three apps keep pictures — a company's logo, a plugin's mark, a person's face — and until
now each said for itself what an image was, how large it was allowed to be, and what it was
re-encoded to. `plugins/logos.py` reached into `jobs/logos.py` through a function-level
import to borrow the answer, which is the wrong direction: a plugin's logo policy should not
come out of the job-search app. It lives here instead, and both import it at module level.

**A picture is bounded by its file size, not by its dimensions.** Every stored image used to
be normalised to a 256-pixel square, which was enough for the largest surface that exists
today and a ceiling on every surface nobody has built yet — a company header, a logo on a
printed report, a photograph on a Europass CV. The originals are not kept, so that ceiling
would have been discovered years later by a design that could not have what it needed. What
is kept now is what was given, reduced only as far as it has to be to fit a byte budget.

**Three numbers, and they guard different things.**

* `MAX_PIXELS` bounds what Pillow *allocates while decoding*. A two-megabyte file can decode
  to hundreds of megabytes of RGBA, and that happens before any decision about storage can
  be taken. It is the decompression-bomb guard and has nothing to do with what is stored.
* An **input cap** refuses the bytes before they are decoded at all.
* An **output budget** bounds what is written down. It is needed because re-encoding does
  not preserve size — a photographic logo arriving as JPEG and leaving as PNG can grow — so
  the rule cannot be "refuse what is too big", it has to be "reduce until it fits".

**SVG is a document, not a picture.** It can carry `<script>`, `onload=`, a
`<foreignObject>` holding arbitrary HTML, `@import`, and `<image href="https://…">` — that
last is precisely what these modules exist to prevent, since it would tell somebody else's
server which companies a person is looking at, on every page view. Rendered through an
`<img>` a browser runs none of it; the exposure is a **direct visit**, where the file is a
same-origin document of ours. So an SVG is sanitised on the way in, against an allowlist,
and the view that serves it is hardened as well. Neither half is enough alone.
"""

from __future__ import annotations

import io
import re
from xml.etree import ElementTree

from defusedxml.ElementTree import fromstring as parse_xml
from django.utils.translation import gettext as _
from PIL import Image, ImageOps, UnidentifiedImageError

#: Decoded images above this many pixels are refused: a decompression bomb, or a mistake.
#: Not a size limit — a limit on what decoding is allowed to allocate.
MAX_PIXELS = 40_000_000

#: What a stored picture may weigh. Chosen so the reduction below effectively never runs: a
#: flat wordmark at a thousand pixels is well under two hundred kilobytes, and only a
#: photographic or pathological file comes near this (#264).
DEFAULT_BUDGET = 1024 * 1024

#: How much smaller each attempt is when the budget is missed, and how far that may go. A
#: gentle step because the first one usually succeeds; the floor is there so a file that
#: cannot be made to fit fails instead of looping.
REDUCTION = 0.8
SMALLEST_EDGE = 64


class UnusablePicture(ValueError):
    """The bytes are not a picture Postulo will keep. The message is for the person."""


# ------------------------------------------------------------------------ raster


def decode(data: bytes, *, max_pixels: int = MAX_PIXELS) -> Image.Image:
    """The bytes as an image, or a refusal saying why.

    The dimensions are checked against ``max_pixels`` before anything is converted, and
    Pillow's own guard is set to the same number: the header is read first, so an image
    that claims to be enormous is refused without the pixels ever being allocated.
    """
    Image.MAX_IMAGE_PIXELS = max_pixels
    try:
        image = Image.open(io.BytesIO(data))
        if image.width * image.height > max_pixels:
            raise UnusablePicture(str(_("That image is far larger than it needs to be.")))
        image.load()
        return image
    except UnusablePicture:
        raise
    except (UnidentifiedImageError, Image.DecompressionBombError, OSError, ValueError) as error:
        raise UnusablePicture(str(_("That file could not be read as an image."))) from error


def encode_within(image: Image.Image, *, budget: int = DEFAULT_BUDGET) -> bytes:
    """PNG bytes no larger than ``budget``, reducing the picture only as far as it must.

    A small picture comes back exactly as it was given. A large one is halved towards the
    budget a step at a time, so what is stored is the biggest version that fits rather than
    a fixed size everything is flattened to.
    """
    picture = image
    while True:
        out = io.BytesIO()
        picture.save(out, format="PNG", optimize=True)
        written = out.getvalue()
        if len(written) <= budget:
            return written
        longest = max(picture.width, picture.height)
        if longest <= SMALLEST_EDGE:
            # Already tiny and still over budget: the file is not a picture in any useful
            # sense, and reducing further would leave nothing to look at.
            raise UnusablePicture(str(_("That image cannot be made small enough to keep.")))
        target = max(SMALLEST_EDGE, int(longest * REDUCTION))
        scale = target / longest
        picture = picture.resize(
            (max(1, int(picture.width * scale)), max(1, int(picture.height * scale))),
            Image.Resampling.LANCZOS,
        )


def as_stored(data: bytes, *, budget: int = DEFAULT_BUDGET, square: bool = False) -> bytes:
    """Decode, drop everything the file carried, and write it out again within the budget.

    Re-encoding is the point rather than a side effect: it is what removes the metadata a
    phone's picture carries — where it was taken, and when.

    ``square`` crops to the shorter edge before encoding. That is right for a face, which
    belongs cropped to the tile it sits in, and wrong for a wordmark, which would be cut in
    half; the two call sites differ on purpose and should not be reconciled (#265).
    """
    with decode(data) as opened:
        image = ImageOps.exif_transpose(opened) or opened
        image = image.convert("RGBA")
        if square:
            side = min(image.width, image.height)
            image = ImageOps.fit(image, (side, side), Image.Resampling.LANCZOS)
        return encode_within(image, budget=budget)


# --------------------------------------------------------------------------- SVG

SVG_NS = "http://www.w3.org/2000/svg"
XLINK_NS = "http://www.w3.org/1999/xlink"

#: What an SVG may contain. An **allowlist**, which is the shape of every sanitiser that has
#: survived contact: a blocklist is a list of the attacks somebody had thought of.
#:
#: `script` and `foreignObject` are the obvious absences. So is `style`: a stylesheet can
#: carry `@import` and `url()`, and a policy of presentation attributes only needs no parser
#: of its own. `image` is absent because the only thing it is for is referring to another
#: file, which is the whole of what this must not allow.
SVG_ELEMENTS = frozenset(
    {
        "svg",
        "g",
        "defs",
        "symbol",
        "use",
        "title",
        "desc",
        "path",
        "rect",
        "circle",
        "ellipse",
        "line",
        "polyline",
        "polygon",
        "text",
        "tspan",
        "clipPath",
        "mask",
        "linearGradient",
        "radialGradient",
        "stop",
    }
)

#: What an element may carry: geometry, and the presentation attributes that paint it.
#: Anything beginning `on` is an event handler and is refused by the rule below whether or
#: not somebody remembered to leave it out of this list.
SVG_ATTRIBUTES = frozenset(
    {
        "id",
        "class",
        "viewBox",
        "width",
        "height",
        "x",
        "y",
        "x1",
        "y1",
        "x2",
        "y2",
        "cx",
        "cy",
        "r",
        "rx",
        "ry",
        "d",
        "points",
        "transform",
        "fill",
        "fill-opacity",
        "fill-rule",
        "stroke",
        "stroke-width",
        "stroke-opacity",
        "stroke-linecap",
        "stroke-linejoin",
        "stroke-dasharray",
        "stroke-dashoffset",
        "stroke-miterlimit",
        "opacity",
        "color",
        "offset",
        "stop-color",
        "stop-opacity",
        "gradientUnits",
        "gradientTransform",
        "spreadMethod",
        "clip-path",
        "clip-rule",
        "mask",
        "maskUnits",
        "clipPathUnits",
        "preserveAspectRatio",
        "font-family",
        "font-size",
        "font-weight",
        "font-style",
        "text-anchor",
        "dominant-baseline",
        "letter-spacing",
        "xml:space",
    }
)

#: The two attributes that may name something, and the only thing they may name. A reference
#: into the same document is how `use` and `clip-path` work at all; anything with a scheme,
#: a host or a path is a request to somebody else's server.
REFERENCE_ATTRIBUTES = ("href", f"{{{XLINK_NS}}}href")

#: `url(#id)` is how a fill names a gradient in the same file. `url(https://…)` is not.
_EXTERNAL_URL = re.compile(r"url\(\s*['\"]?(?!#)", re.I)

#: Enough of a look to tell an SVG from a PNG without parsing it.
_LOOKS_LIKE_SVG = re.compile(rb"<\s*svg[\s>]", re.I)

#: An SVG larger than this is refused before it is parsed.
MAX_SVG_BYTES = 512 * 1024


def looks_like_svg(data: bytes) -> bool:
    """Whether these bytes are offered as an SVG.

    Read from the content rather than from a file name or a declared type, both of which
    are the uploader's to choose. A leading XML declaration or comment is ordinary, so the
    opening tag is looked for in the first part of the file rather than at position zero.
    """
    return bool(_LOOKS_LIKE_SVG.search(data[:4096]))


def _local(tag: str) -> str:
    """The element name without its namespace, and "" for a tag in a foreign namespace."""
    if tag.startswith("{"):
        namespace, _brace, name = tag[1:].partition("}")
        return name if namespace == SVG_NS else ""
    return tag


def _keep_attribute(name: str, value: str) -> bool:
    lowered = name.lower()
    if lowered.startswith("on"):
        return False  # an event handler, whatever it is called
    if name in REFERENCE_ATTRIBUTES:
        return value.startswith("#")
    if _local(name) not in SVG_ATTRIBUTES and name not in SVG_ATTRIBUTES:
        return False
    return not _EXTERNAL_URL.search(value)


def _clean(element) -> None:
    """Strip this element's attributes and drop the children that are not allowed."""
    for name, value in list(element.attrib.items()):
        if not _keep_attribute(name, value):
            del element.attrib[name]
    for child in list(element):
        if _local(child.tag) not in SVG_ELEMENTS:
            # The whole subtree, not just the tag: what is inside a `script` is the script,
            # and what is inside a `foreignObject` is the HTML.
            element.remove(child)
        else:
            _clean(child)


def sanitise_svg(data: bytes) -> bytes:
    """An SVG with only what this allowlist permits, or a refusal saying why.

    Parsed with `defusedxml`, which refuses a document type declaration and every kind of
    entity — the billion-laughs and external-entity families — before any of the walking
    below begins.
    """
    if len(data) > MAX_SVG_BYTES:
        raise UnusablePicture(str(_("That file is larger than a logo should be.")))
    try:
        root = parse_xml(data, forbid_dtd=True)
    except Exception as error:
        raise UnusablePicture(str(_("That file could not be read as an image."))) from error

    if _local(root.tag) != "svg":
        raise UnusablePicture(str(_("That file could not be read as an image.")))

    _clean(root)

    # Written back with the SVG namespace as the default one, so the result reads as an
    # ordinary SVG rather than carrying `ns0:` on every tag.
    ElementTree.register_namespace("", SVG_NS)
    written = ElementTree.tostring(root, encoding="utf-8", xml_declaration=True)
    if len(written) > MAX_SVG_BYTES:  # pragma: no cover - re-serialising shrinks it
        raise UnusablePicture(str(_("That file is larger than a logo should be.")))
    return written
