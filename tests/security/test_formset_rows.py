"""A row of somebody else's, named in a post to *Your details* (#422).

Postal addresses, telephone numbers, web links and identifiers have no address of their own
to ask for, so `tests/security/test_isolation_sweep.py` cannot reach them: they are edited
through inline formsets, and the id of the row is a hidden box in the body. A page that
looked a row up by that id among everybody's, and not among the holder's own, would let
one account change or delete another's by posting a number into its own form.

For each kind of row: the page is posted with somebody else's row named as a saved row of
mine, once to change it and once to remove it, and the row has to be exactly as it was.
Removal by address is covered by the sweep (#303); this is the other way in.
"""

from __future__ import annotations

import pytest
from django.urls import reverse

pytestmark = pytest.mark.django_db


def _address(owner):
    from postulo.core.models import PostalAddress

    return PostalAddress.objects.create(
        owner=owner, holder=owner.profile, street="Their street 1", country="PT"
    )


def _number(owner):
    from postulo.core.models import PhoneNumber

    return PhoneNumber.objects.create(owner=owner, holder=owner.profile, number="+351912345678")


def _link(owner):
    from postulo.core.models import WebLink

    return WebLink.objects.create(
        owner=owner, holder=owner.profile, kind=WebLink.Kind.WEBSITE, url="https://theirs.example"
    )


def _identifier(owner):
    from postulo.accounts.models import PersonIdentifier

    return PersonIdentifier.objects.create(profile=owner.profile, scheme="wikidata", value="Q95")


#: prefix, how to make the other person's row, and what to post as a change to it.
KINDS = {
    "postal address": (
        "addresses",
        _address,
        {
            "street": "Taken over 9",
            "postcode": "1000-001",
            "municipality": "Lisboa",
            "country": "PT",
        },
    ),
    "telephone number": (
        "phone_numbers",
        _number,
        {"kind": "", "label": "", "number_0": "PT", "number_1": "913000000"},
    ),
    "web link": ("websites", _link, {"service": "", "label": "", "url": "https://mine.example"}),
    "identifier": ("identifiers", _identifier, {"scheme": "wikidata", "value": "Q1", "label": ""}),
}


def _held(row) -> tuple:
    """Everything about a row that a post could change, and whose it is."""
    row.refresh_from_db()
    return tuple(
        getattr(row, field.attname)
        for field in row._meta.concrete_fields
        if field.name != "updated_at"
    )


def _post(client, prefix, row, fields, *, remove):
    data = {
        "first_name": "Alex",
        "last_name": "Morgan",
        "location": "",
        f"{prefix}-TOTAL_FORMS": "1",
        f"{prefix}-INITIAL_FORMS": "1",
        f"{prefix}-MIN_NUM_FORMS": "0",
        f"{prefix}-MAX_NUM_FORMS": "1000",
        f"{prefix}-0-id": str(row.pk),
        f"{prefix}-primary": f"{prefix}-0",
        **{f"{prefix}-0-{name}": value for name, value in fields.items()},
    }
    if remove:
        data[f"{prefix}-0-DELETE"] = "on"
    return client.post(reverse("accounts:profile"), data)


@pytest.mark.parametrize("remove", [False, True], ids=["change", "remove"])
@pytest.mark.parametrize("kind", sorted(KINDS))
def test_somebody_elses_row_in_my_formset_is_left_alone(client, user, other_user, kind, remove):
    prefix, make, fields = KINDS[kind]
    theirs = make(other_user)
    before = _held(theirs)
    model = type(theirs)
    total = model.objects.count()
    client.force_login(user)

    response = _post(client, prefix, theirs, fields, remove=remove)

    assert response.status_code in (200, 302, 404), f"{kind}: answered {response.status_code}"
    assert model.objects.filter(pk=theirs.pk).exists(), f"{kind}: somebody else's row was removed"
    assert _held(theirs) == before, f"{kind}: somebody else's row was changed or taken over"
    assert model.objects.count() == total, f"{kind}: the post made or removed a row"
