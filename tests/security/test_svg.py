"""What an uploaded SVG may not do (#264).

An SVG is a document, not a picture. Rendered through an `<img>` a browser runs none of
what it carries — but Postulo stores it and serves it from its own origin, so **a direct
visit to the stored file is a same-origin document of ours**, and anything in it runs as us.

Two defences, and this file tests both because either one alone is a single point of
failure: `core.pictures.sanitise_svg` walks an allowlist on the way in, and every private
file is served under `default-src 'none'; sandbox`.

The vectors below are the ones the issue enumerated plus the families they belong to. An
allowlist is the shape that survives contact — a blocklist is a list of the attacks somebody
had already thought of — so the last test here is the one that matters most: it asserts the
*shape*, that an element nobody has heard of is dropped rather than kept.
"""

from __future__ import annotations

import pytest

from postulo.core.pictures import UnusablePicture, sanitise_svg

WRAPPER = (
    b'<svg xmlns="http://www.w3.org/2000/svg" '
    b'xmlns:xlink="http://www.w3.org/1999/xlink">%s<rect width="8" height="8"/></svg>'
)


def cleaned(inner: bytes) -> bytes:
    return sanitise_svg(WRAPPER % inner)


# ------------------------------------------------------------------ running code


@pytest.mark.parametrize(
    "inner, forbidden",
    [
        (b"<script>alert(1)</script>", b"script"),
        (b'<script xlink:href="https://evil.test/x.js"/>', b"script"),
        (b'<a xlink:href="javascript:alert(1)">x</a>', b"javascript"),
        (b'<set attributeName="onload" to="alert(1)"/>', b"onload"),
        (b'<animate attributeName="href" to="javascript:alert(1)"/>', b"javascript"),
        (b'<handler xmlns="http://www.w3.org/2001/xml-events">alert(1)</handler>', b"alert"),
    ],
)
def test_nothing_that_could_run_survives(inner, forbidden):
    assert forbidden not in cleaned(inner)


@pytest.mark.parametrize(
    "attribute",
    ["onload", "onclick", "onmouseover", "onfocus", "onbegin", "onerror", "ONLOAD"],
)
def test_no_event_handler_survives_whatever_it_is_called(attribute):
    """Matched by the `on` prefix rather than by a list, so tomorrow's handler is caught."""
    written = sanitise_svg(
        b'<svg xmlns="http://www.w3.org/2000/svg" %s="alert(1)"><rect/></svg>' % attribute.encode()
    )

    assert b"alert" not in written
    assert attribute.encode().lower() not in written.lower()


def test_a_foreign_object_takes_its_html_with_it():
    """`foreignObject` is how arbitrary HTML gets inside an SVG.

    Written as well-formed XML on purpose. The sloppy HTML a real payload would use is
    refused a step earlier, by the parser, which is safe but tests nothing about the
    allowlist — and a test that passes for the wrong reason is worse than no test.
    """
    written = cleaned(
        b'<foreignObject width="10" height="10">'
        b'<body xmlns="http://www.w3.org/1999/xhtml">'
        b'<img src="x" onerror="alert(1)"/></body>'
        b"</foreignObject>"
    )

    assert b"foreignObject" not in written
    assert b"onerror" not in written and b"body" not in written


def test_markup_that_is_not_well_formed_is_refused_outright():
    """Which is how a real payload usually arrives, and is the safe answer to it."""
    with pytest.raises(UnusablePicture):
        cleaned(b"<foreignObject><img src=x onerror=alert(1)></foreignObject>")


# ------------------------------------------------- telling somebody else's server


@pytest.mark.parametrize(
    "inner",
    [
        b'<image href="https://evil.test/pixel.png"/>',
        b'<image xlink:href="https://evil.test/pixel.png"/>',
        b'<use href="https://evil.test/x.svg#a"/>',
        b'<use xlink:href="//evil.test/x.svg#a"/>',
        b'<rect fill="url(https://evil.test/p)"/>',
        b"<rect fill='url(\"https://evil.test/p\")'/>",
        b"<style>@import url(https://evil.test/a.css);</style>",
        b"<style>@font-face{src:url(https://evil.test/f.woff)}</style>",
        b'<rect style="fill:url(https://evil.test/p)"/>',
    ],
)
def test_no_reference_to_another_server_survives(inner):
    """The leak these modules exist to prevent.

    `img-src 'self'` is the policy precisely so that looking at a page never tells a third
    party which companies somebody is applying to. An SVG that fetches is that leak with
    extra steps, and it arrives by accident far more often than by malice: a designer's
    export is full of CDN references.
    """
    assert b"evil.test" not in cleaned(inner)


def test_a_reference_inside_the_same_file_is_kept():
    """Because otherwise the sanitiser would break the ordinary case it has to allow.

    A gradient is named by `url(#id)` and `use` points at `#id`. A sanitiser that refuses
    those refuses most real logos, and a defence that breaks the thing it guards gets
    turned off.
    """
    written = sanitise_svg(
        b'<svg xmlns="http://www.w3.org/2000/svg">'
        b'<defs><linearGradient id="g"><stop offset="0" stop-color="#f00"/></linearGradient></defs>'
        b'<rect width="8" height="8" fill="url(#g)"/><use href="#g"/></svg>'
    )

    assert b"url(#g)" in written
    assert b'href="#g"' in written


# ----------------------------------------------------------------- the parser itself


@pytest.mark.parametrize(
    "hostile",
    [
        b'<?xml version="1.0"?><!DOCTYPE s [<!ENTITY a "aa"><!ENTITY b "&a;&a;&a;">]>'
        b'<svg xmlns="http://www.w3.org/2000/svg"><title>&b;</title></svg>',
        b'<?xml version="1.0"?><!DOCTYPE s [<!ENTITY x SYSTEM "file:///etc/passwd">]>'
        b'<svg xmlns="http://www.w3.org/2000/svg"><title>&x;</title></svg>',
        b'<?xml version="1.0"?><!DOCTYPE s SYSTEM "https://evil.test/x.dtd">'
        b'<svg xmlns="http://www.w3.org/2000/svg"/>',
    ],
)
def test_entities_and_doctypes_are_refused_before_the_walk_begins(hostile):
    """`defusedxml`'s job, not the allowlist's: billion laughs, and external entities.

    Refused rather than stripped. A document that wants a DTD at all is not a logo.
    """
    with pytest.raises(UnusablePicture):
        sanitise_svg(hostile)


@pytest.mark.parametrize(
    "not_a_logo",
    [b"", b"not xml", b"<html><body>hi</body></html>", b"<svg>unclosed", b"\x89PNG\r\n\x1a\n"],
)
def test_what_is_not_an_svg_is_refused(not_a_logo):
    with pytest.raises(UnusablePicture):
        sanitise_svg(not_a_logo)


def test_something_enormous_is_refused_before_it_is_parsed():
    """An allowlist walk over a gigabyte of XML is a denial of service with extra steps."""
    with pytest.raises(UnusablePicture, match="larger than"):
        sanitise_svg(b'<svg xmlns="http://www.w3.org/2000/svg">' + b"<rect/>" * 200_000)


# ------------------------------------------------------------------- and the shape


def test_an_element_nobody_thought_of_is_dropped():
    """The property that makes this an allowlist, and the reason it can be trusted.

    Every test above names an attack. This one names none: it asserts that something
    invented on the spot — which is what next year's vector will be — is gone.
    """
    written = cleaned(b"<somethingNobodyHasHeardOf>x</somethingNobodyHasHeardOf>")

    assert b"somethingNobodyHasHeardOf" not in written
    assert b"<rect" in written, "and what is allowed is untouched"


def test_an_attribute_nobody_thought_of_is_dropped():
    written = sanitise_svg(
        b'<svg xmlns="http://www.w3.org/2000/svg"><rect surprising="yes" width="8"/></svg>'
    )

    assert b"surprising" not in written
    assert b'width="8"' in written


def test_the_drawing_itself_comes_through_untouched():
    """A sanitiser that mangles ordinary logos is one somebody works around."""
    written = sanitise_svg(
        b'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 64 32" width="64" height="32">'
        b"<title>Acme</title>"
        b'<path d="M4 4 L20 20" stroke="#000" stroke-width="2" stroke-linecap="round"/>'
        b'<circle cx="40" cy="16" r="8" fill="#0af" fill-opacity="0.5"/>'
        b'<text x="8" y="28" font-family="serif" font-size="10">Acme</text></svg>'
    )

    for kept in (b"viewBox", b"M4 4 L20 20", b"stroke-linecap", b"fill-opacity", b"Acme"):
        assert kept in written, kept
