"""A weak secret key stops the instance rather than printing a line nobody reads (#111).

`prod.py` refused to start with **no** key and said nothing about a short one, so
`POSTULO_SECRET_KEY=changeme` started perfectly well. Django notices — `security.W009` is
this exact check — but the container runs `check --deploy --fail-level ERROR` and W009 is a
*warning*, so it went into a start-up log while the instance served traffic.

It matters more here than in most Django applications because the key does not only sign
sessions: `plugins/secrets.py` derives the Fernet key protecting every stored connection
credential from it, through one unsalted SHA-256. A guessable key is a guessable key for
other people's passwords to other people's services.

Which is also why every refusal has to say what it says: replacing the key on a running
instance makes those credentials unreadable. A message that sent an operator to fix one
problem by causing a worse one would be worse than the warning it replaced.

Every case is a start of the production settings with a different environment, and they
used to be one interpreter each: fifteen Python start-ups, at up to two seconds apiece,
for fifteen assertions about a string (#233). They are one subprocess now, which imports
the settings afresh for each case -- the modules are dropped and the environment swapped
between them -- and reports every outcome at once. The environment is scrubbed the same
way, like everything else that asks what the production settings say; see `conftest.py`
beside this.
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path

import pytest

from postulo.config.settings import keys

#: Long enough, varied enough, and obviously not a placeholder.
GOOD = "kQ7vN2xR9wT4yU6iO8pA3sD5fG1hJ0kL2zX4cV6bN8mQ7wE9rT5yU3iO1pA6sD4f"
#: Random, and short of Django's own threshold.
SHORT = "q7vN2xR9wT4yU6iO8pA3s"
PLACEHOLDERS = ["changeme", "CHANGEME", "  change-me  ", "secret", "password", "postulo", "todo"]

#: Every start attempted, by name: the environment it is given on top of the scrubbed one.
CASES: dict[str, dict[str, str]] = {
    "good": {"POSTULO_SECRET_KEY": GOOD},
    "empty": {"POSTULO_SECRET_KEY": ""},
    **{f"placeholder:{word}": {"POSTULO_SECRET_KEY": word} for word in PLACEHOLDERS},
    "django-insecure": {"POSTULO_SECRET_KEY": "django-insecure-" + "x9k2m" * 12},
    "short-random": {"POSTULO_SECRET_KEY": SHORT},
    "one-character": {"POSTULO_SECRET_KEY": "a" * 80},
    "weak-field-key": {"POSTULO_SECRET_KEY": GOOD, "POSTULO_FIELD_KEY": "changeme"},
    "good-field-key": {"POSTULO_SECRET_KEY": GOOD, "POSTULO_FIELD_KEY": GOOD[::-1]},
    "no-field-key": {"POSTULO_SECRET_KEY": GOOD, "POSTULO_FIELD_KEY": ""},
    "allowed-short": {"POSTULO_SECRET_KEY": SHORT, "POSTULO_ALLOW_WEAK_SECRET_KEY": "true"},
    "allowed-placeholder": {
        "POSTULO_SECRET_KEY": "changeme",
        "POSTULO_ALLOW_WEAK_SECRET_KEY": "true",
    },
    "allowed-empty": {"POSTULO_SECRET_KEY": "", "POSTULO_ALLOW_WEAK_SECRET_KEY": "true"},
}

#: Runs every case in one interpreter. The settings modules are dropped between cases so
#: that each import reads the environment it was given, and the .env in the repository is
#: taken out of the picture because what is under test is what the module itself says.
PROBE = """
import importlib, json, os, sys
import environ
environ.Env.read_env = lambda *args, **kwargs: None
cases = json.loads(sys.stdin.read())
scrubbed = dict(os.environ)
outcomes = {}
for name, variables in cases.items():
    os.environ.clear()
    os.environ.update(scrubbed)
    os.environ.update(variables)
    for module in [m for m in sys.modules if m.startswith("postulo.config.settings")]:
        del sys.modules[module]
    try:
        importlib.import_module("postulo.config.settings.prod")
    except Exception as error:
        outcomes[name] = {"started": False, "error": f"{type(error).__name__}: {error}"}
    else:
        outcomes[name] = {"started": True, "error": ""}
print(json.dumps(outcomes))
"""


@dataclass(frozen=True)
class Outcome:
    started: bool
    error: str


@pytest.fixture(scope="session")
def outcomes() -> dict[str, Outcome]:
    """Every case started once, in one subprocess, with the environment scrubbed."""
    scrubbed = {k: v for k, v in os.environ.items() if not k.startswith("POSTULO_")}
    scrubbed["POSTULO_ALLOWED_HOSTS"] = "postulo.example.org"
    scrubbed["DJANGO_SETTINGS_MODULE"] = "postulo.config.settings.prod"
    run = subprocess.run(  # noqa: S603 - this interpreter, and a string written here
        [sys.executable, "-c", PROBE],
        input=json.dumps(CASES),
        capture_output=True,
        text=True,
        env=scrubbed,
        cwd=Path(__file__).resolve().parents[2],
    )
    assert run.returncode == 0, run.stderr
    return {name: Outcome(**outcome) for name, outcome in json.loads(run.stdout).items()}


# ----------------------------------------------------------------- what is refused


def test_a_real_key_starts(outcomes):
    assert outcomes["good"].started, outcomes["good"].error


def test_no_key_is_still_refused(outcomes):
    """The check that was already there, kept."""
    refused = outcomes["empty"]

    assert not refused.started
    assert "POSTULO_SECRET_KEY must be set" in refused.error


@pytest.mark.parametrize("key", PLACEHOLDERS)
def test_a_placeholder_is_refused_however_it_is_typed(outcomes, key):
    refused = outcomes[f"placeholder:{key}"]

    assert not refused.started
    assert "is a placeholder" in refused.error


def test_djangos_own_generated_placeholder_is_refused(outcomes):
    """`startproject` writes this prefix, and it has been documented as not-a-secret since."""
    refused = outcomes["django-insecure"]

    assert not refused.started
    assert "is a placeholder" in refused.error


def test_a_short_key_is_refused_even_when_it_is_random(outcomes):
    refused = outcomes["short-random"]

    assert not refused.started
    assert "too weak" in refused.error and "21 characters" in refused.error


def test_a_long_key_of_one_character_is_refused(outcomes):
    """Django's other half of W009: length alone proves nothing."""
    refused = outcomes["one-character"]

    assert not refused.started
    assert "distinct characters" in refused.error


# ------------------------------------------------------- the key that matters most


def test_a_weak_field_key_is_refused_too(outcomes):
    """Whenever POSTULO_FIELD_KEY is set, *it* is the key protecting stored credentials.

    Not in the issue, and the same hole with the same blast radius: checking one and not the
    other would leave the shorter path to the same place open.
    """
    refused = outcomes["weak-field-key"]

    assert not refused.started
    assert "POSTULO_FIELD_KEY" in refused.error


def test_a_good_field_key_is_fine(outcomes):
    assert outcomes["good-field-key"].started, outcomes["good-field-key"].error


def test_no_field_key_at_all_is_fine(outcomes):
    """It is optional, and its absence means the secret key is used instead."""
    assert outcomes["no-field-key"].started, outcomes["no-field-key"].error


# --------------------------------------------------------------- what it tells you


def test_the_refusal_says_how_to_generate_one(outcomes):
    assert "secrets.token_urlsafe" in outcomes["placeholder:changeme"].error


def test_the_refusal_warns_that_replacing_the_key_loses_the_credentials(outcomes):
    """The obvious fix is the one that destroys data, so the message cannot omit it."""
    refused = outcomes["placeholder:changeme"]

    assert "POSTULO_FIELD_KEY" in refused.error
    assert "unreadable" in refused.error


# ------------------------------------------------------------------- the way out


def test_an_operator_can_start_a_short_key_deliberately(outcomes):
    """For the instance this catches at three in the morning, not as an answer."""
    allowed = outcomes["allowed-short"]

    assert allowed.started, allowed.error


def test_the_way_out_does_not_open_for_a_placeholder(outcomes):
    """Somebody who typed `changeme` has chosen nothing, so there is nothing to buy time for."""
    refused = outcomes["allowed-placeholder"]

    assert not refused.started
    assert "is a placeholder" in refused.error


def test_the_way_out_does_not_open_for_an_empty_key(outcomes):
    assert not outcomes["allowed-empty"].started


# ------------------------------------------------------------------ the rules alone


@pytest.mark.parametrize("key", ["", "a", "x" * 49])
def test_anything_under_the_threshold_is_weak(key):
    assert keys.weakness_of(key)


def test_the_threshold_is_djangos_own():
    """Moved from a warning to a refusal, not invented.

    Read out of Django rather than retyped, so the day it revises its own numbers this
    disagrees loudly instead of quietly enforcing the old ones.
    """
    from django.core.checks.security import base as djangos

    assert keys.MIN_LENGTH == djangos.SECRET_KEY_MIN_LENGTH
    assert keys.MIN_UNIQUE_CHARACTERS == djangos.SECRET_KEY_MIN_UNIQUE_CHARACTERS
    assert djangos.SECRET_KEY_INSECURE_PREFIX in keys.PLACEHOLDER_PREFIX

    # 55 characters, 5 distinct: over both thresholds and nothing else to object to.
    assert not keys.weakness_of("q7vNx" * 11)
