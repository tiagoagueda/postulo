"""Ask this machine, before anything is sent, which scripts it can actually draw (#74).

The declaration test reads the Dockerfile; this reads the fonts the running instance
has. The answer is the same one a render would have asked: for each script the offered
languages need, the font the renderer resolves for that language is asked for the
character that would be in the document, and the question is about the character, not
the package.

Exit codes: ``0`` when every offered script draws or the question cannot be asked on
this machine at all (and the reason is said), ``1`` when an offered script would come
out boxes.
"""

from __future__ import annotations

from django.core.management.base import BaseCommand, CommandError

from postulo.core import languages
from postulo.documents import fonts


class Command(BaseCommand):
    help = (
        "Say which of the scripts Postulo offers this instance can draw, and exit "
        "non-zero when one of them would come out boxes."
    )

    def handle(self, *args, **options) -> None:
        offered = sorted(languages.scripts_offered())
        answer = fonts.renderable_scripts()
        if answer is None:
            self.stdout.write(
                "WeasyPrint's system libraries are not on this machine, so the "
                "question cannot be asked here. Documents do not render on a machine "
                "like this; where they do render, this command asks the fonts that "
                "draw them."
            )
            return

        missing = [script for script, drawable in answer.items() if not drawable]
        for script in offered:
            if answer.get(script):
                self.stdout.write(self.style.SUCCESS(f"draws      {script}"))
            else:
                self.stdout.write(self.style.ERROR(f"cannot draw  {script}"))
        if missing:
            raise CommandError(
                f"{' and '.join(missing)} would come out as boxes. On Debian or "
                "Ubuntu: apt install fonts-noto-core, and fonts-noto-cjk for CJK, "
                "then re-run this."
            )
        self.stdout.write(
            self.style.SUCCESS("Every script the offered languages need, this machine draws.")
        )
