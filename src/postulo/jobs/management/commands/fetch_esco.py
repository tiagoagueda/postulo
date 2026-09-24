"""Download the ESCO classification in place, where the loader reads it (#266).

``postulo/jobs/esco.py`` reads ``data/esco-<revision>.json``; this command is how that
file gets there. It asks the ESCO web-service API — the access the ESCO services
document for machines, on the ESCO portal under *Use ESCO → Use ESCO Services (API)* —
for every ISCO-08 unit group and every occupation, in every language the classification
is published in, and writes the file beside the code that reads it. The package files
on the download page go through an e-mail consent a provisioning step cannot wait on,
and this does not.

A run is a deliberate harvest, the way the first one was: what is written is checked
against the shape the tests pin before anything is replaced, and the command says what
it found. The revision is recorded inside the file, so a new one is a re-run with
``--revision``, the pinned tests bumped to the revision they now hold, and the old file
deleted. Nothing in a request path calls this; it runs once, at provisioning.
"""

from __future__ import annotations

import json
import os
import re
import time
from collections import defaultdict

import httpx
from django.core.management.base import BaseCommand, CommandError

from postulo.jobs.esco import DATA_DIR, FALLBACK

#: The ESCO web-service API, the machine-facing access the ESCO services document points
#: to. If a run fails to fetch, this block is the first thing to check against the API's
#: own documentation at https://api.esco.ec.europa.eu/doc.
API_BASE = "https://api.esco.ec.europa.eu"
CONCEPTS_PATH = "/resource/skos/concept"

#: The ESCO model classes this harvest wants. A unit group's class is the one the model
#: gives the ISCO-08 unit groups, and the alternative is tried only where the first is
#: refused; a refusal is reported with the API's answer, which names its classes.
OCCUPATION_TYPE = "http://data.europa.eu/esco/model#Occupation"
UNIT_GROUP_TYPES = (
    "http://data.europa.eu/esco/model#ISCO-08UnitGroup",
    "http://data.europa.eu/esco/model#UnitGroup",
)

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

#: A minute is enough for one of the small responses this harvest makes; more than that
#: is a server in trouble, not a classification.
PUBLISHER = "European Commission, Directorate-General for Employment, Social Affairs and Inclusion"

TIMEOUT = 30.0
#: The courtesy pause between queries; the harvest is fifty-six of them.
DELAY = 0.25


class Command(BaseCommand):
    help = "Download the ESCO classification in place, where postulo.jobs.esco reads it."

    def add_arguments(self, parser):
        parser.add_argument(
            "--revision",
            help="which ESCO revision to download, e.g. 1.2.1; the API's newest where "
            "omitted, where it says which on the response",
        )

    def handle(self, *args, **options):
        version = (options["revision"] or "").lstrip("vV")
        extra = {"selectedVersion": version} if version else {}
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
            units, occupations, served = self._harvest(client, extra)
        if not version:
            if not served:
                raise CommandError(
                    "the API did not say which revision it served, so the file could "
                    "not be named; run the command again with --revision, "
                    "e.g. --revision 1.2.1"
                )
            version = served
        uncoded = sorted(i for i, e in occupations.items() if not e["code"])
        if uncoded:
            raise CommandError(
                f"{len(uncoded)} of the occupations carried no ISCO-08 code in any "
                f"language; the first is {uncoded[0]}. The API gives them in a shape "
                "this command does not read yet; adjust occupation_code."
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

    def _harvest(self, client, extra) -> tuple[dict, dict, str]:
        """Every unit group and occupation, in every language, as identifier-to-entry.

        Returns the unit groups, the occupations, and the revision the API said it
        served, the last from the response where the revision was not pinned.
        """
        units: dict[str, dict] = {}
        occupations: dict[str, dict] = {}
        served = ""
        group_type = None
        for language in LANGUAGES:
            if group_type is None:
                group_type, group_response = self._unit_group_type(client, extra, language)
            else:
                group_response = self._query(client, {"concepttype": group_type, **extra}, language)
            for concept in self._concepts(group_response):
                code = unit_group_code(concept)
                name = label(concept, language)
                if code and name:
                    entry = units.setdefault(code, {"major": code[0], "names": {}})
                    entry["names"][language] = name
            response = self._query(client, {"concepttype": OCCUPATION_TYPE, **extra}, language)
            for concept in self._concepts(response):
                identifier = identifier_of(concept)
                name = label(concept, language)
                if not identifier or not name:
                    continue
                entry = occupations.setdefault(identifier, {"code": "", "names": {}})
                if not entry["code"]:
                    entry["code"] = occupation_code(concept)
                entry["names"][language] = name
            served = served or _revision_from_response(response)
        return units, occupations, served

    def _query(self, client, params, language) -> httpx.Response:
        """One query to the API, with the URL and the answer in the error where it fails."""
        params = {**params, "lang": language}
        url = API_BASE + CONCEPTS_PATH
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
        time.sleep(DELAY)
        return response

    def _unit_group_type(self, client, extra, language) -> tuple[str, httpx.Response]:
        """The class the API accepts for the ISCO-08 unit groups, the first that answers.

        Tried once and kept for the rest of the harvest; a refusal is reported with the
        API's answer, which names the classes the API knows.
        """
        failures = []
        for concept_type in UNIT_GROUP_TYPES:
            try:
                response = self._query(client, {"concepttype": concept_type, **extra}, language)
            except CommandError as exc:
                failures.append(f"{concept_type}:\n{exc}")
                continue
            return concept_type, response
        shown = "\n".join(failures)
        raise CommandError(f"the ESCO API accepted no class for the unit groups:\n{shown}")

    def _concepts(self, response) -> list[dict]:
        """The concepts out of the answer, whatever wrapper the API put them in."""
        payload = response.json()
        if isinstance(payload, list):
            return payload
        if isinstance(payload, dict):
            for key in ("results", "concepts", "data", "items"):
                value = payload.get(key)
                if isinstance(value, list):
                    return value
            for value in payload.values():
                if isinstance(value, list) and value and isinstance(value[0], dict):
                    return value
        shown = str(payload).replace("\n", " ")[:400]
        raise CommandError(f"could not find the concepts in the API's answer: {shown}")

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


def _revision_from_response(response) -> str:
    """The revision the API says it served, from the response headers, else the empty string."""
    for key, value in response.headers.items():
        if key.lower().endswith(("revision", "version")):
            match = re.search(r"(\d+\.\d+(?:\.\d+)?)", value)
            if match:
                return match.group(1)
    return ""


def identifier_of(concept) -> str:
    """The identifier a concept carries, in whatever shape the API gives it."""
    for key in ("@id", "id"):
        value = concept.get(key)
        if isinstance(value, str) and value:
            return value
    return ""


def label(concept, language) -> str:
    """The name a concept carries in one language, in whatever shape the API gives it."""
    for key in ("skos:prefLabel", "prefLabel", "label", "skos:altLabel"):
        value = concept.get(key)
        if isinstance(value, str) and value:
            return value
        if isinstance(value, dict):
            candidate = value.get(language)
            if isinstance(candidate, str) and candidate:
                return candidate
        if isinstance(value, list):
            for item in value:
                if isinstance(item, dict) and item.get("@language") == language:
                    text = item.get("@value", item.get("value"))
                    if isinstance(text, str) and text:
                        return text
    return ""


def unit_group_code(concept) -> str:
    """The four-digit ISCO-08 code of a unit group, from the identifier or a notation."""
    tail = identifier_of(concept).rsplit("#", 1)[-1].rsplit("/", 1)[-1]
    if tail.startswith("C") and tail[1:].isdigit() and len(tail[1:]) == 4:
        return tail[1:]
    for key in ("skos:notation", "notation", "code"):
        value = concept.get(key)
        if isinstance(value, str) and value.isdigit() and len(value) == 4:
            return value
    return ""


def occupation_code(concept) -> str:
    """An occupation's own code, ``unit group.index``, or the unit group it sits under."""
    for key in (
        "skos:notation",
        "notation",
        "code",
        "dataEsco:occupationCode",
        "dataEsco:iscoCode",
        "iscoCode",
    ):
        value = concept.get(key)
        if isinstance(value, str) and re.fullmatch(r"\d{4}(\.\d+)?", value):
            return value
    tail = identifier_of(concept).rsplit("#", 1)[-1].rsplit("/", 1)[-1]
    if re.fullmatch(r"\d{4}(\.\d+)?", tail):
        return tail
    for key in ("skos:broader", "broader"):
        parent = concept.get(key)
        for one in parent if isinstance(parent, list) else [parent]:
            target = one if isinstance(one, str) else (one or {}).get("@id", "")
            tail = target.rsplit("#", 1)[-1].rsplit("/", 1)[-1]
            if tail.startswith("C") and tail[1:].isdigit() and len(tail[1:]) == 4:
                return tail[1:]
    return ""
