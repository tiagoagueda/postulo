"""Read an exported archive back into an account."""

import zipfile

from django.contrib.auth import get_user_model
from django.core.management.base import BaseCommand, CommandError

from postulo.core.importer import ArchiveError, load


class Command(BaseCommand):
    help = "Import a Postulo export into an account. Creates records; never merges."

    def add_arguments(self, parser) -> None:
        parser.add_argument("email", help="The account to import into.")
        parser.add_argument("archive", help="The zip produced by export_data.")
        parser.add_argument(
            "--force",
            action="store_true",
            help=(
                "Import even though the account already holds a job search. This adds a "
                "second copy of everything rather than merging."
            ),
        )

    def handle(self, *args, **options) -> None:
        try:
            user = get_user_model().objects.get(email=options["email"])
        except get_user_model().DoesNotExist as exc:
            raise CommandError(f"No account with the address {options['email']!r}.") from exc

        try:
            with zipfile.ZipFile(options["archive"]) as archive:
                report = load(user, archive, force=options["force"])
        except (ArchiveError, zipfile.BadZipFile) as exc:
            raise CommandError(str(exc)) from exc

        for line in report.as_lines():
            self.stdout.write(f"  {line}")
        for skipped in report.skipped:
            self.stdout.write(self.style.WARNING(f"  skipped: {skipped}"))
        self.stdout.write(self.style.SUCCESS(f"Imported into {user.email}"))
