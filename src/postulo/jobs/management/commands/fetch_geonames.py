"""Download the GeoNames city dataset in place, where the geocoder reads it (#108).

``postulo/jobs/places.py`` reads ``data/geonames-cities1000.txt`` and
``data/geonames-countryinfo.txt``; this command is how the files get there. It asks
download.geonames.org for the table of the cities above a thousand inhabitants and the
table of the countries, and writes them beside the code that reads them. The download
is one request per table, at provisioning, for data that is a few megabytes — a
repository is not where reference data is kept, and neither is a request on the path
of a save.

Without the files Postulo runs on: a location is simply not placed, and the map page
says the dataset has not been downloaded. What is written is checked before anything
is replaced — the loader itself is asked about the cities it needs to be true — and
the command says what it found. ``data/GEONAMES-LICENCE.md``, committed, says on whose
terms the table is carried.
"""

from __future__ import annotations

import io
import os
import pathlib
import tempfile
import zipfile

import httpx
from django.core.management.base import BaseCommand, CommandError

from postulo.jobs import places

CITIES_URL = "https://download.geonames.org/export/dump/cities1000.zip"
COUNTRIES_URL = "https://download.geonames.org/export/dump/countryInfo.txt"

#: A minute and a half for a file of a few megabytes; more than that is a connection
#: in trouble, not a dataset.
TIMEOUT = 90.0

#: The cities1000 table is twenty thousand rows; an answer with a handful is not the
#: table, and the download is a refusal in a different coat.
MINIMUM_CITIES = 10000

#: The country table is one row a country; there are more than two hundred.
MINIMUM_COUNTRIES = 200

#: The cities the loader would be useless without, in the languages the table itself
#: answers to. If any of them is absent, the file is not the table.
REQUIRED_CITIES = ("Berlin", "Lisbon", "Tokyo", "Nairobi", "São Paulo")


class Command(BaseCommand):
    help = "Download the GeoNames city dataset in place, where postulo.jobs.places reads it."

    def handle(self, *args, **options) -> None:
        self.stdout.write("Downloading the GeoNames city dataset from download.geonames.org ...")
        with httpx.Client(
            timeout=httpx.Timeout(TIMEOUT, connect=10.0),
            follow_redirects=True,
            headers={
                "User-Agent": "postulo fetch_geonames (a self-hosted Postulo provisioning data)",
            },
        ) as client:
            cities = self._fetch(client, CITIES_URL, "the city table")
            countries = self._fetch(client, COUNTRIES_URL, "the country table")
        self._validate(cities, countries)
        self._write(places.DATA_DIR / places.CITIES_FILE, cities)
        self._write(places.DATA_DIR / places.COUNTRIES_FILE, countries)
        # The loader caches what it has read, and this process may have read the
        # absence; the new file is the state now.
        places._index.cache_clear()
        places._countries.cache_clear()
        self.stdout.write(
            self.style.SUCCESS(
                "Wrote the city table and the country table. A location is placed from "
                "them when it is saved."
            )
        )

    def _fetch(self, client, url: str, what: str) -> str:
        response = client.get(url)
        if response.status_code != 200:
            raise CommandError(f"the download of {what} answered {response.status_code}: {url}")
        if url.endswith(".zip"):
            try:
                with zipfile.ZipFile(io.BytesIO(response.content)) as archive:
                    if len(archive.namelist()) != 1:
                        raise ValueError
                    return archive.read(archive.namelist()[0]).decode("utf-8")
            except (zipfile.BadZipFile, ValueError, UnicodeDecodeError):
                raise CommandError(
                    f"the download of {what} was not the zip of one tab-separated table: {url}"
                ) from None
        return response.text

    def _validate(self, cities: str, countries: str) -> None:
        """What the loader will read, checked before anything is replaced."""
        problems = []
        city_rows = [line for line in cities.splitlines() if len(line.split("\t")) >= 15]
        if len(city_rows) < MINIMUM_CITIES:
            problems.append(
                f"the city table has {len(city_rows)} rows; the download is not "
                "the table of the cities above a thousand inhabitants"
            )
        country_rows = [line for line in countries.splitlines() if line.split("\t")[0]]
        if len(country_rows) < MINIMUM_COUNTRIES:
            problems.append(f"the country table has {len(country_rows)} rows")
        if not problems:
            problems.extend(self._loader_problems(cities, countries))
        if problems:
            raise CommandError(
                "the download is not the shape the loader reads:\n" + "\n".join(problems)
            )

    def _loader_problems(self, cities: str, countries: str) -> list[str]:
        """The loader itself, asked about the cities it would be useless without.

        It is asked against the tables in a directory of its own, so the check is
        exactly what a save would read — and the machine's own data is left alone.
        """
        problems = []
        with tempfile.TemporaryDirectory() as staging:
            (pathlib.Path(staging) / places.CITIES_FILE).write_text(cities, encoding="utf-8")
            (pathlib.Path(staging) / places.COUNTRIES_FILE).write_text(countries, encoding="utf-8")
            original = places.DATA_DIR
            try:
                places.DATA_DIR = pathlib.Path(staging)
                places._index.cache_clear()
                places._countries.cache_clear()
                for city in REQUIRED_CITIES:
                    if places.resolve(city) is None:
                        problems.append(f"{city} is not in the table the download answered with")
            finally:
                places.DATA_DIR = original
                places._index.cache_clear()
                places._countries.cache_clear()
        return problems

    def _write(self, target, text: str) -> None:
        temporary = target.with_name(target.name + ".tmp")
        temporary.write_text(text, encoding="utf-8")
        os.replace(temporary, target)
