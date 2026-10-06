"""Find saved captures whose listing is gone, and optionally remove them (#664).

A saved capture and what it kept of its page go when the listing they became goes. Before
that was so, deleting a listing left its captures behind, saved and pointing nowhere, which
nothing expires and nothing in the interface lists; their pages still count against the
account's room. This names them and, when told to, deletes them, pages included.

It lists by default and removes only when told to, as `prune_media` does: these rows were
made under a different rule, and the files are somebody's.
"""

from __future__ import annotations

from django.core.management.base import BaseCommand
from django.template.defaultfilters import filesizeformat

from postulo.jobs.models import Capture, CaptureStatus


def stranded():
    """Saved captures with no listing and no application to be the data of."""
    return (
        Capture.objects.filter(
            status=CaptureStatus.ACCEPTED, posting__isnull=True, application__isnull=True
        )
        .select_related("page", "owner")
        .order_by("owner_id", "pk")
    )


class Command(BaseCommand):
    help = "List saved captures that no listing or application has; --remove deletes them."

    def add_arguments(self, parser) -> None:
        parser.add_argument(
            "--remove",
            action="store_true",
            help="Delete the captures and their kept pages as well as listing them.",
        )

    def handle(self, *args, **options) -> None:
        rows = list(stranded())
        weight = 0
        for capture in rows:
            page = capture.kept_page
            size = (page.source_size + page.rendering_size) if page else 0
            weight += size
            self.stdout.write(
                f"stranded  capture {capture.pk}  owner {capture.owner_id}  "
                f"{'page ' + filesizeformat(size) if page else 'no page'}  {capture.url}"
            )
        self.stdout.write(f"{len(rows)} stranded capture(s), {filesizeformat(weight)} of pages.")
        if not options["remove"]:
            if rows:
                self.stdout.write("Nothing was deleted. Pass --remove to delete them.")
            return
        # One at a time, so every row sends the deletion that takes its files.
        for capture in rows:
            capture.delete()
        self.stdout.write(self.style.SUCCESS(f"Removed {len(rows)} capture(s)."))
