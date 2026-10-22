"""The date boxes every form draws: the browser's own, with Postulo's calendar beside it (#933).

(Not ``core/widgets.py``, which is the dashboard's.)

**The box is the form.** A date is posted as it always was, by an ``<input type="date">``, so
validation, ``cleaned_data``, the API and the import formats see nothing new, and with scripts
off the box is all there is. Beside it, ``partials/date_widget.html`` draws a button that the
script shows and a ``<c-date-picker>`` that writes ``YYYY-MM-DD`` back into the box
(``readyDateFields`` in ``app.js``).

**Always ISO 8601 in the box.** Django's ``DateInput`` writes its value in the language's first
input format, and British English's is ``%d/%m/%Y``: ``value="19/10/2026"`` in a box of type
``date`` is a value the browser throws away, so a stored date drew as an empty box. The wire
format of a date box is ``Y-m-d`` whatever the language, and the browser shows it in the
reader's.

**A date and a time are two boxes posting one value.** ``DateTimeInput`` draws the date box,
with its calendar, and the browser's own ``type="time"`` box, and reads them back as the
``YYYY-MM-DDTHH:MM`` the single ``datetime-local`` box used to post, so the form's field, its
input formats and its checks ("cannot end before it starts") are unchanged. The date box keeps
the field's name and id, which is what the label points at; the time box is ``<name>_time``.
A request that posts the field whole -- an older page, a test, a script -- is read as before.
"""

from __future__ import annotations

import datetime as dt

from django import forms
from django.utils import timezone

#: What a date box holds and posts, in every language.
DATE_FORMAT = "%Y-%m-%d"
#: And a time box.
TIME_FORMAT = "%H:%M"
#: The suffix of the time box's name and id.
TIME_SUFFIX = "_time"


class DateInput(forms.DateInput):
    """A date box with Postulo's calendar beside it, posting ``YYYY-MM-DD``."""

    input_type = "date"
    template_name = "partials/date_widget.html"

    def __init__(self, attrs=None, format=None):
        super().__init__(attrs, format=format or DATE_FORMAT)


class TimeInput(forms.TimeInput):
    """The browser's own time box, ``HH:MM``."""

    input_type = "time"

    def __init__(self, attrs=None, format=None):
        super().__init__(attrs, format=format or TIME_FORMAT)


class DateTimeInput(forms.MultiWidget):
    """A date box with its calendar and a time box, read back as one ``datetime-local`` value."""

    template_name = "partials/datetime_widget.html"
    #: The visible label names the date box, which is what the help and the errors describe.
    use_fieldset = False

    def __init__(self, attrs=None):
        super().__init__({"": DateInput(), "time": TimeInput()}, attrs)

    def decompress(self, value):
        if not value:
            return [None, None]
        if isinstance(value, str):
            day, _sep, time = value.strip().replace(" ", "T", 1).partition("T")
            return [day or None, time[:5] or None]
        if isinstance(value, dt.datetime):
            if timezone.is_aware(value):
                value = timezone.make_naive(value)
            return [value.date(), value.time().replace(second=0, microsecond=0)]
        if isinstance(value, dt.date):
            return [value, None]
        return [None, None]

    def format_value(self, value):
        """The value as the two boxes post it together, ``YYYY-MM-DDTHH:MM``: what a form
        compares a posted time with, to tell whether anybody changed it (#545)."""
        day, time = self.decompress(value)
        if not day:
            return None
        day = day.strftime(DATE_FORMAT) if isinstance(day, dt.date) else str(day)
        if not time:
            return day
        time = time.strftime(TIME_FORMAT) if isinstance(time, dt.time) else str(time)
        return f"{day}T{time}"

    def value_from_datadict(self, data, files, name):
        if f"{name}{TIME_SUFFIX}" not in data:
            return data.get(name)
        day = (data.get(name) or "").strip()
        time = (data.get(f"{name}{TIME_SUFFIX}") or "").strip()
        if "T" in day or not time:
            return day
        if not day:
            # A time with no day is no moment: left for the field to refuse.
            return time
        return f"{day}T{time}"

    def value_omitted_from_data(self, data, files, name):
        return name not in data and f"{name}{TIME_SUFFIX}" not in data

    def id_for_label(self, id_):
        return id_

    def get_context(self, name, value, attrs):
        context = super().get_context(name, value, attrs)
        date, time = context["widget"]["subwidgets"]
        identifier = (attrs or {}).get("id") or self.attrs.get("id")
        if identifier:
            date["attrs"]["id"] = identifier
            time["attrs"]["id"] = f"{identifier}{TIME_SUFFIX}"
        return context
