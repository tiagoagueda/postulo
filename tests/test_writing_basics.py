"""Writing basics: the bundled, local reference writing checker (#1258).

Each check over plain text through the published `writing_findings`, each where it does not
apply, the contract check run on the plugin, and the plugin off on an instance that has just
migrated -- so that with it off a letter's page is what it was.
"""

from __future__ import annotations

import pytest

from postulo.core import writing_checks
from postulo.core.memo import forget_decisions
from postulo.documents import checking
from postulo.documents.models import CoverLetter
from postulo.plugins import policy, registry
from postulo.plugins.models import PluginPolicy
from postulo.plugins.testing import assert_is_a_writing_checker, writing_findings
from postulo.plugins.writing_basics import WRITING_BASICS, WritingBasics

FACTS = {
    "company": "Aperture Science",
    "company_names": "Aperture Science\nASI",
    "role": "Test engineer",
    "location": "",
    "contact": "",
    "name": "A. Person",
}


#: Words no two of which are alike, for text that is long without repeating itself.
VOCABULARY = ("alpha", "beta", "gamma", "delta", "epsilon", "zeta", "eta")


def prose(count: int) -> str:
    """``count`` words, none of them the word before it."""
    return " ".join(VOCABULARY[i % len(VOCABULARY)] for i in range(count))


def found(text, **kwargs) -> list[dict]:
    return writing_findings(WritingBasics(), text, **kwargs)


def checks(text, **kwargs) -> list[str]:
    return [entry["check"] for entry in found(text, **kwargs)]


def test_it_keeps_the_contract():
    assert_is_a_writing_checker(WritingBasics())


def test_it_ships_as_a_writing_checker_with_five_switches(db):
    plugin = registry.find_plugin("writing-checker", WRITING_BASICS)

    assert plugin is not None and not policy.is_required(WRITING_BASICS)
    assert [sub.id for sub in plugin.sub_plugins] == [
        "names",
        "starter-notes",
        "length",
        "repetition",
        "sentences",
    ]
    assert all(checker.offline and not checker.hosts for checker in plugin.checkers)
    assert {checker.sub_plugin for checker in plugin.checkers} == {
        sub.id for sub in plugin.sub_plugins
    }


# ----------------------------------------------------------------------------- names


def test_a_letter_that_names_neither_company_nor_role_is_told_so():
    said = found("Dear hiring team, I would like the job.", facts=FACTS)

    assert [entry["check"] for entry in said] == ["company-not-named", "role-not-named"]
    assert all(entry["severity"] == "hint" and entry["passage"] == "" for entry in said)
    assert "“Aperture Science”" in said[0]["message"]


@pytest.mark.parametrize(
    "text",
    [
        "Dear {{ company }}, about the {{ role }}.",
        "Dear aperture science, about the test engineer post.",
        "At ASI, as a Test Engineer.",
    ],
)
def test_a_letter_that_names_them_any_way_is_not(text):
    assert checks(text, facts=FACTS) == []


def test_with_no_application_or_for_a_follow_up_nobody_is_asked_for():
    assert "company-not-named" not in checks("Dear team.")
    assert checks("Thank you for your time.", variant="follow_up", facts=FACTS) == []


# --------------------------------------------------------------------- starter notes


def test_a_note_left_from_the_starter_is_pointed_at():
    text = "Dear {{ company }},\n\n[Why this role, in a sentence.]\n"

    notes = [e for e in found(text, subject="[subject]") if e["check"] == "starter-note"]

    assert [e["text"] for e in notes] == ["[subject]", "[Why this role, in a sentence.]"]
    assert [e["passage"] for e in notes] == ["subject", "body"]
    assert all(e["replacements"] == () for e in notes), "only the person knows what goes there"


def test_a_placeholder_is_not_a_note():
    assert "starter-note" not in checks("Dear {{ company }}, {{ name }}")


# ---------------------------------------------------------------------------- length


def test_a_letter_longer_than_its_kind_is_told_its_length():
    text = prose(401)

    (entry,) = [e for e in found(text) if e["check"] == "long-for-its-kind"]

    assert "401 words" in entry["message"] and "400" in entry["message"]
    assert "long-for-its-kind" not in checks(text, variant="motivation")
    assert "long-for-its-kind" in checks(prose(151), variant="follow_up")


def test_a_letter_in_a_script_without_spaces_is_counted_in_characters():
    text = "我想申请这个职位" * 101  # 808 characters, and one "word" to a regular expression

    (entry,) = [e for e in found(text, language="zh-Hans") if e["check"] == "long-for-its-kind"]

    assert "808 characters" in entry["message"]


# ------------------------------------------------------------------------ repetition


def test_the_word_after_a_pair_is_compared_too():
    assert [e["text"] for e in found("so the the end")] == ["the the"]
    assert len(found("I led the the the team")) == 2


def test_a_word_twice_in_a_row_is_offered_once():
    (entry,) = found("I have led the The team.")

    assert entry["check"] == "repeated-word" and entry["text"] == "the The"
    assert entry["replacements"] == ("the",)


@pytest.mark.parametrize(
    "text",
    ["the\nthe", "11 11", "{{ name }} {{ name }}", "the theme", "café cafés"],
)
def test_what_only_looks_repeated_is_left_alone(text):
    assert "repeated-word" not in checks(text)


def test_several_spaces_between_words_are_offered_as_one():
    (entry,) = found("Dear  team,\n    indented is fine.")

    assert entry["check"] == "doubled-space" and entry["text"] == "  "
    assert entry["replacements"] == (" ",) and "“Dear”" in entry["message"]


def test_a_right_to_left_letter_is_read_the_same_way():
    said = found("أنا أنا مهندس", language="ar")

    assert [entry["check"] for entry in said] == ["repeated-word"]
    assert said[0]["replacements"] == ("أنا",)


def test_a_script_without_spaces_has_no_repeated_words():
    assert checks("我我想申请", language="zh") == []


# ------------------------------------------------------------------------- sentences


def test_a_very_long_sentence_is_pointed_at_and_a_short_one_is_not():
    long = prose(41) + "."
    text = f"A short one. {long} Another short one؟ And a last."

    (entry,) = found(text)

    assert entry["check"] == "long-sentence" and entry["text"] == long
    assert "41 words" in entry["message"]
    assert checks(prose(40) + ".") == []


def test_a_line_ends_a_sentence_too():
    assert checks((prose(30) + "\n") * 2) == []


# ------------------------------------------------------------- off, and switched on


def test_it_is_off_for_the_instance_after_migrating(user):
    """Style hints are opinions: an instance offers them when its administrator says so."""
    row = PluginPolicy.objects.get(plugin=WRITING_BASICS, person=None)

    assert row.state == PluginPolicy.State.FORCED_OFF
    assert not policy.decide(WRITING_BASICS, user).on
    keys = [checker.key for checker in writing_checks.checkers("letter", user)]
    assert keys == ["placeholder-checks"]


def test_switched_off_a_letters_page_shows_none_of_its_hints(client, user):
    letter = CoverLetter.objects.create(owner=user, name="L", body="I led the the team.")
    client.force_login(user)

    page = client.get(letter.get_absolute_url()).content.decode()

    assert "repeated-word" not in page and "Nothing to point out." in page


def test_switched_on_its_hints_join_the_one_list_and_one_sort_can_be_off(client, user):
    PluginPolicy.objects.filter(plugin=WRITING_BASICS, person=None).delete()
    PluginPolicy.objects.create(
        plugin=f"{WRITING_BASICS}:sentences", person=None, state=PluginPolicy.State.FORCED_OFF
    )
    forget_decisions()
    long = prose(45) + "."
    letter = CoverLetter.objects.create(owner=user, name="L", body=f"I led the the team. {long}")
    client.force_login(user)

    page = client.get(letter.get_absolute_url()).content.decode()
    listing = checking.letter_findings(letter, None, user)

    assert page.count('id="writing-findings"') == 1
    assert 'data-check="repeated-word"' in page
    assert 'data-check="long-sentence"' not in page, "that one sort is off"
    assert listing.problems == () and len(listing.hints) == 1
    assert checking.blocking(listing) == (), "a hint never stands before freezing"
