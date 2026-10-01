"""Why an application ended, and where it had got to when it did (#239).

Listings have kept a reason for being discarded since they existed; a rejection and a
withdrawal had none, so the funnel could say where applications stop and never why.

Two things are held to here, and both are about the timeline being the truth. **The reason
is on the entry that ended the application**, not on the application, so an application
that ends twice keeps both reasons and nothing is ever rewritten to change one. **The stage
it had reached is never typed**: it is what the entry says the application was moved from.
"""

from __future__ import annotations

import datetime as dt
import json

import pytest
from django.contrib.messages import get_messages
from django.urls import reverse
from django.utils import timezone

from postulo.api.models import ApiToken
from postulo.applications import analytics, endings
from postulo.applications.models import (
    END_STATUSES,
    Application,
    ApplicationEvent,
    EndReason,
    EventKind,
    Status,
)
from postulo.applications.services import change_status
from postulo.jobs.models import Company, JobPosting

pytestmark = pytest.mark.django_db

HTMX = {"HTTP_HX_REQUEST": "true", "HTTP_HX_TARGET": "status-card"}


@pytest.fixture
def company(user):
    return Company.objects.create(owner=user, name="Aperture Science")


def an_application(owner, company, *through: str, title: str = "Test Engineer") -> Application:
    """An application moved through ``through``, in order, a day apart."""
    posting = JobPosting.objects.create(owner=owner, company=company, title=title)
    application = Application.objects.create(owner=owner, posting=posting, status=Status.DRAFT)
    start = timezone.now() - dt.timedelta(days=len(through) + 1)
    for step, status in enumerate(through):
        change_status(application, status, occurred_at=start + dt.timedelta(days=step))
    return application


@pytest.fixture
def application(user, company):
    return an_application(user, company, Status.APPLIED, Status.INTERVIEWING)


def bearer(user, *scopes) -> dict:
    _record, raw = ApiToken.issue(user, "Agent", scopes=scopes or ("read", "write"))
    return {"HTTP_AUTHORIZATION": f"Bearer {raw}"}


def post_json(client, path, payload, **headers):
    return client.post(path, data=json.dumps(payload), content_type="application/json", **headers)


# ------------------------------------------------------------- on the entry, not the row


def test_the_three_endings_are_the_ones_nobody_won():
    assert {Status.REJECTED, Status.WITHDRAWN, Status.GHOSTED} == END_STATUSES
    assert Status.ACCEPTED not in END_STATUSES, "nobody asks why an accepted one ended"


def test_the_reasons_are_the_six_the_issue_names():
    assert EndReason.values == [
        "pay",
        "location",
        "not_a_match",
        "filled_internally",
        "my_choice",
        "other",
    ]


def test_the_reason_is_written_on_the_entry_that_ended_it(application):
    entry = change_status(
        application, Status.REJECTED, note="Ten thousand under.", end_reason=EndReason.PAY
    )

    assert entry.kind == EventKind.STATUS_CHANGE
    assert (entry.from_status, entry.to_status) == (Status.INTERVIEWING, Status.REJECTED)
    assert entry.end_reason == EndReason.PAY
    assert entry.body == "Ten thousand under."


def test_the_application_has_no_column_for_it():
    """The log is the truth: a field on the row would be a second account of the same thing,
    and the one that survived a reopening would be the wrong one."""
    names = {field.name for field in Application._meta.get_fields()}

    assert not {"end_reason", "end_note", "last_stage"} & names
    assert "end_reason" in {field.name for field in ApplicationEvent._meta.get_fields()}


def test_an_ending_without_a_reason_is_still_an_ending(application):
    change_status(application, Status.GHOSTED)

    ending = application.ending

    assert ending.status == Status.GHOSTED
    assert ending.reason == "" and ending.reason_label == ""
    assert ending.last_stage == Status.INTERVIEWING


def test_an_application_that_has_not_ended_has_no_ending(application):
    assert application.ending is None


def test_an_accepted_application_has_no_ending_either(application):
    change_status(application, Status.OFFER)
    change_status(application, Status.ACCEPTED)

    assert application.ending is None


def test_a_reason_with_a_status_that_is_not_an_ending_is_a_mistake_in_the_caller(application):
    with pytest.raises(ValueError, match="is not an ending"):
        change_status(application, Status.OFFER, end_reason=EndReason.PAY)

    application.refresh_from_db()
    assert application.status == Status.INTERVIEWING, "and nothing moved"


def test_a_reason_nobody_has_heard_of_is_refused(application):
    with pytest.raises(ValueError, match="is not a reason"):
        change_status(application, Status.REJECTED, end_reason="bad-vibes")


# ------------------------------------------------------------ the stage is never typed


@pytest.mark.parametrize(
    ("through", "stage"),
    [
        ((Status.APPLIED, Status.REJECTED), Status.APPLIED),
        ((Status.APPLIED, Status.SCREENING, Status.INTERVIEWING, Status.REJECTED), "interviewing"),
        ((Status.APPLIED, Status.OFFER, Status.WITHDRAWN), Status.OFFER),
        ((Status.APPLIED, Status.ACKNOWLEDGED, Status.GHOSTED), Status.ACKNOWLEDGED),
    ],
)
def test_the_last_stage_is_what_it_was_moved_from(user, company, through, stage):
    application = an_application(user, company, *through)

    assert application.ending.last_stage == stage


def test_a_reply_recorded_straight_away_ended_at_applied(user, company):
    """Recording a rejection somebody already had moves it from *Draft*, and it had been
    sent all the same (#222). *Draft* would say it never went anywhere."""
    rejected = an_application(user, company, Status.REJECTED)
    ghosted = an_application(user, company, Status.GHOSTED, title="Another")

    assert rejected.ending.last_stage == Status.APPLIED
    assert ghosted.ending.last_stage == Status.APPLIED


def test_a_draft_abandoned_ended_as_a_draft(user, company):
    """Withdrawn is the one ending that does not mean it was sent."""
    abandoned = an_application(user, company, Status.WITHDRAWN)

    assert abandoned.ending.last_stage == Status.DRAFT


def test_moving_from_one_ending_to_another_keeps_the_stage(application):
    """Ghosted, and then they wrote to say no. It still ended at *Interviewing*."""
    change_status(application, Status.GHOSTED)
    change_status(application, Status.REJECTED)

    ending = application.ending

    assert ending.status == Status.REJECTED
    assert ending.last_stage == Status.INTERVIEWING


def test_the_reason_given_while_it_was_ending_stands_until_another_is(application):
    change_status(application, Status.GHOSTED, end_reason=EndReason.FILLED_INTERNALLY)
    change_status(application, Status.REJECTED)

    assert application.ending.reason == EndReason.FILLED_INTERNALLY


def test_reopening_forgets_the_ending_and_ending_again_is_a_new_one(application):
    change_status(application, Status.REJECTED, end_reason=EndReason.PAY)
    change_status(application, Status.SCREENING, note="They came back.")
    assert application.ending is None

    change_status(application, Status.WITHDRAWN, end_reason=EndReason.MY_CHOICE)
    ending = application.ending

    assert ending.reason == EndReason.MY_CHOICE, "the first ending's reason is not this one's"
    assert ending.last_stage == Status.SCREENING
    reasons = application.events.exclude(end_reason="").values_list("end_reason", flat=True)
    assert sorted(reasons) == ["my_choice", "pay"], "and the timeline still holds both"


def test_a_rejection_dated_before_the_last_move_is_still_the_ending(application):
    """A rejection accepted from a mailbox is dated when the message arrived, which can be
    before a move somebody made by hand in the meantime."""
    change_status(
        application,
        Status.REJECTED,
        occurred_at=timezone.now() - dt.timedelta(days=30),
        end_reason=EndReason.NOT_A_MATCH,
    )

    ending = application.ending

    assert ending.reason == EndReason.NOT_A_MATCH
    assert ending.last_stage == Status.INTERVIEWING


def test_a_row_that_ended_with_no_timeline_says_what_it_can(user, company):
    """Made by an import or a fixture, with nothing on its timeline: it ended, and the
    record does not say where or why. Neither is invented."""
    posting = JobPosting.objects.create(owner=user, company=company, title="Old Role")
    bare = Application.objects.create(owner=user, posting=posting, status=Status.REJECTED)

    ending = bare.ending

    assert ending.status == Status.REJECTED
    assert ending.last_stage == "" and ending.reason == "" and ending.note == ""


def test_a_reason_this_version_has_never_heard_of_is_shown_as_it_is(application):
    """An archive from a later Postulo may carry one, and the page must still open."""
    change_status(application, Status.REJECTED)
    application.events.filter(to_status=Status.REJECTED).update(end_reason="overqualified")

    assert application.ending.reason_label == "overqualified"


# -------------------------------------------------------------- a reason learnt later


def test_a_reason_learnt_afterwards_is_an_entry_of_its_own(application):
    ended = change_status(application, Status.REJECTED)
    before = application.events.count()

    added = change_status(
        application, Status.REJECTED, note="Told on the phone.", end_reason=EndReason.PAY
    )

    assert added is not None and added.pk != ended.pk
    assert (added.from_status, added.to_status) == (Status.REJECTED, Status.REJECTED)
    assert added.end_reason == EndReason.PAY
    assert application.events.count() == before + 1
    ended.refresh_from_db()
    assert ended.end_reason == "", "the entry that ended it is as it was written"
    assert application.ending.reason == EndReason.PAY
    assert application.ending.note == "Told on the phone."
    assert application.ending.last_stage == Status.INTERVIEWING, "and the stage is unmoved"


def test_a_second_thought_replaces_the_first_and_both_stay_on_the_timeline(application):
    change_status(application, Status.REJECTED, end_reason=EndReason.NOT_A_MATCH)
    change_status(application, Status.REJECTED, end_reason=EndReason.FILLED_INTERNALLY)

    assert application.ending.reason == EndReason.FILLED_INTERNALLY
    assert application.events.exclude(end_reason="").count() == 2


def test_saying_the_same_reason_again_writes_nothing(application):
    change_status(application, Status.REJECTED, note="Budget.", end_reason=EndReason.PAY)
    before = application.events.count()

    assert change_status(application, Status.REJECTED, end_reason=EndReason.PAY) is None
    assert (
        change_status(application, Status.REJECTED, note="Budget.", end_reason=EndReason.PAY)
        is None
    )
    assert application.events.count() == before


def test_the_same_reason_with_something_new_to_say_is_written(application):
    change_status(application, Status.REJECTED, end_reason=EndReason.OTHER)

    added = change_status(
        application, Status.REJECTED, note="The role was cancelled.", end_reason=EndReason.OTHER
    )

    assert added is not None
    assert application.ending.note == "The role was cancelled."


def test_a_reason_given_later_does_not_move_the_dates(application):
    change_status(application, Status.REJECTED)
    application.refresh_from_db()
    closed, applied = application.closed_at, application.applied_at

    change_status(application, Status.REJECTED, end_reason=EndReason.LOCATION)
    application.refresh_from_db()

    assert application.closed_at == closed and application.applied_at == applied


def test_a_reason_given_later_is_not_the_day_anybody_replied(user, company):
    """The entry is dated when somebody typed it. Read as a reply it would make the employer
    a month slower than they were, and put a rejection in a month it did not happen in."""
    from postulo.applications import reports

    applied = timezone.now() - dt.timedelta(days=40)
    posting = JobPosting.objects.create(owner=user, company=company, title="Slow Role")
    application = Application.objects.create(owner=user, posting=posting, status=Status.DRAFT)
    change_status(application, Status.APPLIED, occurred_at=applied)
    change_status(application, Status.REJECTED, occurred_at=applied + dt.timedelta(days=3))

    before = analytics.build(user)
    change_status(application, Status.REJECTED, end_reason=EndReason.PAY)
    after = analytics.build(user)

    assert before.median_days_to_reply == after.median_days_to_reply == 3
    today = timezone.localdate()
    this_month = reports.build(user, reports.month_period(today.year, today.month))
    rejected_on = timezone.localdate(applied + dt.timedelta(days=3))
    expected = 1 if (rejected_on.year, rejected_on.month) == (today.year, today.month) else 0
    assert this_month.happened.rejections == expected


# ------------------------------------------------------------------- on the page


def test_the_card_offers_the_reason_and_says_what_it_is_for(client, user, application):
    client.force_login(user)

    page = client.get(application.get_absolute_url()).content.decode()
    card = page[page.index('id="status-card"') : page.index('id="event-form"')]

    assert 'name="end_reason"' in card
    for value, label in EndReason.choices:
        assert f'value="{value}"' in card and str(label) in card
    assert 'id="id_end_reason_helptext"' in card, "the help the select says describes it"
    assert 'aria-describedby="id_end_reason_helptext"' in card
    assert "data-ending" not in card, "nothing has ended yet"


def test_recording_an_ending_with_its_reason(client, user, application):
    client.force_login(user)

    response = client.post(
        reverse("applications:status", args=[application.pk]),
        {"status": Status.REJECTED, "end_reason": EndReason.LOCATION, "note": "Too far."},
    )

    assert response.status_code == 302
    ending = Application.objects.get(pk=application.pk).ending
    assert (ending.reason, ending.note) == (EndReason.LOCATION, "Too far.")
    assert [str(m) for m in get_messages(response.wsgi_request)] == ["Status updated."]


def test_the_page_says_how_it_ended(client, user, application):
    change_status(application, Status.REJECTED, note="Too far.", end_reason=EndReason.LOCATION)
    client.force_login(user)

    page = client.get(application.get_absolute_url()).content.decode()
    card = page[page.index('id="status-card"') : page.index('id="event-form"')]

    assert "data-ending" in card
    assert "Last stage reached" in card and "Interviewing" in card
    assert "Why it ended" in card and "The location" in card
    assert "Too far." in card
    timeline = page[page.index('id="timeline"') :]
    assert "Why: The location" in timeline, "and the entry says it as well"


def test_an_ending_nobody_explained_says_so(client, user, application):
    change_status(application, Status.GHOSTED)
    client.force_login(user)

    page = client.get(application.get_absolute_url()).content.decode()
    card = page[page.index('id="status-card"') : page.index('id="event-form"')]

    assert "data-end-reason" in card and "Not recorded" in card


def test_the_swapped_card_says_how_it_ended(client, user, application):
    """The card is the region a status change swaps, which is why the ending is in it: said
    anywhere else it would go on saying what was true before the button was pressed."""
    client.force_login(user)

    response = client.post(
        reverse("applications:status", args=[application.pk]),
        {"status": Status.WITHDRAWN, "end_reason": EndReason.MY_CHOICE, "note": ""},
        **HTMX,
    )

    page = response.content.decode()
    card = page[page.index('id="status-card"') : page.index('id="timeline"')]
    assert "data-ending" in card and "My own choice" in card and "Interviewing" in card
    assert "Why: My own choice" in page[page.index('id="timeline"') :]


def test_a_reason_added_from_the_page_later(client, user, application):
    change_status(application, Status.REJECTED)
    client.force_login(user)

    response = client.post(
        reverse("applications:status", args=[application.pk]),
        {"status": Status.REJECTED, "end_reason": EndReason.FILLED_INTERNALLY, "note": ""},
    )

    assert [str(m) for m in get_messages(response.wsgi_request)] == ["Reason recorded."]
    assert Application.objects.get(pk=application.pk).ending.reason == "filled_internally"


def test_a_reason_beside_a_status_that_is_not_an_ending_is_told_and_not_kept(
    client, user, application
):
    """The box is on the form whatever the status, so this is a slip and not an attack: the
    move is still made, and the person is told the reason went nowhere."""
    client.force_login(user)

    response = client.post(
        reverse("applications:status", args=[application.pk]),
        {"status": Status.OFFER, "end_reason": EndReason.PAY, "note": ""},
    )

    application.refresh_from_db()
    assert application.status == Status.OFFER
    assert not application.events.exclude(end_reason="").exists()
    said = [str(m) for m in get_messages(response.wsgi_request)]
    assert any("The reason was not kept" in line for line in said)
    assert "Status updated." in said


def test_a_reason_that_is_not_one_is_refused_by_the_form(client, user, application):
    client.force_login(user)

    client.post(
        reverse("applications:status", args=[application.pk]),
        {"status": Status.REJECTED, "end_reason": "bad-vibes", "note": ""},
    )

    application.refresh_from_db()
    assert application.status == Status.INTERVIEWING


def test_the_board_and_the_quiet_block_still_move_it_without_being_asked_why(
    client, user, application
):
    """One click, as it always was. The reason can be said afterwards."""
    client.force_login(user)

    client.post(reverse("applications:status", args=[application.pk]), {"status": "ghosted"})

    ending = Application.objects.get(pk=application.pk).ending
    assert ending.status == Status.GHOSTED and ending.reason == ""


def test_somebody_elses_application_cannot_be_given_a_reason(client, other_user, application):
    client.force_login(other_user)

    response = client.post(
        reverse("applications:status", args=[application.pk]),
        {"status": Status.REJECTED, "end_reason": EndReason.PAY, "note": ""},
    )

    assert response.status_code == 404
    assert not application.events.exclude(end_reason="").exists()


# ---------------------------------------------------------------------- the API


def test_the_api_takes_the_reason_and_gives_back_the_ending(client, user, application):
    response = post_json(
        client,
        f"/api/v1/applications/{application.pk}/status",
        {"status": "rejected", "note": "Under the range.", "end_reason": "pay"},
        **bearer(user),
    )

    assert response.status_code == 200, response.content
    body = response.json()
    assert body["status"] == "rejected"
    assert body["end_reason"] == "pay"
    assert body["end_note"] == "Under the range."
    assert body["last_stage"] == "interviewing"
    ended = next(event for event in body["events"] if event["to_status"] == "rejected")
    assert ended["end_reason"] == "pay"
    assert ended["actor"], "and the timeline says a token wrote it"


def test_the_api_adds_a_reason_learnt_afterwards(client, user, application):
    change_status(application, Status.GHOSTED)

    response = post_json(
        client,
        f"/api/v1/applications/{application.pk}/status",
        {"status": "ghosted", "end_reason": "filled_internally"},
        **bearer(user),
    )

    assert response.status_code == 200
    assert response.json()["end_reason"] == "filled_internally"
    assert application.events.exclude(end_reason="").count() == 1


def test_the_api_refuses_a_reason_that_has_nothing_to_go_with(client, user, application):
    response = post_json(
        client,
        f"/api/v1/applications/{application.pk}/status",
        {"status": "offer", "end_reason": "pay"},
        **bearer(user),
    )

    assert response.status_code == 422
    application.refresh_from_db()
    assert application.status == Status.INTERVIEWING, "refused whole, not half done"


def test_the_api_refuses_a_reason_that_is_not_one(client, user, application):
    response = post_json(
        client,
        f"/api/v1/applications/{application.pk}/status",
        {"status": "rejected", "end_reason": "bad-vibes"},
        **bearer(user),
    )

    assert response.status_code == 422
    assert "end_reason" in response.content.decode()


def test_a_live_application_says_nothing_about_an_ending(client, user, application):
    body = client.get(f"/api/v1/applications/{application.pk}", **bearer(user)).json()

    assert (body["end_reason"], body["end_note"], body["last_stage"]) == ("", "", "")


def test_the_list_says_how_each_ended_and_asks_once_for_all_of_them(client, user, company):
    from django.db import connection
    from django.test.utils import CaptureQueriesContext

    for number in range(6):
        ended = an_application(
            user, company, Status.APPLIED, Status.SCREENING, title=f"Role {number}"
        )
        change_status(ended, Status.REJECTED, end_reason=EndReason.NOT_A_MATCH)
    headers = bearer(user, "read")
    client.get("/api/v1/applications", **headers)

    with CaptureQueriesContext(connection) as captured:
        response = client.get("/api/v1/applications", **headers)

    rows = response.json()["items"]
    assert len(rows) == 6
    assert {row["end_reason"] for row in rows} == {"not_a_match"}
    assert {row["last_stage"] for row in rows} == {"screening"}
    timelines = [q for q in captured if "applications_applicationevent" in q["sql"]]
    assert len(timelines) <= 2, (
        f"one read of the timelines for the page, not one a row: {timelines}"
    )


def test_the_schema_describes_the_reason(client, user):
    schema = client.get("/api/v1/openapi.json", **bearer(user, "read")).json()
    shapes = schema["components"]["schemas"]

    assert "end_reason" in shapes["StatusIn"]["properties"]
    assert {"end_reason", "end_note", "last_stage"} <= set(shapes["ApplicationOut"]["properties"])
    assert "end_reason" in shapes["EventOut"]["properties"]


# ------------------------------------------------------- the export and the way back


def test_the_reason_travels_on_the_entry_and_comes_back(user, other_user, application):
    import zipfile

    from postulo.core import export, importer

    change_status(application, Status.REJECTED, note="Under.", end_reason=EndReason.PAY)

    document = export.build_document(user)
    exported = document["companies"][0]["postings"][0]["applications"][0]
    assert "end_reason" not in exported, "on the entry, not on the application"
    ended = next(event for event in exported["events"] if event["to_status"] == "rejected")
    assert ended["end_reason"] == "pay"

    importer.load(other_user, zipfile.ZipFile(export.write_archive(user)))

    restored = Application.objects.get(owner=other_user)
    assert restored.ending.reason == EndReason.PAY
    assert restored.ending.note == "Under."
    assert restored.ending.last_stage == Status.INTERVIEWING


def test_an_archive_from_before_there_were_reasons_still_restores(user, other_user, application):
    import io
    import json as json_module
    import zipfile

    from postulo.core import export, importer

    change_status(application, Status.REJECTED, end_reason=EndReason.PAY)
    document = export.build_document(user)
    document["postulo"]["format"] = 18
    for company in document["companies"]:
        for posting in company["postings"]:
            for exported in posting["applications"]:
                exported.pop("referred_by_id", None)
                exported.pop("through_agency", None)
                for event in exported["events"]:
                    event.pop("end_reason", None)
    document.pop("contacts", None)
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w") as archive:
        archive.writestr(export.MANIFEST_NAME, json_module.dumps(document))
    buffer.seek(0)

    importer.load(other_user, zipfile.ZipFile(buffer))

    restored = Application.objects.get(owner=other_user)
    assert restored.status == Status.REJECTED
    assert restored.ending.reason == "", "nobody had said, and nothing is invented"
    assert restored.ending.last_stage == Status.INTERVIEWING


# ---------------------------------------------------------------- the dashboard


def test_the_figures_count_where_and_why(user, company):
    pay = an_application(user, company, Status.APPLIED, Status.INTERVIEWING, title="A")
    change_status(pay, Status.REJECTED, end_reason=EndReason.PAY)
    silent = an_application(user, company, Status.APPLIED, title="B")
    change_status(silent, Status.GHOSTED)
    mine = an_application(user, company, Status.APPLIED, Status.INTERVIEWING, title="C")
    change_status(mine, Status.WITHDRAWN, end_reason=EndReason.MY_CHOICE)
    an_application(user, company, Status.APPLIED, title="Still live")

    found = analytics.build(user).endings

    assert found.total == 3 and found.explained == 2
    stages = {
        row.key: (row.total, row.rejected, row.withdrawn, row.ghosted) for row in found.stages
    }
    assert stages == {"applied": (1, 0, 0, 1), "interviewing": (2, 1, 1, 0)}
    assert [row.key for row in found.stages] == ["applied", "interviewing"], "in the board's order"
    reasons = {row.key: row.total for row in found.reasons}
    assert reasons == {"pay": 1, "my_choice": 1, "": 1}
    assert found.reasons[-1].key == "", "what is not known comes after what is"
    assert found.reasons[-1].label == "Not recorded"


def test_the_figures_are_each_persons_own(user, other_user, company):
    ended = an_application(user, company, Status.APPLIED)
    change_status(ended, Status.REJECTED, end_reason=EndReason.PAY)

    assert analytics.build(other_user).endings.total == 0


def test_the_widget_is_registered_and_waits_to_be_chosen():
    from postulo.core import widgets

    widget = widgets.get("endings")

    assert widget is not None
    assert str(widget.label) == "Where and why applications end"
    assert widget.default_order is None, "a page somebody arranged is not rearranged for them"
    assert "endings" not in widgets.default_keys()


def show(user, *keys):
    profile = user.profile
    profile.dashboard_widgets = list(keys)
    profile.save(update_fields=["dashboard_widgets"])


def test_the_widget_draws_both_tables(client, user, company):
    ended = an_application(user, company, Status.APPLIED, Status.INTERVIEWING)
    change_status(ended, Status.REJECTED, end_reason=EndReason.PAY)
    show(user, "endings")
    client.force_login(user)

    page = client.get(reverse("core:home")).content.decode()
    widget = page[page.index('data-widget="endings"') :]

    assert "Where and why applications end" in widget
    assert "data-endings-where" in widget and "data-endings-why" in widget
    assert '<table class="table"' in widget
    assert "Interviewing" in widget and "The pay" in widget
    assert "1 of 1 ending has a reason recorded" in " ".join(widget.split())


def test_the_widget_is_quiet_when_nothing_has_ended(client, user, company):
    an_application(user, company, Status.APPLIED)
    show(user, "endings")
    client.force_login(user)

    page = client.get(reverse("core:home")).content.decode()
    widget = page[page.index('data-widget="endings"') :]

    assert 'data-variant="quiet"' in widget
    assert "Nothing has ended yet" in widget
    assert "data-endings-where" not in widget


def test_figures_kept_by_an_earlier_release_are_not_read_by_this_one(user, company):
    """The cache is a table and outlives an upgrade. Figures cached before the endings
    existed have no endings in them, so the key they were kept under must not be asked for."""
    an_application(user, company, Status.APPLIED)

    key = analytics.fingerprint(user)
    shape = analytics.SHAPE
    try:
        analytics.SHAPE = shape + 1
        assert analytics.fingerprint(user) != key
    finally:
        analytics.SHAPE = shape


def test_the_figures_are_worked_out_again_in_another_language(user, company):
    """They carry words -- a stage, a reason, *Not recorded* -- in the language they were
    worked out in."""
    from django.utils import translation

    an_application(user, company, Status.APPLIED)

    with translation.override("en-GB"):
        english = analytics.fingerprint(user)
    with translation.override("fr-FR"):
        french = analytics.fingerprint(user)

    assert english != french


def test_endings_reads_the_timeline_it_was_handed(user, company, django_assert_num_queries):
    ended = an_application(user, company, Status.APPLIED, Status.SCREENING)
    change_status(ended, Status.REJECTED, end_reason=EndReason.LOCATION)

    [loaded] = list(Application.objects.for_user(user).with_status_log())
    with django_assert_num_queries(0):
        found = endings.of(loaded)

    assert (found.reason, found.last_stage) == (EndReason.LOCATION, Status.SCREENING)
