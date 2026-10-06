"""Read every kept file and say which are missing, which changed, and which nothing names (#663).

Reports and records; deletes nothing. A damaged file may be the only copy of somebody's
diploma, so the row is marked and the person replaces it. The files no record names are
listed for `prune_media` to remove.
"""

from __future__ import annotations

from django.core.management.base import BaseCommand

from postulo.documents import scrub


class Command(BaseCommand):
    help = "Check each uploaded file and snapshot against its record. Deletes nothing."

    def handle(self, *args, **options) -> None:
        findings = scrub.verify()
        for name in findings.missing:
            self.stdout.write(f"missing  {name}")
        for name in findings.changed:
            self.stdout.write(f"changed  {name}")
        for name in findings.stray:
            self.stdout.write(f"stray    {name}")
        self.stdout.write(
            f"{findings.checked} file(s) read: {len(findings.missing)} missing, "
            f"{len(findings.changed)} changed; {len(findings.stray)} with no record. "
            "Nothing was deleted."
        )
