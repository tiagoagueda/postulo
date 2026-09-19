"""Ask the configured source whether a newer Postulo exists, and say (#272)."""

from django.core.management.base import BaseCommand

from postulo.core import updates


class Command(BaseCommand):
    help = "Ask the configured release source whether a newer Postulo exists."

    def handle(self, *args, **options) -> None:
        if not updates.enabled():
            self.stdout.write(
                "The update check is off. Set POSTULO_UPDATE_CHECK=true to let this instance "
                "ask its configured source, and nothing else, once a day."
            )
            return
        answer = updates.check()
        if answer["error"]:
            self.stdout.write(f"Could not check: {answer['error']}")
        elif answer["behind"]:
            self.stdout.write(
                f"Postulo {answer['latest']} is out; this instance runs {answer['current']}. "
                f"{answer['url']}"
            )
        else:
            self.stdout.write(f"This instance runs {answer['current']}, the newest release.")
