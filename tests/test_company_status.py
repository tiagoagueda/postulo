"""A company's status: its legal state, and the dated hiring signals beside it (#941).

The legal state is a fact about the company and the signals are dated entries, both shared
with everybody who can see the company; the derived "open listings" suggestion is stored
nowhere. Each test says which of those it holds.
"""

from __future__ import annotations

import datetime as dt
import io
import json
import zipfile

import pytest
from django.contrib.auth import get_user_model
from django.core.exceptions import ValidationError
from django.urls import reverse
from django.utils import timezone

from postulo.api.models import ApiToken
from postulo.core import export, importer
from postulo.jobs import merging, status
from postulo.jobs.forms import CompanyForm
from postulo.jobs.models import (
    SIGNAL_STALE_AFTER_DAYS,
    Company,
    CompanyHiringSignal,
    HiringSignalKind,
    JobPosting,
    LegalState,
    RecordChange,
    Share,
)
from postulo.jobs.tables import CompaniesTable

User = get_user_model()

pytestmark = pytest.mark.django_db


def person(name):
    return User.objects.create_user(email=f"{name}@example.org", password="not-a-real-password")


def company_of(owner, name="Acme", **fields):
    return Company.objects.create(owner=owner, name=name, **fields)


def signal(company, kind=HiringSignalKind.HIRING, days_ago=0, author=None, source=""):
    return CompanyHiringSignal.objects.create(
        company=company,
        kind=kind,
        noted_on=timezone.localdate() - dt.timedelta(days=days_ago),
        author=author,
        source=source,
    )


# ----------------------------------------------------------------------------- the model


def test_a_new_company_is_active_with_nothing_said(user):
    company = company_of(user)
    assert company.legal_state == LegalState.ACTIVE
    assert company.legal_state_since is None
    assert company.acquired_by is None
    assert not company.signals.exists()


def test_a_company_cannot_be_acquired_by_itself(user):
    company = company_of(user, legal_state=LegalState.ACQUIRED)
    company.acquired_by = company
    with pytest.raises(ValidationError) as refused:
        company.clean()
    assert "acquired_by" in refused.value.message_dict


def test_an_acquisition_cannot_close_a_loop(user):
    first = company_of(user, "First", legal_state=LegalState.ACQUIRED)
    second = company_of(user, "Second", legal_state=LegalState.ACQUIRED, acquired_by=first)
    first.acquired_by = second
    with pytest.raises(ValidationError) as refused:
        first.clean()
    assert "loop" in str(refused.value.message_dict["acquired_by"])


def test_an_acquirer_needs_the_acquired_state(user):
    buyer = company_of(user, "Buyer")
    company = company_of(user, "Target", acquired_by=buyer)
    with pytest.raises(ValidationError) as refused:
        company.clean()
    assert "acquired_by" in refused.value.message_dict


def test_an_acquired_company_names_its_acquirer(user):
    buyer = company_of(user, "Buyer")
    company = company_of(user, "Target", legal_state=LegalState.ACQUIRED, acquired_by=buyer)
    company.clean()
    assert list(buyer.acquired.all()) == [company]


def test_deleting_the_acquirer_leaves_the_state_and_clears_the_link(user):
    buyer = company_of(user, "Buyer")
    company = company_of(user, "Target", legal_state=LegalState.ACQUIRED, acquired_by=buyer)
    buyer.delete()
    company.refresh_from_db()
    assert company.legal_state == LegalState.ACQUIRED
    assert company.acquired_by is None


# ----------------------------------------------------------------- staleness and the latest


def test_an_entry_is_stale_after_ninety_days_and_not_on_the_ninetieth(user):
    company = company_of(user)
    assert SIGNAL_STALE_AFTER_DAYS == 90
    assert not signal(company, days_ago=90).is_stale
    assert signal(company, days_ago=91).is_stale


def test_the_latest_entry_is_the_signal_and_the_rest_the_history(user):
    company = company_of(user)
    signal(company, HiringSignalKind.LAYING_OFF, days_ago=200)
    signal(company, HiringSignalKind.HIRING, days_ago=10)
    shown = status.for_company(company, user)
    assert shown.signal.kind == HiringSignalKind.HIRING
    assert [row.kind for row in shown.history] == ["hiring", "laying_off"]
    assert not shown.stale


def test_a_stale_entry_reads_as_of_its_date_on_the_page(client, user):
    company = company_of(user)
    old = signal(company, HiringSignalKind.HIRING, days_ago=120)
    client.force_login(user)
    page = client.get(reverse("jobs:company_detail", args=[company.pk])).content.decode()
    assert "data-stale" in page
    assert "as of" in page
    assert old.noted_on.isoformat() in page


def test_a_fresh_entry_has_no_as_of(client, user):
    company = company_of(user)
    signal(company, HiringSignalKind.FREEZE, days_ago=3)
    client.force_login(user)
    page = client.get(reverse("jobs:company_detail", args=[company.pk])).content.decode()
    assert 'data-hiring-signal="freeze"' in page
    assert "data-stale" not in page


# ------------------------------------------------------ the derived "open listings" suggestion


def listing(owner, company, title, **fields):
    return JobPosting.objects.create(owner=owner, company=company, title=title, **fields)


def test_open_listings_are_counted_and_closed_or_past_ones_are_not(user):
    company = company_of(user)
    listing(user, company, "Open")
    listing(
        user, company, "Open until later", closes_at=timezone.localdate() + dt.timedelta(days=5)
    )
    listing(user, company, "Past", closes_at=timezone.localdate() - dt.timedelta(days=1))
    listing(user, company, "Closed", closed_at=timezone.now())
    assert status.open_listing_count(company, user) == 2
    shown = status.for_company(company, user)
    assert shown.suggests_hiring
    assert shown.open_listings_label == "2 open listings"
    # Derived: nothing was stored.
    assert not company.signals.exists()


def test_the_suggestion_is_not_made_against_a_current_freeze(user):
    company = company_of(user)
    listing(user, company, "Open")
    signal(company, HiringSignalKind.FREEZE, days_ago=5)
    assert not status.for_company(company, user).suggests_hiring


def test_the_suggestion_returns_when_the_freeze_is_stale(user):
    company = company_of(user)
    listing(user, company, "Open")
    signal(company, HiringSignalKind.FREEZE, days_ago=300)
    assert status.for_company(company, user).suggests_hiring


def test_the_count_is_of_what_the_reader_can_see(user, other_user):
    company = company_of(user)
    Share.objects.create(company=company, grantee_user=other_user, scope=Share.Scope.READ)
    listing(user, company, "Private to the owner")
    assert status.open_listing_count(company, user) == 1
    assert status.open_listing_count(company, other_user) == 0


def test_the_page_shows_the_suggestion_as_derived(client, user):
    company = company_of(user)
    listing(user, company, "Open")
    client.force_login(user)
    page = client.get(reverse("jobs:company_detail", args=[company.pk])).content.decode()
    assert "data-derived-hiring" in page
    assert "1 open listing" in page


# ----------------------------------------------------------------------------- the warning


def test_the_warning_says_what_was_recorded_and_when(user):
    company = company_of(
        user,
        legal_state=LegalState.DISSOLVED,
        legal_state_since=dt.date(2026, 3, 1),
    )
    sentence = status.warning(company)
    assert sentence.startswith("This company was dissolved on")
    assert "2026" in sentence


def test_an_active_company_with_no_layoffs_has_no_warning(user):
    company = company_of(user)
    signal(company, HiringSignalKind.HIRING)
    assert status.warning(company) == ""


def test_laying_off_warns_while_fresh_and_not_when_stale(user):
    fresh = company_of(user, "Fresh")
    signal(fresh, HiringSignalKind.LAYING_OFF, days_ago=5)
    stale = company_of(user, "Stale")
    signal(stale, HiringSignalKind.LAYING_OFF, days_ago=400)
    assert "laying people off" in status.warning(fresh)
    assert status.warning(stale) == ""


def test_the_listing_and_application_pages_carry_the_quiet_warning(client, user):
    from postulo.applications.services import create_application

    company = company_of(user, legal_state=LegalState.DISSOLVED)
    posting = listing(user, company, "Engineer")
    application = create_application(
        user, company=company, posting_data={"title": "Dev"}, application_data={"status": "applied"}
    )
    client.force_login(user)
    for url in (posting.get_absolute_url(), application.get_absolute_url()):
        page = client.get(url).content.decode()
        assert "data-company-notice" in page
        assert "dissolved" in page


def test_a_listing_for_an_active_company_has_no_notice(client, user):
    company = company_of(user)
    posting = listing(user, company, "Engineer")
    client.force_login(user)
    assert "data-company-notice" not in client.get(posting.get_absolute_url()).content.decode()


# ----------------------------------------------------- adding work at a dissolved company


def test_adding_a_listing_for_a_dissolved_company_asks_first(client, user):
    company = company_of(user, legal_state=LegalState.DISSOLVED)
    client.force_login(user)
    data = {"company_name": company.name, "title": "Engineer", "source": "Manual"}

    asked = client.post(reverse("listings:create"), data)
    assert asked.status_code == 200
    assert "data-company-notice" not in asked.content.decode()
    assert "confirm_dissolved" in asked.content.decode()
    assert not JobPosting.objects.filter(title="Engineer").exists()

    confirmed = client.post(reverse("listings:create"), {**data, "confirm_dissolved": "on"})
    assert confirmed.status_code == 302
    assert JobPosting.objects.filter(title="Engineer", company=company).exists()


def test_an_active_company_is_not_asked_about(client, user):
    company_of(user, "Fine")
    client.force_login(user)
    response = client.post(
        reverse("listings:create"),
        {"company_name": "Fine", "title": "Engineer", "source": "Manual"},
    )
    assert response.status_code == 302


def test_a_company_not_recorded_yet_is_not_asked_about(client, user):
    client.force_login(user)
    response = client.post(
        reverse("listings:create"),
        {"company_name": "Brand new", "title": "Engineer", "source": "Manual"},
    )
    assert response.status_code == 302


# ----------------------------------------------------------------------------- permissions


@pytest.fixture
def writer(user):
    other = person("writer")
    return other


def shared(company, who, scope):
    Share.objects.create(company=company, grantee_user=who, scope=scope, granted_by=company.owner)


def post_signal(client, company, **extra):
    data = {"kind": "hiring", "noted_on": timezone.localdate().isoformat(), **extra}
    return client.post(reverse("jobs:company_signal_add", args=[company.pk]), data)


def test_an_owner_adds_an_entry_with_themselves_as_author(client, user):
    company = company_of(user)
    client.force_login(user)
    response = post_signal(client, company, source="https://example.org/news")
    assert response.status_code == 302
    row = company.signals.get()
    assert row.author == user
    assert row.source == "https://example.org/news"
    assert row.owner == user
    assert RecordChange.objects.filter(object_id=company.pk, fields=["hiring_signals"]).exists()


def test_a_write_share_may_add_change_and_remove_an_entry(client, user, writer):
    company = company_of(user)
    shared(company, writer, Share.Scope.WRITE)
    client.force_login(writer)

    assert post_signal(client, company).status_code == 302
    row = company.signals.get()
    assert row.author == writer

    changed = client.post(
        reverse("jobs:company_signal_edit", args=[row.pk]),
        {"kind": "freeze", "noted_on": row.noted_on.isoformat(), "source": ""},
    )
    assert changed.status_code == 302
    row.refresh_from_db()
    assert row.kind == "freeze"

    gone = client.post(reverse("jobs:company_signal_remove", args=[row.pk]))
    assert gone.status_code == 302
    assert not company.signals.exists()


def test_a_read_share_sees_the_entries_and_may_not_change_them(client, user, writer):
    company = company_of(user)
    row = signal(company, author=user)
    shared(company, writer, Share.Scope.READ)
    client.force_login(writer)

    page = client.get(reverse("jobs:company_detail", args=[company.pk])).content.decode()
    assert "data-hiring-history" in page
    assert user.get_username() in page

    assert post_signal(client, company).status_code == 404
    assert (
        client.post(
            reverse("jobs:company_signal_edit", args=[row.pk]),
            {"kind": "freeze", "noted_on": row.noted_on.isoformat()},
        ).status_code
        == 404
    )
    assert client.post(reverse("jobs:company_signal_remove", args=[row.pk])).status_code == 404
    assert company.signals.count() == 1


def test_somebody_else_reaches_nothing(client, user, writer):
    company = company_of(user)
    row = signal(company)
    client.force_login(writer)
    assert client.get(reverse("jobs:company_detail", args=[company.pk])).status_code == 404
    assert post_signal(client, company).status_code == 404
    assert client.post(reverse("jobs:company_signal_remove", args=[row.pk])).status_code == 404
    assert company.signals.count() == 1


def test_a_write_share_changes_the_legal_state_through_the_form(client, user, writer):
    company = company_of(user)
    shared(company, writer, Share.Scope.WRITE)
    client.force_login(writer)
    response = client.post(
        reverse("jobs:company_update", args=[company.pk]),
        {
            "name": company.name,
            "kind": "employer",
            "legal_state": "dissolved",
            "legal_state_since": "2026-02-03",
            "legal_state_source": "https://register.example/acme",
        },
    )
    assert response.status_code == 302
    company.refresh_from_db()
    assert company.legal_state == LegalState.DISSOLVED
    assert company.legal_state_since == dt.date(2026, 2, 3)
    assert company.legal_state_source == "https://register.example/acme"


def test_a_read_share_cannot_open_the_form(client, user, writer):
    company = company_of(user)
    shared(company, writer, Share.Scope.READ)
    client.force_login(writer)
    assert client.get(reverse("jobs:company_update", args=[company.pk])).status_code == 404


def test_a_future_date_or_a_script_address_is_refused(client, user):
    company = company_of(user)
    client.force_login(user)
    tomorrow = (timezone.localdate() + dt.timedelta(days=1)).isoformat()
    post_signal(client, company, noted_on=tomorrow)
    post_signal(client, company, source="javascript:alert(1)")
    assert not company.signals.exists()


def test_the_form_refuses_an_acquirer_the_people_it_is_shared_with_cannot_see(user, writer):
    company = company_of(user, "Target")
    private = company_of(user, "Private buyer")
    shared(company, writer, Share.Scope.READ)
    form = CompanyForm(
        {
            "name": "Target",
            "kind": "employer",
            "legal_state": "acquired",
            "acquired_by": private.pk,
        },
        instance=company,
        user=user,
    )
    assert not form.is_valid()
    assert "acquired_by" in form.errors


def test_the_form_accepts_an_acquirer_everybody_can_see(user, writer):
    company = company_of(user, "Target")
    buyer = company_of(user, "Buyer")
    shared(company, writer, Share.Scope.READ)
    shared(buyer, writer, Share.Scope.READ)
    form = CompanyForm(
        {
            "name": "Target",
            "kind": "employer",
            "legal_state": "acquired",
            "acquired_by": buyer.pk,
        },
        instance=company,
        user=user,
    )
    assert form.is_valid(), form.errors
    form.save()
    company.refresh_from_db()
    assert company.acquired_by == buyer


# -------------------------------------------------------------------------- table and map


def table_rows(client, **query):
    response = client.get(reverse("jobs:company_list"), query)
    assert response.status_code == 200
    return [c.name for c in response.context["companies"]]


def test_the_table_has_a_status_column_and_two_filters(client, user):
    assert "status" in {column.key for column in CompaniesTable.columns}
    assert {"hiring", "live"} <= set(CompaniesTable.filter_names())


def test_the_hiring_filter_takes_a_current_signal_or_open_listings(client, user):
    signalled = company_of(user, "Signalled")
    signal(signalled, HiringSignalKind.HIRING, days_ago=4)
    stale_hiring = company_of(user, "Stale hiring")
    signal(stale_hiring, HiringSignalKind.HIRING, days_ago=500)
    listed = company_of(user, "Listed")
    listing(user, listed, "Open")
    frozen = company_of(user, "Frozen")
    signal(frozen, HiringSignalKind.FREEZE, days_ago=4)
    listing(user, frozen, "Open but frozen")
    company_of(user, "Quiet")
    client.force_login(user)
    assert sorted(table_rows(client, hiring="1")) == ["Listed", "Signalled"]


def test_exclude_dissolved_leaves_every_other_state(client, user):
    company_of(user, "Alive")
    company_of(user, "Gone", legal_state=LegalState.DISSOLVED)
    company_of(user, "Bought", legal_state=LegalState.ACQUIRED)
    client.force_login(user)
    assert sorted(table_rows(client, live="1")) == ["Alive", "Bought"]


def test_the_status_column_filters_by_legal_state(client, user):
    company_of(user, "Alive")
    company_of(user, "Gone", legal_state=LegalState.DISSOLVED)
    client.force_login(user)
    assert table_rows(client, status="dissolved") == ["Gone"]


def test_the_status_cell_draws_the_state_the_signal_and_the_count(client, user):
    company = company_of(user, "Everything", legal_state=LegalState.ADMINISTRATION)
    signal(company, HiringSignalKind.LAYING_OFF, days_ago=2)
    other = company_of(user, "Counted")
    listing(user, other, "Open")
    client.force_login(user)
    profile = user.profile
    profile.table_settings = {
        "companies": {"columns": ["name", "status"], "page_size": 50, "widths": {}}
    }
    profile.save()
    page = client.get(reverse("jobs:company_list"))
    html = page.content.decode()
    assert 'data-legal-state="administration"' in html
    assert 'data-hiring-signal="laying_off"' in html
    assert "data-derived-hiring" in html


def test_the_map_data_carries_each_companys_state(user):
    from postulo.core.models import PostalAddress
    from postulo.jobs.views import pinned_addresses

    gone = company_of(user, "Gone", legal_state=LegalState.DISSOLVED)
    alive = company_of(user, "Alive")
    for company in (gone, alive):
        PostalAddress.objects.create(
            owner=user, holder=company, kind="work", municipality="Lisbon", country="PT"
        )
    states = {row["name"]: row["state"] for row in pinned_addresses(user)}
    assert states == {"Gone": "dissolved", "Alive": ""}


# ----------------------------------------------------------------------------------- merging


def test_a_merge_moves_the_signals_and_the_legal_state(user):
    kept = company_of(user, "Kept")
    other = company_of(user, "Other", legal_state=LegalState.DISSOLVED)
    signal(kept, HiringSignalKind.HIRING, days_ago=1)
    signal(other, HiringSignalKind.LAYING_OFF, days_ago=40)

    merging.merge_companies(kept, other)

    kept.refresh_from_db()
    assert kept.legal_state == LegalState.DISSOLVED
    assert [row.kind for row in kept.signals.all()] == ["hiring", "laying_off"]


def test_a_merge_keeps_the_kept_companys_own_state(user):
    kept = company_of(user, "Kept", legal_state=LegalState.ADMINISTRATION)
    other = company_of(user, "Other", legal_state=LegalState.DISSOLVED)
    merging.merge_companies(kept, other)
    kept.refresh_from_db()
    assert kept.legal_state == LegalState.ADMINISTRATION


def test_a_merge_repoints_those_acquired_by_the_other(user):
    kept = company_of(user, "Kept")
    other = company_of(user, "Other")
    bought = company_of(user, "Bought", legal_state=LegalState.ACQUIRED, acquired_by=other)
    merging.merge_companies(kept, other)
    bought.refresh_from_db()
    assert bought.acquired_by == kept


def test_a_merge_does_not_leave_the_kept_company_acquired_by_itself(user):
    kept = company_of(user, "Kept")
    other = company_of(user, "Other")
    kept.legal_state, kept.acquired_by = LegalState.ACQUIRED, other
    kept.save()
    merging.merge_companies(kept, other)
    kept.refresh_from_db()
    assert kept.acquired_by is None


def test_a_copy_made_when_access_ends_carries_the_signals(user, writer):
    from postulo.jobs import detach

    company = company_of(user)
    signal(company, HiringSignalKind.HIRING, days_ago=2, author=user)
    # The reader's own application points at the company, so leaving makes a copy.
    posting = listing(writer, company, "Mine")
    assert posting.pk
    shared(company, writer, Share.Scope.READ)
    copies = detach.after_losing([company], [writer], check=False)
    clone = next(iter(copies.values()))
    assert clone.owner == writer
    assert [row.kind for row in clone.signals.all()] == ["hiring"]


# ------------------------------------------------------------------- archive and importing


def archive_of(user):
    return zipfile.ZipFile(io.BytesIO(export.write_archive(user).getvalue()))


def test_the_format_is_75_and_says_what_75_added():
    assert export.FORMAT_VERSION == 75
    with open(export.__file__, encoding="utf-8") as source:
        assert "75 added a company's status" in source.read()


def test_the_archive_round_trips_status_signals_and_acquirer(user, other_user):
    buyer = company_of(user, "Buyer")
    target = company_of(
        user,
        "Target",
        legal_state=LegalState.ACQUIRED,
        legal_state_since=dt.date(2025, 6, 30),
        legal_state_source="https://example.org/deal",
        acquired_by=buyer,
    )
    signal(
        target,
        HiringSignalKind.LAYING_OFF,
        days_ago=400,
        author=user,
        source="https://example.org/n",
    )
    signal(target, HiringSignalKind.HIRING, days_ago=3, author=user)

    with archive_of(user) as archive:
        manifest = json.loads(archive.read("postulo.json"))
        written = next(c for c in manifest["companies"] if c["name"] == "Target")
        assert written["legal_state"] == "acquired"
        assert written["acquired_by"] == "Buyer"
        assert {row["kind"] for row in written["hiring_signals"]} == {"laying_off", "hiring"}
        importer.load(other_user, archive)

    restored = Company.objects.get(owner=other_user, name="Target")
    assert restored.legal_state == LegalState.ACQUIRED
    assert restored.legal_state_since == dt.date(2025, 6, 30)
    assert restored.legal_state_source == "https://example.org/deal"
    assert restored.acquired_by.name == "Buyer"
    assert [row.kind for row in restored.signals.order_by("-noted_on")] == ["hiring", "laying_off"]
    # Who noted it is a username on the instance that wrote the file: nobody over here.
    assert all(row.author is None for row in restored.signals.all())


def test_an_archive_read_back_by_its_own_author_keeps_the_author(user):
    company = company_of(user, "Mine again")
    signal(company, author=user)
    data = export.write_archive(user)
    Company.objects.filter(owner=user).delete()
    importer.load(user, zipfile.ZipFile(io.BytesIO(data.getvalue())))
    assert CompanyHiringSignal.objects.get(company__name="Mine again").author == user


def test_an_older_archive_restores_every_company_active(user, other_user):
    company_of(user, "Old")
    with archive_of(user) as archive:
        manifest = json.loads(archive.read("postulo.json"))
    entry = manifest["companies"][0]
    for key in (
        "legal_state",
        "legal_state_since",
        "legal_state_source",
        "acquired_by",
        "hiring_signals",
    ):
        entry.pop(key, None)
    manifest["postulo"]["format"] = 74

    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w") as rebuilt:
        rebuilt.writestr("postulo.json", json.dumps(manifest))
    importer.load(other_user, zipfile.ZipFile(io.BytesIO(buffer.getvalue())))

    restored = Company.objects.get(owner=other_user, name="Old")
    assert restored.legal_state == LegalState.ACTIVE
    assert not restored.signals.exists()


def test_a_hand_written_archive_cannot_close_a_loop_or_carry_a_script(user, other_user):
    company_of(user, "A", legal_state=LegalState.ACQUIRED)
    company_of(user, "B", legal_state=LegalState.ACQUIRED)
    with archive_of(user) as archive:
        manifest = json.loads(archive.read("postulo.json"))
    by_name = {c["name"]: c for c in manifest["companies"]}
    by_name["A"]["acquired_by"] = "B"
    by_name["B"]["acquired_by"] = "A"
    by_name["A"]["legal_state_source"] = "javascript:alert(1)"
    by_name["A"]["legal_state"] = "something-new"
    by_name["B"]["hiring_signals"] = [
        {"kind": "hiring", "noted_on": "2026-01-02", "source_url": "javascript:1"},
        {"kind": "not-a-kind", "noted_on": "2026-01-02"},
        {"kind": "freeze", "noted_on": "not a date"},
    ]
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w") as rebuilt:
        rebuilt.writestr("postulo.json", json.dumps(manifest))
    importer.load(other_user, zipfile.ZipFile(io.BytesIO(buffer.getvalue())))

    a = Company.objects.get(owner=other_user, name="A")
    b = Company.objects.get(owner=other_user, name="B")
    assert a.legal_state == LegalState.ACTIVE
    assert a.legal_state_source == ""
    # Only one of the two links could be made without closing a loop.
    assert [a.acquired_by_id, b.acquired_by_id].count(None) >= 1
    assert [(row.kind, row.source) for row in b.signals.all()] == [("hiring", "")]


# ------------------------------------------------------------------------------------- API


def bearer(user, *scopes):
    _record, raw = ApiToken.issue(user, "t", scopes=scopes or ("read", "write"))
    return {"HTTP_AUTHORIZATION": f"Bearer {raw}"}


def test_the_api_reads_the_state_and_the_entries(client, user):
    buyer = company_of(user, "Buyer")
    company = company_of(user, "Target", legal_state=LegalState.ACQUIRED, acquired_by=buyer)
    signal(company, HiringSignalKind.HIRING, days_ago=1, author=user)
    body = client.get(f"/api/v1/companies/{company.pk}", **bearer(user)).json()
    assert body["legal_state"] == "acquired"
    assert body["acquired_by"] == buyer.pk
    assert [(row["kind"], row["author"]) for row in body["hiring_signals"]] == [
        ("hiring", user.get_username())
    ]


def test_the_api_sets_the_state_and_adds_and_removes_an_entry(client, user):
    buyer = company_of(user, "Buyer")
    company = company_of(user, "Target")
    auth = bearer(user)

    patched = client.patch(
        f"/api/v1/companies/{company.pk}",
        data=json.dumps(
            {
                "legal_state": "acquired",
                "legal_state_since": "2026-01-02",
                "legal_state_source": "https://example.org/x",
                "acquired_by": buyer.pk,
            }
        ),
        content_type="application/json",
        **auth,
    )
    assert patched.status_code == 200, patched.content
    assert patched.json()["legal_state"] == "acquired"
    assert patched.json()["acquired_by"] == buyer.pk

    added = client.post(
        f"/api/v1/companies/{company.pk}/signals",
        data=json.dumps({"kind": "freeze", "source": "https://example.org/n"}),
        content_type="application/json",
        **auth,
    )
    assert added.status_code == 201
    row_id = added.json()["id"]
    assert added.json()["noted_on"] == timezone.localdate().isoformat()
    assert company.signals.get().author == user

    removed = client.delete(f"/api/v1/companies/{company.pk}/signals/{row_id}", **auth)
    assert removed.status_code == 204
    assert not company.signals.exists()


def test_the_api_refuses_what_the_form_refuses(client, user, other_user):
    company = company_of(user, "Target")
    strangers = company_of(other_user, "Theirs")
    auth = bearer(user)

    def patch(body):
        return client.patch(
            f"/api/v1/companies/{company.pk}",
            data=json.dumps(body),
            content_type="application/json",
            **auth,
        )

    assert patch({"legal_state": "vanished"}).status_code == 422
    assert patch({"legal_state": "acquired", "acquired_by": strangers.pk}).status_code == 422
    assert patch({"legal_state": "acquired", "acquired_by": company.pk}).status_code == 422
    assert patch({"acquired_by": company.pk}).status_code == 422
    assert (
        client.post(
            f"/api/v1/companies/{company.pk}/signals",
            data=json.dumps({"kind": "hiring", "source": "javascript:1"}),
            content_type="application/json",
            **auth,
        ).status_code
        == 422
    )
    assert (
        client.post(
            f"/api/v1/companies/{company.pk}/signals",
            data=json.dumps({"kind": "nonsense"}),
            content_type="application/json",
            **auth,
        ).status_code
        == 422
    )
    company.refresh_from_db()
    assert company.legal_state == LegalState.ACTIVE


def test_the_api_lets_a_write_share_but_not_a_read_share_add_an_entry(client, user, writer):
    company = company_of(user)
    shared(company, writer, Share.Scope.READ)
    auth = bearer(writer)
    url = f"/api/v1/companies/{company.pk}/signals"
    body = json.dumps({"kind": "hiring"})
    assert client.post(url, data=body, content_type="application/json", **auth).status_code == 404
    Share.objects.filter(company=company).update(scope=Share.Scope.WRITE)
    assert client.post(url, data=body, content_type="application/json", **auth).status_code == 201


def test_a_company_added_through_the_api_can_carry_its_state(client, user):
    created = client.post(
        "/api/v1/companies",
        data=json.dumps({"name": "Gone Ltd", "legal_state": "dissolved"}),
        content_type="application/json",
        **bearer(user),
    )
    assert created.status_code == 201
    assert created.json()["legal_state"] == "dissolved"


# ------------------------------------------------------------------------------- vCard


def test_a_card_carries_a_note_only_for_a_state_other_than_active(user):
    from postulo.jobs import vcards

    alive = company_of(user, "Alive")
    gone = company_of(
        user,
        "Gone",
        legal_state=LegalState.DISSOLVED,
        legal_state_since=dt.date(2026, 3, 1),
        legal_state_source="https://example.org/register",
    )
    assert vcards.card_for_company(alive).notes == ""
    note = vcards.card_for_company(gone).notes
    assert "Dissolved" in note and "2026-03-01" in note and "https://example.org/register" in note


# --------------------------------------------------------------------------------- Wikidata


def claim_of(**kwargs):
    return {"mainsnak": {"datavalue": {"value": kwargs}}, "rank": kwargs.pop("rank", "normal")}


def test_wikidata_reads_the_day_it_ended_only_when_it_is_a_day():
    from postulo.jobs import wikidata

    day = {
        "P576": [
            {
                "mainsnak": {
                    "datavalue": {"value": {"time": "+2019-05-17T00:00:00Z", "precision": 11}}
                }
            }
        ]
    }
    year = {
        "P576": [
            {
                "mainsnak": {
                    "datavalue": {"value": {"time": "+2019-00-00T00:00:00Z", "precision": 9}}
                }
            }
        ]
    }
    assert wikidata.dissolved_from(day) == (True, dt.date(2019, 5, 17))
    assert wikidata.dissolved_from(year) == (True, None)
    assert wikidata.dissolved_from({}) == (False, None)
    assert wikidata.dissolved_from({"P576": [{"rank": "deprecated"}]}) == (False, None)


def test_wikidata_reads_who_replaced_it():
    from postulo.jobs import wikidata

    claims = {
        "P1366": [
            {"mainsnak": {"datavalue": {"value": {"id": "Q42"}}}},
            {"mainsnak": {"datavalue": {"value": {"id": "not-an-id"}}}},
            {"rank": "deprecated", "mainsnak": {"datavalue": {"value": {"id": "Q7"}}}},
        ]
    }
    assert wikidata.replaced_by_from(claims) == ["Q42"]


def held_answer(client, company, **held):
    session = client.session
    session[f"wikidata-{company.pk}"] = {
        "qid": "Q29671",
        "names": {},
        "point": None,
        "dissolved": False,
        "dissolved_on": None,
        "replaced_by": [],
        **held,
    }
    session.save()


@pytest.fixture
def wikidata_on(settings, monkeypatch):
    from postulo.jobs import views

    monkeypatch.setattr(views, "wikidata_offered", lambda person: True)


def test_the_review_page_offers_dissolved_and_changes_nothing_until_chosen(
    client, user, wikidata_on
):
    company = company_of(user)
    client.force_login(user)
    held_answer(client, company, dissolved=True, dissolved_on="2019-05-17")

    page = client.get(reverse("jobs:company_wikidata", args=[company.pk])).content.decode()
    assert "data-wikidata-ending" in page
    assert "Record it as dissolved" in page
    company.refresh_from_db()
    assert company.legal_state == LegalState.ACTIVE

    # Keeping names without choosing the legal state leaves it as it is.
    client.post(reverse("jobs:company_wikidata", args=[company.pk]), {"keep": "1"})
    company.refresh_from_db()
    assert company.legal_state == LegalState.ACTIVE

    held_answer(client, company, dissolved=True, dissolved_on="2019-05-17")
    client.post(
        reverse("jobs:company_wikidata", args=[company.pk]),
        {"keep": "1", "legal_state": "dissolved"},
    )
    company.refresh_from_db()
    assert company.legal_state == LegalState.DISSOLVED
    assert company.legal_state_since == dt.date(2019, 5, 17)
    assert company.legal_state_source == "https://www.wikidata.org/wiki/Q29671"


def test_the_review_page_offers_the_acquirer_it_recognises(client, user, wikidata_on):
    from postulo.jobs.models import CompanyIdentifier

    buyer = company_of(user, "Buyer")
    CompanyIdentifier.objects.create(owner=user, company=buyer, scheme="wikidata", value="Q42")
    company = company_of(user)
    client.force_login(user)
    held_answer(client, company, dissolved=True, replaced_by=["Q42", "Q99"])

    page = client.get(reverse("jobs:company_wikidata", args=[company.pk])).content.decode()
    assert "acquired or merged into" in page
    assert "Q99" in page  # named, with nothing to link it to

    client.post(
        reverse("jobs:company_wikidata", args=[company.pk]),
        {"keep": "1", "legal_state": "acquired"},
    )
    company.refresh_from_db()
    assert company.legal_state == LegalState.ACQUIRED
    assert company.acquired_by == buyer


def test_a_choice_that_was_not_offered_is_not_applied(client, user, wikidata_on):
    company = company_of(user)
    client.force_login(user)
    held_answer(client, company)  # nothing about an end in the answer
    client.post(
        reverse("jobs:company_wikidata", args=[company.pk]),
        {"keep": "1", "legal_state": "dissolved"},
    )
    company.refresh_from_db()
    assert company.legal_state == LegalState.ACTIVE


def test_the_form_holds_the_source_to_a_web_address(user):
    company = company_of(user)
    form = CompanyForm(
        {
            "name": company.name,
            "kind": "employer",
            "legal_state": "dissolved",
            "legal_state_source": "ftp://example.org/x",
        },
        instance=company,
        user=user,
    )
    assert not form.is_valid()
    assert "legal_state_source" in form.errors
