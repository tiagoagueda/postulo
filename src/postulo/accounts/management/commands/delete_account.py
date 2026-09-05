"""Delete an account from the command line, for the operator who is asked to.

Everything the web page does, with the same service: rows, files, invitations, and the
rule that the last administrator stays. Asks before it acts unless told not to.
"""

from __future__ import annotations

from django.contrib.auth import get_user_model
from django.core.management.base import BaseCommand, CommandError

from postulo.accounts.deletion import LastAdministrator, delete_account


class Command(BaseCommand):
    help = "Delete an account and everything it owns, including its files on disk."

    def add_arguments(self, parser) -> None:
        parser.add_argument("account", help="The username or email address of the account.")
        parser.add_argument("--yes", action="store_true", help="Do not ask for confirmation.")

    def handle(self, *args, **options) -> None:
        User = get_user_model()
        handle = options["account"].strip()
        user = (
            User.objects.filter(username__iexact=handle).first()
            or User.objects.filter(email__iexact=handle).first()
        )
        if user is None:
            raise CommandError(f"No account called {handle!r}.")

        if not options["yes"]:
            answer = input(
                f"Delete {user.username} <{user.email}> and everything it owns? "
                "This cannot be undone. [y/N] "
            )
            if answer.strip().lower() not in ("y", "yes"):
                self.stdout.write("Nothing deleted.")
                return

        try:
            report = delete_account(user)
        except LastAdministrator as error:
            raise CommandError(str(error)) from error
        for line in report.as_lines():
            self.stdout.write(line)
