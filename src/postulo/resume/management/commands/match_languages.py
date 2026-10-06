"""Say which of the spoken languages in the career records have a code, and which do not (#689).

The migration that added the code placed it wherever a typed name was exactly one language.
This reports what that left, and what a name added since would match now; ``--apply`` sets
the code on those. It never rewrites a name, and a name that is ambiguous (*Norwegian*,
*Chinese*, *Portuguese*) or unknown stays text, listed here so that somebody may choose.
"""

from __future__ import annotations

from collections import Counter

from django.core.management.base import BaseCommand

from postulo.core import language_names
from postulo.resume.models import LanguageSkill


class Command(BaseCommand):
    help = "Report which spoken languages have a code, and set it where a name is one language."

    def add_arguments(self, parser):
        parser.add_argument(
            "--apply",
            action="store_true",
            help="set the code on the entries whose name is exactly one language",
        )

    def handle(self, *args, **options):
        coded = LanguageSkill.objects.exclude(code="").count()
        placed: Counter[tuple[str, str]] = Counter()
        left: Counter[str] = Counter()
        for entry in LanguageSkill.objects.filter(code="").iterator():
            code = language_names.match(entry.name)
            if code:
                placed[(entry.name, code)] += 1
                if options["apply"]:
                    LanguageSkill.objects.filter(pk=entry.pk).update(code=code)
            else:
                left[entry.name] += 1

        self.stdout.write(f"{coded} entries already have a code.")
        verb = "Set" if options["apply"] else "Would set"
        for (name, code), count in sorted(placed.items()):
            self.stdout.write(f"{verb} {code} on {count} entry named {name!r}.")
        for name, count in sorted(left.items()):
            self.stdout.write(f"Left as text: {name!r} ({count}).")
