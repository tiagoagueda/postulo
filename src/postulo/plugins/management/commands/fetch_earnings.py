"""Refresh Eurostat's earnings statistics, where the earnings plugin reads them (#1257).

A copy ships with Postulo (``plugins/earnings/data/eurostat-earnings.json``), so nothing has
to run for the plugin to work. This is how it is brought up to date: seven GET requests to
Eurostat's dissemination API (JSON-stat 2.0, no key, one host), the slices
`postulo.plugins.earnings.eurostat` names, reduced to the file the reader reads, checked
against the shape it reads before anything is replaced, and written whole or not at all into
`POSTULO_EARNINGS_DIR`. The reader then prefers it over the shipped copy because it was
retrieved later.

The minimum wage changes twice a year; the survey every four years, with tables whose codes
carry the year (``earn_ses22_28``), so a new wave is ``--wave 2026`` once Eurostat publishes
it. Nothing in a request path calls this.

**No library.** The Eurostat and SDMX clients on PyPI (`eurostat`, `sdmx1`, `pyjstat`,
`pandaSDMX`) each bring pandas and their own HTTP client, where every request Postulo makes
goes through `public_only_client`; a JSON-stat answer is a row-major array read in fifteen
lines (`eurostat.cells`).
"""

from __future__ import annotations

import datetime as dt
import json
import os

import httpx
from django.core.management.base import BaseCommand, CommandError

from postulo.plugins.earnings import eurostat, reader
from postulo.plugins.http import public_only_client

#: How long one table may take. They are tens of kilobytes; the service is sometimes slow.
TIMEOUT = 90.0


def text_of(document: dict) -> str:
    """The file as written: compact, a country to a line, so a refresh reads as a diff."""

    def compact(value) -> str:
        return json.dumps(value, ensure_ascii=False, separators=(",", ":"))

    head = {key: value for key, value in document.items() if key != "countries"}
    lines = ["{"]
    lines += [f"{compact(key)}:{compact(value)}," for key, value in head.items()]
    lines.append('"countries":{')
    rows = [f"{compact(code)}:{compact(entry)}" for code, entry in document["countries"].items()]
    lines.append(",\n".join(rows))
    lines += ["}", "}"]
    return "\n".join(lines) + "\n"


class Command(BaseCommand):
    help = "Refresh Eurostat's earnings statistics, where the earnings plugin reads them."

    def add_arguments(self, parser):
        parser.add_argument(
            "--wave",
            type=int,
            default=eurostat.WAVE,
            help=f"the Structure of Earnings Survey's year (default {eurostat.WAVE}); the "
            "detailed tables' codes carry it",
        )

    def handle(self, *args, **options):
        wave = options["wave"]
        asked = eurostat.queries(wave)
        self.stdout.write(f"Asking {eurostat.API} for {len(asked)} tables ...")
        answers = {}
        with public_only_client(
            timeout=httpx.Timeout(TIMEOUT, connect=10.0),
            headers={
                "User-Agent": "postulo fetch_earnings (a self-hosted Postulo refreshing data)",
                "Accept": "application/json",
            },
        ) as client:
            for code, query in asked.items():
                try:
                    response = client.get(f"{eurostat.API}{code}?{query}")
                    response.raise_for_status()
                    answers[code] = response.json()
                except (httpx.HTTPError, ValueError) as error:
                    raise CommandError(f"{code}: {error}") from error
        try:
            document = eurostat.build(answers, wave, dt.date.today())
        except eurostat.Refused as error:
            raise CommandError(f"Eurostat's answer was not used: {error}") from error

        directory = reader.data_dir()
        directory.mkdir(parents=True, exist_ok=True)
        target = directory / reader.FILE
        temporary = target.with_name(target.name + ".tmp")
        temporary.write_text(text_of(document), encoding="utf-8")
        os.replace(temporary, target)
        reader.forget()
        self.stdout.write(
            f"Wrote {target}: {len(document['countries'])} countries, wave {wave}, minimum "
            f"wage {document['minimum_when'] or 'none'}, {target.stat().st_size / 1000:.0f} kB."
        )
