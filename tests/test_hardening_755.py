"""The cache the rate limits live in is not one an outsider can make forget them (#755)."""

from __future__ import annotations

from django.core.cache import caches


def test_the_database_cache_holds_far_more_than_django_s_three_hundred():
    from django.conf import settings

    default = settings.CACHES["default"]
    options = default.get("OPTIONS", {})

    if default["BACKEND"].endswith("db.DatabaseCache"):
        assert options.get("MAX_ENTRIES", 300) >= 100_000


def test_the_setting_is_what_base_settings_apply(monkeypatch):
    import importlib

    from postulo.config.settings import base

    assert base.POSTULO_CACHE_MAX_ENTRIES >= 100_000
    cache = base.CACHES["default"]
    if cache["BACKEND"].endswith("db.DatabaseCache"):
        assert cache["OPTIONS"]["MAX_ENTRIES"] == base.POSTULO_CACHE_MAX_ENTRIES
    assert importlib is not None


def test_counters_survive_more_writes_than_the_old_limit(db):
    cache = caches["default"]
    cache.clear()
    cache.set("postulo:rate:probe:1:0", 4, 300)

    for number in range(400):
        cache.set(f"zzz-noise-{number}", 1, 300)

    assert cache.get("postulo:rate:probe:1:0") == 4, "the counter was not culled by the noise"
