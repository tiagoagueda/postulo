"""Put a backup back: database, then media and plugins, then migrations."""

from django.core.management.base import BaseCommand, CommandError

from postulo.core.backup import BackupError, restore_backup
from postulo.plugins import secrets


class Command(BaseCommand):
    help = (
        "Restore an archive written by `backup` onto this instance. Nothing else may be "
        "using the database while it runs — in the container, `docker compose --profile "
        "scheduler --profile worker stop` and then `docker compose run --rm "
        "-e POSTULO_SKIP_MIGRATE=1 postulo python manage.py restore "
        "/app/data/backups/....tar.gz`, and `up -d` with the same profiles afterwards. "
        "Refuses an instance that "
        "already has accounts unless --force is given, and then replaces everything."
    )

    def add_arguments(self, parser) -> None:
        parser.add_argument("archive", help="The .tar.gz written by `manage.py backup`.")
        parser.add_argument(
            "--force",
            action="store_true",
            help=(
                "Restore onto an instance that is not empty, replacing what is there, and "
                "go ahead even though something else looks to be using the database."
            ),
        )

        parser.add_argument(
            "--allow-newer",
            action="store_true",
            help=(
                "Restore an archive written by a newer Postulo than this one. The database "
                "then has a schema this code does not know; only for archives whose schema "
                "is known to match."
            ),
        )

    def handle(self, *args, **options) -> None:
        try:
            report = restore_backup(
                options["archive"], force=options["force"], allow_newer=options["allow_newer"]
            )
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
        if report.plugins:
            self.stdout.write(
                f"  {report.plugin_files} plugin files written: {', '.join(report.plugins)}"
            )
            self.stdout.write("  restart Postulo for the restored plugins to be loaded")
        if report.plugins_skipped:
            self.stdout.write(
                self.style.WARNING(
                    f"  {report.plugins_skipped} plugin files already existed and were kept; "
                    "pass --force to replace them"
                )
            )

        # The one thing a restore cannot bring with it, and the one thing whose absence is
        # invisible until a connection fails weeks later.
        unreadable = []
        if report.connections_with_secrets:
            unreadable.append(f"{report.connections_with_secrets} connections")
        if report.site_secrets:
            unreadable.append(
                f"{report.site_secrets} of the mail and text settings on Server settings "
                "(the SMTP password, the mail consent, the transports' secrets)"
            )
        if report.key_matches is False and unreadable:
            self.stdout.write(
                self.style.ERROR(
                    f"  {' and '.join(unreadable)} hold encrypted secrets that CANNOT be "
                    "read here: this instance's key is not the one the backup was taken "
                    "with. Set POSTULO_FIELD_KEY to that key — this instance derives its own "
                    f"from {secrets.key_source()} — or enter each password or token again: "
                    "on each connection, and on Server settings → Email for the SMTP "
                    "password or the mail consent."
                )
            )
        elif report.key_matches is False:
            self.stdout.write(
                self.style.WARNING(
                    "  this instance's encryption key is not the one the backup was taken "
                    "with; no connection and no server setting holds a secret, so nothing "
                    "is lost by it"
                )
            )
        # Only where it can be true: a restore writes what the archive holds and never
        # deletes, so an instance that had files of its own still has them.
        if options["force"] or report.media_skipped:
            self.stdout.write(
                "  media this archive did not carry is still on this instance; "
                "`manage.py prune_media` lists what no record points at"
            )
