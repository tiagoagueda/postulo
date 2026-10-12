"""A backup's manifest carries a salted, stretched mark of the key, not a bare hash (#811)."""

from __future__ import annotations

import hashlib

from postulo.plugins import secrets


def test_the_mark_is_salted_so_two_archives_differ():
    first, second = secrets.mark(), secrets.mark()
    assert first["salt"] != second["salt"] and first["mark"] != second["mark"]
    assert first["kdf"] == "scrypt"


def test_it_is_not_a_plain_hash_of_the_key():
    material = secrets._material()
    plain = {
        hashlib.sha256(material.encode()).hexdigest(),
        hashlib.sha256(b"postulo-field-key-fingerprint:" + material.encode()).hexdigest(),
    }
    assert secrets.mark()["mark"] not in plain


def test_this_instances_key_matches_and_another_does_not(settings):
    held = secrets.mark()
    assert secrets.matches(held)
    settings.POSTULO_FIELD_KEY = "an-entirely-different-key-42"
    assert not secrets.matches(held)


def test_an_archive_from_before_still_checks_against_the_old_mark():
    assert secrets.matches(secrets.fingerprint())
    assert not secrets.matches("0" * 64)


def test_a_mangled_mark_is_a_no_not_a_crash():
    assert not secrets.matches({"salt": "not hex", "mark": "x"})
    assert not secrets.matches({})
    assert not secrets.matches(None)
