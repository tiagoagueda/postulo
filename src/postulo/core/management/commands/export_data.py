"""Write one account's entire job search to a zip."""

from django.contrib.auth import get_user_model
from django.core.management.base import BaseCommand, CommandError

from postulo.core.export import suggested_filename, write_archive


class Command(BaseCommand):
    help = "Export everything belonging to one account as a zip archive."

    def add_arguments(self, parser) -> None:
        parser.add_argument("email", help="The account to export.")
        parser.add_argument(
            "--output",
            help="Where to write it. Defaults to a dated name in the current directory.",
        )

    def handle(self, *args, **options) -> None:
        try:
            user = get_user_model().objects.get(email=options["email"])
        except get_user_model().DoesNotExist as exc:
            raise CommandError(f"No account with the address {options['email']!r}.") from exc

        path = options["output"] or suggested_filename(user)
        with open(path, "wb") as handle:
            write_archive(user, target=handle)

        self.stdout.write(self.style.SUCCESS(f"Wrote {path}"))
