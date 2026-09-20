"""Run the worker that does Postulo's slow work (#247).

    manage.py work

A thin loop around `django_tasks_db`'s own worker, for one thing it does not do: say that it
is still going round. The upstream `db_worker` loops for ever and sleeps inside its own loop,
so there is no moment a heartbeat could be written from without reaching into it. Run in
*batch* mode it drains the queue and returns, which puts the sleeping — and therefore the
heartbeat — out here where this command can own it. Everything that actually runs a task is
upstream's: claiming a row, the exclusive transaction around the claim, the signals, the
handling of a locked database.

**Why a heartbeat at all.** The worker is a second container. Its own healthcheck has no port
to curl and no database credentials, and the page that reports it runs in a *different*
container, so a file on the shared volume is the one thing both can see — which is exactly
the argument `core/scheduler.py` makes for the scheduler's (#221).

**It is another writer on the SQLite file**, so it follows the rule #220 set: the work
itself holds no transaction, and each write inside a handler is a short one of its own.
"""

from __future__ import annotations

import time

from django.core.management.base import BaseCommand
from django.utils.crypto import get_random_string

from postulo.core import scheduler


class Command(BaseCommand):
    help = "Do the slow work Postulo has queued: fetches, renders, archives, notifications."

    def add_arguments(self, parser) -> None:
        parser.add_argument(
            "--every",
            type=float,
            default=2.0,
            dest="interval",
            help="Seconds to wait after emptying the queue before looking again.",
        )
        parser.add_argument(
            "--once",
            action="store_true",
            help="Drain the queue once and stop. For cron, and for tests.",
        )
        parser.add_argument(
            "--queue",
            default="default",
            help="The queue to take work from.",
        )

    def handle(self, *args, **options) -> None:
        from django_tasks_db.management.commands.db_worker import Worker

        interval = max(float(options["interval"]), 0.1)
        name = f"postulo-{get_random_string(8)}"

        while True:
            worker = Worker(
                queue_names=[options["queue"]],
                interval=interval,
                # The whole point: it returns when there is nothing left, so the sleeping
                # and the heartbeat happen here rather than inside somebody else's loop.
                batch=True,
                backend_name="default",
                startup_delay=False,
                max_tasks=None,
                worker_id=name,
                excluded_queue_names=[],
            )
            worker.configure_signals()
            worker.run()
            # Last, and only on a pass that finished, for the reason the scheduler's is
            # last: the heartbeat answers "is it still going round", and a pass that died
            # half way did not go round.
            scheduler.worker_beat()
            if options["once"]:
                return
            time.sleep(interval)
