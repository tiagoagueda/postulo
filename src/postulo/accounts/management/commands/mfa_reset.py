"""Remove a person's second factor, for the day the phone is gone and so are the codes.

A self-hosted instance has no support desk. The recovery codes are the first way back;
this command is the second, run by whoever has a shell on the server — which, for the
administrator who locked themselves out, is themselves.
"""

from allauth.mfa.models import Authenticator
from django.contrib.auth import get_user_model
from django.core.management.base import BaseCommand, CommandError


class Command(BaseCommand):
    help = "Remove every second factor from an account, so a password alone signs it in."

    def add_arguments(self, parser) -> None:
        parser.add_argument("username", help="The account to reset.")

    def handle(self, *args, **options) -> None:
        User = get_user_model()
        user = User.objects.filter(username=options["username"].strip().casefold()).first()
        if user is None:
            raise CommandError(f"No account with the username {options['username']!r}.")
        removed, _by_type = Authenticator.objects.filter(user=user).delete()
        if not removed:
            self.stdout.write(f"{user.username} had no second factor set up; nothing to remove.")
            return
        self.stdout.write(
            self.style.SUCCESS(
                f"Removed the second factor from {user.username}: a password alone signs in now. "
                "Suggest setting it up again under Settings → Account."
            )
        )
