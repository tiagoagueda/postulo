"""One identifier of each kind, offered as well as enforced (#307).

Both tables have always refused a second ORCID or a second Wikidata item, but the rows on
*Your details* and on a company's form offered every kind on every row, so the rule was found
out on saving -- and on a person in the constraint's own name. A kind another row holds is
switched off in a row's choice now, with `<option disabled>`, which needs no script. The
refusal stays, because two new rows typed in one go can still pick the same kind, and it
names the kind on the row.

Every test runs on both pages: one component draws the rows and one mixin holds the rule.
The kinds used, Wikidata and LinkedIn, identify people and companies alike.
"""

from __future__ import annotations

import re
from collections.abc import Callable
from dataclasses import dataclass
from html.parser import HTMLParser

import pytest
from django.urls import reverse

from postulo.accounts.models import PersonIdentifier
from postulo.jobs.models import Company, CompanyIdentifier

pytestmark = pytest.mark.django_db


@dataclass
class Holder:
    """A page that draws identifier rows, and what it needs besides them."""

    url: str
    form: dict[str, str]
    add: Callable[..., object]
    saved: Callable[[], list[tuple[str, str]]]


@pytest.fixture(params=["your details", "a company"])
def holder(request, client, user) -> Holder:
    client.force_login(user)
    if request.param == "your details":
        return Holder(
            url=reverse("accounts:profile"),
            form={
                "first_name": "Alex",
                "last_name": "Morgan",
                "headline": "",
                "phone_0": "",
                "phone_1": "",
                "location": "",
                "website": "",
                "linkedin_url": "",
                "source_repo_url": "",
            },
            add=lambda scheme, value, label="": PersonIdentifier.objects.create(
                profile=user.profile, scheme=scheme, value=value, label=label
            ),
            saved=lambda: list(user.profile.identifiers.values_list("scheme", "value")),
        )
    company = Company.objects.create(owner=user, name="Acme")
    return Holder(
        url=reverse("jobs:company_update", args=[company.pk]),
        form={"name": company.name, "kind": company.kind},
        add=lambda scheme, value, label="": CompanyIdentifier.objects.create(
            owner=user, company=company, scheme=scheme, value=value, label=label
        ),
        saved=lambda: list(company.identifiers.values_list("scheme", "value")),
    )


def row(scheme: str, value: str, *, saved=None, remove: bool = False) -> dict:
    return {"saved": saved, "scheme": scheme, "value": value, "remove": remove}


def posted(holder: Holder, *rows: dict) -> dict:
    """The page as submitted: the rest of its form, then the rows, saved ones first."""
    data = {
        **holder.form,
        "identifiers-TOTAL_FORMS": str(len(rows)),
        "identifiers-INITIAL_FORMS": str(sum(1 for r in rows if r["saved"] is not None)),
        "identifiers-MIN_NUM_FORMS": "0",
        "identifiers-MAX_NUM_FORMS": "1000",
    }
    for index, r in enumerate(rows):
        prefix = f"identifiers-{index}-"
        if r["saved"] is not None:
            data[prefix + "id"] = str(r["saved"].pk)
        data[prefix + "scheme"] = r["scheme"]
        data[prefix + "value"] = r["value"]
        data[prefix + "label"] = ""
        if r["remove"]:
            data[prefix + "DELETE"] = "on"
    return data


class Selects(HTMLParser):
    """Every select on a page by name: each option's value, and whether it is switched off."""

    def __init__(self, html: str) -> None:
        super().__init__()
        self.found: dict[str, dict[str, bool]] = {}
        self._open: dict[str, bool] | None = None
        self.feed(html)

    def handle_starttag(self, tag, attrs):
        attrs = dict(attrs)
        if tag == "select":
            self._open = self.found.setdefault(attrs.get("name") or "", {})
        elif tag == "option" and self._open is not None:
            self._open[attrs.get("value") or ""] = "disabled" in attrs

    def handle_endtag(self, tag):
        if tag == "select":
            self._open = None


def switched_off(html: str, index: int) -> set[str]:
    options = Selects(html).found[f"identifiers-{index}-scheme"]
    return {value for value, disabled in options.items() if disabled}


def kind_error(html: str, index: int) -> str:
    """What the page says under one row's choice of kind, or nothing."""
    found = re.search(
        rf'id="id_identifiers-{index}-scheme_error"[^>]*>(.*?)</div>', html, flags=re.S
    )
    return re.sub(r"<[^>]+>", " ", found.group(1)).strip() if found else ""


# -------------------------------------------------------------------- what is offered


def test_the_new_row_offers_no_kind_already_held(client, holder):
    holder.add("wikidata", "Q95")
    holder.add("linkedin", "acme")

    html = client.get(holder.url).content.decode()

    # Two saved rows, so the new one is the third.
    assert switched_off(html, 2) == {"wikidata", "linkedin"}


def test_a_filled_row_keeps_its_own_kind(client, holder):
    holder.add("wikidata", "Q95")
    holder.add("linkedin", "acme")

    html = client.get(holder.url).content.decode()

    # Listed by kind: LinkedIn first, then Wikidata.
    assert switched_off(html, 0) == {"wikidata"}, "its own kind stays, the other row's goes"
    assert switched_off(html, 1) == {"linkedin"}


def test_other_is_never_switched_off(client, holder):
    holder.add("other", "4711", "Staff number")
    holder.add("other", "A-99", "Library card")

    html = client.get(holder.url).content.decode()

    for index in range(3):
        assert "other" not in switched_off(html, index)
    assert switched_off(html, 2) == set(), "two of them, and still nothing held"


def test_a_row_marked_for_removal_gives_its_kind_up(client, holder):
    wikidata = holder.add("wikidata", "Q95")

    response = client.post(
        holder.url,
        posted(
            holder,
            row("wikidata", "Q95", saved=wikidata, remove=True),
            row("linkedin", "not a slug!"),
        ),
    )

    assert response.status_code == 200, "refused for the malformed slug"
    assert "wikidata" not in switched_off(response.content.decode(), 1)


# ------------------------------------------------------------------- what is refused


def test_two_new_rows_of_one_kind_are_still_refused_on_the_row(client, holder):
    response = client.post(
        holder.url, posted(holder, row("wikidata", "Q95"), row("wikidata", "Q42"))
    )

    assert response.status_code == 200
    html = response.content.decode()
    assert kind_error(html, 1) == "Wikidata is already listed on another row."
    assert kind_error(html, 0) == "", "the first one keeps it"
    assert holder.saved() == []


def test_a_new_row_beside_a_saved_one_is_refused_in_words(client, holder):
    """On a person this used to read *Constraint "one_identifier_per_scheme_per_person" is
    violated*: the table's own check, reached because the formset skipped unchanged rows."""
    wikidata = holder.add("wikidata", "Q95")

    response = client.post(
        holder.url,
        posted(holder, row("wikidata", "Q95", saved=wikidata), row("wikidata", "Q42")),
    )

    assert response.status_code == 200
    html = response.content.decode()
    assert kind_error(html, 1) == "Wikidata is already listed on another row."
    assert "Constraint" not in html
    assert holder.saved() == [("wikidata", "Q95")]


def test_the_kind_a_removal_frees_can_be_taken_in_the_same_save(client, holder):
    wikidata = holder.add("wikidata", "Q95")

    response = client.post(
        holder.url,
        posted(holder, row("wikidata", "Q95", saved=wikidata, remove=True), row("wikidata", "Q42")),
    )

    assert response.status_code == 302
    assert holder.saved() == [("wikidata", "Q42")]


def test_a_saved_row_can_take_the_kind_of_one_removed_below_it(client, holder):
    """Django writes saved rows in the order they are listed; the removal has to go first,
    or the change above it reaches the table while the kind is still there."""
    linkedin = holder.add("linkedin", "acme")
    wikidata = holder.add("wikidata", "Q95")

    response = client.post(
        holder.url,
        posted(
            holder,
            row("wikidata", "Q42", saved=linkedin),
            row("wikidata", "Q95", saved=wikidata, remove=True),
        ),
    )

    assert response.status_code == 302
    assert holder.saved() == [("wikidata", "Q42")]


def test_two_saved_rows_trading_kinds_are_refused_rather_than_written(client, holder):
    """Each would reach the table while the other still held its kind, so a saved row keeps
    first claim on the kind it was saved with until it has been written."""
    linkedin = holder.add("linkedin", "acme")
    wikidata = holder.add("wikidata", "Q95")

    response = client.post(
        holder.url,
        posted(
            holder,
            row("wikidata", "Q42", saved=linkedin),
            row("linkedin", "black-mesa", saved=wikidata),
        ),
    )

    assert response.status_code == 200
    assert kind_error(response.content.decode(), 0) == "Wikidata is already listed on another row."
    assert sorted(holder.saved()) == [("linkedin", "acme"), ("wikidata", "Q95")]
