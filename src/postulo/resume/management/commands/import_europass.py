"""Import a Europass career record from the command line.

The same reader the page uses, for an operator with a shell and a file. Either format —
the XML or the JSON — and the file itself decides which. It says what it found before it
writes anything, and ``--dry-run`` stops there.
"""

from __future__ import annotations

from pathlib import Path

from django.contrib.auth import get_user_model
from django.core.management.base import BaseCommand, CommandError
from django.db import transaction

from postulo.resume import europass


class Command(BaseCommand):
    help = "Import a career record from a Europass XML file."

    def add_arguments(self, parser):
        parser.add_argument("path", help="the Europass file to read, XML or JSON")
        parser.add_argument(
            "--user",
            required=True,
            help="the username or email address whose career record this is",
        )
        parser.add_argument(
            "--dry-run",
            action="store_true",
            help="say what would be added, and write nothing",
        )

    def handle(self, *args, **options):
        path = Path(options["path"])
        if not path.is_file():
            raise CommandError(f"No file at {path}")

        User = get_user_model()
        wanted = options["user"]
        owner = (
            User.objects.filter(username=wanted).first()
            or User.objects.filter(email__iexact=wanted).first()
        )
        if owner is None:
            raise CommandError(f"No account called {wanted!r}")

        try:
            record = europass.read(path.read_bytes())
        except europass.EuropassError as error:
            raise CommandError(str(error)) from error

        counts = record.counts()
        self.stdout.write(f"Read {path.name} as Europass {record.source.upper()}:")
        for kind, total in counts.items():
            self.stdout.write(f"  {kind}: {total}")
        if record.person:
            self.stdout.write(f"  personal details: {', '.join(sorted(record.person))}")
        for note in record.skipped:
            self.stdout.write(self.style.WARNING(f"  {note}"))

        if record.is_empty:
            self.stdout.write(self.style.WARNING("Nothing to import."))
            return

        if options["dry_run"]:
            self.stdout.write(self.style.WARNING("Dry run: nothing was written."))
            return

        with transaction.atomic():
            report = europass.apply(owner, record)

        self.stdout.write(self.style.SUCCESS(f"Added {report.total} entries for {owner}."))
        for kind, total in sorted(report.added.items()):
            self.stdout.write(f"  {kind}: {total}")
        if report.profile_filled:
            self.stdout.write(f"  filled blank profile fields: {', '.join(report.profile_filled)}")
        for note in report.skipped:
            self.stdout.write(self.style.WARNING(f"  {note}"))
        self.stdout.write("Nothing was overwritten. Anything duplicated is yours to delete.")
