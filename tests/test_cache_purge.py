"""Expired cache entries are deleted once an hour, and live ones never are (#1167).

Django's database cache and its file cache both delete an expired entry only when that same
key is asked for again, or when the store reaches `MAX_ENTRIES`, which is 200,000 here so
that a counter cannot be pushed out (#755). A key that is never asked for again (the last
hour's rate-limit window, the dashboard figures of a fingerprint that has since changed)
therefore stayed for good, and every write counted the table or listed the directory.

Looked at where the entries are kept, the table and the directory, because the cache's own
`get` hides an expired entry and deletes it as it does.
"""

from __future__ import annotations

import pytest
from django.core.cache import caches
from django.core.cache.backends.filebased import FileBasedCache
from django.core.management import call_command
from django.db import connection

from postulo.core import slow, throttle

pytestmark = pytest.mark.django_db

DATABASE = "django.core.cache.backends.db.DatabaseCache"
FILES = "django.core.cache.backends.filebased.FileBasedCache"
MEMORY = "django.core.cache.backends.locmem.LocMemCache"


def rows() -> set[str]:
    """The keys the cache table holds, expired or not."""
    with connection.cursor() as cursor:
        cursor.execute("SELECT cache_key FROM postulo_cache")
        return {key.split(":", 2)[2] for (key,) in cursor.fetchall()}


@pytest.fixture
def file_store(settings, tmp_path):
    """The production arrangement: the database cache, and the allowances in files beside it."""
    settings.CACHES = {
        "default": {"BACKEND": DATABASE, "LOCATION": "postulo_cache"},
        "ratelimit": {"BACKEND": FILES, "LOCATION": str(tmp_path)},
    }
    return caches["ratelimit"]


# ------------------------------------------------------------ the database cache


def test_a_pass_deletes_the_expired_rows_and_keeps_the_live_ones():
    """A row past its expiry stayed in the table until 200,000 had piled up, because nothing
    asked for its key again. A scheduler pass deletes it; a live row and its value stay."""
    caches["default"].set("figures:old", "stale", -60)
    caches["default"].set("counter:live", 4, 3600)
    assert rows() >= {"figures:old", "counter:live"}

    call_command("send_due_reminders")

    assert "figures:old" not in rows()
    assert "counter:live" in rows() and caches["default"].get("counter:live") == 4


def test_the_tidy_up_that_pages_ask_for_deletes_them_too():
    """An instance without a scheduler is covered by the hourly tidy a request does."""
    caches["default"].set("figures:old", "stale", -60)
    caches["default"].set("counter:live", 4, 3600)

    slow.tidy_up_if_due()

    assert "figures:old" not in rows()
    assert caches["default"].get("counter:live") == 4


def test_the_rows_go_in_one_statement_however_many(django_assert_num_queries):
    from postulo.core import cache_purge

    for n in range(40):
        caches["default"].set(f"old:{n}", n, -60)
    caches["default"].set("live", "kept", 3600)

    with django_assert_num_queries(1):
        assert cache_purge.expired_rows(caches["default"]) == 40

    assert rows() == {"live"}


def test_it_is_done_once_an_hour_not_on_every_pass():
    """The gate is a key that lasts an hour: a second pass inside it leaves the table alone."""
    from postulo.core import cache_purge

    caches["default"].set("first", 1, -60)
    assert cache_purge.purge_if_due() == 1

    caches["default"].set("second", 1, -60)
    assert cache_purge.purge_if_due() == 0
    assert "second" in rows(), "left for the next hour"

    caches["default"].delete(cache_purge.GATE)
    assert cache_purge.purge_if_due() == 1
    assert "second" not in rows()


# ------------------------------------------------------- the allowances' file cache


def test_a_pass_deletes_the_expired_counter_files_and_keeps_the_live_ones(file_store, tmp_path):
    """Every allowance leaves a file per caller per window, and a past window's key is never
    built again, so its file was never opened again and never removed. A pass removes it;
    a counter still running keeps its file and what it has counted."""
    file_store.set("postulo:rate:api:7:480000", 3, -60)
    file_store.set("postulo:rate:api:7:480001", 2, 3600)
    assert len(list(tmp_path.glob("*.djcache"))) == 2

    call_command("send_due_reminders")

    assert len(list(tmp_path.glob("*.djcache"))) == 1
    assert file_store.get("postulo:rate:api:7:480001") == 2


def test_an_allowance_being_counted_is_not_started_again(file_store, tmp_path):
    """The purge must never give anybody their tries back: two of three spent before it are
    still spent after it, and the fourth is refused."""
    rate = throttle.parse("3/h")
    throttle.consume("probe", "someone", rate)
    throttle.consume("probe", "someone", rate)
    file_store.set("postulo:rate:probe:someone:0", 3, -60)

    slow.tidy_up_if_due()

    assert len(list(tmp_path.glob("*.djcache"))) == 1, "only the window that ended went"
    throttle.consume("probe", "someone", rate)
    with pytest.raises(throttle.TooOften):
        throttle.consume("probe", "someone", rate)


def test_a_file_that_cannot_be_read_does_not_stop_the_rest(tmp_path):
    """A file another process is replacing, or one that is not a cache file at all, is left
    where it is, and the purge goes on to the next."""
    from postulo.core import cache_purge

    store = FileBasedCache(str(tmp_path), {})
    store.set("old", 1, -60)
    (tmp_path / "zzzz.djcache").write_bytes(b"not a pickle")

    assert cache_purge.expired_files(store) == 1
    assert [path.name for path in tmp_path.glob("*.djcache")] == ["zzzz.djcache"]


def test_the_files_are_given_a_few_seconds_and_the_rest_wait(tmp_path):
    """One caller is a request, and a directory built up over a year can take minutes to
    empty on a slow disk. Out of time, the purge stops; the next one carries on."""
    from postulo.core import cache_purge

    store = FileBasedCache(str(tmp_path), {})
    for n in range(3):
        store.set(f"old:{n}", n, -60)

    assert cache_purge.expired_files(store, seconds=0) == 0
    assert len(list(tmp_path.glob("*.djcache"))) == 3

    assert cache_purge.expired_files(store) == 3
    assert not list(tmp_path.glob("*.djcache"))


# ------------------------------------------------------------- any other backend


def test_with_another_backend_nothing_is_touched(settings, tmp_path, django_assert_num_queries):
    """Redis and Memcached expire their own entries. Pointed at one (a memory cache stands
    in for it here), the purge issues no statement, opens no file and takes no gate."""
    from postulo.core import cache_purge

    caches["default"].set("figures:old", "stale", -60)
    FileBasedCache(str(tmp_path), {}).set("old", 1, -60)
    settings.CACHES = {
        "default": {"BACKEND": MEMORY, "LOCATION": "purge-test"},
        "ratelimit": {"BACKEND": MEMORY, "LOCATION": "purge-test"},
    }

    with django_assert_num_queries(0):
        assert cache_purge.purge_if_due() == 0

    assert caches["default"].get(cache_purge.GATE) is None
    assert "figures:old" in rows()
    assert len(list(tmp_path.glob("*.djcache"))) == 1


def test_each_kind_of_purge_refuses_a_backend_that_is_not_its_own(tmp_path):
    from postulo.core import cache_purge

    files = FileBasedCache(str(tmp_path), {})
    files.set("old", 1, -60)
    caches["default"].set("old", 1, -60)

    assert cache_purge.expired_rows(files) == 0
    assert cache_purge.expired_files(caches["default"]) == 0
    assert "old" in rows() and len(list(tmp_path.glob("*.djcache"))) == 1
