"""Take a backup of the whole instance: database and media, in one archive."""

from django.core.management.base import BaseCommand, CommandError

from postulo.core.backup import BackupError, write_backup


class Command(BaseCommand):
    help = (
        "Write one archive holding the database and the media directory, taken consistently "
        "while Postulo runs, and verify it. With no target, it goes to POSTULO_BACKUP_DIR."
    )

    def add_arguments(self, parser) -> None:
        parser.add_argument(
            "target",
            nargs="?",
            help="A file to write, or a directory to write a timestamped file into.",
        )
        parser.add_argument(
            "--no-media",
            action="store_true",
            help="Leave the media directory out. The database alone cannot be restored usefully.",
        )

    def handle(self, *args, **options) -> None:
        try:
            report = write_backup(options["target"], include_media=not options["no_media"])
        except BackupError as error:
            raise CommandError(str(error)) from error

        size_mb = report.bytes / (1024 * 1024)
        self.stdout.write(self.style.SUCCESS(f"Backed up to {report.path} ({size_mb:.1f} MB)"))
        for name, value in report.counts.items():
            self.stdout.write(f"  {value} {name}")
        if options["no_media"]:
            self.stdout.write(self.style.WARNING("  media left out, as asked"))
        else:
            self.stdout.write(f"  {report.media_files} media files")
        self.stdout.write(
            "  verified: the manifest reads back and the database matches its checksum"
        )
