"""Download the ESCO classification in place, where the loader reads it (#266).

``postulo/jobs/esco.py`` reads ``data/esco-<revision>.json``; this command is how that
file gets there. It asks the ESCO web-service API, the machine-facing access the ESCO
services document at
https://esco.ec.europa.eu/en/use-esco/use-esco-services-api/esco-web-service-api, for
every ISCO-08 unit group and every occupation, in every language the classification is
published in, and writes the file beside the code that reads it. The package files on
the download page go through an e-mail consent a provisioning step cannot wait on, and
this does not.

Two searches do the harvest, one per class: the concepts of the ISCO-08 concept scheme,
of which the unit groups are the four-digit codes, and the class the API names
``occupation``. Each concept's answer carries its preferred label in every language in
the same object, so there is one pass, not one per language, and the paging is followed
until the answer's total is in. The API names its versions with a ``v`` prefix and says
nothing about which it served where none is asked, and its default is not the newest, so
the revision is a required argument, recorded inside the file; a new revision is a
re-run with the new ``--revision`` and the old file deleted. What is written is checked
against the shape the tests pin before anything is replaced, and the command says what
it found. Nothing in a request path calls this; it runs once, at provisioning.
"""

from __future__ import annotations

import json
import os
import re
from collections import defaultdict

import httpx
from django.core.management.base import BaseCommand, CommandError

from postulo.jobs.esco import DATA_DIR, FALLBACK

#: The ESCO web-service API. If a run fails to fetch, this block is the first thing to
#: check against the API's own documentation, at https://ec.europa.eu/esco/api/doc/.
API_BASE = "https://ec.europa.eu/esco/api"
SEARCH_PATH = "/search"

#: The concept scheme the ISCO-08 hierarchy is published in. The scheme carries the
#: majors, the sub-majors and the unit groups together, and the unit groups are the
#: four-digit codes, so the rest of the scheme is not a unit group and not a name.
ISCO_SCHEME = "http://data.europa.eu/esco/concept-scheme/isco"

#: The class the API knows for occupations, in the short name its ``type`` filter takes.
OCCUPATION_TYPE = "occupation"

#: One page of the harvest; a full classification answers in one of these, and the
#: paging that follows is for the day it stops.
PAGE_SIZE = 10000

#: The language a concept's title is negotiated in; the preferred labels carry the rest.
NEGOTIATED_LANGUAGE = "en"

#: The 28 languages the classification is published in, the portal's own list, which the
#: tests the file against also hold.
LANGUAGES = (
    "ar",
    "bg",
    "cs",
    "da",
    "de",
    "el",
    "en",
    "es",
    "et",
    "fi",
    "fr",
    "ga",
    "hr",
    "hu",
    "is",
    "it",
    "lt",
    "lv",
    "mt",
    "nl",
    "no",
    "pl",
    "pt",
    "ro",
    "sk",
    "sl",
    "sv",
    "uk",
)

PUBLISHER = "European Commission, Directorate-General for Employment, Social Affairs and Inclusion"

#: A minute is enough for one of the pages this harvest makes, which are a few
#: megabytes; more than that is a server in trouble, not a classification.
TIMEOUT = 60.0


class Command(BaseCommand):
    help = "Download the ESCO classification in place, where postulo.jobs.esco reads it."

    def add_arguments(self, parser):
        parser.add_argument(
            "--revision",
            required=True,
            help="which ESCO revision to download, e.g. 1.2.1; required, because the "
            "API does not say which revision it served where none is asked, and its "
            "default is not the newest",
        )

    def handle(self, *args, **options):
        version = (options["revision"] or "").strip().lstrip("vV")
        if not version:
            raise CommandError(
                "--revision is required: the API does not say which revision it served "
                "where none is asked, and its default is not the newest; "
                "e.g. --revision 1.2.1"
            )
        self.stdout.write(
            f"Downloading the ESCO classification in {len(LANGUAGES)} languages from {API_BASE} ..."
        )
        with httpx.Client(
            timeout=httpx.Timeout(TIMEOUT, connect=10.0),
            follow_redirects=True,
            headers={
                "User-Agent": "postulo fetch_esco (a self-hosted Postulo provisioning data)",
                "Accept": "application/json",
            },
        ) as client:
            units, occupations = self._harvest(client, f"v{version}")
        uncoded = sorted(i for i, e in occupations.items() if not e["code"])
        if uncoded:
            raise CommandError(
                f"{len(uncoded)} of the occupations carried no ISCO-08 code; the first "
                f"is {uncoded[0]}. The API gives them in a shape this command does not "
                "read yet; adjust occupation_code."
            )
        document = self._document(units, occupations, version)
        self._validate(document)
        target = DATA_DIR / f"esco-{version}.json"
        temporary = target.with_name(target.name + ".tmp")
        payload = json.dumps(document, ensure_ascii=False, indent=2) + "\n"
        temporary.write_text(payload, encoding="utf-8")
        os.replace(temporary, target)
        self.stdout.write(
            f"Wrote {target}: {len(document['unit_groups'])} unit groups and "
            f"{len(document['occupations'])} occupations, revision "
            f"{document['revision']}."
        )
        others = sorted(p.name for p in DATA_DIR.glob("esco-*.json") if p != target)
        if others:
            self.stdout.write(
                self.style.WARNING(
                    f"Delete {', '.join(others)} once the new file is checked: the "
                    "loader reads one revision at a time."
                )
            )

    def _harvest(self, client, selected_version) -> tuple[dict, dict]:
        """Every unit group and occupation, in every language, as identifier-to-entry.

        Two searches do it: the ISCO concept scheme, of which the unit groups are the
        four-digit codes, and the occupation class. Each concept's answer carries its
        preferred label in every language, so there is one pass, not one per language.
        """
        units: dict[str, dict] = {}
        occupations: dict[str, dict] = {}
        for concept in self._search(
            client, {"isInScheme": ISCO_SCHEME, "selectedVersion": selected_version}
        ):
            code = unit_group_code(concept)
            if not code:
                continue
            entry = units.setdefault(code, {"major": code[0], "names": {}})
            self._labels(concept, entry["names"])
        for concept in self._search(
            client, {"type": OCCUPATION_TYPE, "selectedVersion": selected_version}
        ):
            identifier = identifier_of(concept)
            if not identifier:
                continue
            entry = occupations.setdefault(identifier, {"code": "", "names": {}})
            if not entry["code"]:
                entry["code"] = occupation_code(concept)
            self._labels(concept, entry["names"])
        return units, occupations

    def _labels(self, concept, names) -> None:
        """The concept's preferred label in each of the classification's languages."""
        labels = concept.get("preferredLabel")
        if not isinstance(labels, dict):
            return
        for language in LANGUAGES:
            value = labels.get(language)
            if isinstance(value, str) and value and language not in names:
                names[language] = value

    def _search(self, client, params) -> list[dict]:
        """Every concept a search returns, the pages followed until the total is in."""
        concepts: list[dict] = []
        seen: set[str] = set()
        offset = 0
        while True:
            response = self._query(
                client,
                {
                    **params,
                    "full": "false",
                    "language": NEGOTIATED_LANGUAGE,
                    "limit": PAGE_SIZE,
                    "offset": offset,
                },
            )
            try:
                payload = response.json()
            except ValueError:
                payload = None
            if not isinstance(payload, dict):
                raise CommandError(
                    "the ESCO API's answer was not a search result: "
                    f"{response.text.replace(chr(10), ' ')[:400]}"
                )
            embedded = payload.get("_embedded")
            items = embedded.get("results") if isinstance(embedded, dict) else None
            if not isinstance(items, list):
                raise CommandError(
                    "could not find the concepts in the API's answer: "
                    f"{response.text.replace(chr(10), ' ')[:400]}"
                )
            total = payload.get("total")
            if not isinstance(total, int):
                raise CommandError(f"the API's answer carried no total: {response.text[:400]}")
            for item in items:
                if not isinstance(item, dict):
                    continue
                key = identifier_of(item)
                if key:
                    if key in seen:
                        continue
                    seen.add(key)
                concepts.append(item)
            offset += len(items)
            if not items or offset >= total:
                break
        return concepts

    def _query(self, client, params) -> httpx.Response:
        """One query to the API, with the URL and the answer in the error where it fails."""
        url = API_BASE + SEARCH_PATH
        try:
            response = client.get(url, params=params)
        except httpx.HTTPError as exc:
            raise CommandError(f"the ESCO API did not answer {url}: {exc}") from exc
        if response.status_code == 429:
            raise CommandError(
                "the ESCO API is rate-limiting the harvest; wait a minute and run the command again"
            )
        if response.status_code != 200:
            shown = response.text.replace("\n", " ")[:400]
            raise CommandError(
                f"the ESCO API answered {response.status_code} for {url} with "
                f"{params}, answering:\n{shown}"
            )
        return response

    def _document(self, units, occupations, version) -> dict:
        """The file, in the order and shape the tests read it."""
        by_group: dict[str, list[str]] = defaultdict(list)
        for identifier, entry in occupations.items():
            by_group[entry["code"][:4]].append(identifier)
        occupation_entries = {}
        for group in sorted(by_group):
            identifiers = sorted(by_group[group])
            for position, identifier in enumerate(identifiers, start=1):
                entry = occupations[identifier]
                code = entry["code"] if "." in entry["code"] else f"{group}.{position}"
                names = {lang: entry["names"][lang] for lang in sorted(entry["names"])}
                occupation_entries[code] = {"isco": group, "names": names}
        unit_entries = {}
        for code in sorted(units):
            entry = units[code]
            unit_entries[code] = {
                "major": entry["major"],
                "names": {lang: entry["names"][lang] for lang in sorted(entry["names"])},
            }
        return {
            "revision": f"ESCO v{version}",
            "source": "https://esco.ec.europa.eu",
            "publisher": PUBLISHER,
            "licence": "EUPL 1.2",
            "languages": sorted(LANGUAGES),
            "unit_groups": unit_entries,
            "occupations": occupation_entries,
        }

    def _validate(self, document) -> None:
        """The invariants the loader and the tests assume, checked before anything is written."""
        problems = []
        groups = document["unit_groups"]
        occupations = document["occupations"]
        if not groups:
            problems.append("no unit groups at all")
        if not occupations:
            problems.append("no occupations at all")
        if any(not re.fullmatch(r"\d{4}", code) for code in groups):
            problems.append("a unit group is not a four-digit ISCO-08 code")
        missing = sorted({e["isco"] for e in occupations.values() if e["isco"] not in groups})
        if missing:
            problems.append(f"occupations mapped to unit groups the harvest has not: {missing[:5]}")
        if any(not entry["names"].get(FALLBACK) for entry in groups.values()):
            problems.append("a unit group has no English name")
        if any(not entry["names"].get(FALLBACK) for entry in occupations.values()):
            problems.append("an occupation has no English name")
        if problems:
            shown = "\n".join(f"- {problem}" for problem in problems)
            raise CommandError(f"the download is not the shape the loader reads:\n{shown}")


def identifier_of(concept) -> str:
    """The identifier a concept carries, the URI the API gives it."""
    value = concept.get("uri")
    return value if isinstance(value, str) and value else ""


def unit_group_code(concept) -> str:
    """The four-digit ISCO-08 code of a unit group, from its code or its identifier."""
    value = concept.get("code")
    if isinstance(value, str) and re.fullmatch(r"\d{4}", value):
        return value
    tail = identifier_of(concept).rsplit("#", 1)[-1].rsplit("/", 1)[-1]
    if tail.startswith("C") and tail[1:].isdigit() and len(tail[1:]) == 4:
        return tail[1:]
    return ""


def occupation_code(concept) -> str:
    """An occupation's own code, the unit group with its place in the group's tree."""
    values = [concept.get("code")]
    codes = concept.get("codes")
    if isinstance(codes, list):
        values.extend((item or {}).get("value") for item in codes if isinstance(item, dict))
    for value in values:
        if isinstance(value, str) and re.fullmatch(r"\d{4}(\.\d+)*", value):
            return value
    tail = identifier_of(concept).rsplit("#", 1)[-1].rsplit("/", 1)[-1]
    if re.fullmatch(r"\d{4}(\.\d+)*", tail):
        return tail
    return ""
