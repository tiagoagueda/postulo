"""A translation is text: it is escaped, and only its plain slots are filled (#806)."""

from __future__ import annotations

from django.utils.safestring import mark_safe

from postulo.core.htmlstrings import sentence


def test_markup_a_translator_wrote_is_escaped():
    html = sentence("Open <script>alert(1)</script> {link}", link="here")
    assert "<script>" not in html
    assert "&lt;script&gt;" in html


def test_a_slot_is_filled_with_a_safe_piece_as_it_is():
    assert (
        sentence("See {link}.", link=mark_safe("<a href='/x'>x</a>")) == "See <a href='/x'>x</a>."
    )


def test_an_unsafe_piece_is_escaped():
    assert "<b>" not in sentence("Hello {name}", name="<b>Ana</b>")


def test_a_slot_that_reaches_into_an_object_is_not_followed():
    text = sentence("{link.__class__} {link!r} {link:>20} {0}", link="x")
    assert "class" in text
    assert "str" not in text.replace("&lt;", "")  # nothing was evaluated


def test_a_slot_nobody_passed_is_left_as_written():
    assert sentence("Hi {nobody}") == "Hi {nobody}"


def test_the_numbers_a_caller_passes_are_text():
    assert sentence("{count} of them", count=3) == "3 of them"
