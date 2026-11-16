"""What Postulo asks of the occupation and skill vocabulary, and how it is asked (#1252).

The vocabulary is ESCO, and it is a plugin (`postulo.plugins.esco`): a person switches it
off, and the application runs with plainer fields. So nothing here knows ESCO. This module is
the one place the rest of Postulo asks "which unit group is this title", "which skill is this
name", "what does this skill box offer", and it asks the registry: the `suggestion-source`
plugin called ``esco`` -- if it is installed and on -- is handed the question, and an answer
that comes back empty is what every caller already copes with, because a title that matches
nothing is the ordinary state of a title.

**Off keeps what is kept.** A posting's unit group and a skill's identifier follow the name
they were worked out from. While the plugin is off they are not worked out again and not
cleared: the caller says what it holds (``otherwise``) and gets that back, so switching the
plugin on later finds every code where it was left.

**Who decides whether it is on** is the plugin policy, which sits above the layers a model
lives in. So it is handed in: the plugins app sets `is_on` when it is ready, and until then
(or in a process that never loads it) the vocabulary is on, which is what it is by default.
"""

from __future__ import annotations

import logging
from collections.abc import Callable

logger = logging.getLogger(__name__)

#: The plugin's name, as the registry and the policy rows know it.
NAME = "esco"

#: ``is_on(user, name) -> bool``: whether the plugin ``name`` is on for ``user`` (``None``
#: for the instance). Replaced by the plugins app; see the module.
is_on: Callable[[object, str], bool] = lambda user, name: True  # noqa: E731


def _reading(user=None):
    """The enabled plugin's classification reader, or ``None`` where there is none."""
    from postulo.plugins.registry import find_plugin

    try:
        plugin = find_plugin("suggestion-source", NAME)
        if plugin is None or not is_on(user, NAME):
            return None
        return getattr(plugin, "classification", None)
    except Exception:
        logger.exception("The %r plugin could not be asked", NAME)
        return None


def available(user=None) -> bool:
    """Whether the plugin is installed and on, whether or not its data has been downloaded."""
    return _reading(user) is not None


def code_for(title: str, language: str = "", *, otherwise: str = "") -> str:
    """The ISCO-08 unit group a title matches, ``""`` where it matches none, ``otherwise``
    where the plugin is off."""
    reading = _reading()
    return otherwise if reading is None else reading.code_for(title, language)


def name_for(code: str, language: str = "") -> str:
    """What the classification calls a unit group, in the language read; ``""`` without it."""
    reading = _reading()
    return "" if reading is None else reading.name_for(code, language)


def divisions_for(unit_group: str) -> frozenset[str]:
    """The NACE divisions the occupations of a unit group are employed in."""
    reading = _reading()
    return frozenset() if reading is None else reading.divisions_for(unit_group)


def skill_for(name: str, *languages: str, otherwise: str = "") -> str:
    """The identifier of the skill a name is, ``""`` where it is none, ``otherwise`` where
    the plugin is off."""
    reading = _reading()
    return otherwise if reading is None else reading.skill_for(name, *languages)


def skill_name(uri: str, language: str = "", *, strict: bool = True, user=None) -> str:
    """What the classification calls a skill in a language; ``""`` where it says nothing."""
    reading = _reading(user)
    return "" if reading is None else reading.skill_name(uri, language, strict=strict)


def skill_suggestions(
    typed: str, *languages: str, limit: int | None = None, user=None
) -> list[str]:
    """The skills whose names begin with what was typed; none where the plugin is off."""
    reading = _reading(user)
    if reading is None:
        return []
    if limit is None:
        return reading.skill_suggestions(typed, *languages)
    return reading.skill_suggestions(typed, *languages, limit=limit)


def skills_named(text: str, *languages: str, user=None) -> tuple[str, ...]:
    """The identifiers of the skills a text names, in the order it names them."""
    reading = _reading(user)
    return () if reading is None else reading.skills_named(text, *languages)


def skills_available(user=None) -> bool:
    """Whether the skills can be read: the plugin is on and its skills file is here."""
    reading = _reading(user)
    return reading is not None and reading.skills_available()
