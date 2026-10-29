"""The slow work this app asks for: drawing the report (#247).

The report is built from the whole record and then drawn, and pressing *Download* filed it
under *Sent documents* while somebody watched. Filing is still what the press means; the
waiting is not.

**The GET is unchanged and stays in the request.** Opening the address is a draft: the same
PDF, filed nowhere, served straight back and cached in-process (`documents/pdf.py`). Moving
that to a worker would have put the cache in a process the web one cannot read, for a
response that has to be a file download anyway. The press is what became an errand, because
the press is what leaves a record.
"""

from __future__ import annotations

from django.utils.translation import gettext_lazy as _

from postulo.core import languages
from postulo.core.errands import Refused, handler


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
    # The worker has no request to have chosen a language, so the one chosen on the page
    # travels with the errand, and the theme with it (#340). An errand sent before either was
    # chosen there carries neither: the report is drawn in the person's remembered theme and in
    # the language a CV of theirs with none would be, as the draft the GET serves is (#568).
    try:
        report, html, theme, language = reports.draw(
            errand.owner,
            period,
            theme=str(payload.get("theme") or ""),
            language=str(payload.get("language") or ""),
        )
    except CannotRender as refused:  # pragma: no cover - `theme_for` offers only setters
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
            )
        except PDFBackendUnavailable as unavailable:
            raise Refused(str(unavailable)) from unavailable

    return {
        "message": str(
            _("Filed under Sent documents, dated today, so what you handed over is kept.")
        ),
        "url": reverse("documents:rendered_download", args=[document.pk]),
    }
