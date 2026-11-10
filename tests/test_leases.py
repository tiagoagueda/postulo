"""The lease a scheduler pass, a backup and a sync each hold while they run (#1130)."""

from __future__ import annotations

import datetime as dt
import threading

import pytest
from django.core.cache import cache
from django.db import connection, connections, transaction
from django.test.utils import CaptureQueriesContext
from django.utils import timezone

from postulo.core import backups, leases, scheduler
from postulo.core.models import Lease

pytestmark = pytest.mark.django_db

KEY = "postulo:test:lease"


def an_expired_lease(key: str = KEY, *, held_by: str = "a-dead-run") -> Lease:
    """What a killed worker leaves behind: a row whose time has passed."""
    return Lease.objects.create(
        key=key, token=held_by, expires=timezone.now() - dt.timedelta(minutes=1)
    )


# --------------------------------------------------------------------------------- take


def test_a_lease_is_taken_once():
    """The second taker is refused, and the row stays the first one's (#1130)."""
    mine = leases.take(KEY, 60)

    assert mine
    assert leases.take(KEY, 60) is None
    assert Lease.objects.get(key=KEY).token == mine
    assert leases.held(KEY)


def test_an_expired_lease_is_taken_by_whoever_comes_next():
    """A run that died without letting go does not hold the work for ever (#1130)."""
    an_expired_lease()
    assert not leases.held(KEY)

    mine = leases.take(KEY, 60)

    row = Lease.objects.get(key=KEY)
    assert mine and row.token == mine
    assert row.expires > timezone.now()
    assert leases.take(KEY, 60) is None


def test_taking_an_expired_lease_says_expired_in_the_statement_that_takes_it():
    """The cache read the row, found it expired, and then updated it by its key alone, so on
    PostgreSQL a second taker waiting on the first one's update wrote over it (#1130). The
    update carries the condition itself, which is what the waiting one is held to."""
    an_expired_lease()

    with CaptureQueriesContext(connection) as queries:
        assert leases.take(KEY, 60)

    updates = [query["sql"] for query in queries if query["sql"].startswith("UPDATE")]
    assert len(updates) == 1, "one statement takes it over"
    where = updates[0].split(" WHERE ", 1)[1]
    assert '"key" = ' in where
    assert '"expires" < ' in where
    assert not [query for query in queries if query["sql"].startswith("SELECT")], (
        "nothing is decided from a row read beforehand"
    )


def test_two_takers_over_one_expired_lease_leave_one_holder(monkeypatch):
    """Both found the expired row and both took it, so two backups or two syncs of one
    connection ran side by side (#1130). Here the second finds the row there, the first
    takes it over, and only then does the second try: it must change nothing."""
    an_expired_lease()
    insert = leases._insert
    first = []

    def the_first_gets_in_between(key, token, expires):
        """The second taker's insert is refused; before its update, the first taker's lands."""
        refused = insert(key, token, expires)
        monkeypatch.setattr(leases, "_insert", insert)
        first.append(leases.take(KEY, 60))
        return refused

    monkeypatch.setattr(leases, "_insert", the_first_gets_in_between)

    second = leases.take(KEY, 60)

    assert first[0], "the first taker has it"
    assert second is None, "and the second, who also saw it expired, does not"
    assert Lease.objects.get(key=KEY).token == first[0]


def test_a_refused_take_leaves_the_callers_transaction_usable():
    """The insert that finds a row there is refused inside a savepoint (#1130)."""
    leases.take(KEY, 60)

    with transaction.atomic():
        assert leases.take(KEY, 60) is None
        assert Lease.objects.filter(key=KEY).count() == 1


# ------------------------------------------------------------------------------ release


def test_a_lease_given_back_can_be_taken_again():
    mine = leases.take(KEY, 60)

    assert leases.release(KEY, mine) is True
    assert not Lease.objects.filter(key=KEY).exists()
    assert leases.take(KEY, 60)


def test_only_the_holder_gives_a_lease_back():
    """A run that outlived its lease must not free the one a later run took (#576, #988):
    the delete names the token, so it is one statement and not a look and then a delete."""
    an_expired_lease(held_by="the-run-that-took-too-long")
    theirs = leases.take(KEY, 60)

    with CaptureQueriesContext(connection) as queries:
        assert leases.release(KEY, "the-run-that-took-too-long") is False

    assert Lease.objects.get(key=KEY).token == theirs
    deletes = [query["sql"] for query in queries if query["sql"].startswith("DELETE")]
    assert len(deletes) == 1
    assert '"key" = ' in deletes[0] and '"token" = ' in deletes[0]


# -------------------------------------------------------------------------------- renew


def test_the_holder_can_keep_a_lease_for_longer():
    mine = leases.take(KEY, 60)
    before = Lease.objects.get(key=KEY).expires

    assert leases.renew(KEY, mine, 3600) is True

    assert Lease.objects.get(key=KEY).expires > before + dt.timedelta(minutes=30)


def test_nobody_else_can_keep_a_lease_for_longer():
    """A run whose lease expired and was taken by another cannot stretch the other's (#1130)."""
    an_expired_lease(held_by="the-run-that-took-too-long")
    leases.take(KEY, 60)
    before = Lease.objects.get(key=KEY).expires

    assert leases.renew(KEY, "the-run-that-took-too-long", 3600) is False
    assert leases.renew("postulo:test:never-taken", "anything", 3600) is False

    assert Lease.objects.get(key=KEY).expires == before


# -------------------------------------------------------------------------- the callers


def test_a_pass_keeps_its_lease_when_the_cache_is_emptied():
    """The lease lived in the cache, so flushing the cache let a second scheduler in beside
    the first, and so did the database cache's own race over an expired key (#1130)."""
    with scheduler.only_one_pass(60) as mine:
        assert mine is True
        cache.clear()
        with scheduler.only_one_pass(60) as theirs:
            assert theirs is False


def test_a_backup_keeps_its_lease_when_the_cache_is_emptied(monkeypatch):
    """The same for *Back up now* pressed while the scheduled backup runs (#1130)."""
    inner = []

    def a_second_one_starts_meanwhile(alive=None):
        cache.clear()
        inner.append(backups.running())
        with pytest.raises(backups.Busy):
            backups.take()
        raise backups.BackupError("stopped here")

    monkeypatch.setattr(backups, "write_backup", a_second_one_starts_meanwhile)

    with pytest.raises(backups.BackupError):
        backups.take()

    assert inner == [True]
    assert not backups.running(), "and it is given back when the backup fails"


def test_a_pass_starts_over_the_lease_of_one_that_died():
    an_expired_lease(scheduler.LEASE_KEY)

    with scheduler.only_one_pass(60) as mine:
        assert mine is True
        with scheduler.only_one_pass(60) as theirs:
            assert theirs is False
    assert not Lease.objects.filter(key=scheduler.LEASE_KEY).exists()


# --------------------------------------------------------------------------- PostgreSQL


@pytest.mark.django_db(transaction=True)
def test_two_connections_taking_one_expired_lease_have_one_winner():
    """The race itself, where it happened: two connections under READ COMMITTED, both
    arriving over an expired row before either has committed (#1130). The first holds its
    transaction open so that the second really is waiting on it, and the second must come
    away with nothing once the first commits."""
    if connection.vendor != "postgresql":
        pytest.skip("SQLite serialises its writers; only PostgreSQL lets two in at once")
    an_expired_lease()
    first_has_written = threading.Event()
    second_is_trying = threading.Event()
    taken: dict[str, str | None] = {}

    def first():
        try:
            with transaction.atomic():
                taken["first"] = leases.take(KEY, 60)
                first_has_written.set()
                second_is_trying.wait(5)
                # Long enough for the second taker's statement to reach the row and wait.
                threading.Event().wait(0.5)
        finally:
            connections.close_all()

    def second():
        try:
            first_has_written.wait(5)
            second_is_trying.set()
            taken["second"] = leases.take(KEY, 60)
        finally:
            connections.close_all()

    threads = [threading.Thread(target=first), threading.Thread(target=second)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join(30)

    assert taken["first"], "the first taker has it"
    assert taken["second"] is None, "the one that waited found it taken"
    assert Lease.objects.get(key=KEY).token == taken["first"]
