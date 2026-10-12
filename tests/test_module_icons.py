"""A module's icon is a Lucide icon, drawn where a logo goes (#874).

``Manifest.icon`` is Postulo's own vocabulary for what ships inside it; a package's logo is
its author's file and goes through the checks files go through. These hold the line between
the two: an icon must be one the stylesheet-side set holds, it draws in place of the initials
tile, and a file a plugin shipped is still preferred to it.
"""

from __future__ import annotations

from pathlib import Path

import pytest
from django.template import Context, Template

from postulo.plugins import base, registry
from postulo.plugins.api import declares, shipped

REPO = Path(__file__).resolve().parent.parent


def _listed() -> set[str]:
    names = set()
    for line in (REPO / "assets" / "icons.txt").read_text(encoding="utf-8").splitlines():
        name = line.split("#", 1)[0].strip()
        if name:
            names.add(name)
    return names


def _module(name, **extra):
    @declares(shipped(name=name, label=name, kind="feature", description=name, **extra))
    class Module:
        pass

    return Module


def _draw(plugin) -> str:
    return Template("{% load postulo %}{% plugin_logo plugin %}").render(
        Context({"plugin": plugin})
    )


def test_a_module_with_an_icon_draws_it_in_place_of_the_initials():
    html = _draw(_module("iconic", icon="map")())
    assert 'data-icon="map"' in html
    assert '<span class=""' not in html
    assert 'aria-hidden="true"' in html


def test_a_module_without_one_still_draws_its_initials():
    html = _draw(_module("plain")())
    assert "data-icon" not in html
    assert ">P<" in html


def test_a_logo_file_the_plugin_shipped_beats_its_icon(monkeypatch):
    from postulo.plugins import logos

    monkeypatch.setattr(logos, "logo_for", lambda plugin: (b"x", "image/png"))
    html = _draw(_module("both", icon="map")())
    assert "<img" in html
    assert "data-icon" not in html


def test_every_icon_a_shipped_module_names_is_one_the_set_holds():
    listed = _listed()
    for classes in registry.builtins().values():
        for plugin_class in classes:
            manifest = getattr(plugin_class, "manifest", None)
            if isinstance(manifest, base.Manifest) and manifest.icon:
                assert manifest.icon in listed, f"{manifest.name}: {manifest.icon} is not synced"
                assert (REPO / "src/postulo/static/icons" / f"{manifest.icon}.svg").exists()


@pytest.mark.parametrize(
    ("name", "icon"),
    [("employer-structure", "pyramid"), ("maps", "map")],
)
def test_the_modules_the_maintainer_named_wear_the_icons_asked_for(name, icon):
    found = registry.find_any(name)
    assert found is not None
    assert base.manifest_of(found).icon == icon
