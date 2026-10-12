"""The picture tile escapes every part it is given (#805)."""

from __future__ import annotations

from postulo.core.templatetags.postulo import _tile


def test_a_picture_address_cannot_close_the_attribute():
    html = _tile("size-8", "rounded", picture='/x.png" onerror="alert(1)')
    assert 'onerror="alert' not in html
    assert "&quot;" in html


def test_the_class_and_the_initials_are_escaped_too():
    html = _tile('size-8 "><script>', "rounded", colour="bg-red", letters="<b>")
    assert "<script>" not in html
    assert "&lt;b&gt;" in html


def test_an_ordinary_tile_is_what_it_was():
    assert _tile("size-8 text-xs", "rounded", colour="bg-brand-600", letters="AB") == (
        '<span class="avatar size-8 bg-brand-600 rounded font-semibold text-white" '
        'aria-hidden="true"><span class="text-xs">AB</span></span>'
    )
