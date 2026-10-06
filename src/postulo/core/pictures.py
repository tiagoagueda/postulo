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
from django.db import transaction
from django.utils import timezone
from django.utils.translation import gettext as _
from PIL import Image, ImageFile, ImageOps, UnidentifiedImageError

#: Decoded images above this many pixels are refused: a decompression bomb, or a mistake.
#: Not a size limit — a limit on what decoding is allowed to allocate.
MAX_PIXELS = 40_000_000

#: What a stored picture may weigh. Flat artwork -- a wordmark at a thousand pixels is well
#: under two hundred kilobytes -- stays at the size it was given; a photograph never gets
#: near this as a PNG at camera resolution, so it is reduced, in one estimated step (#264, #482).
DEFAULT_BUDGET = 1024 * 1024

#: The longest edge a picture is brought down to before it is first encoded, so a phone's
#: twelve megapixels are never written out as PNG only to be thrown away (#482).
WORKING_EDGE = 2048

#: How much of the budget an estimated reduction aims at, since a PNG's size does not follow
#: the area exactly and a photograph loses less than its share when shrunk; and the floor,
#: so a file that cannot be made to fit fails instead of looping.
MARGIN = 0.6
SMALLEST_EDGE = 64


class UnusablePicture(ValueError):
    """The bytes are not a picture Postulo will keep. The message is for the person."""


class WrongKind(UnusablePicture):
    """The bytes are a kind of file the caller does not keep, or no kind the image library
    knows. The caller says which kinds it keeps, so the caller words the refusal."""


#: Kinds of file the image library names apart that are, to everybody who has one, the
#: kind named here. A phone's portrait or burst photograph is a JPEG with more pictures
#: after the first, which Pillow calls MPO; somebody who keeps JPEGs keeps those (#302).
SAME_KIND_AS = {"MPO": "JPEG"}


def content_type_of(image: Image.Image) -> str:
    """What an opened image is, as a content type: ``image/png``. Read from the file's own
    bytes, never from its name or from what an upload said it was."""
    kind = image.format or ""
    return Image.MIME.get(SAME_KIND_AS.get(kind, kind), "")


# ------------------------------------------------------------------------ raster


def decode(
    data: bytes, *, max_pixels: int = MAX_PIXELS, kinds=None, draft_to: int | None = None
) -> Image.Image:
    """The bytes as an image, or a refusal saying why.

    The dimensions are checked against ``max_pixels`` before anything is converted, and
    Pillow's own guard is set to the same number: the header is read first, so an image
    that claims to be enormous is refused without the pixels ever being allocated.

    ``kinds`` is the content types the caller keeps, where it keeps only some. The kind is
    what the image library reads from the bytes (#302): a BMP sent as ``image/png`` is a
    BMP, and is refused with `WrongKind`, and so is a file the library recognises as no
    picture at all -- an SVG, a PDF, text -- since that is not one of the kinds either.

    ``draft_to`` lets a JPEG be decoded at a fraction of its size when that is still at
    least this many pixels on the longest edge, which makes the decode itself cheaper (#482).
    """
    Image.MAX_IMAGE_PIXELS = max_pixels
    try:
        image = Image.open(io.BytesIO(data))
        if kinds is not None and content_type_of(image) not in kinds:
            raise WrongKind(str(_("That file could not be read as an image.")))
        if image.width * image.height > max_pixels:
            raise UnusablePicture(str(_("That image is far larger than it needs to be.")))
        if draft_to and image.format == "JPEG":
            image.draft(None, (draft_to, draft_to))
        # The whole picture, or a refusal: never a picture padded out to its size. Pillow
        # pads a file that stops part way when `LOAD_TRUNCATED_IMAGES` is set, and WeasyPrint
        # sets it, for the whole process, as it is imported -- so once a server had drawn
        # one PDF, a cut-off upload was kept as a picture with a grey half. Unset for this
        # load and put back: what WeasyPrint does with a document's images is its own affair.
        loads_truncated = ImageFile.LOAD_TRUNCATED_IMAGES
        ImageFile.LOAD_TRUNCATED_IMAGES = False
        try:
            image.load()
        finally:
            ImageFile.LOAD_TRUNCATED_IMAGES = loads_truncated
        return image
    except UnusablePicture:
        raise
    except UnidentifiedImageError as error:
        if kinds is not None:
            raise WrongKind(str(_("That file could not be read as an image."))) from error
        raise UnusablePicture(str(_("That file could not be read as an image."))) from error
    except (Image.DecompressionBombError, OSError, ValueError) as error:
        raise UnusablePicture(str(_("That file could not be read as an image."))) from error


def encode_within(image: Image.Image, *, budget: int = DEFAULT_BUDGET) -> bytes:
    """PNG bytes no larger than ``budget``, reducing the picture only as far as it must.

    A small picture comes back exactly as it was given, after one plain encode. A large one
    is reduced once, to the size the first encode suggests will fit, and written with the
    optimiser on only then, when it is the smaller picture (#482).
    """
    picture = image
    optimize = False
    while True:
        out = io.BytesIO()
        picture.save(out, format="PNG", optimize=optimize)
        written = out.getvalue()
        if len(written) <= budget:
            return written
        longest = max(picture.width, picture.height)
        if longest <= SMALLEST_EDGE:
            # Already tiny and still over budget: the file is not a picture in any useful
            # sense, and reducing further would leave nothing to look at.
            raise UnusablePicture(str(_("That image cannot be made small enough to keep.")))
        # A PNG's size follows the area, so the edge follows its square root.
        scale = min(0.95, (budget * MARGIN / len(written)) ** 0.5)
        target = max(SMALLEST_EDGE, int(longest * scale))
        scale = target / longest
        picture = picture.resize(
            (max(1, int(picture.width * scale)), max(1, int(picture.height * scale))),
            Image.Resampling.LANCZOS,
        )
        optimize = True


def as_stored(
    data: bytes, *, budget: int = DEFAULT_BUDGET, square: bool = False, kinds=None
) -> bytes:
    """Decode, drop everything the file carried, and write it out again within the budget.

    Re-encoding is the point rather than a side effect: it is what removes the metadata a
    phone's picture carries — where it was taken, and when.

    ``square`` crops to the shorter edge before encoding. That is right for a face, which
    belongs cropped to the tile it sits in, and wrong for a wordmark, which would be cut in
    half; the two call sites differ on purpose and should not be reconciled (#265).

    ``kinds`` is passed to `decode`: the content types the caller keeps, or every kind.
    """
    with decode(data, kinds=kinds, draft_to=WORKING_EDGE) as opened:
        image = ImageOps.exif_transpose(opened) or opened
        image = image.convert("RGBA")
        if square:
            side = min(image.width, image.height)
            image = ImageOps.fit(image, (side, side), Image.Resampling.LANCZOS)
        image.thumbnail((WORKING_EDGE, WORKING_EDGE), Image.Resampling.LANCZOS)
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


# ---------------------------------------------------------------------- keeping

#: How a stored PNG begins: `as_stored` writes nothing else of the raster kinds.
PNG_SIGNATURE = b"\x89PNG\r\n\x1a\n"


def media_type_of_stored(data: bytes) -> str:
    """What these bytes are, read from them: ``image/png`` or ``image/svg+xml``, or refused.

    The guard on the one write function. What it is given has been through `as_stored` or
    `sanitise_svg`, so it is one of these two; anything else is a caller that skipped the
    checks, and is refused rather than kept (#662, #466).
    """
    if data.startswith(PNG_SIGNATURE):
        return "image/png"
    if looks_like_svg(data):
        return "image/svg+xml"
    raise UnusablePicture(str(_("That file could not be read as an image.")))


def let_go_of_the_file(owner, field_name: str) -> list[str]:
    """LEGACY (#662): remove the file a picture used to be, once its row has taken over.

    Until the release that drops the old file fields they stay, so that a rollback finds its
    files; but a picture the person replaces or removes must not linger on disk because of
    that. Empties the field on ``owner`` and deletes the file when the transaction commits,
    and returns the field names for the caller's ``update_fields`` -- none when there was no
    file. Goes with the fields.
    """
    held = getattr(owner, field_name)
    if not held:
        return []
    storage, name = held.storage, held.name
    setattr(owner, field_name, "")

    def remove() -> None:
        try:
            storage.delete(name)
        except OSError:  # pragma: no cover - a file already gone is what was wanted
            pass

    transaction.on_commit(remove)
    return [field_name]


def keep(model, content, **owner):
    """Write one picture, replacing the one its owner had: the only place that does (#662).

    ``model`` is a `core.stored_pictures.StoredPicture` subclass and ``owner`` says whose row it is
    (``company=...``, or ``profile=..., kind=...``). ``content`` is the bytes, or a file
    holding them, **after** `as_stored` or `sanitise_svg`. This refuses what is neither a
    PNG nor an SVG and what is over the budget its kind is kept to, so the upload form,
    *Find logo*, the Gravatar fetch, a merge and the archive importer cannot write a picture
    that was not checked. One statement; the caller owns the transaction around it and the
    flag on the owner.
    """
    data = content.read() if hasattr(content, "read") else bytes(content)
    media_type = media_type_of_stored(data)
    ceiling = MAX_SVG_BYTES if media_type == "image/svg+xml" else DEFAULT_BUDGET
    if len(data) > ceiling:
        raise UnusablePicture(str(_("That image is far larger than it needs to be.")))
    row, _made = model.objects.update_or_create(
        **owner, defaults={"data": data, "media_type": media_type, "stored_at": timezone.now()}
    )
    return row
