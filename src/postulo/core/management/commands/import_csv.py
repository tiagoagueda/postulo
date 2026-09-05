"""Import a spreadsheet from the command line, for the person with a long history.

The same reading, mapping and importing as the web page. Without ``--mapping`` the
columns are guessed from the headers, as on the page; ``--show`` prints the guess so it
can be corrected in a JSON file and passed back.
"""

from __future__ import annotations

import json
from pathlib import Path

from django.contrib.auth import get_user_model
from django.core.management.base import BaseCommand, CommandError

from postulo.core import csv_import


class Command(BaseCommand):
    help = "Import applications and listings from a CSV into an account."

    def add_arguments(self, parser) -> None:
        parser.add_argument("account", help="The username or email address of the account.")
        parser.add_argument("file", help="The CSV file.")
        parser.add_argument(
            "--mapping",
            help='A JSON file mapping header names to fields, e.g. {"Entreprise": "company"}.',
        )
        parser.add_argument(
            "--month-first", action="store_true", help="Read 12/31/2026 rather than 31/12/2026."
        )
        parser.add_argument(
            "--show", action="store_true", help="Print the guessed mapping and stop."
        )
        parser.add_argument(
            "--dry-run", action="store_true", help="Parse and report; import nothing."
        )

    def handle(self, *args, **options) -> None:
        User = get_user_model()
        handle = options["account"].strip()
        user = (
            User.objects.filter(username__iexact=handle).first()
            or User.objects.filter(email__iexact=handle).first()
        )
        if user is None:
            raise CommandError(f"No account called {handle!r}.")

        path = Path(options["file"])
        try:
            sheet = csv_import.read_sheet(path.read_bytes(), path.name)
        except (OSError, csv_import.SheetError) as error:
            raise CommandError(str(error)) from error

        mapping = csv_import.guess_mapping(sheet.headers)
        if options["mapping"]:
            wanted = json.loads(Path(options["mapping"]).read_text(encoding="utf-8"))
            mapping = csv_import.clean_mapping(
                [wanted.get(header, "ignore") for header in sheet.headers], sheet.headers
            )
        if options["show"]:
            self.stdout.write(json.dumps(dict(zip(sheet.headers, mapping, strict=True)), indent=2))
            return
        if "company" not in mapping or "role" not in mapping:
            raise CommandError("Map a column to 'company' and one to 'role' (see --show).")

        day_first = not options["month_first"]
        if options["dry_run"]:
            rows = csv_import.parse_rows(sheet, mapping, day_first=day_first)
            for row in rows[:20]:
                self.stdout.write(
                    f"  row {row.number}: {row.company} / {row.role} -> {row.becomes}"
                    + (f" ({', '.join(row.problems)})" if row.problems else "")
                )
            self.stdout.write(f"{len(rows)} rows parsed; nothing imported.")
            return

        report = csv_import.perform(user, sheet, mapping, day_first=day_first)
        for line in report.as_lines():
            self.stdout.write(line)
        for skipped in report.skipped:
            self.stdout.write(self.style.WARNING(f"  {skipped}"))
