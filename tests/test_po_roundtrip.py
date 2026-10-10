"""One format, two writers: Weblate and `scripts/messages.py` leave each other's files alone.

Every translation is made in Weblate and comes back as a pull request (#706, #349). That only
works if what Weblate writes is what `extract --check` expects, and what `extract` writes is
what Weblate would write itself. Weblate writes through translate-toolkit, at the version
pinned in the dev group, configured on every component with a line width of 65535 and
without its own ``X-Generator``.

So these tests do to a real catalogue what saving in Weblate does -- every entry re-quoted,
drafts approved, a machine draft added to an empty slot, the header rewritten in Weblate's
spelling -- and then ask the tool to extract it again: nothing may change. A layout the two
disagree on fails here, rather than on the first of Weblate's pull requests.

Three catalogues of different shapes on every run; every catalogue of every set under
``-m release``.
"""

from __future__ import annotations

from pathlib import Path

import pytest

translate_storage = pytest.importorskip("translate.storage.pypo")

REPO = Path(__file__).resolve().parents[1]

#: What `po_line_wrap` is set to on every Weblate component.
WIDTH = 65535

#: A language of two forms, one of six, and one whose Weblate default is spelt differently.
SAMPLES = ["fr-FR", "ar", "pt-BR"]


@pytest.fixture(scope="module")
def tool():
    from postulo.core import messages_tool

    messages_tool.use(REPO)
    return messages_tool


@pytest.fixture(scope="module")
def extracted(tool):
    """Each set's messages as the source has them, extracted once for the module."""
    cache: dict[str, dict] = {}

    def of(subject):
        if subject.name not in cache:
            cache[subject.name] = tool.extract_all(subject)
        return cache[subject.name]

    return of


def weblate_save(data: bytes, code: str, *, approve: bool) -> bytes:
    """What a speaker saving every string in Weblate leaves in the file."""
    store = translate_storage.pofile(data, width=WIDTH)
    filled = False
    for unit in store.units:
        if unit.isheader():
            continue
        draft = unit.isfuzzy()
        if unit.hasplural():
            forms = list(unit.target.strings)
            unit.target = [form + "⁣" for form in forms]
            unit.target = forms
        else:
            text = unit.target
            if not text and not filled:
                # The machine translation add-on fills an empty slot as needing editing.
                unit.target = f"MT: {unit.source}"
                unit.markfuzzy(True)
                filled = True
                continue
            unit.target = text + "⁣"
            unit.target = text
        unit.markfuzzy(False if approve else draft)
    store.updateheader(
        add=True,
        PO_Revision_Date="2026-10-10 12:00+0000",
        Last_Translator="A Speaker <speaker@example.org>",
        Language_Team=(
            f"{code} <https://translate.tiagoagueda.com/projects/postulo/postulo/"
            f"{code.replace('-', '_')}/>"
        ),
    )
    return bytes(store)


def unchanged_by_extract(tool, extracted, subject, code: str, data: bytes) -> bool:
    text = data.decode("utf-8")
    again = tool.dump(tool.merge(extracted(subject), tool.parse(text), code), code, subject)
    return again == text


def check(tool, extracted, subject, code: str) -> None:
    path = tool.po_path(code, subject)
    original = path.read_bytes()
    for approve in (False, True):
        saved = weblate_save(original, code, approve=approve)
        assert saved != original, "the save is meant to change something"
        assert unchanged_by_extract(tool, extracted, subject, code, saved), (
            f"{path.relative_to(REPO)}: `extract` would rewrite what Weblate saved "
            f"({'approved' if approve else 'drafts kept'})"
        )


@pytest.mark.parametrize("code", SAMPLES)
def test_what_weblate_saves_is_what_extract_writes(code, tool, extracted):
    check(tool, extracted, tool.core_set(), code)


def test_a_plugin_sets_catalogue_too(tool, extracted):
    subject = next(s for s in tool.catalogue_sets() if not s.is_core)
    check(tool, extracted, subject, "fr-FR")


def test_an_untouched_catalogue_is_written_back_byte_for_byte(tool):
    """translate-toolkit keeps an entry it did not change exactly as it read it."""
    path = tool.po_path("fr-FR")
    original = path.read_bytes()
    store = translate_storage.pofile(original, width=WIDTH)
    assert bytes(store) == original


def test_weblates_spelling_of_the_plural_rule_is_the_same_rule(tool):
    """Weblate writes ``n > 1`` where the table says ``(n > 1)``: the same rule, kept."""
    ours = tool.PLURAL_FORMS["fr-FR"]
    theirs = "nplurals=2; plural=n > 1;"
    assert tool.same_plural_rule(ours, theirs)
    header = tool.header_for("fr-FR", {"Plural-Forms": theirs})
    assert header["Plural-Forms"] == theirs
    assert not tool.same_plural_rule(ours, "nplurals=2; plural=(n != 1);")


def test_approving_in_weblate_makes_a_draft_reviewed(tool):
    """The review step, end to end: the flag Weblate clears is the one the counts read."""
    path = tool.po_path("fr-FR")
    original = path.read_bytes()
    before = tool.stats_for(tool.parse(original.decode("utf-8")))
    after = tool.stats_for(
        tool.parse(weblate_save(original, "fr-FR", approve=True).decode("utf-8"))
    )
    assert before["drafts"], "the sample has drafts to approve"
    # Every draft approved. The save also drafts one empty slot by machine, when there is
    # one -- whether there is depends on how far Weblate has got, which is not this test's.
    added = after["translated"] - before["translated"]
    assert added in (0, 1)
    assert after["drafts"] == added
    assert after["reviewed"] == before["translated"]


@pytest.mark.release
def test_every_catalogue_survives_a_weblate_save(tool, extracted):
    for subject in tool.catalogue_sets():
        for code in tool.translated_languages():
            check(tool, extracted, subject, code)
