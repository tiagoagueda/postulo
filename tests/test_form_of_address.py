"""*Your name* and the contact block: a form of address, pronouns, and a location (#309).

Two fields, because they are two questions: a form of address is written before a name,
and pronouns say how to refer to somebody. Each is offered from a list in the language the
career record is written in, with a box for anything else, and is stored as the text
itself. Neither is printed anywhere yet.

And the location: blank means the town and country of the primary postal address, worked
out whenever it is printed, through one function every reader asks.
"""

from __future__ import annotations

import json
import re
import zipfile
from io import BytesIO

import pytest
from django.urls import reverse
from django.utils import translation

from postulo.accounts import addressing
from postulo.accounts.forms import ProfileForm
from postulo.api.models import ApiToken
from postulo.core import export, importer, postal
from postulo.core.models import PostalAddress
from postulo.documents.models import CV
from postulo.documents.rendering import contact_details, render_cv_html
from postulo.resume import candidate

pytestmark = pytest.mark.django_db


def an_address(user, **parts) -> PostalAddress:
    fields = {
        "street": "Rua do Exemplo 1",
        "postcode": "1000-001",
        "municipality": "Lisboa",
        "country": "PT",
        "is_primary": True,
    }
    fields.update(parts)
    return PostalAddress.objects.create(owner=user, holder=user.profile, **fields)


def named(user, **profile):
    user.first_name, user.last_name = "Alex", "Morgan"
    user.save()
    for name, value in profile.items():
        setattr(user.profile, name, value)
    user.profile.save()
    return user


def posted(user, **fields) -> dict:
    """What the page posts, with nothing but the name filled in unless told otherwise."""
    data = {
        "first_name": "Alex",
        "last_name": "Morgan",
        "form_of_address": "",
        "form_of_address_other": "",
        "pronouns": "",
        "pronouns_other": "",
        "headline": "",
        "location": "",
        "record_language": "",
    }
    data.update(fields)
    return data


def options(html: str, name: str) -> list[str]:
    """The values a menu offers, in order."""
    select = re.search(rf'<select name="{name}"[^>]*>(.*?)</select>', html, re.S)
    assert select, f"no menu called {name}"
    return re.findall(r'<option value="([^"]*)"', select.group(1))


def chosen(html: str, name: str) -> str:
    select = re.search(rf'<select name="{name}"[^>]*>(.*?)</select>', html, re.S)
    found = re.search(r'<option value="([^"]*)"[^>]*selected', select.group(1))
    return found.group(1) if found else ""


def box(html: str, name: str) -> str:
    """The value the server drew into a text box, or nothing."""
    tag = re.search(rf'<input type="text" name="{name}"[^>]*>', html)
    assert tag, f"no box called {name}"
    value = re.search(r'value="([^"]*)"', tag.group(0))
    return value.group(1) if value else ""


def page(client, user) -> str:
    client.force_login(user)
    return client.get(reverse("accounts:profile")).content.decode()


# ------------------------------------------------------------------------- the lists


@pytest.mark.parametrize(
    ("language", "forms", "pronouns"),
    [
        ("en-gb", "Mx", "they/them"),
        ("fr-fr", "Mme", "iel"),
        ("pt-pt", "Eng.ª", "elu/delu"),
        ("pt-br", "Sra.", "elu/delu"),
    ],
)
def test_each_language_has_its_own_list(language, forms, pronouns):
    """Data, not translations: *Eng.ª* is Portuguese usage and translates nothing."""
    assert forms in addressing.forms_of_address(language)
    assert pronouns in addressing.pronouns(language)


def test_english_keeps_the_full_stop_on_a_word_cut_at_its_end():
    """The rule the list cites (*New Hart's Rules*): a contraction, which keeps the word's
    last letter, drops the stop -- Mr, Dr -- and a word cut off at its end keeps it."""
    english = addressing.forms_of_address("en-gb")
    assert "Prof." in english and "Prof" not in english
    assert {"Mr", "Mrs", "Ms", "Mx", "Dr"} <= set(english), "the contractions have none"


def test_the_lists_are_not_one_list_translated():
    assert "Eng.ª" not in addressing.forms_of_address("pt-br"), "Portuguese, not Brazilian"
    assert "Sr.ª" in addressing.forms_of_address("pt-pt")
    assert "Sra." in addressing.forms_of_address("pt-br")
    assert "Me" not in addressing.forms_of_address("en-gb")


def test_a_language_with_no_list_offers_none_and_a_variant_takes_its_familys():
    assert addressing.forms_of_address("de") == () and addressing.pronouns("de") == ()
    assert addressing.forms_of_address("pt-ao") == addressing.forms_of_address("pt-pt")
    assert addressing.written_in("pt-ao") == "pt-pt"
    assert addressing.written_in("de") == ""


# ---------------------------------------------------------------------- the model


def test_both_start_blank_and_nothing_is_assumed(client, user):
    named(user)
    assert user.profile.form_of_address == "" and user.profile.pronouns == ""
    html = page(client, user)
    assert chosen(html, "form_of_address") == "" and chosen(html, "pronouns") == ""
    assert box(html, "form_of_address_other") == "" and box(html, "pronouns_other") == ""


# ------------------------------------------------------------------------ the page


def test_the_list_follows_the_language_of_the_career_record(client, user):
    """Not the interface's, when the record says: the name is written beside the record."""
    named(user, record_language="fr-fr")
    html = page(client, user)
    assert options(html, "form_of_address") == ["", "M.", "Mme", "Dr", "Pr", "Me", "other"]
    assert options(html, "pronouns") == ["", "elle", "il", "iel", "other"]
    # Said to be French, so a screen reader reading the English page says *Mme* in French.
    assert re.search(r'<option value="Mme" lang="fr"', html)
    assert not re.search(r'<option value="other" lang=', html), "Other… is the page's word"


def test_the_interface_language_when_the_record_says_nothing(user):
    named(user)
    with translation.override("pt-br"):
        form = ProfileForm(instance=user.profile)
        assert "Sra." in [value for value, _label in form.fields["form_of_address"].widget.choices]
    with translation.override("en-gb"):
        form = ProfileForm(instance=user.profile)
        assert "Mx" in [value for value, _label in form.fields["form_of_address"].widget.choices]


def test_a_language_with_no_list_offers_only_other(client, user):
    named(user, record_language="de")
    html = page(client, user)
    assert options(html, "form_of_address") == ["", "other"]
    assert options(html, "pronouns") == ["", "other"]


def test_a_stored_value_in_the_list_is_chosen_in_the_menu(client, user):
    named(user, form_of_address="Dr", pronouns="they/them", record_language="en-gb")
    html = page(client, user)
    assert chosen(html, "form_of_address") == "Dr" and chosen(html, "pronouns") == "they/them"
    assert box(html, "form_of_address_other") == "" and box(html, "pronouns_other") == ""


def test_a_stored_value_not_in_the_list_shows_as_other_with_its_text(client, user):
    """Typed as Other, or chosen from another language's list before the record changed
    language: either way it is the answer, shown where it can be read and changed."""
    named(user, form_of_address="Eng.ª", pronouns="xe/xem", record_language="en-gb")
    html = page(client, user)
    assert chosen(html, "form_of_address") == "other"
    assert box(html, "form_of_address_other") == "Eng.ª"
    assert chosen(html, "pronouns") == "other"
    assert box(html, "pronouns_other") == "xe/xem"


def test_a_prof_stored_before_the_list_gained_its_full_stop_still_shows(client, user):
    """What is stored is the text, so a list being corrected loses nobody's answer."""
    named(user, form_of_address="Prof", record_language="en-gb")
    html = page(client, user)
    assert chosen(html, "form_of_address") == "other"
    assert box(html, "form_of_address_other") == "Prof"


def test_what_is_listed_and_what_is_typed_keep_their_own_direction(client, user):
    """On a page drawn right to left, *Sr.* and *Prof. Dr.* are otherwise laid out as
    right-to-left text, and the full stop is drawn at the wrong end. The listed options
    say which way their language is written; the boxes take it from what is typed."""
    named(user, language="ar", record_language="pt-pt", form_of_address="Prof.")
    html = page(client, user)
    assert '<html lang="ar" dir="rtl"' in html
    for text in addressing.forms_of_address("pt-pt") + addressing.pronouns("pt-pt"):
        assert f'<option value="{text}" lang="pt-pt" dir="ltr"' in html.replace(" selected", ""), (
            text
        )
    for words in ("", "other"):
        assert re.search(rf'<option value="{words}"( selected)?>', html), "the page's own words"
    for name in ("form_of_address_other", "pronouns_other"):
        tag = re.search(rf'<input type="text" name="{name}"[^>]*>', html).group(0)
        assert 'dir="auto"' in tag, name


def test_the_box_is_drawn_for_the_stylesheet_to_show_only_for_other(client, user):
    """The rule #284 made for a kind's name, adopted by a group that is not a row: the box is
    always in the page, and the stylesheet hides it unless Other is chosen."""
    html = page(client, user)
    for marker in ("data-form-of-address", "data-pronouns"):
        group = re.search(rf"<div [^>]*data-if-other {marker}>", html)
        assert group, marker
    assert html.count("data-name-if-other") >= 2
    from pathlib import Path

    css = Path(__file__).resolve().parents[1] / "src/postulo/static/css/app.css"
    text = css.read_text(encoding="utf-8")
    assert (
        '[data-if-other]:not([data-has-name]):not(:has(select option[value="other"]:checked))'
        in text
    )


def test_the_form_of_address_says_what_it_is_for(client, user):
    """SC 1.3.5: a form of address is `honorific-prefix`, on the menu and on the box."""
    html = page(client, user)
    assert re.search(r'<select name="form_of_address"[^>]*autocomplete="honorific-prefix"', html)
    assert re.search(
        r'<input type="text" name="form_of_address_other"[^>]*autocomplete="honorific-prefix"',
        html,
    )


def test_the_page_says_neither_is_printed_yet(client, user):
    html = page(client, user)
    assert "Neither is printed on your CVs or letters for now." in html


def test_both_menus_are_described_by_the_sentence_that_explains_them(client, user):
    """One sentence under the card, named by each menu, and there to be named."""
    html = page(client, user)
    for name in ("form_of_address", "pronouns"):
        tag = re.search(rf'<select name="{name}"[^>]*>', html).group(0)
        assert 'aria-describedby="name-addressing-help"' in tag, name
    assert html.count('id="name-addressing-help"') == 1


# ------------------------------------------------------------------------ the form


def test_a_choice_from_the_list_is_stored_as_its_text(client, user):
    named(user, record_language="pt-pt")
    client.force_login(user)
    response = client.post(
        reverse("accounts:profile"),
        posted(user, form_of_address="Eng.ª", pronouns="ela/dela", record_language="pt-pt"),
    )
    assert response.status_code == 302
    user.profile.refresh_from_db()
    assert (user.profile.form_of_address, user.profile.pronouns) == ("Eng.ª", "ela/dela")


def test_other_stores_what_was_typed(client, user):
    named(user)
    client.force_login(user)
    client.post(
        reverse("accounts:profile"),
        posted(
            user,
            form_of_address="other",
            form_of_address_other="  Rev  ",
            pronouns="other",
            pronouns_other="she/they",
        ),
    )
    user.profile.refresh_from_db()
    assert (user.profile.form_of_address, user.profile.pronouns) == ("Rev", "she/they")


def test_a_box_left_behind_by_a_choice_from_the_list_is_not_the_answer(user):
    """Hidden text is never what gets saved: the menu says Mr, and Mr it is."""
    named(user)
    form = ProfileForm(
        posted(user, form_of_address="Mr", form_of_address_other="Sir"), instance=user.profile
    )
    assert form.is_valid(), form.errors
    assert form.save().form_of_address == "Mr"


def test_other_with_nothing_typed_asks_for_it(user):
    named(user)
    form = ProfileForm(posted(user, pronouns="other", pronouns_other=" "), instance=user.profile)
    assert not form.is_valid()
    assert "pronouns_other" in form.errors


def test_the_empty_choice_clears_it(user):
    named(user, form_of_address="Dr", pronouns="he/him")
    form = ProfileForm(posted(user), instance=user.profile)
    assert form.is_valid(), form.errors
    profile = form.save()
    assert (profile.form_of_address, profile.pronouns) == ("", "")


def test_a_menu_takes_text_from_a_list_it_no_longer_shows(user):
    """The career record's language changed between drawing the page and posting it: the
    answer is still an answer, because what is stored is the text."""
    named(user, record_language="en-gb")
    form = ProfileForm(posted(user, form_of_address="Mme"), instance=user.profile)
    assert form.is_valid(), form.errors
    assert form.save().form_of_address == "Mme"


def test_neither_may_run_past_its_column(user):
    named(user)
    long = "x" * 41
    form = ProfileForm(
        posted(user, form_of_address="other", form_of_address_other=long), instance=user.profile
    )
    assert not form.is_valid() and "form_of_address_other" in form.errors
    form = ProfileForm(posted(user, pronouns=long), instance=user.profile)
    assert not form.is_valid() and "pronouns" in form.errors


def test_a_box_that_is_too_long_says_that_and_nothing_else(user):
    """It was also told to type something, under text it had just refused for its length."""
    named(user)
    form = ProfileForm(
        posted(user, form_of_address="other", form_of_address_other="x" * 41),
        instance=user.profile,
    )
    assert not form.is_valid()
    assert list(form.errors) == ["form_of_address_other"]
    (message,) = form.errors["form_of_address_other"]
    assert "40" in message and "Type it here" not in message


def test_what_is_wrong_with_a_box_the_menu_did_not_choose_is_not_an_error(user):
    """The menu says Mr, and a hidden box holds 41 characters or a NUL: that text was never
    going to be saved, and its error was drawn inside a box the stylesheet hides -- a page
    refused with nothing on screen to say why."""
    named(user)
    for left_behind in ("z" * 41, "a\x00b"):
        form = ProfileForm(
            posted(user, form_of_address="Mr", form_of_address_other=left_behind),
            instance=user.profile,
        )
        assert form.is_valid(), form.errors
        assert form.save().form_of_address == "Mr"


def test_the_empty_other_message_names_the_list_and_not_where_it_is(user):
    """On a phone the menu is above the box, not beside it (WCAG 1.3.3)."""
    named(user)
    form = ProfileForm(posted(user, pronouns="other"), instance=user.profile)
    assert not form.is_valid()
    assert form.errors["pronouns_other"] == ["Type it here, or choose one from the list."]


@pytest.mark.parametrize("language", ["fr-fr", "pt-pt", "pt-br"])
def test_its_translations_name_no_position_and_agree_with_either_menu(language):
    """One sentence serves a feminine noun (*forma de tratamento*) and a masculine plural
    (*pronomes*), so the Portuguese says neither *um* nor *uma*; and none says *beside*."""
    from postulo.core import messages_tool

    catalogue = messages_tool.parse(messages_tool.po_path(language).read_text(encoding="utf-8"))
    (text,) = catalogue.messages[(None, "Type it here, or choose one from the list.")].msgstr
    assert text, language
    lowered = f" {text.lower()} "
    for word in (" um ", " uma ", " ao lado", " à côté", " ci-contre"):
        assert word not in lowered, (language, text)


def test_a_page_that_comes_back_keeps_a_choice_from_a_list_it_no_longer_shows(client, user):
    """Drawn in French with *Mme* chosen; the record becomes English in another tab; the
    first tab is posted with something else wrong. The menu drawn back used to have no
    *Mme* to select, so the browser showed *Not stated* and the next save cleared it."""
    named(user, record_language="en-gb", form_of_address="Mme")
    client.force_login(user)
    response = client.post(
        reverse("accounts:profile"),
        posted(user, last_name="", form_of_address="Mme", record_language="en-gb"),
    )
    assert response.status_code == 200, "the page comes back, because the last name is missing"
    html = response.content.decode()
    assert chosen(html, "form_of_address") == "Mme"
    values = options(html, "form_of_address")
    assert values[-2:] == ["Mme", "other"] and "Mr" in values, values
    # It is nobody's list's, so it claims no language, and its direction is its letters'.
    assert re.search(r'<option value="Mme" selected dir="auto">', html)
    # Sent again as drawn, it is saved.
    client.post(
        reverse("accounts:profile"),
        posted(user, form_of_address=chosen(html, "form_of_address"), record_language="en-gb"),
    )
    user.profile.refresh_from_db()
    assert user.profile.form_of_address == "Mme"


def test_a_page_that_comes_back_offers_nothing_extra_for_an_ordinary_choice(client, user):
    named(user, record_language="en-gb")
    client.force_login(user)
    for sent in ("Dr", "other", ""):
        html = client.post(
            reverse("accounts:profile"),
            posted(user, last_name="", form_of_address=sent, form_of_address_other="Rev"),
        ).content.decode()
        assert options(html, "form_of_address") == [
            "",
            *addressing.forms_of_address("en-gb"),
            "other",
        ]
        assert chosen(html, "form_of_address") == sent


def test_a_menus_own_error_is_in_its_description(client, user):
    """Both menus name the sentence under the card, and that used to be all they named: an
    error drawn under a menu was tied to nothing a screen reader would read with it."""
    named(user)
    client.force_login(user)
    html = client.post(
        reverse("accounts:profile"), posted(user, pronouns="p" * 41)
    ).content.decode()
    tag = re.search(r'<select name="pronouns"[^>]*>', html).group(0)
    assert 'aria-describedby="name-addressing-help id_pronouns_error"' in tag
    assert 'aria-invalid="true"' in tag
    assert html.count('id="id_pronouns_error"') == 1
    # The other menu has no error, and names none.
    other = re.search(r'<select name="form_of_address"[^>]*>', html).group(0)
    assert 'aria-describedby="name-addressing-help"' in other


# -------------------------------------------------------------------- not printed yet


def test_neither_is_printed_on_a_cv_yet(user):
    """#308 lets a CV choose to print them. Until then, nothing does."""
    named(user, form_of_address="Dr.ª", pronouns="elu/delu")
    cv = CV.objects.create(owner=user, name="Main")
    html = render_cv_html(cv)
    assert "Alex Morgan" in html
    assert "Dr.ª" not in html and "elu/delu" not in html
    details = contact_details(user)
    assert "Dr.ª" not in json.dumps(details, default=str)


# ------------------------------------------------------------------------ the location


def test_a_blank_location_prints_the_primary_addresss_town_and_country(user):
    named(user)
    an_address(user)
    assert postal.printed_location(user.profile) == "Lisboa, Portugal"
    assert contact_details(user)["location"] == "Lisboa, Portugal"
    cv = CV.objects.create(owner=user, name="Main")
    html = render_cv_html(cv)
    assert "Lisboa, Portugal" in html
    assert "Rua do Exemplo" not in html, "never the street"


def test_a_typed_location_prints_itself(user):
    named(user, location="Porto, or anywhere remote")
    an_address(user)
    assert postal.printed_location(user.profile) == "Porto, or anywhere remote"
    assert contact_details(user)["location"] == "Porto, or anywhere remote"
    html = render_cv_html(CV.objects.create(owner=user, name="Main"))
    assert "Porto, or anywhere remote" in html and "Lisboa, Portugal" not in html


def test_no_location_and_no_address_prints_nothing(user):
    named(user)
    assert postal.printed_location(user.profile) == ""
    details = contact_details(user)
    assert details["location"] == ""
    assert "" not in details["details"] and "" not in details["brief_details"]


def test_a_new_primary_address_moves_the_location_without_saving_the_profile(user):
    """The blank is what is stored, so nothing has to be copied anywhere when it changes."""
    named(user)
    an_address(user)
    stamp = user.profile.updated_at
    berlin = an_address(user, municipality="Berlin", country="DE", is_primary=False)
    postal.make_primary(berlin)

    user.profile.refresh_from_db()
    assert user.profile.location == "" and user.profile.updated_at == stamp
    assert postal.printed_location(user.profile) == "Berlin, Germany"


def test_the_letters_sender_block_prints_the_same_location(user):
    named(user)
    an_address(user)
    assert "Lisboa, Portugal" in contact_details(user)["brief_details"]


def test_the_page_shows_the_derived_location_and_where_it_came_from(client, user):
    named(user)
    an_address(user)
    html = page(client, user)
    tag = re.search(r'<input type="text" name="location"[^>]*>', html).group(0)
    assert 'placeholder="Lisboa, Portugal"' in tag
    assert "the town and country of your primary postal address" in html
    assert "value=" not in tag, "the blank is what is stored and what is drawn"


def test_without_an_address_the_page_says_what_a_blank_will_do(client, user):
    named(user)
    html = page(client, user)
    tag = re.search(r'<input type="text" name="location"[^>]*>', html).group(0)
    assert "placeholder" not in tag
    assert "once you have one" in html


def test_saving_a_blank_location_keeps_it_blank(client, user):
    named(user)
    an_address(user)
    client.force_login(user)
    client.post(reverse("accounts:profile"), posted(user))
    user.profile.refresh_from_db()
    assert user.profile.location == ""


# ------------------------------------------------------------------------- the archive


def test_both_travel_in_the_archive_and_come_back(user, other_user):
    named(user, form_of_address="Mx", pronouns="they/them")
    document = export.build_document(user)
    assert document["account"]["profile"]["form_of_address"] == "Mx"
    assert document["account"]["profile"]["pronouns"] == "they/them"

    importer.load(other_user, zipfile.ZipFile(export.write_archive(user)))
    other_user.profile.refresh_from_db()
    assert other_user.profile.form_of_address == "Mx"
    assert other_user.profile.pronouns == "they/them"


def test_the_archive_keeps_a_blank_location_blank(user):
    """It stores, it does not print: writing the town in would pin it to today's address."""
    named(user)
    an_address(user)
    assert export.build_document(user)["account"]["profile"]["location"] == ""


def test_an_older_archive_without_them_restores_both_blank(user, other_user):
    named(user, form_of_address="Mx", pronouns="they/them")
    document = export.build_document(user)
    document["postulo"]["format"] = 25
    del document["account"]["profile"]["form_of_address"]
    del document["account"]["profile"]["pronouns"]
    buffer = BytesIO()
    with zipfile.ZipFile(buffer, "w") as archive:
        archive.writestr(export.MANIFEST_NAME, json.dumps(document, default=str))
    buffer.seek(0)

    importer.load(other_user, zipfile.ZipFile(buffer))
    other_user.profile.refresh_from_db()
    assert (other_user.profile.form_of_address, other_user.profile.pronouns) == ("", "")


def test_the_candidate_file_carries_them_and_fills_a_blank(user, other_user):
    """The file of #181 carries the name, so it carries what goes beside it."""
    named(user, form_of_address="Eng.ª", pronouns="ela/dela")
    data = json.dumps(export.build_candidate_document(user)).encode()
    held = candidate.read(data)
    assert held["details"]["form_of_address"] == "Eng.ª"

    named(other_user, pronouns="she/her")
    candidate.apply(other_user, held)
    other_user.profile.refresh_from_db()
    assert other_user.profile.form_of_address == "Eng.ª", "a blank is filled"
    assert other_user.profile.pronouns == "she/her", "an answer is kept"


def test_a_candidate_file_with_one_too_long_is_refused_for_that_row(user):
    named(user)
    document = export.build_candidate_document(user)
    document["account"]["profile"]["pronouns"] = "x" * 41
    plan = candidate.plan(user, candidate.read(json.dumps(document).encode()))
    refused = [
        row
        for section in plan.sections
        if section.key == "details"
        for row in section.rows
        if row.outcome == candidate.REFUSED
    ]
    assert [row.label for row in refused] == ["Pronouns"]


# ----------------------------------------------------------------------------- the API


def bearer(user, *scopes) -> dict:
    _record, raw = ApiToken.issue(user, "Agent", scopes=scopes or ("read",))
    return {"HTTP_AUTHORIZATION": f"Bearer {raw}"}


def patch(client, payload, **headers):
    return client.patch(
        "/api/v1/profile", data=json.dumps(payload), content_type="application/json", **headers
    )


def test_the_api_reads_them_and_the_location_both_ways(client, user):
    named(user, form_of_address="Dr", pronouns="he/him")
    an_address(user)
    body = client.get("/api/v1/profile", **bearer(user)).json()
    assert body["first_name"] == "Alex" and body["last_name"] == "Morgan"
    assert (body["form_of_address"], body["pronouns"]) == ("Dr", "he/him")
    assert body["location"] == "", "as stored"
    assert body["printed_location"] == "Lisboa, Portugal", "as printed"


def test_the_api_writes_them_as_text(client, user):
    named(user)
    response = patch(
        client,
        {"form_of_address": "Eng.ª", "pronouns": " iel ", "location": "Porto"},
        **bearer(user, "write"),
    )
    assert response.status_code == 200, response.content
    body = response.json()
    assert (body["form_of_address"], body["pronouns"]) == ("Eng.ª", "iel")
    assert body["location"] == body["printed_location"] == "Porto"
    user.profile.refresh_from_db()
    assert (user.profile.form_of_address, user.profile.pronouns) == ("Eng.ª", "iel")


def test_a_field_left_out_is_left_alone_and_printed_location_is_not_written(client, user):
    named(user, form_of_address="Mx", headline="Engineer")
    response = patch(client, {"printed_location": "Mars"}, **bearer(user, "write"))
    assert response.status_code == 200
    user.profile.refresh_from_db()
    assert user.profile.form_of_address == "Mx" and user.profile.headline == "Engineer"
    assert user.profile.location == ""


def test_the_api_refuses_what_the_column_would_not_hold(client, user):
    named(user)
    for field in ("form_of_address", "pronouns"):
        response = patch(client, {field: "x" * 41}, **bearer(user, "write"))
        assert response.status_code == 422, field
        assert field in json.dumps(response.json())
    response = patch(client, {"location": "x" * 121}, **bearer(user, "write"))
    assert response.status_code == 422
    user.profile.refresh_from_db()
    assert user.profile.form_of_address == "" and user.profile.location == ""


def test_the_api_will_not_empty_a_name(client, user):
    named(user)
    response = patch(client, {"first_name": "   "}, **bearer(user, "write"))
    assert response.status_code == 422
    user.refresh_from_db()
    assert user.first_name == "Alex"


@pytest.mark.parametrize(
    "field", ["first_name", "last_name", "form_of_address", "pronouns", "headline", "location"]
)
def test_the_api_refuses_a_nul_as_the_page_does_and_names_the_field(client, user, field):
    """It answered 200 and stored it, where the page refuses, and where PostgreSQL, which
    cannot hold one in text, would have answered with a 500."""
    named(user, headline="Engineer")
    response = patch(client, {field: "a\x00b"}, **bearer(user, "write"))
    assert response.status_code == 422, response.content
    assert response["Content-Type"].startswith("application/problem+json")
    said = json.dumps(response.json())
    assert field in said and "Null characters are not allowed" in said
    user.refresh_from_db()
    user.profile.refresh_from_db()
    assert (user.first_name, user.last_name) == ("Alex", "Morgan")
    assert user.profile.headline == "Engineer"
    assert user.profile.form_of_address == user.profile.pronouns == user.profile.location == ""

    # The page's answer to the same text, which is the one the API now gives.
    form = ProfileForm(posted(user, **{field: "a\x00b"}), instance=user.profile)
    assert not form.is_valid() and field in form.errors


def test_the_api_strips_before_it_measures_as_the_page_does(client, user):
    """A location of 120 characters after a space was saved by the page and refused here."""
    named(user)
    padded = " " + "l" * 120 + " "
    response = patch(client, {"location": padded}, **bearer(user, "write"))
    assert response.status_code == 200, response.content
    assert response.json()["location"] == "l" * 120
    assert patch(client, {"location": "l" * 121}, **bearer(user, "write")).status_code == 422

    form = ProfileForm(posted(user, location=padded), instance=user.profile)
    assert form.is_valid(), form.errors


@pytest.mark.parametrize(
    "field", ["first_name", "last_name", "form_of_address", "pronouns", "headline", "location"]
)
def test_the_api_still_refuses_half_a_surrogate_pair(client, user, field):
    """Text that is not text. pydantic refuses it for a string it reads, and stripping
    before measuring very nearly stopped it reading these: the value went through to the
    database, which answered with a 500. See `api.schemas._line` for why the order of its
    annotations is what keeps this a 422."""
    named(user)
    response = patch(client, {field: "a\ud800b"}, **bearer(user, "write"))
    assert response.status_code == 422, response.content
    assert field in json.dumps(response.json())
    user.refresh_from_db()
    assert user.first_name == "Alex"


def test_the_api_still_says_how_long_each_may_be(client, user):
    """The bound moved from the field to its type; the description a client reads kept it."""
    schema = client.get("/api/v1/openapi.json", **bearer(user)).json()
    properties = schema["components"]["schemas"]["ProfilePatch"]["properties"]
    longest = {
        "first_name": 150,
        "last_name": 150,
        "form_of_address": 40,
        "pronouns": 40,
        "headline": 200,
        "location": 120,
    }
    for name, length in longest.items():
        assert f'"maxLength": {length}' in json.dumps(properties[name]), name


def test_a_change_to_the_name_alone_moves_updated_at(client, user):
    """The name is on the account and the stamp on the profile; the page moves it on every
    save, and a client comparing stamps was told that nothing had changed."""
    named(user)
    before = user.profile.updated_at
    response = patch(client, {"first_name": "Alexandra"}, **bearer(user, "write"))
    assert response.status_code == 200
    user.profile.refresh_from_db()
    assert user.profile.updated_at > before
    assert response.json()["first_name"] == "Alexandra"

    # And one that changes nothing leaves it where it was.
    stamp = user.profile.updated_at
    assert patch(client, {}, **bearer(user, "write")).status_code == 200
    user.profile.refresh_from_db()
    assert user.profile.updated_at == stamp


# ------------------------------------------------------------------ the CV importer


def test_an_import_that_brings_an_address_leaves_the_location_to_it(user):
    """It typed the file's town into the location and then wrote the address from the same
    file: the location was pinned by text nobody typed, printed less than the blank would
    have, and stayed behind when the address moved."""
    from pathlib import Path

    from postulo.plugins.europass import reader
    from postulo.resume import importing

    named(user)
    record = reader.read((Path(__file__).parent / "data/europass-candidate.xml").read_bytes())
    assert record.person["location"] == "Lisboa", "the file does say where"
    report = importing.apply(user, record)

    user.profile.refresh_from_db()
    assert user.profile.location == ""
    assert "address" in report.profile_filled and "location" not in report.profile_filled
    assert postal.printed_location(user.profile) == "Lisboa, Portugal"

    berlin = an_address(user, municipality="Berlin", country="DE", is_primary=False)
    postal.make_primary(berlin)
    assert postal.printed_location(user.profile) == "Berlin, Germany"


def test_an_import_leaves_the_location_blank_where_there_is_already_an_address(user):
    from postulo.resume import importing

    named(user)
    an_address(user, municipality="Porto")
    report = importing.apply(user, importing.Record(person={"location": "Lisboa"}))

    user.profile.refresh_from_db()
    assert user.profile.location == "" and report.profile_filled == []
    assert postal.printed_location(user.profile) == "Porto, Portugal"


def test_an_import_with_a_place_and_no_address_still_fills_the_location(user):
    """Nothing else would say where: a blank with no address prints nothing."""
    from postulo.resume import importing

    named(user)
    record = importing.Record(person={"location": "Lisboa", "address": {"country": "PT"}})
    report = importing.apply(user, record)

    user.profile.refresh_from_db()
    assert user.profile.location == "Lisboa" and report.profile_filled == ["location"]
    assert not postal.for_holder(user.profile).exists(), "a country alone is not an address"
