"""Where the list of areas of activity comes from (#140).

> you shoud looke for a more extenside and general area of activity list

Thirty-two hand-made names had *Gaming* and *E-commerce* and nothing at all for a third of
the economy. The fix is not more names — a hand-made list of two hundred is two hundred
names in thirty-nine languages, carried by this project for ever — but a classification
somebody else maintains and publishes translated.

Three things have to hold at once, and they pull against each other. The list has to
**cover** the economy, which is why it is NACE. It has to be **usable**, which is why it is
divisions rather than sections or classes. And it must not become a **closed list**, because
`Industry` is one vocabulary per person in their own words, and NACE classifies the business
rather than the job — somebody applying to a bank's software team is applying to
*Financial and insurance activities*, which is true of the employer and useless to them.
"""

from __future__ import annotations

import json

import pytest
from django.utils import translation

from postulo.jobs import industries
from postulo.jobs.models import Industry

pytestmark = pytest.mark.django_db


# --------------------------------------------------------------- what was vendored


def test_the_file_says_which_revision_it_is():
    """A list copied into a module gets copied again; one that names its revision can be
    replaced by somebody who knows what they are replacing.
    """
    assert industries.revision() == "NACE Rev. 2.1"


def test_it_covers_the_economy_at_the_level_that_was_chosen():
    """22 sections and 87 divisions — the published shape of Rev. 2.1.

    Sections would have been coarser than the hand-made list ever was; classes are a form
    nobody fills in. This is the level in between, and it is chosen rather than inherited.
    """
    document = industries.classification()

    assert len(document["sections"]) == 22
    assert len(document["divisions"]) == 87
    assert all(len(code) == 2 and code.isdigit() for code in document["divisions"])


def test_every_division_belongs_to_a_section():
    assert all(industries.section_of(code) for code, _name in industries.divisions())


def test_the_things_the_old_list_had_no_word_for():
    """Named individually, because these are the gap the issue was about."""
    names = {name.casefold() for _code, name in industries.divisions()}

    assert "mining of metal ores" in names
    assert "water collection, treatment and supply" in names
    assert "arts creation and performing arts activities" in names
    assert "public administration and defence; compulsory social security" in names


def test_it_arrives_translated_rather_than_being_translated_here():
    """The single biggest reason to take a standard. 87 × 24 names, written by the body
    that maintains them, and not one of them a gettext string in this project.
    """
    assert len(industries.languages()) == 24
    with translation.override("pt-pt"):
        assert (
            industries.name_for("62")
            == "Consultoria, programação informática e atividades relacionadas"
        )
    with translation.override("de"):
        assert industries.name_for("62").startswith("Erbringung von Dienstleistungen")


def test_a_variant_reads_the_language_it_is_a_variant_of():
    with translation.override("pt-br"):
        assert industries.name_for("01").startswith("Produção")
    with translation.override("en-gb"):
        assert industries.name_for("01").startswith("Crop and animal")


def test_a_language_eurostat_does_not_publish_gets_the_english_name():
    """Fifteen of the languages Postulo speaks are not official EU languages, and inventing
    NACE names for them would be this project asserting a classification it does not
    maintain. The person types their own word instead, which is what almost everybody does.
    """
    with translation.override("uk"):
        assert (
            industries.name_for("62") == "Computer programming, consultancy and related activities"
        )


def test_nothing_in_the_classification_is_a_string_this_project_translates():
    """It is reference data, in the same sense a language's own name is."""
    from pathlib import Path

    source = Path("src/postulo/jobs/industries.py").read_text(encoding="utf-8")
    code = source.split('"""', 2)[2]

    assert "Mining" not in code and "Crop and animal" not in code


# ------------------------------------------------------- a seed, and never a closed list


def test_a_word_somebody_made_up_is_still_an_industry(user):
    industry = Industry.named(user, ["Fintech"])[0]

    assert industry.name == "Fintech"
    assert industry.code == "", "and having no code is not a lesser kind of industry"


def test_a_name_that_is_a_division_keeps_its_code(user):
    industry = Industry.named(user, ["Mining of metal ores"])[0]

    assert industry.code == "07"
    assert industry.name == "Mining of metal ores", "in the person's list, in their words"


def test_the_code_follows_the_name(user):
    """One invariant rather than two fields that can disagree."""
    industry = Industry.named(user, ["Mining of metal ores"])[0]

    industry.name = "Digging things up"
    industry.save()

    assert industry.code == ""


def test_a_division_name_typed_in_the_readers_language_finds_its_code(user):
    with translation.override("pt-pt"):
        industry = Industry.named(user, ["Silvicultura e exploração florestal"])[0]

    assert industry.code == "02"


def test_an_english_name_pasted_into_another_language_still_finds_its_code():
    """Somebody reading Postulo in French may paste a division name out of a form."""
    with translation.override("fr-fr"):
        assert industries.code_for("Mining of metal ores") == "07"


def test_the_suggestions_lead_with_the_words_people_actually_type(user):
    """*Software* before *Manufacture of coke and refined petroleum products*: a datalist
    shows its options in order until somebody types.
    """
    offered = industries.suggestions()

    assert offered[0] == "Software"
    assert "Mining of metal ores" in offered
    assert offered.index("Software") < offered.index("Mining of metal ores")


def test_a_name_in_both_vocabularies_is_offered_once():
    """*Education* is Postulo's own word and NACE division 85."""
    offered = industries.suggestions()

    assert len({name.casefold() for name in offered}) == len(offered)


def test_the_suggestions_are_in_the_readers_language(user):
    with translation.override("pt-pt"):
        offered = industries.suggestions()

    assert "Silvicultura e exploração florestal" in offered


def test_the_company_form_offers_the_classification(client, user):
    from django.urls import reverse

    client.force_login(user)

    html = client.get(reverse("jobs:company_create")).content.decode()

    assert "Mining of metal ores" in html
    assert "Software" in html


# ------------------------------------------------------- and what was already there


def test_nothing_a_person_already_had_was_touched(user):
    """Every existing row is somebody's own word, and seeding must not rename or absorb one.

    The migration adds a column and fills nothing in; a code arrives when a name is saved
    that matches a division, and never retroactively.
    """
    from pathlib import Path

    migration = Path("src/postulo/jobs/migrations/0011_industry_nace.py").read_text(
        encoding="utf-8"
    )

    assert "RunPython" not in migration


def test_two_spellings_are_still_one_industry(user):
    Industry.named(user, ["Fintech"])
    Industry.named(user, ["fintech"])

    assert Industry.objects.for_user(user).count() == 1


def test_a_division_name_fits_the_column(user):
    """Sixty characters was generous for a word somebody types and too short for a
    classification: the longest division here is ninety-two.
    """
    longest = max((name for _code, name in industries.divisions()), key=len)

    industry = Industry.named(user, [longest])[0]

    assert industry.name == longest
    assert len(longest) > 60


# ------------------------------------------------------------------ and the licence


def test_the_file_records_who_published_it_and_on_what_terms():
    """An AGPL project shipping somebody else's data file needs the answer in writing."""
    document = industries.classification()

    assert document["licence"] == "CC BY 4.0"
    assert "Eurostat" in document["publisher"]
    assert document["source"].startswith("https://")


def test_the_attribution_travels_with_the_data():
    from pathlib import Path

    notice = Path("src/postulo/jobs/data/LICENCE.md").read_text(encoding="utf-8")

    assert "CC BY 4.0" in notice
    assert "2011/833/EU" in notice
    assert "© European Union" in notice
    assert "Changes made" in notice, "which CC BY requires to be indicated"


def test_the_data_is_valid_json_and_not_a_python_literal():
    """A data file with its revision recorded, rather than a tuple in a module — which is
    what makes replacing it in four years a replacement rather than a rewrite.
    """
    from pathlib import Path

    raw = Path("src/postulo/jobs/data/nace-2.1.json").read_text(encoding="utf-8")

    assert json.loads(raw)["revision"] == "NACE Rev. 2.1"
