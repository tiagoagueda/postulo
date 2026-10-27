"""System checks for the jobs app: the ESCO data directory (#535), the map's tiles (#898) and
its street look-ups (#913).

The loader refuses to read a directory holding two revisions of either file, and the refusal
used to surface at the first listing saved. `manage.py check`, which the entrypoint runs
before gunicorn starts, names the state instead.
"""

from __future__ import annotations

from django.conf import settings
from django.core.checks import Error, register

from postulo.jobs import esco, geocoding, tiles


@register("postulo")
def esco_one_revision(app_configs, **kwargs):
    """Two `esco-*.json` or two `esco-skills-*.zip` files: one revision is read, never two."""
    errors = []
    for pattern, what, hint_id in (
        ("esco-*.json", "classifications", "postulo.E040"),
        ("esco-skills-*.zip", "skills files", "postulo.E041"),
    ):
        files = sorted(esco.DATA_DIR.glob(pattern))
        if len(files) > 1:
            names = ", ".join(file.name for file in files)
            errors.append(
                Error(
                    f"Two ESCO {what} in {esco.DATA_DIR}: {names}.",
                    hint="Delete the revision being replaced; Postulo reads one at a time.",
                    id=hint_id,
                )
            )
    return errors


@register("postulo")
def map_tile_url(app_configs, **kwargs):
    """`POSTULO_MAP_TILES=custom` with an address that is not a tile template (#898).

    The map then draws no tiles rather than fetching from an address nobody vouched for;
    this says why before anybody opens the page.
    """
    if getattr(settings, "POSTULO_MAP_TILES", "osm") != tiles.CUSTOM:
        return []
    url = getattr(settings, "POSTULO_MAP_TILE_URL", "")
    if not url:
        # The settings page may hold the address; an unset variable pins nothing.
        return []
    problem = tiles.url_problem(url)
    if not problem:
        return []
    return [
        Error(
            f"POSTULO_MAP_TILE_URL is {url!r}: {problem}",
            hint="Use an https:// template with {z}, {x} and {y}, such as "
            "https://tiles.example.org/{z}/{x}/{y}.png, or a path on this instance's origin.",
            id="postulo.E042",
        )
    ]


@register("postulo")
def geocoder(app_configs, **kwargs):
    """`POSTULO_GEOCODER` names no service, or `POSTULO_GEOCODER_URL` is no base address (#913).

    Either way no street is looked up; this says why before anybody wonders.
    """
    service = getattr(settings, "POSTULO_GEOCODER", "")
    if not service:
        return []
    allowed = (geocoding.OFF, *geocoding.SERVICES)
    if service not in allowed:
        return [
            Error(
                f"POSTULO_GEOCODER is {service!r}; it must be one of: {', '.join(allowed)}.",
                hint="Set it in the environment, or unset it to leave the choice to the page.",
                id="postulo.E043",
            )
        ]
    url = getattr(settings, "POSTULO_GEOCODER_URL", "")
    if service == geocoding.OFF or not url or not (problem := geocoding.url_problem(url)):
        return []
    return [
        Error(
            f"POSTULO_GEOCODER_URL is {url!r}: {problem}",
            hint="Use the service's base address, such as https://photon.example.org.",
            id="postulo.E044",
        )
    ]
