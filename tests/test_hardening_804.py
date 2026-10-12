"""A message is HTML only when its tag is the word `safe`, not when the letters appear (#804)."""

from __future__ import annotations

from django.contrib.messages.storage.base import Message
from django.template.loader import render_to_string


def rendered(text, extra_tags):
    return render_to_string("partials/messages.html", {"messages": [Message(25, text, extra_tags)]})


def test_the_word_safe_lets_markup_through():
    assert "<b>bold</b>" in rendered("<b>bold</b>", "safe")


def test_a_tag_that_only_contains_the_letters_does_not():
    html = rendered("<b>bold</b>", "unsafe")
    assert "<b>bold</b>" not in html
    assert "&lt;b&gt;bold&lt;/b&gt;" in html


def test_several_tags_with_safe_among_them_still_do():
    assert "<b>bold</b>" in rendered("<b>bold</b>", "other safe")


def test_no_tag_escapes():
    assert "<b>" not in rendered("<b>bold</b>", "")
