"""A hostile page cannot make the capture parser do quadratic work (#778)."""

from __future__ import annotations

import time

from postulo.plugins.builtin import htmlutil
from postulo.plugins.builtin.htmlutil import MAX_DEPTH, parse_html


def depth_of(element) -> int:
    deepest, stack = 0, [(element, 0)]
    while stack:
        node, depth = stack.pop()
        deepest = max(deepest, depth)
        stack.extend((child, depth + 1) for child in node.children if not isinstance(child, str))
    return deepest


def test_the_tree_is_never_deeper_than_the_limit_however_many_tags_are_open():
    tree = parse_html("<div>" * 20000 + "text" + "</x>" * 20000)

    assert depth_of(tree) <= MAX_DEPTH


def test_a_page_of_unclosed_tags_is_read_in_a_blink_not_in_minutes():
    started = time.monotonic()
    for hostile in (
        "<div>" * 20000 + "</x>" * 20000,
        "<article>" * 8000 + "short",
        "<span>" * 20000 + "word",
        "<li>" * 20000,
    ):
        tree = parse_html(hostile)
        htmlutil.text_of(tree)
    assert time.monotonic() - started < 10


def test_what_follows_a_too_deep_element_stays_beside_it():
    tree = parse_html("<div>" * (MAX_DEPTH + 40) + "<p>kept</p>")

    assert "kept" in htmlutil.text_of(tree)


def test_an_ordinary_page_is_read_as_before():
    tree = parse_html("<main><article><h1>Role</h1><p>Do things.</p></article></main>")

    assert depth_of(tree) == 3
    assert htmlutil.text_of(tree).split() == ["Role", "Do", "things."]
