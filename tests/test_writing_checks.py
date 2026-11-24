"""What reads a document's own words: the `writing-checker` kind and its mechanism (#1258).

A checker is handed plain data and answers with plain data. Postulo decides who is asked
(only offline checkers, only the enabled ones), checks every finding before it is drawn, and
leaves out a checker that raises. None of it may put a letter's words in the log.
"""

from __future__ import annotations

import itertools
import logging

import pytest

from postulo.core import writing_checks
from postulo.core.memo import forget_decisions
from postulo.core.writing_checks import MAX_FINDINGS, MAX_PASSAGE, Document, Passage, Placeholder
from postulo.plugins import api, base, kinds, policy, registry
from postulo.plugins.models import PluginPolicy
from tests import writing

SECRET = "Dear Dr Kleiner, my salary was 61234"


@pytest.fixture(autouse=True)
def _fresh_registry(monkeypatch):
    """A test that adds a plugin must not leave the registry's cache holding it; and the
    mechanism is tested beside the one checker that is always on, whatever else ships."""
    from postulo.plugins.placeholder_checks import PlaceholderChecks

    writing.only(monkeypatch, PlaceholderChecks)
    yield
    writing.forget()


def document(body=SECRET, subject="About the role", **changes):
    values = dict(  # noqa: C408
        kind="letter",
        variant="cover",
        language="en-GB",
        direction="ltr",
        passages=(
            Passage(key="subject", text=subject, language="en-GB", role="heading"),
            Passage(key="body", text=body, language="en-GB"),
        ),
    )
    values.update(changes)
    return Document(**values)


# ------------------------------------------------------------------------ the kind


def test_the_surface_holds_what_a_writing_checker_needs():
    for name in (
        "WritingChecker",
        "WritingCheckerPlugin",
        "WritingDocument",
        "WritingFinding",
        "WritingPassage",
        "WritingPlaceholder",
    ):
        assert name in api.__all__, name
    assert api.WritingChecker is writing_checks.Checker
    assert api.WritingDocument is writing_checks.Document
    assert api.WritingFinding is writing_checks.Finding
    assert api.WritingPassage is writing_checks.Passage
    assert api.WritingPlaceholder is writing_checks.Placeholder
    assert api.WritingCheckerPlugin is base.WritingCheckerPlugin


def test_the_kind_is_governed_has_a_group_and_may_offer_sub_plugins():
    assert registry.GROUPS["writing-checker"] == "postulo.writing_checkers"
    assert "writing-checker" in policy.GOVERNED_KINDS
    assert "writing-checker" in base.SUB_PLUGIN_KINDS
    assert str(kinds.label_for("writing-checker")) == "Writing checks"
    assert kinds.tone_for("writing-checker") != "grey"


# ------------------------------------------------------------- what may be asked


@pytest.mark.parametrize(
    "bad",
    [
        {"key": ""},
        {"key": "Has Capitals"},
        {"key": "x" * 41},
        {"label": ""},
        {"documents": ()},
        {"documents": ("letter", "novel")},
        {"languages": ("",)},
        {"check": None},
        {"offline": False},  # dials out and names no host
        {"offline": False, "hosts": ("api.example.org",)},  # and does not say what it sends
        {"hosts": ("api.example.org",)},  # offline and names a host
    ],
)
def test_a_checker_that_cannot_be_used_says_why(bad):
    declared = writing.checker(**{"check": lambda d: [], **bad})

    assert writing_checks.checker_problems(declared)


def test_a_usable_checker_has_nothing_wrong_with_it():
    assert writing_checks.checker_problems(writing.checker(lambda d: [])) == []
    remote = writing.checker(
        lambda d: [], offline=False, hosts=("lt.example.org",), sends="The letter's text."
    )
    assert writing_checks.checker_problems(remote) == []
    assert writing_checks.checker_problems("not a checker")


def test_a_plugins_checker_is_asked_after_the_ones_postulo_ships(db, monkeypatch):
    writing.install(monkeypatch, writing.plugin("mine", writing.checker(lambda d: [])))

    keys = [checker.key for checker in writing_checks.checkers("letter")]

    assert keys == ["placeholder-checks", "test-checks"]
    assert writing_checks.checkers("cv") == [], "neither reads a CV"


def test_the_first_to_claim_a_key_keeps_it(db, monkeypatch, caplog):
    first = writing.checker(lambda d: [writing.finding(message="The first.")])
    second = writing.checker(lambda d: [writing.finding(message="The second.")])
    writing.install(monkeypatch, writing.plugin("one", first), writing.plugin("two", second))

    with caplog.at_level(logging.WARNING):
        said = [r.finding.message for r in writing_checks.found(document())]

    assert said == ["The first."]
    assert "offered twice" in caplog.text


def test_a_checker_that_cannot_be_used_is_left_out_and_logged(db, monkeypatch, caplog):
    broken = writing.checker(lambda d: [writing.finding()], documents=("novel",))
    writing.install(monkeypatch, writing.plugin("mine", broken, "not a checker"))

    with caplog.at_level(logging.WARNING):
        assert [c.key for c in writing_checks.checkers()] == ["placeholder-checks"]

    assert caplog.text.count("cannot be used") == 2


def test_a_plugin_switched_off_for_a_person_is_not_asked(user, monkeypatch):
    asked = []

    def check(handed):
        asked.append(handed)
        return [writing.finding()]

    writing.install(monkeypatch, writing.plugin("mine", writing.checker(check)))
    assert len(writing_checks.found(document(), user)) == 1

    PluginPolicy.objects.create(plugin="mine", person=None, state=PluginPolicy.State.FORCED_OFF)
    forget_decisions()
    asked.clear()

    assert writing_checks.found(document(), user) == []
    assert asked == [], "it was never handed the letter"


def test_a_checker_whose_sub_plugin_is_off_is_not_asked(user, monkeypatch):
    kept = writing.checker(lambda d: [writing.finding(check="kept")], key="kept")
    folded = writing.checker(
        lambda d: [writing.finding(check="folded")], key="folded", sub_plugin="extra"
    )
    mine = writing.plugin(
        "mine",
        kept,
        folded,
        sub_plugins=(api.SubPlugin(id="extra", label="Extra", default_on=False),),
    )
    writing.install(monkeypatch, mine)

    assert [r.finding.check for r in writing_checks.found(document(), user)] == ["kept"]

    PluginPolicy.objects.create(
        plugin="mine:extra", person=None, state=PluginPolicy.State.AVAILABLE
    )
    forget_decisions()

    assert [r.finding.check for r in writing_checks.found(document(), user)] == ["kept", "folded"]


def test_a_checker_is_asked_only_about_a_language_it_reads(db, monkeypatch):
    french = writing.checker(lambda d: [writing.finding(check="fr")], key="fr", languages=("fr",))
    english = writing.checker(lambda d: [writing.finding(check="en")], key="en", languages=("en",))
    writing.install(monkeypatch, writing.plugin("mine", french, english))

    assert [r.finding.check for r in writing_checks.found(document())] == ["en"]
    assert writing_checks.reads(french, "fr-CA") and not writing_checks.reads(french, "fro")


# ------------------------------------------------------- nothing leaves the instance


def test_a_checker_that_is_not_offline_is_never_asked_by_a_page(db, monkeypatch):
    asked = []

    def check(handed):
        asked.append(handed)
        return [writing.finding()]

    remote = writing.checker(
        check, offline=False, hosts=("lt.example.org",), sends="The letter's text."
    )
    writing.install(monkeypatch, writing.plugin("remote", remote))

    assert [c.key for c in writing_checks.checkers("letter")][-1] == "test-checks"
    assert writing_checks.found(document()) == []
    assert asked == []


def test_a_service_would_be_handed_the_text_and_none_of_the_facts():
    held = Placeholder(name="company", start=5, end=18, known=True, value="Black Mesa")
    whole = document(
        body="Dear {{ company }},",
        facts={"company": "Black Mesa", "contact": "Dr Kleiner"},
        passages=(Passage(key="body", text="Dear {{ company }},", language="en-GB",
                          placeholders=(held,)),),
    )  # fmt: skip

    bare = writing_checks.without_facts(whole)

    assert bare.facts == {}
    assert bare.passages[0].text == "Dear {{ company }},", "the words as typed"
    assert bare.passages[0].placeholders[0].value is None
    assert "Black Mesa" not in repr(bare) and "Kleiner" not in repr(bare)


# --------------------------------------------------------------- what may be drawn


@pytest.mark.parametrize(
    "bad,rule",
    [
        ({"check": "Not A Slug"}, "check"),
        ({"message": ""}, "message"),
        ({"message": "Two\nlines."}, "message"),
        ({"message": "x" * 301}, "message"),
        ({"severity": "catastrophe"}, "severity"),
        ({"passage": "appendix"}, "passage"),
        ({"start": -1}, "span"),
        ({"start": 5, "end": 4}, "span"),
        ({"end": 10_000}, "span"),
        ({"start": True}, "span"),
        ({"start": 1.0}, "span"),
        ({"passage": "", "start": 0, "end": 4}, "span"),
        ({"replacements": ("a",) * 6}, "replacements"),
        ({"replacements": ("x" * 501,)}, "replacements"),
        ({"replacements": (SECRET[:4],)}, "replacements"),  # what is already there
        ({"replacements": "Dear"}, "replacements"),  # a string is not a tuple of them
        ({"passage": "subject", "replacements": ("Two\nlines",)}, "replacements"),
    ],
)
def test_a_finding_that_breaks_a_rule_is_named_by_the_rule(bad, rule):
    assert writing_checks.problem_with(writing.finding(**bad), document()) == rule


def test_a_finding_that_keeps_the_rules_may_be_drawn():
    assert writing_checks.problem_with(writing.finding(), document()) == ""
    assert (
        writing_checks.problem_with(writing.finding(passage="", start=0, end=0), document()) == ""
    )
    between = writing.finding(start=4, end=4, replacements=(",",))
    assert writing_checks.problem_with(between, document()) == ""
    assert writing_checks.problem_with("a sentence", document()) == "type"


def test_a_replacement_for_a_span_too_long_to_put_back_is_refused():
    long = document(body="x" * 600)

    assert writing_checks.problem_with(writing.finding(end=501), long) == ""
    wide = writing.finding(end=501, replacements=("y",))
    assert writing_checks.problem_with(wide, long) == "replacements"


@pytest.mark.parametrize(
    "start,end,allowed",
    [
        (5, 18, True),  # the whole of it
        (0, 19, True),  # and what is round it
        (0, 5, True),  # none of it
        (5, 5, True),  # the place just before it
        (18, 18, True),  # and just after
        (5, 10, False),  # its first half
        (10, 19, False),  # its second, and the comma
        (9, 9, False),  # a place inside it
    ],
)
def test_a_span_takes_a_whole_placeholder_or_none_of_it(start, end, allowed):
    held = Placeholder(name="company", start=5, end=18, known=True)
    passage = Passage(
        key="body", text="Dear {{ company }},", language="en-GB", placeholders=(held,)
    )
    read = document(passages=(passage,))

    rule = writing_checks.problem_with(writing.finding(start=start, end=end), read)

    assert rule == ("" if allowed else "placeholder")


def test_a_finding_that_breaks_a_rule_is_dropped_and_the_log_quotes_nothing(
    db, monkeypatch, caplog
):
    def check(handed):
        yield writing.finding(message=f"{SECRET} is too long a salary.", end=10_000)
        yield writing.finding(message="This one is fine.")

    writing.install(monkeypatch, writing.plugin("mine", writing.checker(check)))

    with caplog.at_level(logging.DEBUG):
        said = [r.finding.message for r in writing_checks.found(document())]

    assert said == ["This one is fine."]
    assert "test-checks" in caplog.text and "'span'" in caplog.text
    assert "Kleiner" not in caplog.text and "61234" not in caplog.text


def test_a_checker_that_raises_is_left_out_and_the_log_quotes_nothing(db, monkeypatch, caplog):
    def broken(handed):
        raise RuntimeError(f"could not read: {handed.passages[1].text}")

    fine = writing.checker(lambda d: [writing.finding()], key="fine")
    writing.install(
        monkeypatch, writing.plugin("mine", writing.checker(broken, key="broken"), fine)
    )

    with caplog.at_level(logging.DEBUG):
        reported = writing_checks.found(document())

    assert [r.checker for r in reported] == ["fine"], "the list is drawn without it"
    assert "broken" in caplog.text and "RuntimeError" in caplog.text
    assert "Kleiner" not in caplog.text and "61234" not in caplog.text


def test_a_checker_that_answers_with_nonsense_is_left_out(db, monkeypatch):
    writing.install(monkeypatch, writing.plugin("mine", writing.checker(lambda d: 42)))

    assert writing_checks.found(document()) == []


def test_at_most_a_hundred_findings_are_taken_from_one_checker(db, monkeypatch):
    def endless(handed):
        return (writing.finding() for _ in itertools.count())

    writing.install(monkeypatch, writing.plugin("mine", writing.checker(endless)))

    assert len(writing_checks.found(document())) == MAX_FINDINGS


def test_a_passage_too_long_is_handed_to_nobody_and_the_list_can_say_so(db, monkeypatch):
    handed_over = []

    def check(handed):
        handed_over.append(handed)
        return [writing.finding(passage="body", start=0, end=1)]

    writing.install(monkeypatch, writing.plugin("mine", writing.checker(check)))
    long = document(body="x" * (MAX_PASSAGE + 1))

    assert writing_checks.found(long) == [], "a finding about a passage nobody was handed"
    assert [p.key for p in handed_over[0].passages] == ["subject"]
    assert writing_checks.unread(long) == ("body",)
    assert writing_checks.unread(document(body="x" * MAX_PASSAGE)) == ()


def test_what_is_reported_says_who_said_it_and_whether_postulo_ships_them(db, monkeypatch):
    writing.ship(
        monkeypatch, writing.plugin("ours", writing.checker(lambda d: [writing.finding()]))
    )
    theirs = writing.checker(lambda d: [writing.finding()], key="theirs", label="Their checks")
    writing.install(monkeypatch, writing.plugin("theirs", theirs))

    ours, other = writing_checks.found(document())

    assert (ours.checker, ours.plugin, ours.shipped) == ("test-checks", "ours", True)
    assert (other.checker, other.label, other.plugin, other.shipped) == (
        "theirs",
        "Their checks",
        "theirs",
        False,
    )
