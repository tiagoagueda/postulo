"""Give back to the disk the room that deleted records left inside the SQLite file (#1128).

A deleted row's pages are kept inside the file to be used again, so the file never gets
smaller by itself: after a large purge it stays at its largest, and every backup copies it
at that size. This says how much of the file is such pages, and then rebuilds it without
them.

It is a command and not something the scheduler does, because of what it costs while it
runs: every other writer waits until it has finished (a page that waits longer than twenty
seconds fails), and everything the database holds is written twice before the file shrinks.
The Server Overview page shows the same figure, so nobody has to run this to find out
whether it is worth running.
"""

from __future__ import annotations

from django.core.management.base import BaseCommand, CommandError

from postulo.core import sqlite_upkeep


class Command(BaseCommand):
    help = (
        "Say how much of the SQLite file is space that deleted records left, then give it "
        "back to the disk by rebuilding the file (VACUUM). While it runs every other writer "
        "waits, so choose a quiet moment or stop the services first, and it needs free disk "
        "about the size of the database, beside it and in the temporary directory. "
        "--dry-run only reports. Does nothing on PostgreSQL, which looks after its own space."
    )

    def add_arguments(self, parser) -> None:
        parser.add_argument(
            "--dry-run",
            action="store_true",
            help="Say how much space compacting would give back; change nothing.",
        )

    def handle(self, *args, **options) -> None:
        in_words = sqlite_upkeep.in_words
        if not sqlite_upkeep.is_sqlite():
            self.stdout.write(
                "This instance keeps its data in PostgreSQL, which looks after its own space. "
                "compact_database is for SQLite and does nothing here."
            )
            return
        path = sqlite_upkeep.database_file()
        if path is None:
            self.stdout.write("The database is kept in memory: there is no file to compact.")
            return

        before = sqlite_upkeep.space()
        self.stdout.write(
            f"{path} is {in_words(before.size)}; compacting would give back "
            f"{in_words(before.free)}."
        )
        if options["dry_run"]:
            self.stdout.write("Nothing was changed. Run it without --dry-run to compact.")
            return
        if not before.free:
            self.stdout.write("There is nothing to give back, so nothing was changed.")
            return

        try:
            after = sqlite_upkeep.compact()
        except sqlite_upkeep.CannotCompact as error:
            raise CommandError(str(error)) from error
        self.stdout.write(
            self.style.SUCCESS(f"Compacted: {path.name} is now {in_words(after.size)}.")
        )
