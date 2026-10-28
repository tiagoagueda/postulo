"""Ask this machine, before anything is sent, which scripts it can actually draw (#74).

The declaration test reads the Dockerfile; this reads the fonts the running instance
has. The answer is the same one a render would have asked: for each script the offered
languages need, the font the renderer resolves for that language is asked for the
character that would be in the document, and the question is about the character, not
the package.

A language set in a style of its script -- Urdu in Nastaliq (#1054) -- is asked about
under the style's own code, after the scripts. A style that is missing is said, in red,
and does not fail the command: the script is still drawn, legibly, in its ordinary style,
and a deployment check that passed yesterday should not stop on a typeface.

Exit codes: ``0`` when every offered script draws or the question cannot be asked on
this machine at all (and the reason is said), ``1`` when an offered script would come
out boxes.
"""

from __future__ import annotations

from django.core.management.base import BaseCommand, CommandError

from postulo.core import languages
from postulo.documents import fonts, theme_assets


class Command(BaseCommand):
    help = (
        "Say which of the scripts Postulo offers this instance can draw, and exit "
        "non-zero when one of them would come out boxes."
    )

    def handle(self, *args, **options) -> None:
        self._theme_fonts()
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

        missing = [languages.script_name(script) for script in offered if not answer.get(script)]
        for script in offered:
            name = languages.script_name(script)
            if answer.get(script):
                self.stdout.write(self.style.SUCCESS(f"draws      {name}"))
            else:
                self.stdout.write(self.style.ERROR(f"cannot draw  {name}"))
        for style in sorted(languages.styles_offered()):
            name = languages.script_name(style)
            if answer.get(style):
                self.stdout.write(self.style.SUCCESS(f"draws      {name}"))
            else:
                self.stdout.write(
                    self.style.ERROR(
                        f"cannot draw  {name}: its documents come out in the script's "
                        "ordinary style. On Debian or Ubuntu: apt install fonts-noto-core."
                    )
                )
        if missing:
            raise CommandError(
                f"{' and '.join(missing)} would come out as boxes. On Debian or "
                "Ubuntu: apt install fonts-noto-core, and fonts-noto-cjk for CJK, "
                "then re-run this."
            )
        if all(answer.get(style) for style in languages.styles_offered()):
            self.stdout.write(
                self.style.SUCCESS("Every script the offered languages need, this machine draws.")
            )
        else:
            self.stdout.write(
                self.style.WARNING(
                    "Every script the offered languages need, this machine draws, though not "
                    "in every style they are written in."
                )
            )

    def _theme_fonts(self) -> None:
        """What each installed theme's own fonts draw of the scripts they declare (#343).

        Asked of the font file's own character map, the same character the server's fonts
        are asked for. Said, and never a reason to exit non-zero: a document in a script a
        theme's font does not draw is set in the server's fonts instead, which is what the
        rest of this command checks.
        """
        for check in theme_assets.theme_fonts():
            where = f"{check.name}, theme {check.theme}"
            if check.problem:
                self.stdout.write(self.style.WARNING(f"cannot read  {where}: {check.problem}"))
                continue
            for script, drawn in check.scripts:
                name = languages.script_name(script)
                if drawn:
                    self.stdout.write(self.style.SUCCESS(f"draws      {name} in {where}"))
                else:
                    self.stdout.write(
                        self.style.WARNING(
                            f"declared, not drawn  {name} in {where}: such a document "
                            "is set in the server's fonts"
                        )
                    )
