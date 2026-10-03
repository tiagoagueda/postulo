"""A telephone number is read against its country's numbering plan (#304).

`core/phones.py` used to put a dialling code in front of whatever was typed, strip every
leading zero, and decide nothing else: a number a digit short was stored without a word,
and Italy's zero, which is part of the number, was stripped with the rest. It carries the
plans now. What cannot exist for its country is refused with the reason; what the plan
cannot place is kept, with a warning; a number stored before any of this is marked and left
exactly as it is until somebody changes it; and a number is grouped the way its own country
writes it wherever it is shown.
"""

from __future__ import annotations

import ast
import json
import re
import subprocess
import sys
import zipfile
from pathlib import Path

import pytest
from django.urls import reverse
from django.utils import translation

from postulo.core import phone_field, phone_numbers, phones
from postulo.core.models import PhoneNumber

pytestmark = pytest.mark.django_db

ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "src" / "postulo"


@pytest.fixture
def contact(user):
    from postulo.jobs.models import Company, Contact

    company = Company.objects.create(owner=user, name="Aperture")
    return Contact.objects.create(owner=user, company=company, name="Cave Johnson")


@pytest.fixture
def single(user):
    """A person who has switched *Several telephone numbers* off for themselves."""
    from postulo.plugins.models import PluginPolicy
    from postulo.plugins.phone_numbers import PHONE_NUMBERS

    PluginPolicy.objects.create(
        plugin=PHONE_NUMBERS, person=user, state=PluginPolicy.State.FORCED_OFF
    )
    return user


def keep(holder, owner, value, *, kind="", label="", primary=True) -> PhoneNumber:
    """One number as it sits in the table, however it got there: no form, no check."""
    return PhoneNumber.objects.create(
        owner=owner, holder=holder, number=value, kind=kind, label=label, is_primary=primary
    )


def rows(*entries, initial=0, primary="phone_numbers-0", prefix="phone_numbers") -> dict:
    """The POST a block of telephone rows sends, management form and all."""
    data = {
        f"{prefix}-TOTAL_FORMS": str(len(entries)),
        f"{prefix}-INITIAL_FORMS": str(initial),
        f"{prefix}-MIN_NUM_FORMS": "0",
        f"{prefix}-MAX_NUM_FORMS": "1000",
        f"{prefix}-primary": primary,
    }
    for index, entry in enumerate(entries):
        for name, value in entry.items():
            data[f"{prefix}-{index}-{name}"] = value
    return data


def the_details(**more) -> dict:
    """What *Your details* posts besides its rows."""
    return {"first_name": "Alex", "last_name": "Morgan", "headline": "", "location": "", **more}


def field(country: str = "") -> phone_field.PhoneField:
    return phone_field.PhoneField(required=False, default_country=country)


def refusal(country: str, typed: str) -> str:
    """What the field says about a number it will not take."""
    from django.core.exceptions import ValidationError

    with pytest.raises(ValidationError) as refused:
        field().clean([country, typed])
    return " ".join(refused.value.messages)


# --------------------------------------------------------- putting the code in front


def test_frances_two_ways_of_writing_a_number_are_one_number():
    """The example the issue gives: with the trunk zero and without it."""
    assert phones.combine("06 12 34 56 78", "FR") == "+33612345678"
    assert phones.combine("6 12 34 56 78", "FR") == "+33612345678"
    assert phones.check("06 12 34 56 78", "FR").fine
    assert phones.check("6 12 34 56 78", "FR").fine


def test_a_number_with_a_plus_says_its_own_country():
    assert phones.combine("+33 6 12 34 56 78", "PT") == "+33612345678", "the chooser is ignored"
    assert phones.check("+33 6 12 34 56 78", "PT").fine
    assert phones.combine("+33 (0)6 12 34 56 78", "") == "+33612345678", "the bracketed zero goes"


def test_a_number_with_two_zeros_says_its_own_country_too():
    assert phones.combine("0033 6 12 34 56 78", "PT") == "+33612345678"
    assert phones.check("0033 6 12 34 56 78", "PT").fine
    assert phones.combine("00351912345678", "FR") == "+351912345678"


def test_a_country_with_no_trunk_prefix_has_nothing_removed():
    """Portugal dials nine digits and no zero. A zero in front is a tenth digit, not a
    prefix, and the plan says so where stripping zeros hid it."""
    assert phones.combine("912 345 678", "PT") == "+351912345678"
    assert phones.check("912 345 678", "PT").fine
    assert phones.check("0912 345 678", "PT").reason == phones.TOO_LONG
    assert phones.combine("612 34 56 78", "ES") == "+34612345678"


def test_italy_keeps_its_leading_zero():
    """It is part of the number. Stripped, `+39 06 6982 1234` became a number nobody has."""
    assert phones.combine("06 6982 1234", "IT") == "+390669821234"
    assert phones.combine("+39 06 6982 1234", "") == "+390669821234"
    assert phones.check("06 6982 1234", "IT").fine
    assert phones.combine("333 123 4567", "IT") == "+393331234567", "a mobile has none"


def test_the_national_prefix_is_whatever_the_country_uses():
    """Not always a zero: the United States dials a one, and it goes the same way."""
    assert phones.combine("1 (415) 555-2671", "US") == "+14155552671"
    assert phones.combine("(415) 555-2671", "US") == "+14155552671"
    assert phones.combine("0176 12345678", "DE") == "+4917612345678"


# ------------------------------------------------------------- what a number is


@pytest.mark.parametrize(
    ("typed", "country", "reason", "says"),
    [
        ("06 12 34", "FR", phones.TOO_SHORT, "too short for France"),
        ("06 12 34 56 78 90", "FR", phones.TOO_LONG, "too long for France"),
        ("512 345 678", "PT", phones.NOT_IN_USE, "not a number Portugal uses"),
        ("415 555 26", "US", phones.TOO_SHORT, "too short for the United States"),
        ("555-1234", "US", phones.NO_AREA_CODE, "has no area code"),
        ("+33 6 12 34", "PT", phones.TOO_SHORT, "too short for France"),
        ("+33", "", phones.TOO_SHORT, "too short for France"),
    ],
)
def test_what_cannot_exist_for_its_country_is_impossible(typed, country, reason, says):
    verdict = phones.check(typed, country)

    assert verdict.impossible and verdict.reason == reason
    assert says in verdict.message
    assert says in refusal(country, typed), "and the field refuses it in those words"


def test_a_national_number_with_no_country_is_not_guessed_at():
    """Thirty-one of the languages Postulo is read in start a row with no country (#456).
    A country put in front by guessing is a wrong number, so the error says what is
    missing and how to supply it."""
    verdict = phones.check("06 12 34 56 78", "")

    assert verdict.impossible and verdict.reason == phones.NO_COUNTRY
    assert "Choose the country" in refusal("", "06 12 34 56 78")
    assert phones.combine("06 12 34 56 78", "") == "06 12 34 56 78", "and nothing is made up"


def test_an_extension_is_refused_rather_than_run_into_the_digits():
    """`020 7946 0000 x123` used to be stored as `+442079460000123`."""
    verdict = phones.check("020 7946 0000 x123", "GB")

    assert verdict.impossible and verdict.reason == phones.EXTENSION
    assert "extension" in refusal("GB", "020 7946 0000 x123")


def test_an_extension_is_never_dropped_on_the_way_in():
    """An importer stores what `combine` returns without asking `check`, and the
    international form has no room for an extension: it comes back as it was written, and
    the page marks it, rather than arriving as a number with part of it gone."""
    assert phones.combine("+44 20 7946 0000 x123", "") == "+44 20 7946 0000 x123"
    assert phones.combine("020 7946 0000 x123", "GB") == "020 7946 0000 x123"
    assert phones.kept("+44 20 7946 0000 x123").reason == phones.EXTENSION
    assert phones.readable("+44 20 7946 0000 x123") == "+44 20 7946 0000 x123"


@pytest.mark.parametrize(
    ("typed", "country", "reason", "stored"),
    [
        ("+999 123 456 78", "", phones.UNKNOWN_CODE, "+999 123 456 78"),
        ("3949", "FR", phones.SHORT_NUMBER, "3949"),
        ("ask reception", "PT", phones.NOT_A_NUMBER, "ask reception"),
    ],
)
def test_what_the_plan_cannot_place_is_kept(typed, country, reason, stored):
    """A dialling code no country has, one of a country's short numbers, words. The plan
    has not said these are wrong; it has said nothing, and the only thing anybody has is
    still worth having. Kept as it was typed, which is what the sentence beside it says:
    a code no country has was once kept as its digits run together, and the sentence was
    not true of it."""
    verdict = phones.check(typed, country)

    assert verdict.unplaceable and verdict.reason == reason
    assert verdict.message, "and there is a sentence to put beside it"
    assert field().clean([country, typed]) == stored


def test_a_number_from_a_neighbour_under_the_same_code_is_fine():
    """Canada chosen beside a number in San Francisco is not a mistake worth refusing."""
    assert phones.check("415 555 2671", "CA").fine
    assert phones.combine("415 555 2671", "CA") == "+14155552671"


def test_a_reason_names_the_country_the_way_english_does_only_in_english():
    """The table's names are English in every language, and the article is English grammar."""
    verdict = phones.check("415 555 26", "US")

    assert "the United States" in verdict.message
    with translation.override("fr-FR"):
        assert "the United States" not in verdict.message
        assert "United States" in verdict.message


def test_a_stored_value_is_judged_as_it_is_kept():
    assert phones.kept("+33612345678").fine
    assert phones.kept("").fine
    assert phones.kept("+33123").impossible
    assert phones.kept("0612345678").reason == phones.AS_TYPED, "no country was ever kept"
    assert phones.kept("0612345678").unplaceable, "which is a fact about it, not an error"
    assert phones.kept("+99912345678").reason == phones.UNKNOWN_CODE


# ---------------------------------------------------------------- showing it


@pytest.mark.parametrize(
    ("stored", "shown"),
    [
        ("+33612345678", "+33 6 12 34 56 78"),
        ("+351912345678", "+351 912 345 678"),
        ("+442079460958", "+44 20 7946 0958"),
        ("+14155552671", "+1 415-555-2671"),
        ("+4930123456", "+49 30 123456"),
        ("+5511912345678", "+55 11 91234-5678"),
        ("+390669821234", "+39 06 6982 1234"),
    ],
)
def test_a_number_is_grouped_the_way_its_country_writes_it(stored, shown):
    assert phones.readable(stored) == shown
    assert re.sub(r"\D", "", phones.readable(stored)) == stored[1:], "the same digits, spaced"


def test_what_is_shown_never_has_other_digits_than_what_is_stored():
    """A trunk zero left after the code by an import from before the plans were read: the
    plan would write it without the zero, and a page showing that over a link that dials
    the stored digits would be showing two numbers."""
    assert phones.readable("+330612345678") == "+330612345678"
    assert phones.readable("0612345678") == "0612345678", "no country to group it by"
    assert phones.readable("+99912345678") == "+99912345678"


def test_the_country_is_read_from_the_number_and_not_only_from_its_code():
    """By the code alone every number under +1 was Antigua and Barbuda, the first of them
    in the alphabet, and that was the flag beside a number in California."""
    assert phones.country_of("+14155552671").code == "US"
    assert phones.country_of("+16045551234").code == "CA"
    assert phones.country_of("+33123").code == "FR", "an impossible number is still placed"
    assert phones.country_of("0612345678") is None


@pytest.mark.parametrize(
    ("stored", "country", "rest"),
    [
        ("+33612345678", "FR", "6 12 34 56 78"),
        ("+390669821234", "VA", "06 6982 1234"),
        ("+14155552671", "US", "415-555-2671"),
        ("+33123", "FR", "123"),
        ("0612345678", "", "0612345678"),
        ("+330612345678", "FR", "+330612345678"),
        ("+99912345678", "", "+99912345678"),
    ],
)
def test_a_field_shows_the_country_and_the_rest(stored, country, rest):
    """The rest is what the international form reads after the code, so the chooser and
    the box read together as the number. A value the plan would write differently is shown
    whole, and one with no country shows none: a chooser on France beside a number nobody
    said was French would read as a fact."""
    assert phones.split(stored) == (country, rest)


def test_typing_back_what_a_field_shows_gives_the_stored_number():
    """For a valid number of every kind in every country of the table: the rule that lets
    a field tell an untouched number from a changed one. Asked of the plan's own examples,
    so a metadata release that broke it for one country fails here."""
    import phonenumbers

    kinds = [
        value
        for name, value in vars(phonenumbers.PhoneNumberType).items()
        if name.isupper() and name != "UNKNOWN"
    ]
    tried = 0
    for code, _dialling, _name in phones.COUNTRIES:
        for kind in kinds:
            example = phonenumbers.example_number_for_type(code, kind)
            if example is None:
                continue
            stored = phonenumbers.format_number(example, phonenumbers.PhoneNumberFormat.E164)
            country, rest = phones.split(stored)
            tried += 1
            assert phones.combine(rest, country) == stored, (code, stored, country, rest)
            assert phones.check(rest, country).fine, (code, stored)
            assert phones.kept(stored).fine, (code, stored)
    assert tried > 800


def test_every_country_in_the_table_is_one_the_plans_know():
    import phonenumbers

    for code, dialling, name in phones.COUNTRIES:
        assert code in phonenumbers.SUPPORTED_REGIONS, f"{code} ({name})"
        assert str(phonenumbers.country_code_for_region(code)) == dialling, f"{code} ({name})"


# -------------------------------------------------------- the plans cost nothing to start


def test_the_plans_are_not_loaded_until_a_number_is_read():
    """Importing the package reads its tables, a quarter of a second on a cold start. A
    process that never meets a telephone number should not pay it, and the application's
    own modules -- the model that normalises a number included -- should not pay it for
    being imported."""
    script = (
        "import sys, django\n"
        "django.setup()\n"
        "from postulo.core import phones, phone_field, phone_numbers, models\n"
        "assert phones.normalise('+351 912 345 678') == '+351912345678'\n"
        "assert 'phonenumbers' not in sys.modules, 'loaded at start-up'\n"
        "assert phones.readable('+351912345678') == '+351 912 345 678'\n"
        "assert 'phonenumbers' in sys.modules\n"
    )
    done = subprocess.run(  # noqa: S603 - this interpreter, running a script written here
        [sys.executable, "-c", script],
        cwd=ROOT,
        capture_output=True,
        text=True,
        timeout=120,
        env={**__import__("os").environ, "DJANGO_SETTINGS_MODULE": "postulo.config.settings.test"},
    )
    assert done.returncode == 0, done.stderr


def test_only_one_module_talks_to_the_plans_and_only_inside_a_function():
    """`core/phones.py` is the one place `phonenumbers` is named, and never at the top of
    it: an import at module level is the start-up cost above, paid by everybody."""
    found = {}
    for path in sorted(SRC.rglob("*.py")):
        tree = ast.parse(path.read_text(encoding="utf-8"))
        top = {id(node) for node in tree.body}
        for node in ast.walk(tree):
            names = []
            if isinstance(node, ast.Import):
                names = [alias.name for alias in node.names]
            elif isinstance(node, ast.ImportFrom):
                names = [node.module or ""]
            if any(name.split(".")[0] == "phonenumbers" for name in names):
                found.setdefault(path.relative_to(SRC).as_posix(), []).append(id(node) in top)
    assert found == {"core/phones.py": [False]}, found


# ------------------------------------------------------------------ the field


def test_the_help_and_the_error_reach_the_number_box(user):
    """#416, at the level of the rows: `tests/test_assistive_names.py` holds the field."""
    holder = user.profile
    formset = phone_numbers.formset_for(
        holder,
        asked_by=user,
        data=rows({"kind": "", "label": "", "number_0": "FR", "number_1": "06 12"}),
    )

    assert not formset.is_valid()
    html = formset.forms[0]["number"].as_widget()
    box = re.search(r"<input[^>]*name=\"phone_numbers-0-number_1\"[^>]*>", html).group(0)
    assert 'aria-invalid="true"' in box
    assert "id_phone_numbers-0-number_helptext" in box and "id_phone_numbers-0-number_error" in box


def test_the_group_is_one_control_with_a_name_and_the_flag_inside_it(user):
    html = str(
        phone_numbers.formset_for(user.profile, default_country="PT", asked_by=user).forms[0][
            "number"
        ]
    )

    group = re.search(r'<div class="input-group"[^>]*>', html).group(0)
    assert 'role="group"' in group and 'aria-label="Telephone number"' in group
    inside = html[html.index(group) : html.index("</div>", html.index(group))]
    assert "data-phone-flag" in inside and 'data-flag="pt"' in inside, "the flag is in it"
    assert 'aria-label="Country the number is in"' in inside, "the chooser has its own name"
    assert inside.index("data-phone-flag") < inside.index("<select") < inside.index("<input")
    assert html.count("<select") == 1 and html.count("<input") == 1


# ---------------------------------------------------------------- the rows on a page


def test_an_impossible_number_is_refused_on_the_page_with_the_reason(client, user):
    client.force_login(user)

    response = client.post(
        reverse("accounts:profile"),
        the_details(**rows({"kind": "", "label": "", "number_0": "FR", "number_1": "06 12 34"})),
    )

    assert response.status_code == 200, "the page comes back"
    assert "That number is too short for France." in response.content.decode()
    assert not PhoneNumber.objects.exists()


def test_the_reason_is_in_the_readers_language(client, user):
    user.profile.language = "fr-FR"
    user.profile.save(update_fields=["language"])
    client.force_login(user)

    response = client.post(
        reverse("accounts:profile"),
        the_details(**rows({"kind": "", "label": "", "number_0": "FR", "number_1": "06 12 34"})),
    )

    html = response.content.decode()
    assert "That number is too short for France." not in html
    assert "trop court pour France" in html


def test_a_number_the_plan_cannot_place_saves_and_is_warned_about(client, user):
    client.force_login(user)

    response = client.post(
        reverse("accounts:profile"),
        the_details(**rows({"kind": "", "label": "", "number_0": "FR", "number_1": "3949"})),
    )

    assert response.status_code == 302
    assert PhoneNumber.objects.get().number == "3949", "kept as typed: it has no other form"
    html = client.get(reverse("accounts:profile")).content.decode()
    mark = re.search(r'<p id="id_phone_numbers-0-number_kept"[^>]*>(.*?)</p>', html, re.S)
    assert mark and 'data-phone-mark="unplaceable"' in mark.group(0)
    assert "Not checked" in mark.group(1) and "kept as it was typed" in mark.group(1)
    box = re.search(r'<input[^>]*name="phone_numbers-0-number_1"[^>]*>', html).group(0)
    assert "id_phone_numbers-0-number_kept" in box, "the warning describes the box"


def test_a_stored_impossible_number_is_marked_and_left_alone(client, user):
    """Recorded before the plans were read. It is drawn with a mark and a sentence, saving
    the page it sits on neither refuses nor rewrites it, and changing it is what checks it."""
    row = keep(user.profile, user, "+33123", kind="work")
    client.force_login(user)

    html = client.get(reverse("accounts:profile")).content.decode()
    mark = re.search(r'<p id="id_phone_numbers-0-number_kept"[^>]*>(.*?)</p>', html, re.S)
    assert mark and 'data-phone-mark="impossible"' in mark.group(0)
    assert "Not a possible number" in mark.group(1)
    assert "too short for France" in mark.group(1)
    assert "left as it is until you change it" in mark.group(1)

    untouched = {"id": row.pk, "kind": "mobile", "label": "", "number_0": "FR", "number_1": "123"}
    response = client.post(reverse("accounts:profile"), the_details(**rows(untouched, initial=1)))
    assert response.status_code == 302, "not refused: nobody touched the number"
    row.refresh_from_db()
    assert row.number == "+33123" and row.kind == "mobile", "the kind changed, the number did not"

    changed = {**untouched, "number_1": "1234"}
    response = client.post(reverse("accounts:profile"), the_details(**rows(changed, initial=1)))
    assert response.status_code == 200, "changed, so checked"
    assert "That number is too short for France." in response.content.decode()
    row.refresh_from_db()
    assert row.number == "+33123"

    mended = {**untouched, "number_1": "06 12 34 56 78"}
    response = client.post(reverse("accounts:profile"), the_details(**rows(mended, initial=1)))
    assert response.status_code == 302
    row.refresh_from_db()
    assert row.number == "+33612345678"
    assert "data-phone-mark" not in client.get(reverse("accounts:profile")).content.decode()


def test_a_stored_number_with_no_country_is_not_given_one_by_being_saved(client, user):
    """The chooser used to fall back to the reader's own country for such a number, and
    saving the page then put that country in front of it: a guess, kept as a fact."""
    user.profile.language = "fr-FR"
    user.profile.save(update_fields=["language"])
    row = keep(user.profile, user, "0612345678")
    client.force_login(user)

    html = client.get(reverse("accounts:profile")).content.decode()
    select = re.search(r'<select name="phone_numbers-0-number_0".*?</select>', html, re.S).group(0)
    assert not re.search(r'<option value="FR"[^>]* selected>', select), "no country is shown"
    assert 'data-phone-mark="unplaceable"' in html

    as_drawn = {"id": row.pk, "kind": "", "label": "", "number_0": "", "number_1": "0612345678"}
    response = client.post(reverse("accounts:profile"), the_details(**rows(as_drawn, initial=1)))
    assert response.status_code == 302
    row.refresh_from_db()
    assert row.number == "0612345678"

    chosen = {**as_drawn, "number_0": "FR"}
    response = client.post(reverse("accounts:profile"), the_details(**rows(chosen, initial=1)))
    assert response.status_code == 302
    row.refresh_from_db()
    assert row.number == "+33612345678", "choosing the country is the change that places it"


def test_a_valid_stored_number_survives_a_save_that_did_not_touch_it(client, user):
    row = keep(user.profile, user, "+5511912345678", kind="mobile")
    client.force_login(user)
    html = client.get(reverse("accounts:profile")).content.decode()
    assert 'value="11 91234-5678"' in html, "grouped as Brazil groups it"

    as_drawn = {
        "id": row.pk,
        "kind": "mobile",
        "label": "",
        "number_0": "BR",
        "number_1": "11 91234-5678",
    }
    response = client.post(reverse("accounts:profile"), the_details(**rows(as_drawn, initial=1)))

    assert response.status_code == 302
    row.refresh_from_db()
    assert row.number == "+5511912345678"


def test_the_row_says_what_it_is_before_the_number(client, user, contact):
    """The telephone half of #213: the kind, the name the kind *Other* asks for, the
    country and the number as one group, then what is done with the row. The markup is in
    that order, which is the reading order and the tab order. On both pages that draw it."""
    keep(user.profile, user, "+351912345678")
    keep(contact, user, "+351211111111")
    client.force_login(user)

    for url in (reverse("accounts:profile"), reverse("jobs:contact_update", args=[contact.pk])):
        html = client.get(url).content.decode()
        row = html[html.index('id="section-phones"') :]
        row = row[row.index("<li") : row.index("</li>")]
        order = [
            row.index('name="phone_numbers-0-kind"'),
            row.index('name="phone_numbers-0-label"'),
            row.index('name="phone_numbers-0-number_0"'),
            row.index('name="phone_numbers-0-number_1"'),
            row.index('name="phone_numbers-primary"'),
        ]
        assert order == sorted(order), url
        assert not re.search(r"(?<![\w-])order-", row), "by the markup, not by a utility"


def test_the_name_is_shown_only_for_other(client, user):
    """Hidden by the stylesheet unless the row's kind is *Other* or it already holds a name:
    `data-if-other`, which follows the select with no script (#309). The help and the error
    of the name are inside what is hidden, so they go with it."""
    keep(user.profile, user, "+351912345678", kind="mobile")
    keep(user.profile, user, "+351211111111", kind="other", label="Reception", primary=False)
    client.force_login(user)

    html = client.get(reverse("accounts:profile")).content.decode()
    block = html[html.index('id="section-phones"') :]
    groups = re.findall(r"<div [^>]*data-if-other[^>]*>", block)[:2]
    assert len(groups) == 2
    assert "data-has-name" not in groups[0], "a mobile: the stylesheet hides its name box"
    assert "data-has-name" in groups[1], "an Other with a name keeps it whatever is chosen"
    named = block[block.index("data-name-if-other") :]
    named = named[: named.index("</div>")]
    assert "phone_numbers-0-label" in named and "_helptext" in named

    css = (SRC / "static" / "css" / "app.css").read_text(encoding="utf-8")
    assert (
        '[data-if-other]:not([data-has-name]):not(:has(select option[value="other"]:checked))'
        in css
    )


def test_a_failed_save_with_other_and_no_name_shows_the_box_with_its_error(client, user):
    client.force_login(user)
    posted = {"kind": "other", "label": "", "number_0": "PT", "number_1": "912345678"}

    response = client.post(reverse("accounts:profile"), the_details(**rows(posted)))

    html = response.content.decode()
    assert response.status_code == 200
    named = html[html.index("data-name-if-other", html.index('id="section-phones"')) :]
    named = named[: named.index("</div>\n        </div>")]
    assert "Say what this number is." in named, "the error is inside the box it is about"
    assert re.search(r'<option value="other"[^>]* selected>', html), "and Other is still chosen"


def test_a_name_typed_under_other_and_then_abandoned_is_cleared(client, user):
    row = keep(user.profile, user, "+351912345678", kind="other", label="Reception")
    client.force_login(user)
    posted = {
        "id": row.pk,
        "kind": "work",
        "label": "Reception",
        "number_0": "PT",
        "number_1": "912 345 678",
    }

    response = client.post(reverse("accounts:profile"), the_details(**rows(posted, initial=1)))

    assert response.status_code == 302
    row.refresh_from_db()
    assert row.kind == "work" and row.label == ""


# --------------------------------------------------- the one box, and a contact's form


def test_the_one_box_refuses_and_keeps_by_the_same_rule(client, user, single):
    """Off is not a lesser version of the field: it is the field."""
    client.force_login(user)

    response = client.post(
        reverse("accounts:profile"), the_details(phone_0="FR", phone_1="06 12 34 56 78 90")
    )
    assert response.status_code == 200
    assert "That number is too long for France." in response.content.decode()
    assert not PhoneNumber.objects.exists()

    response = client.post(
        reverse("accounts:profile"), the_details(phone_0="FR", phone_1="06 12 34 56 78")
    )
    assert response.status_code == 302
    assert PhoneNumber.objects.get().number == "+33612345678"


def test_the_one_box_leaves_a_stored_impossible_number_alone(client, user, single):
    row = keep(user.profile, user, "+33123")
    client.force_login(user)

    html = client.get(reverse("accounts:profile")).content.decode()
    assert 'id="id_phone_kept"' in html and 'data-phone-mark="impossible"' in html
    box = re.search(r'<input[^>]*name="phone_1"[^>]*>', html).group(0)
    assert "id_phone_helptext" in box and "id_phone_kept" in box

    response = client.post(
        reverse("accounts:profile"),
        the_details(headline="Changed beside it", phone_0="FR", phone_1="123"),
    )
    assert response.status_code == 302
    row.refresh_from_db()
    assert row.number == "+33123"


def test_a_contacts_form_refuses_and_keeps_by_the_same_rule(client, user, contact):
    client.force_login(user)
    posted = {"name": "Cave Johnson", "role": "", "company": contact.company_id, "email": ""}
    url = reverse("jobs:contact_update", args=[contact.pk])

    wrong = rows({"kind": "", "label": "", "number_0": "PT", "number_1": "512 345 678"})
    response = client.post(url, {**posted, "notes": "", **wrong})
    assert response.status_code == 200
    assert "That is not a number Portugal uses" in response.content.decode()
    assert not contact.phone_numbers.exists()

    right = rows({"kind": "", "label": "", "number_0": "IT", "number_1": "06 6982 1234"})
    response = client.post(url, {**posted, "notes": "", **right})
    assert response.status_code == 302
    assert contact.phone_numbers.get().number == "+390669821234"


def test_a_contacts_one_box_refuses_by_the_same_rule(client, user, contact, single):
    client.force_login(user)
    posted = {"name": "Cave Johnson", "role": "", "company": contact.company_id, "email": ""}
    url = reverse("jobs:contact_update", args=[contact.pk])

    response = client.post(url, {**posted, "notes": "", "phone_0": "", "phone_1": "912345678"})
    assert response.status_code == 200
    assert "Choose the country" in response.content.decode()

    response = client.post(url, {**posted, "notes": "", "phone_0": "PT", "phone_1": "912345678"})
    assert response.status_code == 302
    assert contact.phone_numbers.get().number == "+351912345678"


# -------------------------------------------------------------- wherever it is shown


def test_a_contacts_number_is_grouped_on_its_companys_page(client, user, contact):
    keep(contact, user, "+442079460958")
    client.force_login(user)

    html = client.get(reverse("jobs:company_detail", args=[contact.company_id])).content.decode()

    assert 'href="tel:+442079460958"' in html, "dialled as it is stored"
    assert "+44 20 7946 0958" in html, "read as the United Kingdom writes it"


def test_a_cv_and_a_letter_print_it_grouped(user):
    from postulo.documents.rendering import contact_details

    keep(user.profile, user, "+33612345678")

    details = contact_details(user)
    assert details["phone"] == "+33 6 12 34 56 78"
    assert "+33 6 12 34 56 78" in details["details"], "a CV's header, its text and its DOCX"
    assert "+33 6 12 34 56 78" in details["brief_details"], "a letter's sender block"


def test_the_removal_dialog_names_the_number_grouped(client, user):
    row = keep(user.profile, user, "+14155552671")
    client.force_login(user)

    html = client.get(reverse("accounts:profile")).content.decode()

    assert "+1 415-555-2671" in html[html.index("<dialog") :], "in the dialog's heading"
    assert str(row.pk) in html


def test_merging_two_people_lists_the_numbers_grouped(user, contact):
    from postulo.jobs.merging import plan_contacts
    from postulo.jobs.models import Contact

    twin = Contact.objects.create(owner=user, company=contact.company, name="C. Johnson")
    keep(twin, user, "+4930123456")

    names = [name for line in plan_contacts(contact, twin).moves for name in line.names]

    assert "+49 30 123456" in names


def test_the_archive_keeps_the_number_as_it_is_stored(user):
    """Grouping is for showing. What somebody leaves with is the stored form."""
    from postulo.core import export

    keep(user.profile, user, "+33612345678")

    document = export.build_document(user)

    assert document["account"]["profile"]["phone_numbers"][0]["number"] == "+33612345678"


# ------------------------------------------------------------------------ the API


def bearer(user, *scopes) -> dict:
    from postulo.api.models import ApiToken

    _record, raw = ApiToken.issue(user, "Agent", scopes=scopes)
    return {"HTTP_AUTHORIZATION": f"Bearer {raw}"}


def add_by_api(client, user, contact, number: str):
    return client.post(
        f"/api/v1/companies/{contact.company_id}/contacts",
        data=json.dumps({"name": "Caroline", "phone": number}),
        content_type="application/json",
        **bearer(user, "write", "read"),
    )


def test_the_api_stores_the_international_form_and_shows_it_both_ways(client, user, contact):
    response = add_by_api(client, user, contact, "+33 (0)6 12 34 56 78")

    assert response.status_code == 201
    answer = response.json()
    assert answer["phone"] == "+33612345678", "as it is stored, where it always was"
    assert answer["phone_numbers"][0]["number"] == "+33612345678"
    assert answer["phone_numbers"][0]["formatted"] == "+33 6 12 34 56 78"


@pytest.mark.parametrize(
    ("number", "says"),
    [
        ("+33 6 12 34", "too short for France"),
        ("+351 512 345 678", "not a number Portugal uses"),
        ("0612345678", "no country in front of it"),
    ],
)
def test_the_api_refuses_what_the_page_refuses(client, user, contact, number, says):
    from postulo.jobs.models import Contact

    response = add_by_api(client, user, contact, number)

    assert response.status_code == 422
    assert says in response.json()["detail"]
    assert not Contact.objects.filter(name="Caroline").exists(), "and makes no contact"


def test_the_api_keeps_what_the_plan_cannot_place(client, user, contact):
    response = add_by_api(client, user, contact, "+999 123 456 78")

    assert response.status_code == 201
    assert response.json()["phone"] == "+999 123 456 78", "as it was sent"


# -------------------------------------------------------------------- the importers


def test_an_archive_is_never_refused_for_a_number(user, other_user):
    """It is restored as the archive has it, and the page marks it: an archive is what
    somebody left with, and one number the plans dislike is no reason to refuse the rest."""
    from postulo.core import export, importer

    keep(user.profile, user, "+33123", kind="work")
    keep(user.profile, user, "0612345678", primary=False)
    archive = zipfile.ZipFile(export.write_archive(user))
    PhoneNumber.objects.all().delete()

    importer.load(other_user, archive)

    assert sorted(row.number for row in other_user.profile.phone_numbers.all()) == [
        "+33123",
        "0612345678",
    ]


def channel(country: str, number: str) -> str:
    return (
        "<Communication><ChannelCode>Telephone</ChannelCode>"
        f"<CountryDialing>{country}</CountryDialing>"
        f"<oa:DialNumber>{number}</oa:DialNumber></Communication>"
    )


@pytest.mark.parametrize(
    ("country", "written", "stored"),
    [
        ("44", "07911 123456", "+447911123456"),
        ("33", "06 12 34 56 78", "+33612345678"),
        ("39", "06 6982 1234", "+390669821234"),
        ("351", "912 345 678", "+351912345678"),
    ],
)
def test_a_europass_number_is_read_against_its_countrys_plan(user, country, written, stored):
    """A file that writes the national form after the dialling code used to keep the trunk
    zero there, a number nobody can dial (#644). Whether a zero is a trunk prefix is the
    country's rule: France drops it and Italy keeps it."""
    from postulo.plugins.europass import reader as europass
    from postulo.resume import importing
    from tests.test_europass_candidate import candidate_xml

    record = europass.read(candidate_xml(person=channel(country, written)))
    importing.apply(user, record)

    assert user.profile.phone_numbers.get().number == stored


def test_a_europass_number_the_plan_calls_impossible_is_imported_and_marked(client, user):
    from postulo.plugins.europass import reader as europass
    from postulo.resume import importing
    from tests.test_europass_candidate import candidate_xml

    report = importing.apply(user, europass.read(candidate_xml(person=channel("33", "06 12"))))

    assert "phone" in report.profile_filled, "a CV is not refused for one number"
    number = user.profile.phone_numbers.get()
    assert phones.kept(number.number).impossible
    client.force_login(user)
    assert (
        'data-phone-mark="impossible"' in client.get(reverse("accounts:profile")).content.decode()
    )


def test_a_candidate_file_is_never_refused_for_a_number(user):
    """The file's own form checks the kind and the name; the number is added as the file
    has it, the row on the review page says what the plan made of it, and *Your details*
    marks it afterwards."""
    from postulo.resume import candidate
    from tests.test_candidate_file import a_file, add, drawn
    from tests.test_candidate_file import rows as rows_of

    data = a_file(
        account={
            "profile": {
                "phone_numbers": [
                    {"kind": "mobile", "number": "+33612345678", "is_primary": True},
                    {"kind": "work", "number": "+33123"},
                    {"kind": "home", "number": "+330612345679"},
                ]
            }
        }
    )

    plan = drawn(user, data)
    add(user, data)

    drawn_rows = rows_of(plan, "phone_numbers")
    assert [row.outcome for row in drawn_rows] == [candidate.ADD] * 3
    assert drawn_rows[0].label == "+33 6 12 34 56 78" and not drawn_rows[0].notes
    assert any("too short for France" in note for note in drawn_rows[1].notes)
    assert sorted(row.number for row in user.profile.phone_numbers.all()) == [
        "+33123",
        "+33612345678",
        "+33612345679",
    ], "the trunk zero after the code is read as the plan reads it"
