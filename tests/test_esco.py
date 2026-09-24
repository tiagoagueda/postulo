"""Where the names of jobs and skills come from (#266).

A job title is free text everywhere in Postulo, and so is a skill. That is correct for
what a person types and wrong for what the application can do with it: *Software
Engineer*, *Ingénieur logiciel* and *Engenheiro de software* are one job and three
strings, and nothing in the code can tell. The fix is the same one NACE had for
industries: a classification somebody else maintains and publishes translated, taken at
the level where a name is still a job somebody recognises, kept as a seed and never a
closed list, and shipped in the repository rather than fetched at runtime.
"""

from __future__ import annotations

from django.utils import translation

from postulo.jobs import esco

# --------------------------------------------------------------- what was vendored


def test_the_file_says_which_revision_it_is():
    """A list copied into a module gets copied again; one that names its revision can be
    replaced by somebody who knows what they are replacing.
    """
    assert esco.revision() == "ESCO v1.2.1"


def test_it_covers_occupations_at_the_level_that_was_chosen():
    """Unit groups are four-digit ISCO-08 codes, and every occupation maps to one of them.

    ESCO's occupations sit at level 5 of the ISCO-08 hierarchy and each is mapped to
    exactly one unit group; the groups are the level a name is still a job somebody
    recognises, as NACE's divisions are for industries.
    """
    document = esco.classification()

    assert len(document["unit_groups"]) == 436
    assert all(len(code) == 4 and code.isdigit() for code in document["unit_groups"])
    assert len(document["occupations"]) == 3039
    for entry in document["occupations"].values():
        assert entry["isco"] in document["unit_groups"]


def test_every_unit_group_belongs_to_a_major_group():
    assert all(esco.major_of(code) == code[0] for code, _name in esco.unit_groups())


def test_the_things_a_hand_made_list_would_not_have():
    """Named individually, because these are the gap the issue was about: a classification
    of the economy rather than a list of the jobs this project's authors know.
    """
    names = {name.casefold() for _code, name in esco.unit_groups()}

    assert "legislators" in names
    assert "systems analysts" in names
    assert "window cleaners" in names
    assert "hand launderers and pressers" in names


def test_it_arrives_translated_rather_than_being_translated_here():
    """The single biggest reason to take a standard: the names arrive written by the body
    that maintains them, in every language the classification is published in, and not one
    of them is a gettext string in this project.
    """
    assert len(esco.languages()) == 28
    with translation.override("fr"):
        assert esco.name_for("2511") == "Analystes de systèmes"
    with translation.override("pt-pt"):
        assert esco.name_for("2511") == "Analistas de sistemas"


def test_a_language_esco_does_not_publish_gets_the_english_name():
    """Postulo speaks languages ESCO does not publish, and inventing names for them would
    be this project asserting a classification it does not maintain.
    """
    first = esco.unit_groups()[0][0]
    with translation.override("tr"):
        assert esco.name_for(first) == esco.name_for(first, "en")


def test_a_variant_reads_the_language_it_is_a_variant_of():
    first = esco.unit_groups()[0][0]
    assert esco.name_for(first, "pt-br") == esco.name_for(first, "pt")
    assert esco.name_for(first, "en-gb") == esco.name_for(first, "en")


def test_nothing_in_the_classification_is_a_string_this_project_translates():
    """It is reference data, in the same sense a language's own name is."""
    from pathlib import Path

    source = Path("src/postulo/jobs/esco.py").read_text(encoding="utf-8")
    code = source.split('"""', 2)[2]

    assert "Chief executives" not in code and "Armed forces" not in code


# ------------------------------------------------------- a seed, and never a closed list


def test_a_word_somebody_made_up_has_no_code():
    """And having no code is not a lesser kind of job."""
    assert esco.code_for("Fintech wizard") == ""
    assert esco.code_for("") == ""


def test_a_unit_group_name_keeps_its_code():
    for code, name in esco.unit_groups():
        assert esco.code_for(name) == code


def test_an_english_occupation_name_finds_its_unit_group():
    """The matching that makes a French listing and a Portuguese CV comparable: the title
    is found among the occupation names, and the unit group it belongs to is the code.
    """
    document = esco.classification()
    for entry in document["occupations"].values():
        assert esco.code_for(entry["names"]["en"], "en") == entry["isco"]


def test_the_suggestions_are_the_unit_groups_and_only_them():
    offered = esco.suggestions()
    assert len(offered) == len(esco.unit_groups())
    assert len(esco.suggestions(offered)) == 0
