"""What somebody types or pastes into a telephone field, and what is made of it (#304).

`tests/test_numbering_plans.py` holds the plans themselves: what is refused, what is kept,
what is marked and how a number is shown. This file holds the edges of that, each of which
was a number handled wrongly once the plans were being read:

- a number is pasted more often than it is typed, and arrives with the spaces of its
  typography, the marks a contact app wraps it in, a ``tel:`` in front;
- words beside a number were read as more digits, because a keypad carries letters;
- what a field said about a number and what it kept for it were worked out separately, and
  disagreed;
- a number already stored that the plan would spell with other digits was called fine;
- a refusal named the wrong country, or said too little for anybody to act on.

And under all of it, the rule none of this may bend: a stored number is marked, never
rewritten, and no page refuses to save because of a number nobody touched.
"""

from __future__ import annotations

import json
import re
import subprocess
import sys
import unicodedata
from html.parser import HTMLParser
from pathlib import Path

import pytest
from django.urls import reverse
from django.utils import translation

from postulo.core import phone_field, phone_numbers, phones
from postulo.core.models import PhoneNumber
from tests.test_numbering_plans import bearer, field, keep, refusal, rows, the_details

pytestmark = pytest.mark.django_db

ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "src" / "postulo"

#: Every space a typographer, a word processor or a keyboard puts between the groups of a
#: number. French typography sets one with U+202F, and the plans know only U+0020.
SPACES = [0x00A0, 0x1680, *range(0x2000, 0x200B), 0x202F, 0x205F, 0x3000]

#: Characters that draw nothing: a soft hyphen, the zero-width ones, and the direction
#: marks a contact app or a right-to-left page wraps a number in.
UNSEEN = [0x00AD, 0x200B, 0x200C, 0x200D, 0x200E, 0x200F, *range(0x202A, 0x202F), 0x2060]
UNSEEN += [*range(0x2066, 0x206A), 0xFEFF]

#: What people write between the groups besides a space.
SEPARATORS = [".", "·", "'", "’", ",", "_", "/", "-"]

FRENCH = "+33612345678"


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


def named(code: int) -> str:
    return f"U+{code:04X}"


def written_with(between: str) -> tuple[str, str]:
    """One French number the national way and the international way, with this between
    its groups."""
    national = between.join(["06", "12", "34", "56", "78"])
    international = "+33" + between + between.join(["6", "12", "34", "56", "78"])
    return national, international


def send(client, user, contact, **payload):
    """A contact added through the API, with whatever the payload says of its number."""
    return client.post(
        f"/api/v1/companies/{contact.company_id}/contacts",
        data=json.dumps({"name": "Caroline", **payload}),
        content_type="application/json",
        **bearer(user, "write", "read"),
    )


def the_one_stored() -> str:
    return PhoneNumber.objects.get().number


# ------------------------------------------------------------- what a number is pasted in


@pytest.mark.parametrize(
    "between",
    [chr(code) for code in SPACES] + SEPARATORS,
    ids=[named(code) for code in SPACES] + [named(ord(mark)) for mark in SEPARATORS],
)
def test_whatever_is_between_its_groups_it_is_the_same_number(client, user, contact, between):
    """A correct number set with narrow no-break spaces was refused as too short, and one
    written with a middle dot, an apostrophe, a comma or an underscore was called
    unplaceable and kept as typed, with no country, though France was chosen. Through the
    function, the field and the API."""
    national, international = written_with(between)

    for typed, country in ((national, "FR"), (international, ""), (international, "PT")):
        assert phones.check(typed, country).fine, (typed, country)
        assert phones.combine(typed, country) == FRENCH
        assert field().clean([country, typed]) == FRENCH

    assert send(client, user, contact, phone=international).json()["phone"] == FRENCH
    PhoneNumber.objects.all().delete()  # a number is kept once on an instance
    answer = send(client, user, contact, phone=national, phone_country="FR")
    assert answer.status_code == 201, answer.content
    assert answer.json()["phone"] == FRENCH


@pytest.mark.parametrize("code", UNSEEN, ids=named)
def test_what_draws_nothing_is_not_part_of_the_number(client, user, contact, code):
    """Round it, as a contact app pastes it, and inside it, as a page that breaks a number
    across lines leaves it."""
    mark = chr(code)
    assert unicodedata.category(mark) == "Cf"
    wrapped = f"{mark}+33 6 12 34 56 78{mark}"
    inside = f"06{mark} 12{mark}34 56 78"

    assert phones.check(wrapped, "").fine and phones.combine(wrapped, "") == FRENCH
    assert phones.check(inside, "FR").fine and phones.combine(inside, "FR") == FRENCH
    assert field().clean(["", wrapped]) == FRENCH
    assert field().clean(["FR", inside]) == FRENCH
    assert send(client, user, contact, phone=wrapped).json()["phone"] == FRENCH


@pytest.mark.parametrize(
    "typed",
    [
        "tel:+33612345678",
        "TEL:+33 6 12 34 56 78",
        "Tel: +33 6 12 34 56 78",
        "(+33 6 12 34 56 78)",
        "( +33 6 12 34 56 78 )",
        "(+33) 6 12 34 56 78",
        "(0033 6 12 34 56 78)",
        "＋33 6 12 34 56 78",
        "‪+33 6 12 34 56 78‬",
        "‎+33 6 12 34 56 78",
        "⁦+33 6 12 34 56 78⁩",
    ],
)
def test_something_before_the_plus_is_not_a_reason(client, user, contact, typed):
    """With no country chosen these were refused as having no country in front: the plus
    was there, and something nobody can see or nobody meant was in front of it."""
    assert phones.check(typed, "").fine
    assert phones.combine(typed, "") == FRENCH
    assert field().clean(["", typed]) == FRENCH
    assert send(client, user, contact, phone=typed).json()["phone"] == FRENCH


def test_brackets_and_a_slash_are_separators_wherever_they_sit():
    for typed, country in (
        ("(06) 12 34 56 78", "FR"),
        ("06 (12) 34 56 78", "FR"),
        ("+33 (6) 12 34 56 78", ""),
        ("06/12/34/56/78", "FR"),
        ("(415) 555-2671", "US"),
    ):
        assert phones.check(typed, country).fine, typed
    assert phones.combine("(06) 12 34 56 78", "FR") == FRENCH


def test_two_commas_are_a_pause_and_not_a_separator():
    """One comma between two digits separates them. Two are what a telephone is told to
    wait on before an extension, and the plan is left to say so."""
    assert phones.combine("06,12,34,56,78", "FR") == FRENCH
    assert phones.check("+1 415 555 2671,,123", "").reason == phones.EXTENSION


def test_a_number_in_another_scripts_digits_is_the_same_number():
    assert phones.combine("+٣٣ ٦ ١٢ ٣٤ ٥٦ ٧٨", "") == FRENCH
    assert phones.combine("０６ １２ ３４ ５６ ７８", "FR") == FRENCH


@pytest.mark.parametrize(
    "stored",
    [
        "tel:+33612345678",
        "‪+33 6 12 34 56 78‬",
        "(+33 6 12 34 56 78)",
        "＋33612345678",
        "06 12 34 56 78",
        "06·12·34·56·78",
    ],
)
def test_a_stored_value_is_read_as_it_is_stored_and_not_tidied(stored):
    """The tidying is for typing. A value the table holds is compared with nothing and
    dialled without its plus, whatever it would be once tidied, and a mark worked out
    from the tidied text would call it fine: it is said to be kept as it was typed, shown
    as it is kept, and left out of every comparison, as it was."""
    assert phones.check(stored, "FR").fine, "typed, it is a number"

    assert phones.kept(stored).reason == phones.AS_TYPED
    assert phones.readable(stored) == stored
    assert phones.normalise(stored) == ""
    assert phones.split(stored) == ("", stored)


def test_a_stored_number_with_a_plus_is_judged_on_its_digits_and_shown_as_it_is():
    """It is compared and dialled by its digits, which are a French number: nothing to
    mark. It is shown as it is kept, since the plan cannot group what it cannot read."""
    stored = "+33 6 12 34 56 78"

    assert phones.normalise(stored) == FRENCH
    assert phones.kept(stored).fine
    assert phones.readable(stored) == stored
    assert phones.split(stored) == ("FR", stored), "whole: typing it back must give it"


# ------------------------------------------------- nothing stored is rewritten or refused

#: Every shape the table can hold: what the field stores now, what it stored before the
#: plans were read, and what the API, a contact card and an import wrote raw.
SHAPES = [
    "+33612345678",
    "0612345678",
    "06 12 34 56 78",
    "(020) 7946-0000",
    "+442079460000123",  # an extension run into the digits
    "+44 20 7946 0001 x123",
    "ask for Marie",
    "3949",
    "+33123",
    "+330612345678",  # the trunk zero kept after the code
    "+33 6 98 76 54 32",
    "0033698765433",
    "+5215512345678",  # Mexico's old mobile prefix
    "+54111523456789",  # Argentina, typed nationally with its 15
    "+80012345678",  # international freephone
    "+999123456",  # a code no country has
    "+1 (415) 555-2671",
    "+12425551234",
    "١٢٣٤٥",
    "+37580152450911",  # Belarus with its 8 0 left in
    "+39669821234",  # Italy with its zero stripped
    "x" * 40,
    "+" + "9" * 39,
    "06 12 34 56 79",
    "‪+33 6 12 34 56 70‬",
    "+33 (0)6 12 34 56 71",
    "tel:+33612345672",
    "+33 6 12 34 56 73 (mobile)",
    "+1-800-FLOWERS",
    "+",
    "0",
]


class Posted(HTMLParser):
    """What a browser would post from the form that holds a named control.

    Read off the page as it was drawn, so that a number is sent back exactly as the field
    showed it: the chooser on whatever it was on, the box holding whatever it held.
    """

    def __init__(self, wanted: str):
        super().__init__(convert_charrefs=True)
        self.wanted = wanted
        self.forms: list[list] = []
        self.form = None
        self.select = None
        self.textarea = None

    def handle_starttag(self, tag, attrs):
        attributes = dict(attrs)
        if tag == "form":
            self.form = []
            self.forms.append(self.form)
        if self.form is None:
            return
        name = attributes.get("name")
        if tag == "input" and name and "disabled" not in attributes:
            kind = (attributes.get("type") or "text").lower()
            if kind in ("submit", "button", "file", "image", "reset"):
                return
            if kind in ("checkbox", "radio") and "checked" not in attributes:
                return
            ticked = "on" if kind in ("checkbox", "radio") else ""
            self.form.append((name, attributes.get("value") or ticked))
        elif tag == "select" and name:
            self.select = {"name": name, "options": [], "chosen": None}
        elif tag == "option" and self.select is not None:
            self.select["options"].append(attributes.get("value", ""))
            if "selected" in attributes:
                self.select["chosen"] = attributes.get("value", "")
        elif tag == "textarea" and name:
            self.textarea = [name, ""]

    def handle_data(self, data):
        if self.textarea is not None:
            self.textarea[1] += data

    def handle_endtag(self, tag):
        if tag == "select" and self.select is not None and self.form is not None:
            chosen = self.select["chosen"]
            if chosen is None:
                chosen = (self.select["options"] or [""])[0]
            self.form.append((self.select["name"], chosen))
            self.select = None
        elif tag == "textarea" and self.textarea is not None and self.form is not None:
            self.form.append(tuple(self.textarea))
            self.textarea = None
        elif tag == "form":
            self.form = None

    def data(self) -> dict:
        for form in self.forms:
            if any(name == self.wanted for name, _value in form):
                posted: dict = {}
                for name, value in form:
                    posted.setdefault(name, []).append(value)
                return posted
        raise AssertionError(f"no form on the page holds {self.wanted}")


def as_a_browser_would(html: str, wanted: str) -> dict:
    reader = Posted(wanted)
    reader.feed(html)
    return reader.data()


def as_kept(shapes=None) -> dict:
    """Everything about each stored row that saving a page it sits on must not move."""
    return {
        row.pk: (row.number, row.normalised, row.kind, row.label, row.is_primary)
        for row in PhoneNumber.objects.all()
        if shapes is None or row.number in shapes
    }


def store(holder, owner, shapes) -> list[PhoneNumber]:
    return [
        keep(holder, owner, shape, kind="work", primary=index == 0)
        for index, shape in enumerate(shapes)
    ]


def a_person_with_a_name(user, language: str = "en-GB"):
    user.first_name, user.last_name = "Alex", "Morgan"
    user.save(update_fields=["first_name", "last_name"])
    user.profile.language = language
    user.profile.save(update_fields=["language"])
    return user


def refused(response) -> list[str]:
    return re.findall(r'role="alert"[^>]*>\s*(?:<p>)?(.*?)</', response.content.decode(), re.S)


@pytest.mark.parametrize("language", ["en-GB", "fr-FR", "uk", "ar"])
def test_your_details_saved_untouched_moves_no_number_whatever_it_holds(client, user, language):
    """Every shape at once, opened and saved as a browser would send it back, in a
    language that starts the chooser on a country, in one that starts it on none, and in
    one written right to left. Nothing is refused, and no row is other than it was."""
    a_person_with_a_name(user, language)
    kept_rows = store(user.profile, user, SHAPES)
    before = as_kept()
    client.force_login(user)

    html = client.get(reverse("accounts:profile")).content.decode()
    response = client.post(
        reverse("accounts:profile"), as_a_browser_would(html, "phone_numbers-TOTAL_FORMS")
    )

    assert response.status_code == 302, refused(response)
    after = as_kept()
    assert {row.pk: after[row.pk] for row in kept_rows} == before


def test_one_row_changed_and_every_other_left_as_it_was(client, user):
    a_person_with_a_name(user)
    kept_rows = store(user.profile, user, SHAPES)
    before = as_kept()
    client.force_login(user)
    html = client.get(reverse("accounts:profile")).content.decode()
    data = as_a_browser_would(html, "phone_numbers-TOTAL_FORMS")
    index = SHAPES.index("0612345678")
    assert data[f"phone_numbers-{index}-number_1"] == ["0612345678"]

    data[f"phone_numbers-{index}-number_0"] = ["FR"]
    data[f"phone_numbers-{index}-number_1"] = ["0612345699"]
    response = client.post(reverse("accounts:profile"), data)

    assert response.status_code == 302, refused(response)
    after = as_kept()
    changed = {row.number for row in kept_rows if after[row.pk] != before[row.pk]}
    assert changed == {"0612345678"}
    assert after[kept_rows[index].pk][0] == "+33612345699"


@pytest.mark.parametrize("shape", SHAPES)
def test_the_one_box_saved_untouched_leaves_its_number_as_it_was(client, user, single, shape):
    a_person_with_a_name(user)
    store(user.profile, user, [shape])
    before = as_kept()
    client.force_login(user)

    html = client.get(reverse("accounts:profile")).content.decode()
    response = client.post(reverse("accounts:profile"), as_a_browser_would(html, "phone_1"))

    assert response.status_code == 302, (shape, refused(response))
    assert as_kept() == before


def test_a_contact_saved_untouched_moves_no_number_whatever_it_holds(client, user, contact):
    kept_rows = store(contact, user, SHAPES)
    before = as_kept()
    client.force_login(user)
    url = reverse("jobs:contact_update", args=[contact.pk])

    html = client.get(url).content.decode()
    response = client.post(url, as_a_browser_would(html, "phone_numbers-TOTAL_FORMS"))

    assert response.status_code == 302, refused(response)
    after = as_kept()
    assert {row.pk: after[row.pk] for row in kept_rows} == before


@pytest.mark.parametrize("shape", SHAPES)
def test_a_contacts_one_box_saved_untouched_leaves_its_number(client, user, contact, single, shape):
    store(contact, user, [shape])
    before = as_kept()
    client.force_login(user)
    url = reverse("jobs:contact_update", args=[contact.pk])

    html = client.get(url).content.decode()
    response = client.post(url, as_a_browser_would(html, "phone_1"))

    assert response.status_code == 302, (shape, refused(response))
    assert as_kept() == before


# ------------------------------------------------- a row nobody typed a number into


def test_a_chooser_showing_its_default_beside_an_empty_box_is_not_a_change():
    """The blank row of a block posts the country its chooser started on and no number.
    With nothing stored, Django compared that with two empty strings, found the country
    different, and called the row changed (#649)."""
    blank = phone_field.PhoneField(required=False, default_country="GB")

    assert not blank.has_changed(None, ["GB", ""])
    assert not blank.has_changed("", ["GB", ""])
    assert not blank.has_changed(None, ["GB", "   "])
    assert not blank.has_changed(None, ["FR", ""]), "a country beside an empty box is no number"
    assert not phone_field.PhoneField(required=False).has_changed(None, ["", ""])

    assert blank.has_changed(None, ["GB", "07911 123456"]), "a number typed is a change"
    assert blank.has_changed(FRENCH, ["FR", ""]), "and so is one emptied"
    assert blank.has_changed(FRENCH, ["PT", "6 12 34 56 78"])
    assert blank.has_changed(FRENCH, ["FR", "6 12 34 56 79"])
    assert not blank.has_changed(FRENCH, ["FR", "6 12 34 56 78"]), "as it was drawn"


def test_a_block_of_rows_does_not_save_the_row_nobody_filled_in(user):
    untouched = {"kind": "", "label": "", "number_0": "GB", "number_1": ""}
    formset = phone_numbers.formset_for(
        user.profile, default_country="GB", data=rows(untouched), asked_by=user
    )

    assert formset.is_valid()
    assert not formset.forms[0].has_changed()
    formset.save()
    assert not PhoneNumber.objects.exists()


@pytest.mark.parametrize("language", ["en-GB", "fr-FR", "pt-PT", "pt-BR", "uk"])
def test_your_details_saved_untouched_stores_no_empty_row(client, user, language):
    """In a language whose chooser starts on a country, pressing *Save* on a page nobody
    touched created a telephone row with an empty number -- and, for an account with no
    number, made it the primary. Posted as a browser posts the page it was given."""
    a_person_with_a_name(user, language)
    client.force_login(user)

    html = client.get(reverse("accounts:profile")).content.decode()
    data = as_a_browser_would(html, "phone_numbers-TOTAL_FORMS")
    starts_on = data["phone_numbers-0-number_0"]
    assert starts_on == ([""] if language == "uk" else [phones.default_country(language)])
    response = client.post(reverse("accounts:profile"), data)

    assert response.status_code == 302, refused(response)
    assert not PhoneNumber.objects.exists()


def test_a_contact_saved_untouched_stores_no_empty_row(client, user, contact):
    a_person_with_a_name(user)
    client.force_login(user)
    url = reverse("jobs:contact_update", args=[contact.pk])

    html = client.get(url).content.decode()
    data = as_a_browser_would(html, "phone_numbers-TOTAL_FORMS")
    assert data["phone_numbers-0-number_0"] == ["GB"], "the chooser starts on a country"
    response = client.post(url, data)

    assert response.status_code == 302, refused(response)
    assert not PhoneNumber.objects.exists()


def test_saving_untouched_adds_no_row_beside_the_ones_held(client, user):
    """The table after is the table before: every row as it was, and no other."""
    a_person_with_a_name(user)
    store(user.profile, user, SHAPES)
    before = as_kept()
    client.force_login(user)

    html = client.get(reverse("accounts:profile")).content.decode()
    response = client.post(
        reverse("accounts:profile"), as_a_browser_would(html, "phone_numbers-TOTAL_FORMS")
    )

    assert response.status_code == 302, refused(response)
    assert as_kept() == before


def test_a_number_typed_into_the_blank_row_is_still_saved(client, user):
    a_person_with_a_name(user)
    client.force_login(user)
    html = client.get(reverse("accounts:profile")).content.decode()
    data = as_a_browser_would(html, "phone_numbers-TOTAL_FORMS")

    data["phone_numbers-0-number_1"] = ["07911 123456"]
    response = client.post(reverse("accounts:profile"), data)

    assert response.status_code == 302, refused(response)
    row = PhoneNumber.objects.get()
    assert row.number == "+447911123456" and row.is_primary


# -------------------------------------------- stored, and not spelt the way it is dialled


@pytest.mark.parametrize(
    ("stored", "dialled"),
    [
        ("+330612345678", "+33612345678"),
        ("+54111523456789", "+5491123456789"),
        ("+37580152450911", "+375152450911"),
        ("+33 (0)6 12 34 56 71", "+33612345671"),
    ],
)
def test_a_stored_number_the_plan_would_spell_otherwise_is_marked(client, user, stored, dialled):
    """A trunk zero left after the code, Argentina's 15, Belarus's 8 0, a bracketed zero.
    The plan can read each and calls the number it reads fine, so these came back *fine*
    and the box showed the raw value with nothing to explain it. Marked, with the spelling
    that can be dialled; not rewritten by a save; put right the day it is changed."""
    verdict = phones.kept(stored)
    assert verdict.miswritten and not verdict.fine and not verdict.impossible
    assert verdict.reason == phones.AS_WRITTEN and verdict.dialled == dialled
    shown = phones.readable(dialled)
    assert verdict.message == f"Kept as it was written. The number that can be dialled is {shown}."

    row = keep(user.profile, user, stored, kind="work")
    client.force_login(user)
    html = client.get(reverse("accounts:profile")).content.decode()
    mark = re.search(r'<p id="id_phone_numbers-0-number_kept"[^>]*>(.*?)</p>', html, re.S)
    assert mark and 'data-phone-mark="miswritten"' in mark.group(0)
    assert "Not as it is dialled" in mark.group(1)
    assert f'The number that can be dialled is <bdi dir="ltr">{shown}</bdi>.' in mark.group(1)
    assert "left as it is until you change it" not in mark.group(1), "that is said of an error"

    country, rest = phones.split(stored)
    assert rest == stored, "the box shows it whole"
    as_drawn = {"id": row.pk, "kind": "work", "label": "", "number_0": country, "number_1": rest}
    response = client.post(reverse("accounts:profile"), the_details(**rows(as_drawn, initial=1)))
    assert response.status_code == 302
    row.refresh_from_db()
    assert row.number == stored, "saved untouched, it is left"

    retyped = {**as_drawn, "number_1": stored.replace("+", "00", 1)}
    response = client.post(reverse("accounts:profile"), the_details(**rows(retyped, initial=1)))
    assert response.status_code == 302
    row.refresh_from_db()
    assert row.number == dialled, "changed, it is read as any typing is"
    assert "data-phone-mark" not in client.get(reverse("accounts:profile")).content.decode()


def test_a_number_merely_written_with_spaces_is_not_marked():
    """The same digits the plan would write: nothing to say."""
    assert phones.kept("+33 6 98 76 54 32").fine
    assert phones.kept("0033698765433").fine
    assert phones.kept("+33612345678").fine


def test_the_candidate_file_knows_a_number_the_account_holds_under_its_other_spelling(user):
    """The file's row is read through `combine`, which corrects the spelling; the
    account's own number is held as it was stored. Compared only as stored, a file read
    back into the account that wrote it offered the account its own numbers to add."""
    from postulo.resume import candidate
    from tests.test_candidate_file import a_file, add, drawn
    from tests.test_candidate_file import rows as rows_of

    shapes = ["+330612345678", "+54111523456789", "+37580152450911"]
    for index, shape in enumerate(shapes):
        keep(user.profile, user, shape, primary=index == 0)
    data = a_file(
        account={
            "profile": {"phone_numbers": [{"kind": "mobile", "number": shape} for shape in shapes]}
        }
    )

    plan = drawn(user, data)
    add(user, data)

    assert [row.outcome for row in rows_of(plan, "phone_numbers")] == [candidate.PRESENT] * 3
    assert sorted(row.number for row in user.profile.phone_numbers.all()) == sorted(shapes)


# ------------------------------------------------------------------ words beside a number

WORDS_BESIDE = [
    ("+33 6 12 34 56 78 (mobile)", ""),
    ("06 12 34 56 78 portable", "FR"),
    ("+33 6 12 34 56 78 WhatsApp", ""),
    ("+33 1 23 45 67 89 poste 12", ""),
    ("+49 30 12345-0 Durchwahl 12", ""),
    ("+1 415 555 2671 p123", ""),
    ("+44 7911 123456 (m)", ""),
    ("+49 30 123456 mob", ""),
    ("06 12 34 56 78 ou 06 98 76 54 32", "FR"),
    ("Fax +33 1 23 45 67 89", ""),
]


@pytest.mark.parametrize(("typed", "country"), WORDS_BESIDE)
def test_words_beside_a_number_are_refused_and_never_read_as_digits(typed, country):
    """The plan puts each letter on the key that carries it, so `(mobile)` was `662453`
    and the number was "too long for France" -- or, in a country whose numbers vary in
    length, a valid number nobody wrote. The field says what is the matter, and nothing
    that is kept has a digit the text did not hold as one."""
    verdict = phones.check(typed, country)

    assert verdict.impossible and verdict.reason == phones.WORDS
    assert "words in it" in refusal(country, typed)
    assert phones.combine(typed, country) == typed, "an importer keeps it as it was written"


def test_letters_are_digits_only_where_they_spell_a_number():
    """`1-800-FLOWERS` is a number, and is one because it is not a number without its
    letters. `+49 30 123456 mob` is a number already, with a word beside it."""
    assert phones.combine("1-800-FLOWERS", "US") == "+18003569377"
    assert phones.combine("+1-800-FLOWERS", "") == "+18003569377"
    assert phones.combine("1-800-flowers", "US") == "+18003569377"
    assert phones.check("1-800-GOT-JUNK", "US").fine
    assert field().clean(["US", "1-800-FLOWERS"]) == "+18003569377"

    assert phones.check("+49 30 123456", "").fine
    assert phones.check("+49 30 123456 mob", "").reason == phones.WORDS
    assert phones.check("1-800-FLOWERS please", "US").reason == phones.WORDS


def test_what_is_kept_never_has_a_digit_the_text_did_not_hold():
    """Over the plan's own example of every kind of number in every country, with words
    after it: what comes back is the text as it was written, digit for digit."""
    import phonenumbers

    kinds = [
        value
        for name, value in vars(phonenumbers.PhoneNumberType).items()
        if name.isupper() and name != "UNKNOWN"
    ]
    after = [" (mobile)", " mob", " poste 12", " Durchwahl 12", " p123", " WhatsApp", " (m)"]
    tried = 0
    for code, _dialling, _name in phones.COUNTRIES:
        for kind in kinds:
            example = phonenumbers.example_number_for_type(code, kind)
            if example is None:
                continue
            written = phonenumbers.format_number(
                example, phonenumbers.PhoneNumberFormat.INTERNATIONAL
            )
            for words in after:
                typed = written + words
                tried += 1
                assert phones.combine(typed, "") == typed, typed
                assert phones.check(typed, "").reason == phones.WORDS, typed
    assert tried > 5000


def test_an_import_keeps_a_number_with_words_as_it_was_written_and_the_page_marks_it(client, user):
    from postulo.resume import importing

    written = "+33 6 12 34 56 78 (mobile)"
    report = importing.apply(user, importing.Record(person={"phone": written}))

    assert "phone" in report.profile_filled, "a CV is not refused for one number"
    assert the_one_stored() == written, "and never +33612345678662453"
    client.force_login(user)
    html = client.get(reverse("accounts:profile")).content.decode()
    mark = re.search(r'<p id="id_phone_numbers-0-number_kept"[^>]*>(.*?)</p>', html, re.S)
    assert mark and 'data-phone-mark="impossible"' in mark.group(0)
    assert "words in it" in mark.group(1)


def test_a_candidate_file_keeps_a_number_with_words_as_it_was_written(user):
    from postulo.resume import candidate
    from tests.test_candidate_file import a_file, add, drawn
    from tests.test_candidate_file import rows as rows_of

    written = "+33 6 12 34 56 78 (mobile)"
    data = a_file(account={"profile": {"phone_numbers": [{"kind": "mobile", "number": written}]}})

    plan = drawn(user, data)
    add(user, data)

    (row,) = rows_of(plan, "phone_numbers")
    assert row.outcome == candidate.ADD and row.label == written
    assert any("words in it" in note for note in row.notes)
    assert the_one_stored() == written


# --------------------------------------- what the field says and what it keeps, together


def test_a_stray_character_does_not_make_a_number_unplaceable():
    """`+33 6 12 #` was "kept as it was typed" to `check` and `+33612` to `combine`, which
    the page then marked as impossible. A number that says its country is judged on its
    digits: too short, here, and said so."""
    verdict = phones.check("+33 6 12 #", "")

    assert verdict.impossible and verdict.reason == phones.TOO_SHORT
    assert "too short for France" in verdict.message
    assert phones.check("0612345678 #", "FR").fine
    assert phones.combine("0612345678 #", "FR") == FRENCH
    assert phones.check("+33 6 12 34 56 78 *", "").fine


def typings() -> list[tuple[str, str]]:
    """A wide set of things somebody might send: numbers of every country written three
    ways, cut short, run long, decorated, and things that are not numbers at all."""
    import phonenumbers

    formats = phonenumbers.PhoneNumberFormat
    found: list[tuple[str, str]] = []
    decorated = ["{}", "{} #", "{}#", "#{}", "{} *", "({})", "{};", "{} x", "{},", "tel:{}"]
    for code, dialling, _name in phones.COUNTRIES:
        example = phonenumbers.example_number(code)
        if example is None:
            continue
        international = phonenumbers.format_number(example, formats.INTERNATIONAL)
        national = phonenumbers.format_number(example, formats.NATIONAL)
        e164 = phonenumbers.format_number(example, formats.E164)
        for shape in decorated:
            found.append((shape.format(international), ""))
            found.append((shape.format(national), code))
        found += [
            (e164[:-3], ""),
            (e164 + "12345", ""),
            (national[:-2], code),
            (national, ""),
            (national, "FR"),
            (f"+{dialling}", ""),
            (f"+{dialling} #", ""),
            (international.replace(" ", " "), ""),
        ]
    for country in ("", "FR", "US", "GB", "BR", "PT"):
        found += [
            (text, country)
            for text in (
                "3949",
                "112",
                "116 000",
                "911",
                "+999 123 456 78",
                "+999",
                "+800 1234 5678",
                "ask reception",
                "ask for Marie on 5",
                "#",
                "+",
                "()",
                "-",
                "0",
                "00",
                "+0",
                "１２３",
                "+33 6 12 #",
                "+33 6 12 34 # 56",
                "06 12 34 56 78 / 79",
                "+١٢٣٤٥",
            )
        ]
    return found


def test_what_the_field_says_and_what_it_keeps_never_disagree():
    """Whatever is called *kept as it was typed* is kept as it was typed, and whatever is
    accepted is kept in a form that is fine when it is read back. And what an importer
    stores -- it asks `combine` and not `check` -- is not changed by being read again."""
    tried = 0
    for typed, country in typings():
        verdict = phones.check(typed, country)
        stored = phones.combine(typed, country)
        tried += 1
        if verdict.unplaceable:
            assert stored == phones.tidy(typed), (typed, country, verdict.reason)
        if verdict.fine:
            assert phones.kept(stored).fine, (typed, country, stored)
            assert phones.normalise(stored) == stored or stored.startswith(("+800", "+808"))
        assert phones.combine(stored, "") == stored, (typed, country, stored)
    assert tried > 5000


def test_a_number_with_a_plus_and_a_known_code_is_never_unplaceable():
    for typed, _country in typings():
        if not typed.startswith("+") or phones.check(typed, "").reason == phones.UNKNOWN_CODE:
            continue
        if not any(ch.isdigit() for ch in typed):
            continue
        assert not phones.check(typed, "").unplaceable, typed


# ---------------------------------------------------------------- what a refusal names


@pytest.mark.parametrize(
    ("typed", "names"),
    [
        ("+1", "the United States"),
        ("+212 6", "Morocco"),
        ("+358 4", "Finland"),
        ("+590 6", "Guadeloupe"),
        ("+7 9", "Russia"),
    ],
)
def test_a_refusal_names_the_country_its_code_is_chiefly_for(typed, names):
    """The first country in the alphabet under a shared code is Antigua and Barbuda,
    Western Sahara, the Åland Islands, Saint Barthélemy and Kazakhstan."""
    verdict = phones.check(typed, "")

    assert verdict.reason == phones.TOO_SHORT
    assert verdict.message == f"That number is too short for {names}."
    assert phones.country_of(typed).name == names.removeprefix("the ")


def test_a_number_dialled_out_through_the_countrys_prefix_is_named_by_where_it_goes():
    """`011` is how the United States dials abroad: what follows is a French number."""
    assert phones.combine("011 33 6 12 34 56 78", "US") == FRENCH
    assert "too short for France" in phones.check("011 33 6 12", "US").message


@pytest.mark.parametrize(
    ("typed", "country"),
    [
        ("555-1234", "US"),
        ("7946 0000", "GB"),
        ("91234-5678", "BR"),
        ("3123-4567", "BR"),
        ("2345 6789", "AR"),
    ],
)
def test_a_number_without_its_area_code_is_told_so(typed, country):
    """The right length for a call within one town. It was "too short for the United
    States", and for Brazil "no number there begins that way": neither says what to add."""
    verdict = phones.check(typed, country)

    assert verdict.impossible and verdict.reason == phones.NO_AREA_CODE
    assert refusal(country, typed) == "That number has no area code."


def test_a_whole_number_that_is_short_is_not_missing_an_area_code():
    """Brazil's nationwide `4004` numbers are eight digits and complete."""
    assert phones.check("4004-1234", "BR").fine
    assert phones.check("512 345 678", "PT").reason == phones.NOT_IN_USE
    assert phones.check("06 12 34", "FR").reason == phones.TOO_SHORT


@pytest.mark.parametrize(
    ("typed", "country", "territory", "says"),
    [
        ("06 92 12 34 56", "FR", "RE", "It is one Réunion uses: choose Réunion beside"),
        ("0262 12 34 56", "FR", "RE", "It is one Réunion uses: choose Réunion beside"),
        ("06 90 00 12 34", "FR", "GP", "It is one Guadeloupe uses: choose Guadeloupe beside"),
        ("0590 27 12 34", "FR", "BL", "It is one Saint Barthélemy uses"),
        ("0594 10 12 34", "FR", "GF", "It is one French Guiana uses"),
        ("87 12 34 56", "FR", "PF", "It is one French Polynesia uses"),
        ("9 767 1234", "NL", "CW", "It is one Curaçao uses: choose Curaçao beside"),
    ],
)
def test_a_number_of_one_of_the_countrys_territories_is_told_which(typed, country, territory, says):
    """Somebody in Saint-Denis chooses France, writes the number as France writes one, and
    is told France does not use it. True, and no help: the cure is choosing Réunion.
    Still a refusal: nothing is guessed and stored."""
    verdict = phones.check(typed, country)

    assert verdict.impossible and verdict.reason == phones.ELSEWHERE
    assert verdict.elsewhere == territory
    said = refusal(country, typed)
    assert said.startswith("That is not a number ") and says in said
    assert phones.check(typed, territory).fine, "and choosing it is the cure"


def test_a_number_two_territories_would_read_differently_names_neither():
    """`41 12 34` is a number in Saint Pierre and Miquelon and another in New Caledonia."""
    verdict = phones.check("41 12 34", "FR")

    assert verdict.impossible and verdict.reason == phones.TOO_SHORT and not verdict.elsewhere
    assert phones.check("32 12 34", "DK").reason == phones.TOO_SHORT
    assert phones.check("07 00 00 00 00", "FR").reason == phones.NOT_IN_USE, "nobody's"


def test_the_territory_is_named_as_the_sentences_language_names_it():
    verdict = phones.check("9 767 1234", "NL")

    assert "the Netherlands uses" in verdict.message
    with translation.override("fr-FR"):
        assert "the Netherlands" not in verdict.message and "Netherlands" in verdict.message
        assert "Curaçao" in verdict.message


def test_a_territorys_plan_is_loaded_only_for_a_refusal_and_only_its_countrys():
    """A number that is fine loads its own country's plan and no other. A refusal under
    France asks France's territories, and nobody else's."""
    script = (
        "import sys, django\n"
        "django.setup()\n"
        "from postulo.core import phones\n"
        "def loaded():\n"
        "    return {name.rpartition('_')[2] for name in sys.modules\n"
        "            if name.startswith('phonenumbers.data.region_')}\n"
        "assert phones.check('06 12 34 56 78', 'FR').fine\n"
        "assert loaded() == {'FR'}, loaded()\n"
        "assert phones.check('912 345 678', 'PT').fine\n"
        "assert phones.check('512 345 678', 'PT').impossible\n"
        "assert loaded() == {'FR', 'PT'}, 'Portugal has no territories to ask'\n"
        "assert phones.check('06 92 12 34 56', 'FR').elsewhere == 'RE'\n"
        "assert loaded() >= set(phones.TERRITORIES['FR']), loaded()\n"
        "assert not loaded() & set(phones.TERRITORIES['NL']), loaded()\n"
        "assert not loaded() & set(phones.TERRITORIES['DK']), loaded()\n"
        "assert 'US' not in loaded(), loaded()\n"
        "assert phones.check('721 542 1234', 'NL').elsewhere == 'SX'\n"
        "assert 'US' not in loaded(), 'Sint Maarten is under +1, and only its own plan is read'\n"
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


def test_every_territory_in_the_table_is_a_country_in_the_table():
    for country, territories in phones.TERRITORIES.items():
        assert country in phones.BY_CODE
        for territory in territories:
            assert territory in phones.BY_CODE, territory
            assert phones.BY_CODE[territory][1] != phones.BY_CODE[country][1], territory


# ---------------------------------------------- where what is left out of a number can go


def test_an_extension_is_sent_to_the_notes_only_where_there_are_notes(client, user, contact):
    """A contact has a notes box and *Your details* has none: a sentence sending somebody
    to a box that is not on the page is worse than one that only says what to do here."""
    client.force_login(user)
    typed = {"kind": "", "label": "", "number_0": "GB", "number_1": "020 7946 0000 x123"}

    response = client.post(reverse("accounts:profile"), the_details(**rows(typed)))
    html = response.content.decode()
    assert response.status_code == 200
    assert "An extension cannot be kept as part of the number. Leave it out." in html
    assert "in the notes" not in html

    posted = {"name": "Cave Johnson", "role": "", "company": contact.company_id, "email": ""}
    url = reverse("jobs:contact_update", args=[contact.pk])
    response = client.post(url, {**posted, "notes": "", **rows(typed)})
    html = response.content.decode()
    assert response.status_code == 200
    assert "Leave it out here, and put it in the notes." in html
    assert not PhoneNumber.objects.exists()


def test_words_are_sent_to_the_notes_only_where_there_are_notes(client, user, contact, single):
    """The same, through the one box that stands in for the rows."""
    client.force_login(user)

    response = client.post(
        reverse("accounts:profile"), the_details(phone_0="FR", phone_1="06 12 34 56 78 portable")
    )
    html = response.content.decode()
    assert "That has words in it as well as a number. Leave them out." in html
    assert "in the notes" not in html

    posted = {"name": "Cave Johnson", "role": "", "company": contact.company_id, "email": ""}
    url = reverse("jobs:contact_update", args=[contact.pk])
    response = client.post(
        url, {**posted, "notes": "", "phone_0": "FR", "phone_1": "06 12 34 56 78 portable"}
    )
    assert "Leave them out here, and put them in the notes." in response.content.decode()


def test_a_stored_number_with_an_extension_is_marked_in_the_forms_own_words(client, user, contact):
    keep(user.profile, user, "+44 20 7946 0000 x123")
    keep(contact, user, "+44 20 7946 0001 x123")
    client.force_login(user)

    mine = client.get(reverse("accounts:profile")).content.decode()
    theirs = client.get(reverse("jobs:contact_update", args=[contact.pk])).content.decode()

    assert "Leave it out." in mine and "in the notes" not in mine
    assert "put it in the notes" in theirs


# ------------------------------------------------------------------------ the help


def test_every_telephone_field_says_the_same_true_thing_under_itself(client, user, contact):
    """Three places said "a number that already starts with + is taken as it is", in two
    wordings, after it had stopped being true: it is read against its country's plan like
    any other, and what the plus changes is that the chooser is not asked."""
    from postulo.accounts.forms import ProfileForm
    from postulo.jobs.forms import ContactForm
    from postulo.plugins.models import PluginPolicy
    from postulo.plugins.phone_numbers import PHONE_NUMBERS

    said = str(phone_field.HELP)
    assert "starts with + says its own country, and the chooser beside it is ignored" in said

    row = phone_numbers.formset_for(user.profile).forms[0]
    assert str(row.fields["number"].help_text) == said
    PluginPolicy.objects.create(
        plugin=PHONE_NUMBERS, person=user, state=PluginPolicy.State.FORCED_OFF
    )
    assert str(ProfileForm(instance=user.profile).fields["phone"].help_text) == said
    assert str(ContactForm(instance=contact, user=user).fields["phone"].help_text) == said

    for path in sorted(SRC.rglob("*.py")):
        assert "is taken as it is" not in path.read_text(encoding="utf-8"), path


# --------------------------------------------------------------- the table, not the plan


def test_what_two_numbers_are_compared_by_is_the_tables_answer():
    """`normalise` decides what a unique index holds, so it must not move with a release
    of the plans: it keeps the trunk zero the plan would drop, has no answer for a code no
    country in the table has, and reads no plan at all."""
    assert phones.normalise("+330612345678") == "+330612345678"
    assert phones.normalise("+80012345678") == ""
    assert phones.normalise("0033 6 12 34 56 78") == FRENCH

    script = (
        "import sys, django\n"
        "django.setup()\n"
        "from postulo.core import phones\n"
        "assert phones.normalise('+330612345678') == '+330612345678'\n"
        "assert phones.normalise('+80012345678') == ''\n"
        "assert phones.normalise('00351 912 345 678') == '+351912345678'\n"
        "assert phones.as_dialled('+351 912 345 678') == '+351912345678'\n"
        "assert 'phonenumbers' not in sys.modules, 'normalise read the plans'\n"
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


# ------------------------------------------------------------------------ the API


def test_the_api_takes_a_national_number_with_its_country(client, user, contact):
    """The chooser beside the box, for a client: it had no way to send one, so a client
    holding a national number and a country could only be refused."""
    response = send(client, user, contact, phone="06 12 34 56 78", phone_country="FR")

    assert response.status_code == 201, response.content
    answer = response.json()
    assert answer["phone"] == FRENCH, "stored and returned in the international form"
    assert answer["phone_numbers"][0]["formatted"] == "+33 6 12 34 56 78"
    assert "phone_country" not in answer, "the country is the number's own from then on"

    lower = send(client, user, contact, phone="912 345 678", phone_country="pt")
    assert lower.status_code == 201 and lower.json()["phone"] == "+351912345678"


def test_the_api_ignores_the_country_beside_a_number_that_says_its_own(client, user, contact):
    response = send(client, user, contact, phone="+33 6 12 34 56 78", phone_country="PT")

    assert response.status_code == 201 and response.json()["phone"] == FRENCH


def test_the_api_keeps_a_short_number_sent_with_its_country(client, user, contact):
    """As the page keeps it: it has no international form, and is not a mistake."""
    response = send(client, user, contact, phone="3949", phone_country="FR")

    assert response.status_code == 201 and response.json()["phone"] == "3949"
    assert send(client, user, contact, phone="3949").status_code == 422, "with no country, refused"


@pytest.mark.parametrize("code", ["ZZ", "XX", "F", "FRA", "33", "+33"])
def test_the_api_refuses_a_country_that_is_not_one(client, user, contact, code):
    from postulo.jobs.models import Contact

    response = send(client, user, contact, phone="06 12 34 56 78", phone_country=code)

    assert response.status_code == 422
    assert not Contact.objects.filter(name="Caroline").exists()


@pytest.mark.parametrize(
    ("payload", "says"),
    [
        ({"phone": "06 12 34 56 78"}, "no country in front of it"),
        ({"phone": "06 12 34", "phone_country": "FR"}, "too short for France"),
        ({"phone": "555-1234", "phone_country": "US"}, "has no area code"),
        ({"phone": "06 92 12 34 56", "phone_country": "FR"}, "It is one Réunion uses"),
        ({"phone": "+44 20 7946 0000 x123"}, "put it in the notes"),
        ({"phone": "+33 6 12 34 56 78 (mobile)"}, "put them in the notes"),
        ({"phone": "+33 6 12 #"}, "too short for France"),
    ],
)
def test_the_api_refuses_in_the_words_the_page_uses(client, user, contact, payload, says):
    from postulo.jobs.models import Contact

    response = send(client, user, contact, **payload)

    assert response.status_code == 422
    assert says in response.json()["detail"]
    assert not Contact.objects.filter(name="Caroline").exists()


def test_the_api_says_what_it_does_with_a_number(client, user):
    """A client reads the description, not the changelog: what is refused, and that what
    comes back is not what was sent."""
    schema = client.get("/api/v1/openapi.json", **bearer(user, "read")).json()
    fields = schema["components"]["schemas"]["ContactIn"]["properties"]

    phone = fields["phone"]["description"]
    assert "neither a `+` nor `phone_country`" in phone and "422" in phone
    assert "returned, in the international form" in phone
    country = fields["phone_country"]["description"]
    assert "ISO 3166-1 alpha-2" in country and "422" in country


# ------------------------------------------- a number is written left to right, anywhere


def test_a_link_to_a_number_says_its_direction(client, user, contact):
    """On a right-to-left page `+33 6 98 76 54 32` drew as `32 54 76 98 6 33+`: each group
    is a number of its own to the line, and the line lays numbers out from the right."""
    keep(contact, user, "+33698765432")
    client.force_login(user)

    html = client.get(reverse("jobs:company_detail", args=[contact.company_id])).content.decode()

    link = re.search(r'<a href="tel:\+33698765432"[^>]*>([^<]*)</a>', html)
    assert link and 'dir="ltr"' in link.group(0)
    assert link.group(1) == "+33 6 98 76 54 32"


@pytest.mark.parametrize("language", ["ar", "he", "en-GB"])
def test_a_cv_and_a_letter_isolate_the_number_they_print(user, language):
    """In markup, which is where a document is laid out; the number itself is what it
    was, with no invisible character put round it."""
    from postulo.documents import rendering
    from postulo.documents.models import CV, CoverLetter

    keep(user.profile, user, "+33612345678")
    cv = CV.objects.create(owner=user, name="Main", language=language)
    letter = CoverLetter.objects.create(owner=user, name="Cover", body="Dear", language=language)

    for html in (rendering.render_cv_html(cv), rendering.render_letter_html(letter)):
        assert '<span dir="ltr">+33 6 12 34 56 78</span>' in html
        assert html.count('dir="ltr"') == (2 if language == "en-GB" else 1), "only the number"
    details = rendering.contact_details(user, cv)
    assert details["phone"] == "+33 6 12 34 56 78"
    assert "⁦" not in "".join(details["details"]) and "⁩" not in rendering.cv_text(cv)


def test_the_row_holds_the_kind_the_name_and_the_group_in_one_grid(client, user, contact):
    """The arrangement is the stylesheet's (`.phone-row`): the kind beside the number on a
    wide screen, the group under the kind and the name where a row shows its name. The
    browser suite holds what that draws; this holds that both pages give it the markup."""
    keep(user.profile, user, "+351912345678", kind="mobile")
    keep(contact, user, "+351211111111", kind="other", label="Reception")
    client.force_login(user)

    for url in (reverse("accounts:profile"), reverse("jobs:contact_update", args=[contact.pk])):
        html = client.get(url).content.decode()
        row = html[html.index('id="section-phones"') :]
        row = row[row.index('<div class="phone-row') : row.index("</li>")]
        group = row[: row.index("primary")]
        assert "data-if-other" in row[: row.index(">")]
        order = [
            group.index('name="phone_numbers-0-kind"'),
            group.index("data-name-if-other"),
            group.index("data-phone-line"),
            group.index("data-phone-field"),
        ]
        assert order == sorted(order), url

    css = (SRC / "static" / "css" / "app.css").read_text(encoding="utf-8")
    assert (
        '.phone-row:not([data-has-name]):not(:has(select option[value="other"]:checked))'
        " > [data-phone-line]" in css
    )
