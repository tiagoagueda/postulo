"""``manage.py plugins`` — the same code the Plugins page calls, for the command line.

An operator who keeps their instance in version control wants this rather than a page,
and the container's entry point wants ``sync``: after an upgrade the image is new and the
volume is not, so anything the record lists but the new environment lacks is installed
again before the first request.
"""

from __future__ import annotations

import tempfile
from pathlib import Path

from django.core.management.base import BaseCommand, CommandError

from postulo.plugins import catalogue, installing


class Command(BaseCommand):
    help = (
        "List, install, remove and restore the plugins on this instance. "
        "Plugins live on the data volume, so they survive an upgrade."
    )

    def add_arguments(self, parser) -> None:
        sub = parser.add_subparsers(dest="action", required=True)
        sub.add_parser("list", help="What is installed, and whether it loads.")
        install = sub.add_parser("install", help="Install a wheel, or a name from a catalogue.")
        install.add_argument("what", help="A path to a .whl file, or a plugin's name.")
        install.add_argument(
            "--by", default="command line", help="Who to record as having done it."
        )
        remove = sub.add_parser("remove", help="Remove a plugin and forget it.")
        remove.add_argument("name")
        for action, help_text in (
            ("disable", "Stop it loading."),
            ("enable", "Let it load again."),
        ):
            parser_for = sub.add_parser(action, help=help_text)
            parser_for.add_argument("name")
        sub.add_parser("sync", help="Reinstall what the record lists and the environment lacks.")
        sub.add_parser("catalogue", help="What the configured catalogues offer.")

    def handle(self, *args, **options) -> None:
        action = options["action"]
        getattr(self, f"_{action}")(options)

    # ------------------------------------------------------------------ list

    def _list(self, options) -> None:
        rows = installing.status()
        if not rows:
            self.stdout.write("No plugins are installed on the data volume.")
            self.stdout.write(f"They would live in {installing.plugins_dir()}.")
            return
        for row in rows:
            state = []
            if row["disabled"]:
                state.append("disabled")
            if not row["present"]:
                state.append("not loadable — run `plugins sync`")
            suffix = f"  ({', '.join(state)})" if state else ""
            self.stdout.write(f"{row['name']} {row['version']}  [{row['origin']}]{suffix}")
            for point in row["entry_points"]:
                self.stdout.write(f"    {point}")

    # --------------------------------------------------------------- install

    def _install(self, options) -> None:
        what = options["what"]
        path = Path(what)
        try:
            if path.suffix == ".whl":
                if not path.is_file():
                    raise CommandError(f"No such file: {path}")
                entry = installing.install_wheel(path, by=options["by"])
            else:
                entry = catalogue.install(what, by=options["by"])
        except (installing.InstallError, catalogue.CatalogueError) as error:
            raise CommandError(str(error)) from error
        self.stdout.write(f"Installed {entry.name} {entry.version} from {entry.origin}.")
        for point in entry.entry_points:
            self.stdout.write(f"    {point}")
        self.stdout.write("Restart Postulo if the plugin adds pages of its own.")

    # ---------------------------------------------------------------- remove

    def _remove(self, options) -> None:
        try:
            entry = installing.remove(options["name"])
        except installing.InstallError as error:
            raise CommandError(str(error)) from error
        self.stdout.write(f"Removed {entry.name} {entry.version}.")

    # ------------------------------------------------------- disable, enable

    def _disable(self, options) -> None:
        self._switch(options["name"], True)

    def _enable(self, options) -> None:
        self._switch(options["name"], False)

    def _switch(self, name: str, disabled: bool) -> None:
        try:
            entry = installing.set_disabled(name, disabled)
        except installing.InstallError as error:
            raise CommandError(str(error)) from error
        self.stdout.write(f"{entry.name} is now {'disabled' if disabled else 'enabled'}.")

    # ------------------------------------------------------------------ sync

    def _sync(self, options) -> None:
        def fetch(entry):
            if not entry.is_from_catalogue or not entry.source.startswith("http"):
                return None
            catalogues, _problems = catalogue.fetch_all()
            _listing, release = catalogue.find(catalogues, entry.name)
            scratch = Path(tempfile.mkdtemp(prefix="postulo-plugin-"))
            return catalogue.download(release, scratch)

        restored, lost = installing.sync(fetch=fetch)
        for name in restored:
            self.stdout.write(f"Reinstalled {name}.")
        for name in lost:
            self.stderr.write(f"{name} is recorded but could not be reinstalled; install it again.")
        if not restored and not lost:
            self.stdout.write("Everything the record lists is present.")

    # ------------------------------------------------------------- catalogue

    def _catalogue(self, options) -> None:
        catalogues, problems = catalogue.fetch_all()
        for problem in problems:
            self.stderr.write(problem)
        if not catalogues:
            self.stdout.write(
                "No catalogue is configured. Set POSTULO_PLUGIN_CATALOGUES to "
                "name|url|public-key entries, separated by commas."
            )
            return
        for one in catalogues:
            self.stdout.write(f"{one.name} ({one.url})")
            for listing in one.listings:
                release = listing.latest
                version = release.version if release else "—"
                self.stdout.write(f"    {listing.name} {version}  {listing.summary}")
