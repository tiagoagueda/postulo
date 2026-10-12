"""Download the GeoNames city dataset in place, where the geocoder reads it (#108).

``postulo/jobs/places.py`` reads ``data/geonames-cities1000.txt`` and
``data/geonames-countryinfo.txt``; this command is how the files get there. It asks
download.geonames.org for the table of the cities above a thousand inhabitants and the
table of the countries, and writes them beside the code that reads them. The download
is one request per table, at provisioning, for data that is a few dozen megabytes — a
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
from postulo.plugins.http import BodyRefused, get_bytes, public_only_client

CITIES_URL = "https://download.geonames.org/export/dump/cities1000.zip"
COUNTRIES_URL = "https://download.geonames.org/export/dump/countryInfo.txt"
#: The postal codes of every country GeoNames has them for (#702), asked for only with
#: ``--postcodes``. CC BY 4.0; for Canada, the Netherlands and the United Kingdom it carries
#: the first part of each code, and the full British file, under a notice of Royal Mail's,
#: is not used.
POSTCODES_URL = "https://download.geonames.org/export/zip/allCountries.zip"
POSTCODES_MEMBER = "allCountries.txt"
#: Well over a million codes; an answer with a handful is not the table.
MINIMUM_POSTCODES = 100000
#: Codes a postcode table that is the real one holds, as (country, code).
REQUIRED_POSTCODES = (("DE", "10115"), ("PT", "1000-001"), ("FR", "75001"))

#: A minute and a half for a file of a few dozen megabytes; more than that is a connection
#: in trouble, not a dataset.
TIMEOUT = 90.0
#: What a download may weigh as it arrives, and what its one table may weigh unpacked (#782).
#: The real city table is about ten megabytes zipped and fifty unpacked.
MAX_DOWNLOAD_BYTES = 64 * 1024 * 1024
MAX_TABLE_BYTES = 512 * 1024 * 1024

#: The cities1000 table is some 170,000 rows; an answer with a handful is not the
#: table, and the download is a refusal in a different coat.
MINIMUM_CITIES = 10000

#: The country table is one row a country; there are more than two hundred.
MINIMUM_COUNTRIES = 200

#: The cities the loader would be useless without, in the languages the table itself
#: answers to. If any of them is absent, the file is not the table.
REQUIRED_CITIES = ("Berlin", "Lisbon", "Tokyo", "Nairobi", "São Paulo")


class Command(BaseCommand):
    help = "Download the GeoNames city dataset in place, where postulo.jobs.places reads it."

    def add_arguments(self, parser) -> None:
        parser.add_argument(
            "--postcodes",
            action="store_true",
            help=(
                "Also download GeoNames' postal codes (about 20 MB), so an address is placed "
                "at its postcode rather than its town. Off unless asked for."
            ),
        )

    def handle(self, *args, **options) -> None:
        self.stdout.write("Downloading the GeoNames city dataset from download.geonames.org ...")
        # The guarded client (rule 5 of the threat model): the addresses are fixed, but
        # this runs at start-up with nobody behind it, and a redirect is checked too.
        with public_only_client(
            timeout=httpx.Timeout(TIMEOUT, connect=10.0),
            headers={
                "User-Agent": "postulo fetch_geonames (a self-hosted Postulo provisioning data)",
            },
        ) as client:
            cities = self._fetch(client, CITIES_URL, "the city table")
            countries = self._fetch(client, COUNTRIES_URL, "the country table")
        self._validate(cities, countries)
        self._write(places.DATA_DIR / places.CITIES_FILE, cities)
        self._write(places.DATA_DIR / places.COUNTRIES_FILE, countries)
        # The table is parsed here, once, into the lookup the saves read (#399).
        places.build_lookup(places.DATA_DIR / places.CITIES_FILE, places._lookup_path())
        # The loader caches what it has read, and this process may have read the
        # absence; the new file is the state now.
        places._index.cache_clear()
        places._countries.cache_clear()
        self.stdout.write(
            self.style.SUCCESS(
                "Wrote the city table, its lookup and the country table. A location is "
                "placed from them when it is saved."
            )
        )
        if options.get("postcodes") or os.environ.get("POSTULO_GEONAMES_POSTCODES") == "1":
            self._postcodes()

    def _postcodes(self) -> None:
        """The postal codes, indexed in place: one more request, only when asked (#702)."""
        self.stdout.write("Downloading the GeoNames postal codes from download.geonames.org ...")
        with public_only_client(
            timeout=httpx.Timeout(TIMEOUT, connect=10.0),
            headers={
                "User-Agent": "postulo fetch_geonames (a self-hosted Postulo provisioning data)",
            },
        ) as client:
            try:
                status, body = get_bytes(
                    client, POSTCODES_URL, limit=MAX_DOWNLOAD_BYTES, seconds=TIMEOUT
                )
            except BodyRefused as refused:
                raise CommandError(
                    f"the download of the postal codes was refused: {refused}"
                ) from None
        if status != 200:
            raise CommandError(f"the download of the postal codes answered {status}")
        try:
            with zipfile.ZipFile(io.BytesIO(body)) as archive:
                member = archive.getinfo(POSTCODES_MEMBER)
                if member.file_size > MAX_TABLE_BYTES:
                    raise ValueError
                with archive.open(member) as inflated:
                    text = inflated.read(MAX_TABLE_BYTES + 1).decode("utf-8")
        except (zipfile.BadZipFile, KeyError, ValueError, UnicodeDecodeError):
            raise CommandError(
                f"the download of the postal codes was not the zip of {POSTCODES_MEMBER}"
            ) from None
        rows = [line for line in text.splitlines() if len(line.split("\t")) >= 11]
        if len(rows) < MINIMUM_POSTCODES:
            raise CommandError(f"the postal code table has {len(rows)} rows; it is not the table")
        target = places.DATA_DIR / places.POSTCODES_FILE
        with tempfile.TemporaryDirectory() as staging:
            staged = pathlib.Path(staging) / places.POSTCODES_FILE
            staged.write_text(text, encoding="utf-8")
            lookup = pathlib.Path(staging) / places.POSTCODES_LOOKUP_FILE
            places.build_postcode_lookup(staged, lookup)
            original = places.DATA_DIR
            try:
                places.DATA_DIR = pathlib.Path(staging)
                missing = [
                    f"{country} {code}"
                    for country, code in REQUIRED_POSTCODES
                    if places.resolve_postcode(code, country) is None
                ]
            finally:
                places.DATA_DIR = original
            if missing:
                raise CommandError(
                    "the postal code table lacks codes it must hold: " + ", ".join(missing)
                )
            self._write(target, text)
            places.build_postcode_lookup(target, places.DATA_DIR / places.POSTCODES_LOOKUP_FILE)
        self.stdout.write(
            self.style.SUCCESS(
                "Wrote the postal codes and their lookup. An address is placed at its "
                "postcode from them when it is saved."
            )
        )

    def _fetch(self, client, url: str, what: str) -> str:
        # Streamed and bounded (#782): this runs at first start with nobody behind it, against
        # a host the operator does not control, and a bomb here would kill the provisioning.
        try:
            status, body = get_bytes(client, url, limit=MAX_DOWNLOAD_BYTES, seconds=TIMEOUT)
        except BodyRefused as refused:
            raise CommandError(f"the download of {what} was refused: {refused}: {url}") from None
        if status != 200:
            raise CommandError(f"the download of {what} answered {status}: {url}")
        if url.endswith(".zip"):
            try:
                with zipfile.ZipFile(io.BytesIO(body)) as archive:
                    if len(archive.namelist()) != 1:
                        raise ValueError
                    member = archive.infolist()[0]
                    if member.file_size > MAX_TABLE_BYTES:
                        raise ValueError
                    with archive.open(member) as inflated:
                        text = inflated.read(MAX_TABLE_BYTES + 1)
                    if len(text) > MAX_TABLE_BYTES:
                        raise ValueError
                    return text.decode("utf-8")
            except (zipfile.BadZipFile, ValueError, UnicodeDecodeError):
                raise CommandError(
                    f"the download of {what} was not the zip of one tab-separated table: {url}"
                ) from None
        try:
            return body.decode("utf-8")
        except UnicodeDecodeError:
            raise CommandError(f"the download of {what} was not text: {url}") from None

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
        # The directory is whatever POSTULO_GEOLOCATIONS_DIR names, and may be new.
        target.parent.mkdir(parents=True, exist_ok=True)
        temporary = target.with_name(target.name + ".tmp")
        temporary.write_text(text, encoding="utf-8")
        os.replace(temporary, target)
