"""One set of date pickers for the calendar and every date in the app (#933).

The component's four modes, the first weekday the pickers read off the page, the date widgets
every form draws, the calendar's picker per view with the agenda's end day, and the report's
and the table filters' boxes. What a script does with them is in
`tests/e2e/test_date_pickers.py`; this is what the server draws and accepts.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest
from django.template import Context, engines
from django.urls import reverse

from postulo.core import weeks

pytestmark = pytest.mark.django_db


def render(source: str, **context) -> str:
    return engines["django"].engine.from_string(source).render(Context(context))


@pytest.fixture
def application(user):
    from postulo.applications.models import Application, Status
    from postulo.jobs.models import Company, JobPosting

    company = Company.objects.create(owner=user, name="Aperture Science")
    posting = JobPosting.objects.create(owner=user, company=company, title="Test Engineer")
    return Application.objects.create(owner=user, posting=posting, status=Status.APPLIED)


@pytest.fixture(autouse=True)
def _locale_weeks():
    weeks.choose(None)
    yield
    weeks.choose(None)


# ------------------------------------------------------------------ the component


@pytest.mark.parametrize("mode", ["day", "week", "month", "range"])
def test_each_mode_is_said_on_the_panel(mode):
    html = render(
        f'{{% load i18n %}}{{% cotton date-picker id="p" label="When" mode="{mode}" / %}}'
    )

    assert f'data-mode="{mode}"' in html
    assert 'role="dialog"' in html and 'aria-label="When"' in html
    assert "data-date-picker-grid" in html and "data-date-picker-year" in html


def test_a_month_picker_steps_by_the_year_and_has_no_month_list():
    html = render('{% cotton date-picker id="p" label="When" mode="month" / %}')

    assert "data-date-picker-month" not in html
    assert 'aria-label="Previous year"' in html and 'aria-label="Next year"' in html
    assert 'aria-label="Choose a month"' in html


def test_a_day_picker_steps_by_the_month():
    html = render('{% cotton date-picker id="p" label="When" / %}')

    assert 'aria-label="Previous month"' in html and 'aria-label="Next month"' in html
    assert "data-date-picker-month" in html


def test_the_week_and_range_pickers_carry_the_sentences_their_status_says():
    week = render('{% cotton date-picker id="p" label="When" mode="week" / %}')
    span = render('{% cotton date-picker id="p" label="When" mode="range" / %}')

    assert 'data-week-status="Week of %(day)s"' in week
    assert 'data-range-status="From %(start)s to %(end)s"' in span
    assert "data-range-start-status=" in span and "data-range-status" not in week


def test_the_limits_are_the_callers_and_empty_means_none():
    open_ended = render('{% cotton date-picker id="p" label="When" / %}')
    bounded = render(
        '{% cotton date-picker id="p" label="When" min="2026-01-01" max="2026-12-31" / %}'
    )

    assert 'data-min=""' in open_ended and 'data-max=""' in open_ended
    assert 'data-min="2026-01-01"' in bounded and 'data-max="2026-12-31"' in bounded


def test_a_panel_for_a_box_names_it_and_draws_no_empty_help():
    html = render(
        '{% cotton date-picker id="p" label="When" mode="range" input="a" until_input="b"'
        " submit / %}"
    )

    assert 'data-for="a"' in html and 'data-until-for="b"' in html and "data-submit" in html
    assert "data-date-picker-help" not in html


def test_the_birth_picker_is_given_its_limits_by_its_page(client, user):
    client.force_login(user)
    html = client.get(reverse("accounts:profile")).content.decode()

    panel = html[html.index('id="birth-picker"') - 200 : html.index('id="birth-picker"') + 600]
    assert 'data-min="1900-01-01"' in panel
    assert re.search(r'data-max="\d{4}-\d{2}-\d{2}"', panel), "today, the latest birth date"


# ------------------------------------------------------------ the first weekday


def html_tag(client) -> str:
    html = client.get(reverse("accounts:profile")).content.decode()
    return html[html.index("<html") : html.index(">", html.index("<html")) + 1]


def test_the_page_says_the_first_weekday_the_server_uses(client, user):
    client.force_login(user)

    assert 'data-first-weekday="1"' in html_tag(client), "en-GB: Monday"

    user.profile.first_weekday = 0
    user.profile.save()
    assert 'data-first-weekday="0"' in html_tag(client), "Sunday is 0, not nothing"


def test_weeks_are_numbered_only_where_the_language_numbers_them(client, user):
    client.force_login(user)

    assert "data-week-numbers" not in html_tag(client)
    assert weeks.numbers_weeks("sv-SE") and weeks.numbers_weeks("de")
    assert weeks.numbers_weeks("nb-NO") and weeks.numbers_weeks("fi")
    assert not weeks.numbers_weeks("en-GB") and not weeks.numbers_weeks("pt-PT")
    assert not weeks.numbers_weeks("")


# ------------------------------------------------------------------- the widgets


def test_a_date_box_holds_iso_8601_whatever_the_language():
    """Django wrote `19/10/2026` in British English, which a date box throws away."""
    import datetime as dt

    from django.utils import translation

    from postulo.core.date_widgets import DateInput

    with translation.override("en-GB"):
        html = DateInput().render("closes_at", dt.date(2026, 10, 19), {"id": "id_closes_at"})

    assert 'type="date"' in html and 'value="2026-10-19"' in html


def test_a_date_box_has_its_calendar_beside_it_hidden_until_a_script_shows_it():
    import datetime as dt

    from postulo.core.date_widgets import DateInput

    html = DateInput().render("closes_at", dt.date(2026, 10, 19), {"id": "id_closes_at"})

    assert 'id="id_closes_at"' in html and 'name="closes_at"' in html
    button = re.search(r"<button[^>]*data-date-opener[^>]*>", html)[0]
    assert " hidden" in button and 'popovertarget="id_closes_at-calendar"' in button
    assert 'aria-label="Choose dates from a calendar"' in button
    panel = html[html.index('id="id_closes_at-calendar"') - 10 :]
    assert 'data-for="id_closes_at"' in panel and "data-named-by-field" in panel
    assert 'data-mode="day"' in panel


def test_a_box_drawn_with_no_id_is_the_box_alone():
    from postulo.core.date_widgets import DateInput

    html = DateInput().render("closes_at", None)

    assert 'type="date"' in html and "data-date-opener" not in html


def test_every_date_in_the_forms_is_drawn_with_the_calendar():
    from postulo.applications.forms import InterviewForm, PostingIntakeForm
    from postulo.core.date_widgets import DateInput, DateTimeInput
    from postulo.documents import forms as document_forms
    from postulo.jobs import forms as job_forms
    from postulo.resume import forms as resume_forms

    assert isinstance(PostingIntakeForm().fields["closes_at"].widget, DateInput)
    assert isinstance(InterviewForm.base_fields["starts_at"].widget, DateTimeInput)
    assert isinstance(resume_forms.DATE_WIDGET, DateInput)
    from postulo.applications import forms as application_forms
    from postulo.jobs import capture_views

    for module in (application_forms, document_forms, job_forms, resume_forms, capture_views):
        source = Path(module.__file__).read_text(encoding="utf-8")
        assert '"type": "date"' not in source and "datetime-local" not in source


class Moment:
    """A form with one moment on it, as the reminder and interview forms have."""

    @staticmethod
    def form(data=None, *, required=True):
        from django import forms

        from postulo.applications.forms import DATETIME_INPUT_FORMATS
        from postulo.core.date_widgets import DateTimeInput

        class Form(forms.Form):
            when = forms.DateTimeField(
                widget=DateTimeInput(), input_formats=DATETIME_INPUT_FORMATS, required=required
            )

        return Form(data)


def test_a_moment_is_two_boxes_the_label_names_the_date():
    import datetime as dt

    form = Moment.form()
    form.initial["when"] = dt.datetime(2030, 3, 13, 9, 0)
    html = str(form["when"])

    assert 'type="date" name="when" value="2030-03-13"' in html and 'id="id_when"' in html
    assert 'type="time" name="when_time" value="09:00"' in html and 'id="id_when_time"' in html
    assert 'aria-label="Time"' in html
    assert form["when"].id_for_label == "id_when"
    assert "datetime-local" not in html


def test_the_two_boxes_post_the_value_the_one_box_did():
    import datetime as dt

    split = Moment.form({"when": "2030-03-13", "when_time": "09:30"})
    whole = Moment.form({"when": "2030-03-13T09:30"})

    assert split.is_valid() and whole.is_valid()
    assert split.cleaned_data["when"] == whole.cleaned_data["when"]
    assert split.cleaned_data["when"].replace(tzinfo=None) == dt.datetime(2030, 3, 13, 9, 30)


def test_a_moment_with_no_day_or_an_unreadable_time_is_refused():
    assert not Moment.form({"when": "", "when_time": "09:30"}).is_valid()
    assert not Moment.form({"when": "2030-03-13", "when_time": "25:99"}).is_valid()
    assert not Moment.form({"when": "", "when_time": ""}).is_valid(), "required"
    assert Moment.form({"when": "", "when_time": ""}, required=False).is_valid()


def test_a_day_with_no_time_is_its_midnight_where_the_moment_may_be_left_out():
    form = Moment.form({"when": "2030-03-13", "when_time": ""}, required=False)

    assert form.is_valid()
    assert form.cleaned_data["when"].hour == 0


def test_the_moment_drawn_is_compared_with_the_moment_posted(client, user, application):
    """An edit page posted unchanged moves nothing (#545), with the time in its own box."""
    import datetime as dt

    from django.utils import timezone

    from postulo.applications.models import InterviewKind
    from postulo.applications.services import reschedule_interview, schedule_interview

    starts = timezone.now().replace(second=0, microsecond=0) + dt.timedelta(days=4)
    interview = schedule_interview(
        application, kind=InterviewKind.VIDEO, starts_at=starts, location="old"
    )
    client.force_login(user)
    url = reverse("applications:interview_update", args=[interview.pk])
    form = client.get(url).context["form"]
    drawn = form["drawn_starts_at"].value()
    day, time = drawn.split("T")

    moved = starts + dt.timedelta(days=5)
    reschedule_interview(interview, starts_at=moved, ends_at=moved + dt.timedelta(hours=1))
    response = client.post(
        url,
        {
            "kind": InterviewKind.VIDEO,
            "starts_at": day,
            "starts_at_time": time,
            "ends_at": "",
            "ends_at_time": "",
            "drawn_starts_at": drawn,
            "drawn_ends_at": form["drawn_ends_at"].value(),
            "location": "new",
            "notes": "",
        },
    )

    assert response.status_code == 302
    interview.refresh_from_db()
    assert interview.location == "new" and interview.starts_at == moved


# --------------------------------------------------------------------- the calendar


def calendar_html(client, **query) -> str:
    return client.get(reverse("applications:calendar"), query).content.decode()


@pytest.mark.parametrize(
    ("query", "mode"),
    [
        ({"month": "2026-09"}, "month"),
        ({"view": "week", "on": "2026-09-16"}, "week"),
        ({"view": "day", "on": "2026-09-16"}, "day"),
        ({"view": "agenda", "on": "2026-09-16"}, "range"),
    ],
)
def test_each_view_opens_its_own_picker(client, user, query, mode):
    client.force_login(user)
    html = calendar_html(client, **query)

    button = re.search(r"<button[^>]*data-picker=\"calendar-period\"[^>]*>", html)[0]
    assert 'popovertarget="calendar-picker"' in button, "the form, with no script"
    assert "data-date-opener" in button
    panel = html[html.index('id="calendar-period"') - 10 :]
    panel = panel[: panel.index(">")]
    assert f'data-mode="{mode}"' in panel and "data-submit" in panel
    assert 'data-for="calendar-picker-on"' in panel
    assert ('data-until-for="calendar-picker-until"' in panel) == (mode == "range")
    # The form that works without a script is still there.
    assert 'id="calendar-picker-on" name="on"' in html


def test_the_agenda_form_without_a_script_asks_for_the_last_day_too(client, user):
    client.force_login(user)
    html = calendar_html(client, view="agenda", on="2026-09-16")

    assert 'name="until" value="2026-10-15"' in html, "thirty days, the last of them"


def agenda(user, **query):
    from postulo.applications import agenda as module

    return module.page_for({"view": "agenda", **query}, user)


def test_an_agenda_with_no_end_day_runs_thirty_days_as_before(user):
    import datetime as dt

    from postulo.applications.agenda import AGENDA_DAYS

    page = agenda(user, on="2026-09-16")

    assert page.start == dt.date(2026, 9, 16) and page.length == AGENDA_DAYS
    assert page.until is None and str(page.title) == "30 days from 16 Sep 2026"
    assert "until=" not in page.earlier + page.later + page.today
    assert page.steps["previous"] == "Previous 30 days"


def test_an_agenda_runs_to_the_end_day_asked_for(user):
    import datetime as dt

    page = agenda(user, on="2026-09-16", until="2026-09-22")

    assert page.start == dt.date(2026, 9, 16) and page.last == dt.date(2026, 9, 22)
    assert page.length == 7 and str(page.title) == "16 Sep 2026 to 22 Sep 2026"
    assert page.steps["previous"] == "Previous 7 days" and page.steps["next"] == "Next 7 days"


def test_earlier_and_later_step_by_the_length_chosen(user):
    page = agenda(user, on="2026-09-16", until="2026-09-22")

    assert "on=2026-09-09&until=2026-09-15" in page.earlier
    assert "on=2026-09-23&until=2026-09-29" in page.later
    assert "&until=" in page.today


def test_an_end_before_the_start_is_the_same_days_the_other_way_round(user):
    import datetime as dt

    page = agenda(user, on="2026-09-22", until="2026-09-16")

    assert page.start == dt.date(2026, 9, 16) and page.last == dt.date(2026, 9, 22)


def test_an_agenda_runs_a_year_at_most(user):
    import datetime as dt

    from postulo.applications.agenda import AGENDA_MAX_DAYS

    page = agenda(user, on="2026-01-01", until="2030-01-01")

    assert page.length == AGENDA_MAX_DAYS == 366
    assert page.last == dt.date(2027, 1, 1)


def test_thirty_days_named_or_an_unreadable_end_are_the_default(user):
    named = agenda(user, on="2026-09-16", until="2026-10-15")
    unread = agenda(user, on="2026-09-16", until="soon")

    assert named.until is None and unread.until is None
    assert named.length == unread.length == 30


def test_the_end_day_travels_with_the_address_and_round_trips(client, user):
    import datetime as dt

    from postulo.applications import agenda as module

    address = module.url_for("agenda", dt.date(2026, 9, 16), until=dt.date(2026, 9, 22))
    assert address.endswith("?view=agenda&on=2026-09-16&until=2026-09-22")
    assert "until=" not in module.url_for("agenda", dt.date(2026, 9, 16))
    assert "until=" not in module.url_for("week", dt.date(2026, 9, 16), until=dt.date(2026, 9, 22))

    client.force_login(user)
    page = client.get(address).context["page"]
    assert page.until == dt.date(2026, 9, 22)
    # The legend and the views keep the period; only the agenda's own link carries the end.
    assert all("until=2026-09-22" in url for *_rest, url in page.legend)
    views = {view: url for view, _label, url, _current, _icon in page.switcher}
    assert "until=2026-09-22" in views["agenda"] and "until=" not in views["week"]


def test_the_end_day_is_ignored_by_the_other_views(user):
    from postulo.applications import agenda as module

    page = module.page_for({"view": "week", "on": "2026-09-16", "until": "2026-12-31"}, user)

    assert page.length == 7 and page.until is None


def test_the_search_keeps_the_end_day(client, user):
    client.force_login(user)
    html = calendar_html(client, view="agenda", on="2026-09-16", until="2026-09-22")

    assert '<input type="hidden" name="until" value="2026-09-22">' in html, "the masthead's"
    assert 'name="until" value="2026-09-22" class="input"' in html, "the period form's"


# --------------------------------------------------------- the report and the filters


def test_the_report_offers_the_months_and_a_range_beside_its_boxes(client, user):
    client.force_login(user)
    html = client.get(reverse("applications:report")).content.decode()

    assert 'type="month" name="on" id="report-month"' in html, "the boxes stay the form"
    assert 'name="from" id="report-from"' in html and 'name="to" id="report-to"' in html
    month = html[html.index('id="report-month-calendar"') - 10 :]
    assert 'data-mode="month"' in month[:400] and 'data-for="report-month"' in month[:400]
    period = html[html.index('id="report-period-calendar"') - 10 :]
    assert 'data-mode="range"' in period[:400]
    assert 'data-for="report-from"' in period[:400] and 'data-until-for="report-to"' in period[:600]
    assert html.count('popovertarget="report-period-calendar"') == 1


def test_a_date_filter_is_one_range_in_the_header_and_one_in_the_narrow_block(
    client, user, application
):
    client.force_login(user)
    html = client.get(reverse("applications:list")).content.decode()

    for suffix in ("", "-narrow"):
        name = f"filter-applied{suffix}"
        assert f'id="{name}-from"' in html and f'id="{name}-to"' in html, "ids kept"
        assert f'data-for="{name}-from" data-until-for="{name}-to"' in html
        assert f'popovertarget="{name}-calendar"' in html


def test_a_stored_date_is_drawn_in_its_box_on_an_edit_page(client, user, application):
    """Before #933 the box said `value="19/10/2026"` in British English, which a browser
    throws away: the stored closing date drew as an empty box."""
    import datetime as dt

    posting = application.posting
    posting.closes_at = dt.date(2026, 10, 19)
    posting.save()
    client.force_login(user)

    html = client.get(reverse("jobs:posting_update", args=[posting.pk])).content.decode()

    assert re.search(r'<input type="date" name="closes_at" value="2026-10-19"', html)
