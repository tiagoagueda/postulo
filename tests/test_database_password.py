"""A database password is read as it is, not parsed as part of a URL (#582)."""

from __future__ import annotations

import environ
import pytest

from postulo.config.database_password import apply_password

AWKWARD = "ab/cd?ef%41"


def test_a_password_with_url_characters_arrives_exactly():
    url = "postgres://postulo@db:5432/postulo"
    database = environ.Env.db_url_config(url)
    apply_password(database, {"POSTULO_DATABASE_PASSWORD": AWKWARD})
    assert database["PASSWORD"] == AWKWARD


def test_the_password_the_database_container_reads_serves_too():
    database = environ.Env.db_url_config("postgres://postulo@db:5432/postulo")
    apply_password(database, {"POSTGRES_PASSWORD": AWKWARD})
    assert database["PASSWORD"] == AWKWARD


def test_the_url_wins_and_sqlite_is_left_alone():
    with_one = environ.Env.db_url_config("postgres://postulo:fromurl@db:5432/postulo")
    apply_password(with_one, {"POSTGRES_PASSWORD": AWKWARD})
    assert with_one["PASSWORD"] == "fromurl"

    sqlite = environ.Env.db_url_config("sqlite:///x.sqlite3")
    apply_password(sqlite, {"POSTGRES_PASSWORD": AWKWARD})
    assert not sqlite.get("PASSWORD")


@pytest.mark.parametrize("url", ["postgres://postulo:ab/cd@db:5432/postulo"])
def test_the_url_form_is_what_broke(url):
    """Why the password has its own variable: `/` in a URL password is not parsed."""
    with pytest.raises(ValueError):
        environ.Env.db_url_config(url)
