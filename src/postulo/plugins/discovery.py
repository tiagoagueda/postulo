"""What a connection's plugin found on the other service, and the rows it is chosen from (#332).

A connection's form is drawn from the plugin's `FieldSpec`s, and until this every choice in it
was fixed when `config_fields()` was called. Only a calendar server knows which address books,
calendars and task lists exist and which of them the account may write (postulo/postulo-dav#13),
so a ``discovered`` field is chosen from what the plugin's optional ``discover(config)`` found
there.

**Kept in the connection's own configuration**, under `KEY`, with when it was found: a table of
its own would need a migration and buy nothing, and a form save already keeps the keys that are
not fields. Nothing in it is secret. The plugin sees the key in the configuration it is handed,
and is free to ignore it; the field itself is stored under the field's name, as a dict of option
value to mode, for the chosen options only.

**An option that vanished is never dropped in silence.** One still chosen keeps its row, its
last label and its mode, marked as no longer on the server, until the person sets it to *Not
used*; what a run does with it meanwhile is the plugin's call. An option discovery never found
is not offered here at all: a plugin that needs one keeps a text field beside this one.

**Postulo draws the choice and does not enforce it.** The plugin is handed what was chosen and
still holds its own requests to it.
"""

from __future__ import annotations

import hashlib
import logging
from collections.abc import Mapping
from dataclasses import dataclass, field

from django.utils import timezone
from django.utils.dateparse import parse_datetime

from .base import DiscoveredOption, FieldSpec

logger = logging.getLogger(__name__)

#: Where the found options live in a connection's configuration. A colon, so that it cannot be
#: the name of a field a plugin declares, and the core's name in front, so that it reads as
#: what it is in an exported archive.
KEY = "postulo:discovered"

#: The most options kept for one field. A server listing more than this is not offering a
#: choice anybody makes from a table, and an unbounded list is a page that never ends.
MAX_OPTIONS = 200

#: The longest value, label, group or note kept, in characters.
MAX_TEXT = 500


def specs_of(specs: list[FieldSpec]) -> list[FieldSpec]:
    """The ``discovered`` fields among ``specs``, in order."""
    return [spec for spec in specs if spec.type == "discovered"]


def can_look(plugin, specs: list[FieldSpec]) -> bool:
    """Whether this plugin has a ``discover`` and a field for what it finds."""
    return callable(getattr(plugin, "discover", None)) and bool(specs_of(specs))


def stored(config: Mapping | None) -> dict:
    """What was found last, as kept: ``{"at": iso, "fields": {name: [entry, ...]}}`` or empty."""
    value = (config or {}).get(KEY)
    return value if isinstance(value, dict) else {}


def found_at(config: Mapping | None):
    """When the service was last looked at, or None."""
    at = stored(config).get("at")
    return parse_datetime(at) if isinstance(at, str) else None


def chosen(config: Mapping | None, name: str) -> dict[str, str]:
    """A ``discovered`` field's choice as stored: option value to mode, the chosen ones only."""
    value = (config or {}).get(name)
    if not isinstance(value, dict):
        return {}
    return {str(key): str(mode) for key, mode in value.items() if key and mode}


def _text(value) -> str:
    return str(value or "").strip()[:MAX_TEXT]


def tidy(spec: FieldSpec, options) -> list[dict]:
    """What a plugin's ``discover`` gave for one field, as plain values Postulo will keep.

    Read rather than trusted: anything that is not a `DiscoveredOption` with a value and a
    label is left out and logged, a value found twice is kept once, and a mode the field does
    not declare is dropped -- an option left with none of its own may take every mode.
    """
    declared = {value for value, _label in spec.modes}
    entries: list[dict] = []
    seen: set[str] = set()
    for option in options or ():
        if not isinstance(option, DiscoveredOption):
            logger.warning("Discovery for %r gave %r, not a DiscoveredOption", spec.name, option)
            continue
        value, label = _text(option.value), _text(option.label)
        if not value or not label or value in seen:
            continue
        seen.add(value)
        entries.append(
            {
                "value": value,
                "label": label,
                "group": _text(option.group),
                "note": _text(option.note),
                "modes": [mode for mode in option.modes if mode in declared],
            }
        )
        if len(entries) >= MAX_OPTIONS:
            break
    return entries


def record(config: Mapping | None, specs: list[FieldSpec], found: Mapping) -> dict:
    """The configuration with what was just found kept in it, under `KEY`.

    ``found`` is what ``discover`` returned, keyed by field name; a name that is not one of
    this connection's ``discovered`` fields is ignored. A field the plugin said nothing about
    keeps what it had. An option still chosen that is no longer found is carried over, marked
    ``gone``, so its row stays with its last label until the person lets it go.
    """
    config = dict(config or {})
    previous = stored(config).get("fields") or {}
    fields = {
        name: list(entries) for name, entries in previous.items() if isinstance(entries, list)
    }
    for spec in specs_of(specs):
        if spec.name not in found:
            continue
        entries = tidy(spec, found[spec.name])
        values = {entry["value"] for entry in entries}
        earlier = {
            entry.get("value"): entry
            for entry in fields.get(spec.name, [])
            if isinstance(entry, dict)
        }
        for value in chosen(config, spec.name):
            if value not in values:
                entries.append(
                    {**earlier.get(value, {"value": value, "label": value}), "gone": True}
                )
        fields[spec.name] = entries
    config[KEY] = {"at": timezone.now().isoformat(), "fields": fields}
    return config


@dataclass
class Row:
    """One option in a ``discovered`` field's table: what it is, and the modes it may take."""

    value: str
    label: str
    group: str = ""
    note: str = ""
    modes: list[tuple[str, str]] = field(default_factory=list)
    #: What it is set to now: a mode, or empty for *Not used*.
    mode: str = ""
    #: Chosen once, and no longer found on the service.
    gone: bool = False

    @property
    def key(self) -> str:
        """A form field's suffix for this option: a value is an address, not a field name."""
        return hashlib.sha256(self.value.encode()).hexdigest()[:16]


def rows(spec: FieldSpec, config: Mapping | None) -> list[Row]:
    """Every row of a ``discovered`` field: what was found, then what is chosen and not found.

    A choice no record explains -- an archive brought in without the found options, say --
    is still a row, under its own value, rather than something kept out of sight.
    """
    kept = (stored(config).get("fields") or {}).get(spec.name) or []
    choice = chosen(config, spec.name)
    named = dict(spec.modes)
    result: list[Row] = []
    seen: set[str] = set()
    for entry in kept:
        if not isinstance(entry, dict) or not entry.get("value"):
            continue
        value = str(entry["value"])
        if value in seen or (entry.get("gone") and value not in choice):
            continue
        seen.add(value)
        allowed = [mode for mode in entry.get("modes") or () if mode in named] or list(named)
        mode = choice.get(value, "")
        if mode in named and mode not in allowed:
            # Chosen in a mode the server no longer allows: kept on the row, never changed
            # behind the person's back, and theirs to set again.
            allowed.append(mode)
        result.append(
            Row(
                value=value,
                label=str(entry.get("label") or value),
                group=str(entry.get("group") or ""),
                note=str(entry.get("note") or ""),
                modes=[(item, named[item]) for item in allowed if item in named],
                mode=mode if mode in named else "",
                gone=bool(entry.get("gone")),
            )
        )
    for value, mode in choice.items():
        if value not in seen and mode in named:
            result.append(Row(value, value, modes=list(spec.modes), mode=mode, gone=True))
    # Each group together, in the order the service first named it, so that a heading is
    # drawn once however the plugin interleaved what it found.
    order = {group: index for index, group in reversed(list(enumerate(r.group for r in result)))}
    return sorted(result, key=lambda row: order[row.group])


def keep_only_chosen_gone(config: dict, specs: list[FieldSpec]) -> dict:
    """The configuration with every vanished option nobody has chosen any more let go."""
    kept = stored(config)
    fields = kept.get("fields")
    if not isinstance(fields, dict):
        return config
    tidied = {}
    for name, entries in fields.items():
        choice = chosen(config, name)
        tidied[name] = [
            entry
            for entry in entries or ()
            if isinstance(entry, dict) and (not entry.get("gone") or entry.get("value") in choice)
        ]
    return {**config, KEY: {**kept, "fields": tidied}}
