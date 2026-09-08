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

Run in a subprocess with the environment scrubbed, like everything else that asks what the
production settings say — see `conftest.py` beside this.
"""

from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

import pytest

from postulo.config.settings import keys

#: Long enough, varied enough, and obviously not a placeholder.
GOOD = "kQ7vN2xR9wT4yU6iO8pA3sD5fG1hJ0kL2zX4cV6bN8mQ7wE9rT5yU3iO1pA6sD4f"

PROBE = (
    "import django, environ;"
    # The repository's .env belongs to whoever is developing here, and what is under test is
    # what the settings module does with the environment it is given.
    " environ.Env.read_env = lambda *a, **k: None;"
    " django.setup();"
    " print('started')"
)


def start_with(**environment) -> subprocess.CompletedProcess:
    """Import the production settings the way a server would, with these variables."""
    scrubbed = {k: v for k, v in os.environ.items() if not k.startswith("POSTULO_")}
    scrubbed.update(
        {
            "POSTULO_ALLOWED_HOSTS": "postulo.example.org",
            "DJANGO_SETTINGS_MODULE": "postulo.config.settings.prod",
            **environment,
        }
    )
    return subprocess.run(  # noqa: S603 - this interpreter, and a string written here
        [sys.executable, "-c", PROBE],
        capture_output=True,
        text=True,
        env=scrubbed,
        cwd=Path(__file__).resolve().parents[2],
    )


# ----------------------------------------------------------------- what is refused


def test_a_real_key_starts():
    assert start_with(POSTULO_SECRET_KEY=GOOD).returncode == 0


def test_no_key_is_still_refused():
    """The check that was already there, kept."""
    refused = start_with(POSTULO_SECRET_KEY="")

    assert refused.returncode != 0
    assert "POSTULO_SECRET_KEY must be set" in refused.stderr


@pytest.mark.parametrize(
    "key", ["changeme", "CHANGEME", "  change-me  ", "secret", "password", "postulo", "todo"]
)
def test_a_placeholder_is_refused_however_it_is_typed(key):
    refused = start_with(POSTULO_SECRET_KEY=key)

    assert refused.returncode != 0
    assert "is a placeholder" in refused.stderr


def test_djangos_own_generated_placeholder_is_refused():
    """`startproject` writes this prefix, and it has been documented as not-a-secret since."""
    refused = start_with(POSTULO_SECRET_KEY="django-insecure-" + "x9k2m" * 12)

    assert refused.returncode != 0
    assert "is a placeholder" in refused.stderr


def test_a_short_key_is_refused_even_when_it_is_random():
    refused = start_with(POSTULO_SECRET_KEY="q7vN2xR9wT4yU6iO8pA3s")

    assert refused.returncode != 0
    assert "too weak" in refused.stderr and "21 characters" in refused.stderr


def test_a_long_key_of_one_character_is_refused():
    """Django's other half of W009: length alone proves nothing."""
    refused = start_with(POSTULO_SECRET_KEY="a" * 80)

    assert refused.returncode != 0
    assert "distinct characters" in refused.stderr


# ------------------------------------------------------- the key that matters most


def test_a_weak_field_key_is_refused_too():
    """Whenever POSTULO_FIELD_KEY is set, *it* is the key protecting stored credentials.

    Not in the issue, and the same hole with the same blast radius: checking one and not the
    other would leave the shorter path to the same place open.
    """
    refused = start_with(POSTULO_SECRET_KEY=GOOD, POSTULO_FIELD_KEY="changeme")

    assert refused.returncode != 0
    assert "POSTULO_FIELD_KEY" in refused.stderr


def test_a_good_field_key_is_fine():
    assert start_with(POSTULO_SECRET_KEY=GOOD, POSTULO_FIELD_KEY=GOOD[::-1]).returncode == 0


def test_no_field_key_at_all_is_fine():
    """It is optional, and its absence means the secret key is used instead."""
    assert start_with(POSTULO_SECRET_KEY=GOOD, POSTULO_FIELD_KEY="").returncode == 0


# --------------------------------------------------------------- what it tells you


def test_the_refusal_says_how_to_generate_one():
    refused = start_with(POSTULO_SECRET_KEY="changeme")

    assert "secrets.token_urlsafe" in refused.stderr


def test_the_refusal_warns_that_replacing_the_key_loses_the_credentials():
    """The obvious fix is the one that destroys data, so the message cannot omit it."""
    refused = start_with(POSTULO_SECRET_KEY="changeme")

    assert "POSTULO_FIELD_KEY" in refused.stderr
    assert "unreadable" in refused.stderr


# ------------------------------------------------------------------- the way out


def test_an_operator_can_start_a_short_key_deliberately():
    """For the instance this catches at three in the morning, not as an answer."""
    allowed = start_with(
        POSTULO_SECRET_KEY="q7vN2xR9wT4yU6iO8pA3s", POSTULO_ALLOW_WEAK_SECRET_KEY="true"
    )

    assert allowed.returncode == 0, allowed.stderr


def test_the_way_out_does_not_open_for_a_placeholder():
    """Somebody who typed `changeme` has chosen nothing, so there is nothing to buy time for."""
    refused = start_with(POSTULO_SECRET_KEY="changeme", POSTULO_ALLOW_WEAK_SECRET_KEY="true")

    assert refused.returncode != 0
    assert "is a placeholder" in refused.stderr


def test_the_way_out_does_not_open_for_an_empty_key():
    refused = start_with(POSTULO_SECRET_KEY="", POSTULO_ALLOW_WEAK_SECRET_KEY="true")

    assert refused.returncode != 0


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
