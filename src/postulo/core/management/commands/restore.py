"""Put a backup back: database, then media, then migrations."""

from django.core.management.base import BaseCommand, CommandError

from postulo.core.backup import BackupError, restore_backup


class Command(BaseCommand):
    help = (
        "Restore an archive written by `backup` onto this instance. Refuses an instance that "
        "already has accounts unless --force is given, and then replaces everything."
    )

    def add_arguments(self, parser) -> None:
        parser.add_argument("archive", help="The .tar.gz written by `manage.py backup`.")
        parser.add_argument(
            "--force",
            action="store_true",
            help="Restore onto an instance that is not empty, replacing what is there.",
        )

    def handle(self, *args, **options) -> None:
        try:
            report = restore_backup(options["archive"], force=options["force"])
        except BackupError as error:
            raise CommandError(str(error)) from error

        self.stdout.write(self.style.SUCCESS(f"Restored {options['archive']}"))
        for name, value in report.counts.items():
            self.stdout.write(f"  {value} {name}")
        self.stdout.write(f"  {report.media_files} media files written")
        if report.media_skipped:
            self.stdout.write(
                self.style.WARNING(
                    f"  {report.media_skipped} media files already existed and were kept; "
                    "pass --force to replace them"
                )
            )
