"""Allowances are counted outside the request's transaction (#756)."""

from __future__ import annotations

from django.core.cache import caches

from postulo.config.settings import base
from postulo.core import throttle


def test_by_default_the_allowances_are_in_a_file_store_not_in_the_database_cache():
    ratelimit = base.CACHES["ratelimit"]

    if base.CACHES["default"]["BACKEND"].endswith("db.DatabaseCache"):
        assert ratelimit["BACKEND"].endswith("filebased.FileBasedCache")
        assert ratelimit["OPTIONS"]["MAX_ENTRIES"] == base.POSTULO_CACHE_MAX_ENTRIES
    else:
        assert ratelimit["BACKEND"] == base.CACHES["default"]["BACKEND"]


def test_the_throttle_counts_in_that_store(monkeypatch, tmp_path):
    from django.core.cache.backends.filebased import FileBasedCache

    store = FileBasedCache(str(tmp_path), {})
    monkeypatch.setattr(throttle, "_store", lambda: store)
    rate = throttle.parse("2/h")

    throttle.consume("probe", "someone", rate)
    throttle.consume("probe", "someone", rate)
    try:
        throttle.consume("probe", "someone", rate)
    except throttle.TooOften:
        refused = True
    else:
        refused = False

    assert refused and any(tmp_path.iterdir()), "and what was counted is on disk"


def test_a_charge_in_a_store_outside_the_database_survives_a_rolled_back_transaction(
    monkeypatch, tmp_path, transactional_db
):
    """The whole point (#756): a view that raises after spending an allowance rolls back the
    request's transaction, and the spend must not go with it."""
    from django.core.cache.backends.filebased import FileBasedCache
    from django.db import transaction

    store = FileBasedCache(str(tmp_path), {})
    monkeypatch.setattr(throttle, "_store", lambda: store)
    rate = throttle.parse("5/h")

    try:
        with transaction.atomic():
            throttle.consume("probe", "someone", rate)
            raise RuntimeError("the view raised after spending")
    except RuntimeError:
        pass

    assert store.get(throttle._key("probe", "someone", rate)) == 1


def test_the_test_settings_share_the_database_cache_so_tests_start_afresh():
    assert caches["ratelimit"].__class__ is caches["default"].__class__
