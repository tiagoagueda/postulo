"""The occupations are read a language at a time, as the skills are (#1110).

The classification was one JSON document, `esco-<revision>.json`: 436 unit groups and 3,039
occupations, each with its name in all 28 languages ESCO publishes. Reading it at all meant
parsing 6.3 MB and keeping 14.6 MB in every process, twice that at the peak, and every
worker read it soon after starting, because saving a listing looks its title up.

It is a zip now, `esco-occupations-<revision>.zip`: how the classification is built in one
member, and each language's names in a member of their own. These run on a small file of
their own, so they do not wait for the real one to be downloaded; `tests/test_esco.py` asks
the same of the real one where there is one.
"""

from __future__ import annotations

import json
import logging

import pytest
from django.utils import translation

from postulo.jobs import checks, esco
from postulo.jobs.management.commands import fetch_esco

from .conftest import write_esco_occupations

#: Three unit groups and five occupations in three languages. Portuguese has no name for
#: one unit group and one occupation, which read the English; two occupations share a
#: French name across two unit groups, which is a name that says neither.
DOCUMENT = {
    "languages": ["en", "fr", "pt"],
    "unit_groups": {
        "2511": {
            "major": "2",
            "names": {
                "en": "Systems analysts",
                "fr": "Analystes de systèmes",
                "pt": "Analistas de sistemas",
            },
        },
        "2512": {
            "major": "2",
            "names": {"en": "Software developers", "fr": "Concepteurs de logiciels"},
        },
        "5131": {
            "major": "5",
            "names": {"en": "Waiters", "fr": "Serveurs", "pt": "Empregados de mesa"},
        },
    },
    "occupations": {
        "2511.1": {
            "isco": "2511",
            "names": {
                "en": "ICT system analyst",
                "fr": "analyste de systèmes TIC",
                "pt": "analista de sistemas de TIC",
            },
            "nace": ["62"],
        },
        "2512.1": {
            "isco": "2512",
            "names": {
                "en": "web developer",
                "fr": "développeur web/développeuse web",
                "pt": "programador web/programadora web",
            },
            "nace": ["62", "63"],
        },
        "2512.2": {
            "isco": "2512",
            "names": {"en": "software developer", "fr": "responsable de salle"},
            "nace": ["62"],
        },
        "5131.1": {
            "isco": "5131",
            "names": {
                "en": "waiter/waitress",
                "fr": "serveur/serveuse",
                "pt": "empregado de mesa/empregada de mesa",
            },
            "nace": ["56"],
        },
        "5131.2": {
            "isco": "5131",
            "names": {"en": "head waiter", "fr": "responsable de salle", "pt": "chefe de mesa"},
            "nace": [],
        },
    },
}

#: The same classification a revision on: a unit group ahead of the others and an occupation
#: among them, so every entry after them sits one further along than it did.
LATER = {
    "languages": ["en", "fr", "pt"],
    "unit_groups": {
        "1111": {
            "major": "1",
            "names": {
                "en": "Legislators",
                "fr": "Membres des corps législatifs",
                "pt": "Membros dos órgãos legislativos",
            },
        },
        **DOCUMENT["unit_groups"],
    },
    "occupations": {
        "1111.1": {
            "isco": "1111",
            "names": {"en": "senator", "fr": "sénateur/sénatrice", "pt": "senador/senadora"},
            "nace": ["84"],
        },
        **DOCUMENT["occupations"],
    },
}


def whole(document, revision="9.9.9") -> dict:
    """The classification as a release before #1110 wrote it: one document, names and all."""
    return {**esco.ABSENT, "revision": f"ESCO v{revision}", **document}


def write_earlier(directory, document=DOCUMENT, revision="9.9.9"):
    """Write the file an earlier release's `fetch_esco` left in the data directory."""
    target = directory / f"esco-{revision}.json"
    target.write_text(json.dumps(whole(document, revision), ensure_ascii=False), encoding="utf-8")
    return target


@pytest.fixture
def directory(tmp_path, monkeypatch):
    """An empty data directory, where the loader looks; what it held dropped either side."""
    monkeypatch.setattr(esco, "DATA_DIR", tmp_path)
    esco.forget()
    yield tmp_path
    esco.forget()


@pytest.fixture
def occupations(directory):
    """The small classification above, written the way `fetch_esco` writes one."""
    write_esco_occupations(directory, DOCUMENT)
    return directory


@pytest.fixture
def members(monkeypatch):
    """Every read of a file `fetch_esco` wrote, by the member read: the file's name where
    it is read whole, which is how an earlier release's is."""
    read = []
    stamped = esco._stamped

    def watched(path, name=""):
        read.append(name or path.name)
        return stamped(path, name)

    monkeypatch.setattr(esco, "_stamped", watched)
    return read


def answers() -> dict:
    """What the loader says of the small classification, to every kind of reader."""
    found = {
        "revision": esco.revision(),
        "languages": esco.languages(),
        "majors": [esco.major_of(code) for code in ("2511", "5131", "9999")],
        "divisions": [sorted(esco.divisions_for(code)) for code in ("2511", "2512", "5131", "9")],
    }
    typed = (
        "ICT system analyst",
        "analyste de systèmes tic",
        "Développeuse  Web",
        "développeur web/développeuse web",
        "programadora web",
        "software developer",
        "responsable de salle",
        "chefe de mesa",
        "Serveurs",
        "Software developers",
        "EMPREGADOS DE MESA",
        "waitress",
        "fintech wizard",
        "",
    )
    for reader in ("en", "en-GB", "fr", "fr-CA", "pt", "pt-BR", "tr", "de"):
        found[reader] = {
            "unit_groups": esco.unit_groups(reader),
            "names": [esco.name_for(code, reader) for code in ("2511", "2512", "5131", "9999")],
            "codes": [esco.code_for(title, reader) for title in typed],
            "offered": esco.suggestions(language=reader),
            "offered_less": esco.suggestions(exclude=["waiters", "SERVEURS"], language=reader),
            "offered_near": esco.suggestions(language=reader, sections=["I"]),
        }
    return found


# ------------------------------------------------- what a process reads, and what it keeps


def test_a_reader_costs_their_language_and_english_and_no_other(occupations, members):
    """Every process parsed the whole classification, all 28 languages of it, to answer a
    reader in one (#1110). A French reader has the member that says how the classification
    is built, the French names and the English ones read, each once however often they
    ask, and Portuguese is not read until somebody reads in Portuguese."""
    for _again in range(3):
        assert esco.name_for("5131", "fr") == "Serveurs"
        assert esco.code_for("Développeuse web", "fr") == "2512"
        assert esco.code_for("head waiter", "fr-CA") == "5131"
        assert esco.suggestions(language="fr") == [
            "Analystes de systèmes",
            "Concepteurs de logiciels",
            "Serveurs",
        ]
        assert esco.divisions_for("2512") == {"62", "63"}

    assert sorted(members) == ["about.json", "names/en.json", "names/fr.json"]

    with translation.override("pt-BR"):
        assert esco.name_for("5131") == "Empregados de mesa"
        assert esco.code_for("programadora web") == "2512"
    assert sorted(members) == ["about.json", "names/en.json", "names/fr.json", "names/pt.json"]


def test_an_english_reader_costs_english_alone(occupations, members):
    assert esco.code_for("Waitress", "en-GB") == "5131"
    assert esco.unit_groups("en")[0] == ("2511", "Systems analysts")
    # Turkish is a language ESCO does not publish, which reads the English names.
    assert esco.name_for("2512", "tr") == "Software developers"

    assert sorted(members) == ["about.json", "names/en.json"]


def test_what_is_held_of_the_classification_has_no_name_in_it(occupations):
    """`classification()` was the whole document. It is how the classification is built:
    each unit group's major group, each occupation's unit group and NACE divisions."""
    document = esco.classification()

    assert document["revision"] == "ESCO v9.9.9" and document["languages"] == ["en", "fr", "pt"]
    assert document["unit_groups"] == {
        "2511": {"major": "2"},
        "2512": {"major": "2"},
        "5131": {"major": "5"},
    }
    assert document["occupations"]["2512.1"] == {"isco": "2512", "nace": ["62", "63"]}
    assert list(document["occupations"]) == list(DOCUMENT["occupations"])
    assert "names" not in json.dumps(document) and "Waiters" not in json.dumps(document)


def test_the_names_a_language_was_built_from_are_not_kept_for_every_language(occupations):
    """A language's names as the file has them are what its matching is built from, once.
    Only two languages' are held at a time, and what was built from the others stays."""
    for reader in ("fr", "pt", "en"):
        assert esco.code_for("waiter", reader) == "5131"

    assert esco._names.cache_info().currsize <= esco.NAMES_HELD == 2
    assert esco._occupations_by_name.cache_info().currsize == 3


# ------------------------------------------------------------ the same answers as before


def test_a_name_the_language_has_not_is_the_english_one(occupations):
    assert esco.name_for("2512", "pt") == "Software developers"
    assert esco.unit_groups("pt-BR") == [
        ("2511", "Analistas de sistemas"),
        ("2512", "Software developers"),
        ("5131", "Empregados de mesa"),
    ]
    assert esco.code_for("Software Developers", "pt") == "2512"
    # An occupation Portuguese has no name for is found by its English one, in Portuguese.
    assert esco.code_for("software developer", "pt") == "2512"


def test_a_name_in_two_unit_groups_still_matches_nothing(occupations):
    assert esco.code_for("responsable de salle", "fr") == ""
    assert esco.code_for("head waiter", "fr") == "5131", "the English name says which"


def test_a_code_nothing_here_has_has_no_name_and_no_group(occupations):
    assert esco.name_for("9999", "fr") == "" and esco.name_for("", "fr") == ""
    assert esco.major_of("9999") == "" and esco.divisions_for("9999") == frozenset()


def test_an_earlier_releases_file_gives_the_same_answers(directory):
    """An instance upgraded past #1110 holds the `esco-<revision>.json` it downloaded, and
    must go on matching titles without anybody running a command. Everything the loader
    says of it is what it says of the zip `fetch_esco` now writes from the same harvest."""
    write_esco_occupations(directory, DOCUMENT)
    from_the_zip = answers()
    assert from_the_zip["pt"]["names"] == [
        "Analistas de sistemas",
        "Software developers",
        "Empregados de mesa",
        "",
    ]
    assert from_the_zip["fr"]["codes"][:4] == ["2511", "2511", "2512", "2512"]

    esco.data_file().unlink()
    write_earlier(directory)
    esco.forget()

    assert esco.data_file().name == "esco-9.9.9.json"
    assert answers() == from_the_zip


def test_an_earlier_releases_file_is_not_kept_whole_either(directory, members, caplog):
    """Read as it was, that file would go on costing every process every language. It is
    parsed for the part that is asked for and let go: once for how the classification is
    built and once for each language read, and the log says once which file it is."""
    write_earlier(directory)

    with caplog.at_level(logging.WARNING, logger="postulo.jobs.esco"):
        for _again in range(2):
            assert esco.code_for("serveuse", "fr") == "5131"
            assert esco.name_for("2512", "fr") == "Concepteurs de logiciels"

    assert members == ["esco-9.9.9.json"] * 3, "how it is built, French, English"
    assert "names" not in json.dumps(esco.classification())
    said = [record.getMessage() for record in caplog.records]
    assert len(said) == 1 and "esco-9.9.9.json" in said[0] and "fetch_esco" in said[0]


# ----------------------------------------------- a file replaced under a running instance


def test_a_running_process_moves_whole_to_the_file_that_replaced_the_one_it_read(directory):
    """`fetch_esco` is run on an instance that is serving, which is how it gets its next
    revision. A process that had read how one file is built and then read a language out of
    the file that replaced it would lay one revision's names against the other's entries:
    here every name one place out. It drops what it held and reads the new file instead."""
    write_esco_occupations(directory, DOCUMENT)
    assert esco.name_for("5131", "fr") == "Serveurs"
    assert esco.code_for("serveuse", "fr") == "5131"

    write_esco_occupations(directory, LATER, revision="9.9.10")
    (directory / "esco-occupations-9.9.9.zip").unlink()

    # Nothing is forgotten by hand. What was read goes on being answered as it was read...
    assert esco.name_for("5131", "fr") == "Serveurs"
    # ...and the first language read after it is read out of the new file, with the rest.
    assert esco.unit_groups("pt") == [
        ("1111", "Membros dos órgãos legislativos"),
        ("2511", "Analistas de sistemas"),
        ("2512", "Software developers"),
        ("5131", "Empregados de mesa"),
    ]
    assert esco.revision() == "ESCO v9.9.10"
    assert esco.code_for("senadora", "pt") == "1111"
    assert esco.code_for("chefe de mesa", "pt") == "5131"
    assert esco.name_for("1111", "fr") == "Membres des corps législatifs"
    assert esco.code_for("serveuse", "fr") == "5131"
    assert esco.divisions_for("1111") == {"84"}


def test_a_running_process_moves_from_an_earlier_releases_file_to_the_zip(directory):
    """The upgrade itself: the instance is serving from `esco-<revision>.json` when somebody
    runs `fetch_esco`, which writes the zip and then deletes that file."""
    write_earlier(directory, DOCUMENT, revision="1.2.1")
    assert esco.code_for("serveuse", "fr") == "5131"

    write_esco_occupations(directory, LATER, revision="1.2.1")
    (directory / "esco-1.2.1.json").unlink()

    assert esco.name_for("5131", "pt") == "Empregados de mesa"
    assert esco.name_for("1111", "pt") == "Membros dos órgãos legislativos"
    assert esco.name_for("1111", "fr") == "Membres des corps législatifs"
    assert esco.code_for("sénatrice", "fr") == "1111"
    assert esco.data_file().name == "esco-occupations-1.2.1.zip"


def test_a_file_fetch_esco_did_not_write_is_refused_rather_than_misread(directory):
    """A language with a name too few would put every name after it on the wrong entry."""
    document = whole(DOCUMENT)
    short = esco.names_of(document, "fr")
    short["occupations"].pop()
    fetch_esco.write_members(
        directory / "esco-occupations-9.9.9.zip",
        {
            esco.OCCUPATIONS_ABOUT: json.dumps(esco.structure_of(document)),
            "names/en.json": json.dumps(esco.names_of(document, "en")),
            "names/fr.json": json.dumps(short),
        },
    )

    assert esco.code_for("waitress", "en") == "5131"
    with pytest.raises(RuntimeError, match="not a file"):
        esco.code_for("serveuse", "fr")


def test_without_the_file_nothing_matches_and_nothing_is_offered(directory):
    """The ordinary state of an instance nobody ran `fetch_esco` on, and not an error."""
    assert esco.classification() == esco.ABSENT
    assert esco.code_for("waiter", "fr") == "" and esco.name_for("5131", "fr") == ""
    assert esco.unit_groups("fr") == [] and esco.suggestions(language="fr") == []


# ---------------------------------------------------------------- what start-up says of it


def test_the_check_says_the_file_is_an_earlier_releases_and_how_to_replace_it(directory):
    """Nothing is wrong with an instance that still holds `esco-<revision>.json`, so the
    start-up goes on; but only `fetch_esco` gets it the file that is read a language at a
    time, and nothing else would ever tell whoever runs it."""
    assert checks.esco_earlier_file(None) == []

    write_earlier(directory, revision="1.2.1")
    (found,) = checks.esco_earlier_file(None)
    assert found.id == "postulo.W004" and not found.is_serious()
    assert "esco-1.2.1.json" in found.msg
    assert "manage.py fetch_esco --revision 1.2.1" in found.hint
    assert checks.esco_one_revision(None) == []

    write_esco_occupations(directory, DOCUMENT, revision="1.2.1")
    assert checks.esco_earlier_file(None) == [], "beside the zip it is not what is read"


def test_two_of_the_earlier_files_beside_a_zip_are_not_two_revisions(directory):
    """The check reports what the loader would refuse, and the loader reads the zip."""
    write_earlier(directory, revision="1.2.0")
    write_earlier(directory, revision="1.2.1")
    assert [error.id for error in checks.esco_one_revision(None)] == ["postulo.E040"]
    assert checks.esco_earlier_file(None) == [], "the two revisions are said, and once"

    write_esco_occupations(directory, DOCUMENT, revision="1.2.1")
    assert checks.esco_one_revision(None) == []
    assert esco.name_for("5131", "fr") == "Serveurs"
