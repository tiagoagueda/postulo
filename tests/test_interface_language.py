"""What the interface does outside English, where nothing in a test suite goes by default.

Four things were being decided in English and applied to sixty-nine languages (#225): which
plural to use, which language the password advice is in, how a date is written, and where
the pieces of a sentence go. Each one is invisible to anybody reading Postulo in English,
which is why each one had lasted, and each one is the sort of thing that gets worse with
every language #70 to #72 adds rather than better.

The date half of this has a second guard beside it: `test_template_lint.py` rejects a date
format written out in a template or a view, so the seventy-three that were there do not come
back one page at a time.
"""

from __future__ import annotations

import datetime as dt
import json
import re
from pathlib import Path

import pytest
from django.urls import reverse
from django.utils import formats, translation

from postulo.core import languages
from postulo.core import messages_tool as tool
from postulo.core.templatetags import postulo as tags

pytestmark = pytest.mark.django_db

APP_JS = Path(__file__).resolve().parents[1] / "src" / "postulo" / "static" / "js" / "app.js"


@pytest.fixture(scope="module")
def messages() -> dict[tuple[str | None, str], object]:
    """Every string Postulo's own catalogue is asked to carry, read out of the source."""
    subject = next(s for s in tool.catalogue_sets() if s.name == "postulo")
    return tool.extract_all(subject)


@pytest.fixture(scope="module")
def msgids(messages) -> set[str]:
    return {msgid for _context, msgid in messages}


# -------------------------------------------------- a plural chosen where the rules are


def test_a_sentence_for_every_count_a_page_can_reach():
    """The bulk bar's whole answer, written out by the server before the page is sent."""
    counts = json.loads(tags.ticked_counts(range(50)))
    assert len(counts) == 51, "one for each row, and index 0 for no rows"
    assert counts[0] == "", "no ticks is the bar's own sentence, not one of these"
    assert counts[1] == "One row ticked."
    assert counts[2] == "2 rows ticked."
    assert counts[50] == "50 rows ticked."


def test_the_count_is_asked_for_every_number_not_for_one_and_many(monkeypatch):
    """The fix, stated as the thing it changed.

    Thirteen of the languages Postulo offers disagree with `n === 1`. Polish has a form for
    2 to 4 and another for 5 and up; Ukrainian's first form covers 21 and 31 as well as 1;
    Welsh has six. None of that is knowable in the browser, and none of it has to be: the
    plural rule is evaluated once per possible count, here, where gettext knows it.
    """
    asked: list[int] = []

    def counting(singular: str, plural: str, number: int) -> str:
        asked.append(number)
        return f"form for {number}"

    monkeypatch.setattr(tags, "ngettext", counting)
    counts = json.loads(tags.ticked_counts(5))
    assert asked == [1, 2, 3, 4, 5]
    assert counts[3] == "form for 3"


def test_the_page_carries_the_sentences_and_the_script_only_counts(client, user):
    from postulo.applications.models import Application
    from postulo.core.models import Tag
    from postulo.jobs.models import Company, JobPosting

    company = Company.objects.create(owner=user, name="Aperture Science")
    for index in range(3):
        posting = JobPosting.objects.create(owner=user, company=company, title=f"Role {index}")
        Application.objects.create(owner=user, posting=posting)
    # The bar is only drawn where there is something to apply in bulk.
    Tag.objects.create(owner=user, name="Dream job")
    client.force_login(user)

    html = client.get(reverse("applications:list"), {"view": "table"}).content.decode()
    attribute = re.search(r'data-bulk-counts="([^"]*)"', html)
    assert attribute, "the bulk bar carries the sentences"
    counts = json.loads(attribute.group(1).replace("&quot;", '"').replace("&amp;", "&"))
    assert len(counts) == 4, "three rows on the page, and index 0"
    assert counts[1] == "One row ticked." and counts[3] == "3 rows ticked."
    assert "data-bulk-one" not in html and "data-bulk-many" not in html


def test_the_script_no_longer_holds_a_plural_rule():
    source = APP_JS.read_text(encoding="utf-8")
    assert "ticked === 1" not in source, "the English plural rule, applied to every language"
    assert "bulkCounts" in source, "it indexes the sentences the page wrote out"


def test_the_bulk_count_is_one_plural_entry_now(messages):
    """One entry with two forms, rather than two entries a translator cannot connect.

    A translator of the old pair had no way to add a third form, because there was nowhere
    to put it. This is the same English, asked for in a way that has room for the answer.
    """
    entry = messages[None, "One row ticked."]
    assert entry.plural == "%(count)s rows ticked."


# --------------------------------------- advice in a language the reader actually reads


def test_the_meter_says_which_language_its_advice_is_in(client, settings):
    settings.POSTULO_REGISTRATION_OPEN = True
    html = client.get(reverse("account_signup")).content.decode()
    assert 'data-advice-language="en"' in html, "the vendored zxcvbn pack, named on the page"
    assert "js/vendor/zxcvbn/language-en" in html


def test_the_advice_is_shown_only_where_the_pack_and_the_page_agree():
    source = APP_JS.read_text(encoding="utf-8")
    block = source.split("var pack = ")[1].split("});")[0]
    assert "document.documentElement.lang" in block, "the language the page is being read in"
    assert "pack === reading" in block, (
        "zxcvbn's warning is the English pack's; appended to a translated strength word it "
        "reads as 'Fort - Add another word or two' to a French reader"
    )


def test_only_the_strength_word_is_announced(client, settings):
    """A live region rewritten on every keystroke is read out on every keystroke.

    The word is what changes meaningfully, and it changes five times at most. The advice
    sits beside the region rather than inside it, so it is there to be read and not to
    interrupt somebody halfway through typing a password.
    """
    settings.POSTULO_REGISTRATION_OPEN = True
    html = client.get(reverse("account_signup")).content.decode()
    word = re.search(r"<span[^>]*data-word>", html)
    assert word and 'role="status"' in word.group(0) and 'aria-live="polite"' in word.group(0)
    suggestion = re.search(r"<span[^>]*data-suggestion>", html)
    assert suggestion and "aria-live" not in suggestion.group(0)

    source = APP_JS.read_text(encoding="utf-8")
    assert "if (word.textContent !== saying) {" in source, "written only when it changes"


# ------------------------------------------ a date written the way the language writes it

NAMED = (
    "DATE_FORMAT",
    "DATETIME_FORMAT",
    "SHORT_DATE_FORMAT",
    "SHORT_DATETIME_FORMAT",
    "TIME_FORMAT",
    "YEAR_MONTH_FORMAT",
    "MONTH_DAY_FORMAT",
)


@pytest.mark.parametrize("code", [code for code, _name in languages.LANGUAGES])
def test_every_named_format_answers_in_every_language_offered(code: str):
    """The reason only Django's own names are used, proved over the list rather than a list.

    ``get_format`` hands back a name it does not recognise, so a format Postulo invented and
    gave to sixty-seven locales prints the literal word to a reader of the sixty-eighth. The
    stock names cannot do that: a locale that defines one wins, a locale that does not falls
    through to the settings, and the settings always answer. The parametrisation reads
    `languages.LANGUAGES`, so a language added for #70, #71 or #72 is covered the day it is
    added and not the day somebody remembers to extend a list here.
    """
    with translation.override(code):
        for name in NAMED:
            answer = formats.get_format(name)
            assert isinstance(answer, str) and answer, f"{code}: {name} is {answer!r}"
            assert answer != name, (
                f"{code}: {name} resolved to its own name, which is what a reader would see"
            )


def test_a_name_nothing_defines_is_printed_to_the_reader():
    """The trap the test above exists for, shown rather than described."""
    assert formats.get_format("SHORT_MONTH_DATE_FORMAT") == "SHORT_MONTH_DATE_FORMAT"


def test_the_order_and_the_month_come_from_the_language():
    """Hungarian puts the year first and Lithuanian marks it; neither was ever going to
    survive a template that wrote ``j M Y`` by hand."""
    with translation.override("hu"):
        assert formats.get_format("DATE_FORMAT") == "Y. F j."
    with translation.override("lt"):
        assert formats.get_format("DATE_FORMAT").startswith("Y")


def test_british_english_keeps_the_clock_the_interface_was_written_on():
    """Django's ``en_GB`` says 2.30 p.m.; every template Postulo has shipped said 14:30.

    The one locale Postulo corrects, and it corrects it in its own format module rather than
    by writing ``H:i`` into ninety templates again.
    """
    with translation.override("en-gb"):
        assert formats.get_format("TIME_FORMAT") == "H:i"
        assert formats.get_format("DATETIME_FORMAT") == "j M Y, H:i"
        assert formats.get_format("DATE_FORMAT") == "j M Y", "Django's own, left alone"


def test_a_language_django_has_no_formats_for_falls_back_to_the_interfaces_own():
    """Thirty of the sixty-nine. Without the settings below them they would land on Django's
    American default -- ``N j, Y`` -- which is neither their convention nor the one they are
    reading today."""
    from django.conf import settings

    assert settings.DATE_FORMAT == "j M Y"
    with translation.override("wo"):
        assert formats.get_format("DATE_FORMAT") == "j M Y"


def test_the_filter_writes_the_date_the_way_the_reader_s_language_does():
    """The whole mechanism in three lines: one template, three languages, three orders."""
    from django.template import Context, Template

    when = dt.datetime(2026, 9, 16, 14, 30, tzinfo=dt.UTC)
    written = {}
    for code in ("en-gb", "hu", "lt"):
        with translation.override(code):
            written[code] = Template('{{ d|date:"DATE_FORMAT" }}').render(Context({"d": when}))
    assert written["en-gb"] == "16 Sep 2026"
    assert written["hu"].startswith("2026.") and written["hu"].endswith("16."), written["hu"]
    assert written["lt"].startswith("2026 m."), written["lt"]


def test_a_page_writes_its_dates_in_the_reader_s_language(client, user):
    """And the same thing through a real view, so the filter is reached the way a reader
    reaches it: through the profile's language and the middleware that activates it."""
    from postulo.accounts.models import Profile
    from postulo.api.models import ApiToken

    ApiToken.issue(user, "Agent", scopes=("read",))
    Profile.objects.filter(user=user).update(language="hu")
    client.force_login(user)

    html = client.get(reverse("api:token_list")).content.decode()
    assert re.search(r"\d{4}\.\s*\w+\s*\d+\.", html), (
        "Hungarian writes 2026. szeptember 16.; the page used to write 16 Sep 2026"
    )


def test_a_percentage_is_one_string_the_language_can_rearrange(msgids):
    """French writes 42 %, Turkish writes %42, and ``{{ share }}%`` writes neither."""
    assert "%(share)s%%" in msgids
    assert tags.percent("42") == "42%"


def test_the_sign_goes_where_the_catalogue_puts_it(monkeypatch):
    monkeypatch.setattr(tags, "gettext", lambda msgid: "%%%(share)s")
    assert tags.percent("42") == "%42"


# ------------------------------------------------- sentences, rather than pieces of them


def test_a_token_says_what_happened_and_when_in_one_clause(messages, msgids, client, user):
    """ "created" and a date beside it is a translator being handed half a clause.

    A verb in front of a date is not a word plus a date in every language, and nothing
    promised the date would stay on the right of it.
    """
    for whole in ("Created %(date)s", "Expires %(date)s", "Expired %(date)s"):
        assert whole in msgids
    assert messages[None, "Last used %(when)s"]
    assert "created" not in msgids and "expired" not in msgids
    assert "never used" not in msgids, "a standalone lower-case fragment among sentences"

    from postulo.api.models import ApiToken

    ApiToken.issue(user, "Agent", scopes=("read",))
    client.force_login(user)
    html = client.get(reverse("api:token_list")).content.decode()
    assert re.search(r"Created \d{1,2} \w{3} \d{4}", html)
    assert "Never used" in html


def test_the_board_hands_over_the_sentence_with_its_link_inside_it(msgids):
    """The clause used to end at a semicolon, with the link's words translated on their own
    and the full stop written in the template. A translator could not move the link, could
    not see what the clause was leading to, and could not end the sentence anywhere else."""
    sentence = next(m for m in msgids if m.startswith("One settled application"))
    assert '<a href="%(url)s"' in sentence and sentence.rstrip().endswith("</a>.")
    assert "see them in the table" not in msgids


def test_the_unit_after_the_number_agrees_with_it(messages, client, user):
    """ "days" on its own has no singular to offer at 1, and no second plural for Polish."""
    entry = messages[None, "day"]
    assert entry.plural == "days"

    from postulo.accounts.models import Profile

    client.force_login(user)
    Profile.objects.filter(user=user).update(quiet_after_days=1)
    assert ">\n          day\n        </p>" in _appearance(client)
    Profile.objects.filter(user=user).update(quiet_after_days=14)
    assert ">\n          days\n        </p>" in _appearance(client)


def _appearance(client) -> str:
    return client.get(reverse("settings:appearance")).content.decode()
