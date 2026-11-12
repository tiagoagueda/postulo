"""The slow work this app asks for: drawing the report (#247).

The report is built from the whole record and then drawn, and pressing *Download* filed it
under *Sent documents* while somebody watched. Filing is still what the press means; the
waiting is not.

**The GET is unchanged and stays in the request.** Opening the address is a draft: the same
PDF, filed nowhere, served straight back and cached in-process (`documents/pdf.py`). Moving
that to a worker would have put the cache in a process the web one cannot read, for a
response that has to be a file download anyway. The press is what became an errand, because
the press is what leaves a record.

The scheduler sends the same errand for the report made on a chosen day (#1095), marked
``scheduled``: it is filed the same way, kept back from the stores unless the person said to
hand it on, and leaves a reminder to read it.
"""

from __future__ import annotations

import logging

from django.utils.translation import gettext_lazy as _

from postulo.core import languages
from postulo.core.errands import Refused, handler

logger = logging.getLogger(__name__)


@handler("report_pdf", working=_("Drawing the report"))
def render_a_report(errand) -> dict:
    """Build the report for a period, draw it, and file it under Sent documents."""
    from django.urls import reverse

    from postulo.documents.pdf import PDFBackendUnavailable
    from postulo.documents.rendering import snapshot_report
    from postulo.documents.themes import CannotRender

    from . import reports

    payload = errand.payload or {}
    period = reports.period_from(payload.get("query") or {})
    scheduled = bool(payload.get("scheduled"))
    profile = getattr(errand.owner, "profile", None)
    hand_on = scheduled and bool(getattr(profile, "report_send", False))
    # The worker has no request to have chosen a language, so the one chosen on the page
    # travels with the errand, and the theme with it (#340). An errand sent before either was
    # chosen there carries neither: the report is drawn in the person's remembered theme and in
    # the language a CV of theirs with none would be, as the draft the GET serves is (#568).
    try:
        try:
            report, html, theme, language = reports.draw(
                errand.owner,
                period,
                theme=str(payload.get("theme") or ""),
                language=str(payload.get("language") or ""),
                # Checked against the theme on the page that sent this (#344); an errand sent
                # before there was a choice carries none, and is drawn on the default.
                paper=str(payload.get("paper") or ""),
            )
        except CannotRender as refused:
            raise Refused(str(refused)) from refused
        # `snapshot_report` writes down the language and the theme it was drawn in, so what was
        # handed over says what it is either way (#283, #340).
        with languages.override(language):
            title = reports.title_of(report)
            try:
                document = snapshot_report(
                    errand.owner,
                    title=title,
                    html=html,
                    filename=reports.filename(report, "pdf"),
                    theme=theme,
                    language=language,
                    # Made without being asked, so read before it goes anywhere unless the
                    # person said otherwise (#1095).
                    kept_back=scheduled and not hand_on,
                )
            except PDFBackendUnavailable as unavailable:
                raise Refused(str(unavailable)) from unavailable
    except Refused as refused:
        if scheduled:
            # Nobody is watching this errand, so the reminder is what says it failed.
            _remind(errand.owner, period, made=False, note=str(refused))
        raise

    if scheduled:
        note = str(_("Filed under Sent documents."))
        if hand_on and getattr(profile, "report_send_to", ""):
            note = _email(errand.owner, profile.report_send_to, document, title)
        _remind(errand.owner, period, made=True, note=note)

    return {
        "message": str(
            _("Filed under Sent documents, dated today, so what you handed over is kept.")
        ),
        "url": reverse("documents:rendered_download", args=[document.pk]),
    }


def _remind(owner, period, *, made: bool, note: str) -> None:
    """The reminder to read a scheduled report, or to say it could not be made (#1095).

    Due now, so the scheduler's next pass announces it through the person's notifiers as it
    does any reminder. One per month: an errand run again leaves the one already there.
    """
    from django.utils import timezone

    from .models import Reminder
    from .scheduled_reports import REMINDER_UID
    from .services import create_reminder

    uid = REMINDER_UID.format(month=period.start)
    if Reminder.objects.filter(owner=owner, uid=uid).exists():
        return
    words = (
        _("Read your job search report for %(period)s")
        if made
        else _("Your job search report for %(period)s could not be made")
    )
    create_reminder(
        owner,
        summary=str(words) % {"period": period.label},
        due=timezone.now(),
        uid=uid,
        note=note,
    )


def _email(owner, address: str, document, title: str) -> str:
    """Mail the filed report from the person's own outbox. What to say on the reminder if it
    did not go, or nothing if it did."""
    import smtplib

    from django.core.mail import EmailMessage

    from postulo.core import correspondence, destinations, throttle
    from postulo.core.mail import ConnectionFailed
    from postulo.plugins.consent import ConsentFailed

    try:
        with document.file.open("rb") as handle:
            content = handle.read()
    except OSError:
        return str(_("Filed under Sent documents. It could not be read, so it was not emailed."))
    message = EmailMessage(
        subject=title,
        body=str(_("The job search report is attached.")),
        to=[address],
    )
    message.attach(document.download_name, content, "application/pdf")
    try:
        correspondence.send(owner, message)
    except (
        correspondence.NoOutbox,
        correspondence.WrongSender,
        throttle.TooOften,
        ConnectionFailed,
        ConsentFailed,
        destinations.Refused,
    ) as refused:
        return str(_("Filed under Sent documents. It was not emailed: %(reason)s")) % {
            "reason": refused
        }
    except (smtplib.SMTPException, OSError):
        # Never the exception's text: a server's reply can quote the recipient.
        logger.warning("An outbox for account %s failed to send a report", owner.pk)
        return str(
            _("Filed under Sent documents. The mail server did not take it, so it was not emailed.")
        )
    return str(_("Filed under Sent documents and emailed to %(address)s.")) % {"address": address}
