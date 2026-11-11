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

**An idle worker is quiet** (#1117). It goes round every two seconds, so whatever it does
each time it does 43,200 times a day. The heartbeat is therefore written once a minute, not
once a pass: everything that reads it allows five. And upstream's two lines a pass, "Starting
worker" and "No more tasks to run", are not logged at all (`LOGGING` hears `django_tasks_db`
from WARNING up); in their place this command says once that it has started.
"""

from __future__ import annotations

import logging
import time

from django.core.management.base import BaseCommand
from django.utils.crypto import get_random_string

from postulo.core import scheduler

logger = logging.getLogger(__name__)

#: Seconds between two writes of the heartbeat (#1117). What reads it calls the worker gone
#: after five minutes of silence: the container's healthcheck (`docker/compose*.yml`) and
#: the page that watches slow work (`views_errands.WORKER_SILENT_AFTER`). A minute leaves
#: both four to spare, and Server settings' "a worker was here ... ago" right to the minute
#: it counts in.
HEARTBEAT_EVERY = 60.0


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
        # Once, and only for the worker that stays: a single pass run from cron every minute
        # would say it every minute. Upstream's own lines are not heard (see `LOGGING`), and
        # without this the container's log would not show that the worker came up (#1117).
        if not options["once"]:
            logger.info(
                "Worker %s started: queue %s, looking every %s s.",
                name,
                options["queue"],
                f"{interval:g}",
            )
        # When the heartbeat was last written, on the clock that never goes backwards: a
        # server whose time is corrected must neither stop the writes nor hurry them.
        beaten: float | None = None

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
            # A stop asked for while a task ran is upstream's `running = False`, and the
            # task has now finished. Building another worker would forget it, and the
            # container would be killed mid-way through the next task (#474).
            if not worker.running:
                return
            # Last, and only on a pass that finished, for the reason the scheduler's is
            # last: the heartbeat answers "is it still going round", and a pass that died
            # half way did not go round. Not on every pass, though: the first one, so that a
            # worker which has just started is seen at once, and then one a minute (#1117).
            now = time.monotonic()
            if beaten is None or now - beaten >= HEARTBEAT_EVERY:
                scheduler.worker_beat()
                beaten = now
            if options["once"]:
                return
            time.sleep(interval)
