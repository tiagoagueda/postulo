"""The page tree is the one a browser builds, for the end tags HTML lets a page leave out (#587)."""

from __future__ import annotations

import json

import pytest

from postulo.plugins.builtin import SchemaOrgSource
from postulo.plugins.builtin.htmlutil import Element, find, parse_html, text_of
from postulo.plugins.registry import page_places


def shape(node: Element) -> list:
    """The tree as nested lists of tag names and text, to say a shape in one line."""
    return [
        child.strip() if isinstance(child, str) else [child.tag, *shape(child)]
        for child in node.children
        if not isinstance(child, str) or child.strip()
    ]


def test_list_items_without_end_tags_are_siblings():
    root = parse_html("<ul><li>a<li>b</ul>")
    assert shape(root) == [["ul", ["li", "a"], ["li", "b"]]]


def test_a_nested_list_keeps_its_own_items():
    root = parse_html("<ul><li>a<ul><li>b<li>c</ul><li>d</ul>")
    assert shape(root) == [["ul", ["li", "a", ["ul", ["li", "b"], ["li", "c"]]], ["li", "d"]]]


def test_a_term_and_its_description_are_siblings():
    root = parse_html("<dl><dt>x<dd>y<dt>z<dd>w</dl>")
    assert shape(root) == [["dl", ["dt", "x"], ["dd", "y"], ["dt", "z"], ["dd", "w"]]]


def test_cells_and_rows_without_end_tags():
    root = parse_html("<table><tr><td>x<td>y<tr><td>z</table>")
    assert shape(root) == [
        ["table", ["tr", ["td", "x"], ["td", "y"]], ["tr", ["td", "z"]]],
    ]


def test_a_block_closes_the_paragraph_before_it():
    assert shape(parse_html("<p>a<p>b")) == [["p", "a"], ["p", "b"]]
    assert shape(parse_html("<p>a<div>b</div>")) == [["p", "a"], ["div", "b"]]
    assert shape(parse_html("<select><option>a<option>b</select>")) == [
        ["select", ["option", "a"], ["option", "b"]]
    ]


def test_a_paragraph_is_not_closed_across_a_table_cell():
    root = parse_html("<table><tr><td><p>a<td>b</table>")
    assert shape(root) == [["table", ["tr", ["td", ["p", "a"]], ["td", "b"]]]]


def test_a_deep_page_is_read_without_exhausting_the_stack():
    deep = parse_html("<div>" * 5000 + "text" + "</div>" * 5000)
    assert text_of(deep) == "text"
    assert len(find(deep, "div")) == 5000


JOB = {
    "@context": "https://schema.org/",
    "@type": "JobPosting",
    "title": "Research Engineer",
    "hiringOrganization": {"name": "Black Mesa"},
    "description": "<p>Science.</p>",
}


def unclosed(count: int) -> str:
    return (
        f'<html><head><script type="application/ld+json">{json.dumps(JOB)}</script></head>'
        f"<body><h1>Research Engineer</h1><dl><dt>Local<dd>Lisboa</dl>"
        f"<ul>{'<li>item' * count}</ul></body></html>"
    )


@pytest.mark.parametrize("count", [1200, 5000])
def test_a_long_list_without_end_tags_still_gives_the_posting(count):
    data = SchemaOrgSource().parse("https://example.org/j/1", unclosed(count))
    assert data is not None and data.title == "Research Engineer"


def test_a_long_list_without_end_tags_still_gives_places():
    places = page_places("https://example.org/j/1", unclosed(5000))
    assert places, "the places are listed instead of an error being logged"
    assert any(place["place"].get("label") == "local" for place in places), places
