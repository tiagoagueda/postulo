"""What the server writes for a select's own control to draw (#301).

Every `<select>` is drawn as a native select, and `app.js` builds Basecoat's select beside
it: a button and a list whose options can hold a flag or an icon. The browser tests
(`tests/e2e/test_select.py`) drive that control. This file reads what it is built from,
which is the server's half and all there is with scripts off:

- an option says the icon it draws in `data-icon`, and the icons a list can show are
  drawn once, after the select, for the script to copy;
- every kind has an icon, the icon is one Postulo ships, and none of them is anybody's
  mark;
- every language and every country carries its flag's address;
- time zones are in groups, by the area their name begins with;
- the page carries the parts and the words the control is made of.
"""

from __future__ import annotations

import re
import zoneinfo
from pathlib import Path

import pytest
from django import forms
from django.template import Context, Template
from django.urls import reverse

from postulo.core import option_icons
from postulo.core.option_icons import IconSelect

ROOT = Path(__file__).resolve().parents[1]

#: The icons Postulo ships, as `assets/icons.txt` lists them.
SHIPPED = {
    line.strip()
    for line in (ROOT / "assets" / "icons.txt").read_text(encoding="utf-8").splitlines()
    if line.strip() and not line.startswith("#")
}

#: Names in the Lucide set that are somebody's mark. `TRADEMARKS.md` keeps them out of the
#: interface, and #301 decided that a list of kinds is no exception.
MARKS = {
    "bitbucket",
    "chrome",
    "chromium",
    "codepen",
    "codesandbox",
    "dribbble",
    "facebook",
    "figma",
    "framer",
    "github",
    "gitlab",
    "instagram",
    "linkedin",
    "pocket",
    "slack",
    "trello",
    "twitch",
    "twitter",
    "youtube",
}


def icons_after(html: str) -> list[str]:
    """The icons drawn in the template after a select, by name, in order."""
    template = re.search(r"<template data-option-icons>(.*?)</template>", html, re.S)
    return re.findall(r'data-icon="([a-z0-9-]+)"', template.group(1)) if template else []


# ------------------------------------------------------------------- the widget's half


def test_a_select_names_an_icon_on_each_option_and_draws_each_icon_once():
    widget = IconSelect(
        choices=[("", "—"), ("a", "Mobile"), ("b", "Work"), ("c", "Another mobile")],
        icons={"a": "smartphone", "b": "briefcase", "c": "smartphone"},
    )
    html = widget.render("kind", "b", attrs={"id": "id_kind"})

    assert '<option value="">—</option>' in html, "an option with no icon says none"
    assert '<option value="a" data-icon="smartphone">Mobile</option>' in html
    assert '<option value="b" data-icon="briefcase" selected>Work</option>' in html
    # After the select, and not inside it: an <option> holds words and nothing else.
    assert re.search(r"</select>\s*<template data-option-icons>", html)
    assert icons_after(html) == ["smartphone", "briefcase"], "each once, however many name it"
    # Drawn as every other icon is, decorative: the option's words say what it is.
    template = html[html.index("<template") :]
    assert template.count("<svg") == 2 and template.count('aria-hidden="true"') == 2


def test_an_icon_postulo_does_not_ship_costs_a_picture_and_not_a_page():
    """A plugin names icons, and a name it got wrong must not raise: `{% icon %}` does, which
    is right for a template somebody wrote and wrong for data."""
    widget = IconSelect(
        choices=[("a", "One"), ("b", "Two")], icons={"a": "no-such-icon", "b": "../etc"}
    )
    html = widget.render("kind", "a", attrs={"id": "id_kind"})

    assert "data-icon" not in html and "<template" not in html
    assert not option_icons.shipped("no-such-icon")
    assert not option_icons.shipped("../etc") and not option_icons.shipped("")
    assert option_icons.shipped("briefcase")


def test_a_select_with_no_icons_is_djangos_own_select():
    plain = forms.Select(choices=[("a", "One")]).render("x", "a", attrs={"id": "id_x"})
    ours = IconSelect(choices=[("a", "One")]).render("x", "a", attrs={"id": "id_x"})
    assert ours.strip() == plain.strip()


def test_the_tag_draws_the_icons_a_template_asks_for_once_each():
    """For a select a template writes out: the board draws its statuses' icons once for
    every card on it."""
    drawn = Template("{% load postulo %}{% option_icons names %}").render(
        Context({"names": ["pencil", "send", "pencil", "no-such-icon"]})
    )
    assert drawn.startswith("<template data-option-icons>") and drawn.endswith("</template>")
    assert re.findall(r'data-icon="([a-z0-9-]+)"', drawn) == ["pencil", "send"]
    nothing = Template("{% load postulo %}{% option_icons names %}").render(Context({"names": []}))
    assert nothing == ""


# ---------------------------------------------------------------------- every kind's icon


def kinds() -> dict[str, dict[str, str]]:
    """Every table of kinds to icons, by what it is for."""
    from postulo.applications.models import STATUS_ICONS
    from postulo.core import identifiers, link_services, messaging_services, phone_numbers, postal

    return {
        "telephone kinds": dict(phone_numbers.KIND_ICONS),
        "address kinds": dict(postal.KIND_ICONS),
        "statuses": dict(STATUS_ICONS),
        "identifier schemes": {
            key: scheme.icon_name for key, scheme in identifiers.registry().items()
        },
        "link services": {
            **{key: service.icon_name for key, service in link_services.registry().items()},
            "other": link_services.OTHER_ICON,
        },
        "messaging services": {
            **{key: service.icon_name for key, service in messaging_services.registry().items()},
            "other": messaging_services.OTHER_ICON,
        },
    }


@pytest.mark.django_db
def test_every_kind_has_an_icon_postulo_ships_and_none_is_anybodys_mark():
    """Decided on #301: a generic Lucide icon per kind, as #305 did for the services of a web
    link. LinkedIn is a person, a forge is a branch, an ORCID is a mortarboard."""
    from postulo.applications.models import Status
    from postulo.core.models import PhoneNumber, PostalAddress

    tables = kinds()
    assert set(tables["telephone kinds"]) == set(PhoneNumber.Kind.values)
    assert set(tables["address kinds"]) == set(PostalAddress.Kind.values)
    assert set(tables["statuses"]) == set(Status.values)
    assert len(tables["identifier schemes"]) >= 10 and len(tables["link services"]) >= 10

    for what, table in tables.items():
        for value, icon in table.items():
            assert icon in SHIPPED, f"{what}: {value} names {icon!r}, which is not shipped"
            assert option_icons.shipped(icon), f"{what}: {icon}.svg is not in static/icons"
            assert icon not in MARKS, f"{what}: {value} draws {icon!r}, which is a mark"


def test_a_scheme_that_names_no_icon_or_one_postulo_lacks_draws_the_generic_one():
    from postulo.core import identifiers

    def scheme(icon: str) -> identifiers.Scheme:
        return identifiers.Scheme("staff", "Staff number", re.compile(r"^\d+$"), icon=icon)

    assert scheme("").icon_name == identifiers.ICON == "id-card"
    assert scheme("somebody-elses-logo").icon_name == identifiers.ICON
    assert scheme("landmark").icon_name == "landmark"


# ------------------------------------------------------------------------- on the pages


def options_of(html: str, name: str) -> list[tuple[str, str]]:
    """Each option of the select of this name, as its value and its other attributes."""
    select = re.search(rf'<select[^>]*name="{re.escape(name)}"[^>]*>(.*?)</select>', html, re.S)
    assert select, f"no select named {name}"
    return re.findall(r'<option value="([^"]*)"([^>]*)>', select.group(1))


@pytest.mark.django_db
def test_your_details_says_the_icon_of_every_kind_and_the_flag_of_every_country(client, user):
    from postulo.core import phones
    from postulo.core.models import PhoneNumber, PostalAddress

    PhoneNumber.objects.create(
        owner=user, holder=user.profile, kind="mobile", number="+351912345678"
    )
    PostalAddress.objects.create(
        owner=user, holder=user.profile, kind="home", municipality="Lisboa", country="PT"
    )
    client.force_login(user)
    html = client.get(reverse("accounts:profile")).content.decode()

    for name, table in (
        ("phone_numbers-0-kind", "telephone kinds"),
        ("addresses-0-kind", "address kinds"),
        ("identifiers-0-scheme", "identifier schemes"),
    ):
        wanted = kinds()[table]
        found = options_of(html, name)
        assert found, name
        for value, attrs in found:
            if value:
                assert f'data-icon="{wanted[value]}"' in attrs, (name, value)
            else:
                assert "data-icon" not in attrs, "the empty choice draws nothing"
        after = html[html.index(f'name="{name}"') :]
        after = after[after.index("</select>") : after.index("</template>") + 11]
        assert set(icons_after(after)) == {wanted[value] for value, _attrs in found if value}

    # A country, in an address and in front of a telephone number, carries its flag.
    for name in ("addresses-0-country", "phone_numbers-0-number_0"):
        found = [(value, attrs) for value, attrs in options_of(html, name) if value]
        assert len(found) == len(phones.COUNTRIES)
        for value, attrs in found:
            assert f"/flags/{value.lower()}" in attrs, (name, value)

    # And so does every language the career record can be written in, where it has one.
    from postulo.core import languages

    found = [(value, attrs) for value, attrs in options_of(html, "record_language") if value]
    assert len(found) > 20
    for value, attrs in found:
        country = languages.flag_country(value)
        if country:
            assert f"/flags/{country.lower()}" in attrs, value
        else:
            assert 'data-flag=""' in attrs, f"{value} has no single home and draws no flag"


@pytest.mark.django_db
def test_a_cvs_language_carries_its_flags(client, user):
    from postulo.documents.models import CV

    cv = CV.objects.create(owner=user, name="Main CV")
    client.force_login(user)
    html = client.get(reverse("documents:cv_update", args=[cv.pk])).content.decode()
    flagged = [attrs for value, attrs in options_of(html, "language") if "/flags/" in attrs]
    assert len(flagged) > 20


@pytest.mark.django_db
def test_the_board_says_each_statuss_icon_and_draws_the_icons_once(client, user):
    """Two hundred cards share one set of statuses: each card's options name their icons,
    and the icons are drawn once, by the page, outside what the live filters swap."""
    from postulo.applications.models import STATUS_ICONS, Application
    from postulo.jobs.models import Company, JobPosting

    company = Company.objects.create(owner=user, name="Aperture Science")
    for title in ("Test Engineer", "Portal Researcher", "Enrichment Associate"):
        posting = JobPosting.objects.create(owner=user, company=company, title=title)
        Application.objects.create(owner=user, posting=posting, status="applied")
    client.force_login(user)

    html = client.get(reverse("applications:list") + "?view=board").content.decode()
    assert html.count("data-card=") == 3
    assert html.count("<template data-option-icons>") == 1
    table = html[html.index('id="applications-table"') :]
    assert "<template data-option-icons>" not in table, "inside what a filter swaps"
    drawn = icons_after(html)
    assert drawn == list(dict.fromkeys(STATUS_ICONS.values()))
    for value, attrs in options_of(table, "status"):
        assert f'data-icon="{STATUS_ICONS[value]}"' in attrs, value

    # The fragment a live filter asks for is the board alone, and its cards name the icons
    # the page already holds.
    fragment = client.get(
        reverse("applications:list") + "?view=board", headers={"HX-Request": "true"}
    ).content.decode()
    assert "<template data-option-icons>" not in fragment
    assert 'data-icon="send"' in fragment

    # The table has no such list, and draws none.
    table_page = client.get(reverse("applications:list") + "?view=table").content.decode()
    assert "<template data-option-icons>" not in table_page


# --------------------------------------------------------------------------- time zones


def test_time_zones_are_grouped_by_the_area_their_name_begins_with():
    from postulo.accounts.forms import (
        OTHER_ZONES,
        TIME_ZONE_AREAS,
        time_zone_area,
        time_zone_choices,
    )

    choices = time_zone_choices(default="Europe/Paris")
    assert choices[0] == ("", "Europe/Paris — Default"), "the default comes first, in no group"
    groups = choices[1:]
    assert [str(label) for label, _zones in groups] == [
        "Africa",
        "Americas",
        "Antarctica",
        "Asia",
        "Atlantic Ocean",
        "Australia",
        "Europe",
        "Indian Ocean",
        "Pacific Ocean",
        "Other time zones",
    ]
    listed = [name for _label, zones in groups for name, _words in zones]
    assert sorted(listed) == sorted(zoneinfo.available_timezones()), "every zone, once"
    for _label, zones in groups:
        assert [name for name, _words in zones] == sorted(name for name, _words in zones)

    by_label = {str(label): [name for name, _words in zones] for label, zones in groups}
    assert "Europe/Lisbon" in by_label["Europe"]
    assert "America/Argentina/Buenos_Aires" in by_label["Americas"]
    assert all(name.startswith("Pacific/") for name in by_label["Pacific Ocean"])
    # The rest: the names with no area, and the areas the nine do not name.
    for name in ("UTC", "Etc/GMT+3", "US/Eastern", "Arctic/Longyearbyen"):
        assert name in by_label["Other time zones"], name
        assert time_zone_area(name) == OTHER_ZONES
    assert time_zone_area("Indian/Maldives") == "Indian" and "Indian" in TIME_ZONE_AREAS
    # A zone's words are still its whole name: the closed menu shows the choice alone.
    words = {name: said for _label, zones in groups for name, said in zones}
    assert words["Europe/Lisbon"] == "Europe/Lisbon"
    assert words["America/New_York"] == "America/New York"


@pytest.mark.django_db
def test_the_settings_page_draws_the_groups_and_keeps_a_zone_from_one(client, user):
    client.force_login(user)
    url = reverse("settings:locale")
    html = client.get(url).content.decode()
    select = re.search(r'<select[^>]*name="time_zone"[^>]*>(.*?)</select>', html, re.S).group(1)
    assert re.findall(r'<optgroup label="([^"]+)"', select)[:3] == [
        "Africa",
        "Americas",
        "Antarctica",
    ]
    europe = select[select.index('<optgroup label="Europe"') :]
    europe = europe[: europe.index("</optgroup>")]
    assert '<option value="Europe/Lisbon">Europe/Lisbon</option>' in europe
    assert "Asia/" not in europe

    posted = client.post(url, {"language": "", "time_zone": "Europe/Lisbon"})
    assert posted.status_code == 302
    user.profile.refresh_from_db()
    assert user.profile.time_zone == "Europe/Lisbon"
    refused = client.post(url, {"language": "", "time_zone": "Mars/Olympus_Mons"})
    assert refused.status_code == 200, "a zone in no group is still not a zone"


# ------------------------------------------------------------------- what the page carries


@pytest.mark.django_db
def test_every_page_carries_what_the_control_is_built_from(client, user):
    """The chevron and the tick, drawn by `{% icon %}` for the script to copy, and the three
    things the control may say, translated with the page: the script holds no words."""
    client.force_login(user)
    html = client.get(reverse("core:home")).content.decode()
    parts = re.search(r'<template id="select-parts"([^>]*)>(.*?)</template>', html, re.S)
    assert parts, "no select-parts on the page"
    attrs, drawn = parts.groups()
    assert 'data-filter="Filter"' in attrs
    assert 'data-empty="Nothing matches."' in attrs
    assert 'data-required="Choose one of the options."' in attrs
    assert re.findall(r'data-icon="([a-z-]+)"', drawn) == ["chevron-down", "check"]

    user.profile.language = "pt-PT"
    user.profile.save(update_fields=["language"])
    html = client.get(reverse("core:home")).content.decode()
    assert 'data-required="Escolha uma das opções."' in html
    assert 'data-empty="Nada corresponde."' in html


def sentences_in(code: str) -> list[str]:
    """The quoted strings of a script that read as words somebody would be shown: two
    words or more, and not a selector."""
    code = re.sub(r"/\*.*?\*/", "", code, flags=re.S)
    code = re.sub(r"^\s*//.*$", "", code, flags=re.M)
    return [
        text
        for text in re.findall(r'"([^"\n]*)"', code)
        if re.search(r"[A-Za-z]{2,} [A-Za-z]{2,}", text) and not re.search(r"[\[\]#:=>]", text)
    ]


def test_the_script_holds_no_words_of_its_own_for_a_select():
    """Whatever a select's control says comes from the page: `data-filter`, `data-empty` and
    `data-required` on `select-parts`. A sentence written into the script is a sentence
    nobody translates."""
    script = (ROOT / "src" / "postulo" / "static" / "js" / "app.js").read_text(encoding="utf-8")
    section = script[script.index("--- selects") : script.index("a row off *Your details*")]
    assert "function buildSelect" in section and "function fillSelectList" in section

    assert sentences_in(section) == []
    # The detector knows a sentence from a selector and from a comment.
    assert sentences_in('said.textContent = "Nothing matches.";') == ["Nothing matches."]
    assert sentences_in('root.querySelector("[data-select-trigger], [data-select-panel]");') == []
    assert sentences_in('// "Choose one" is for the page to say\nvar key = "flag " + flag;') == []
