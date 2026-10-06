"""Find files under the media root that no row points at, and optionally remove them (#217).

Deleting a document removes its file from now on, but every document deleted before that
left its bytes behind, and a restore from an older archive can bring more. This is how an
operator finds them and decides.

It lists by default and removes only when told to, because the failure mode of the opposite
default is somebody's CV.
"""

from __future__ import annotations

from django.core.management.base import BaseCommand

from postulo.core import media
from postulo.documents import filestore


class Command(BaseCommand):
    help = "List files under the media root that no record points at; --remove deletes them."

    def add_arguments(self, parser) -> None:
        parser.add_argument(
            "--remove",
            action="store_true",
            help="Delete the files as well as listing them. Without it, nothing is touched.",
        )

    def handle(self, *args, **options) -> None:
        orphans = list(media.unreferenced())
        kept = filestore.totals()[0] - len(orphans)

        total = sum(stored.size for stored in orphans)
        for stored in orphans:
            self.stdout.write(f"orphan  {stored.name}")
        self.stdout.write(
            f"{kept} file(s) in use, {len(orphans)} with no record ({total / 1024:.0f} KB)."
        )
        if not options["remove"]:
            if orphans:
                self.stdout.write("Nothing was deleted. Pass --remove to delete them.")
            return

        for stored in orphans:
            filestore.delete(stored.name)
        self.stdout.write(self.style.SUCCESS(f"Removed {len(orphans)} file(s)."))
