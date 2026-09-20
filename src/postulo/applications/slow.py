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

from postulo.core.errands import Refused, handler


@handler("report_pdf", working=_("Drawing the report"))
def render_a_report(errand) -> dict:
    """Build the report for a period, draw it, and file it under Sent documents."""
    from django.template.loader import render_to_string
    from django.urls import reverse

    from postulo.documents.pdf import PDFBackendUnavailable
    from postulo.documents.rendering import snapshot_report

    from . import reports

    period = reports.period_from(errand.payload.get("query") or {})
    report = reports.build(errand.owner, period)
    # `render_to_string` rather than the view's `render`: there is no request here, and the
    # print template asks for nothing a request carries. The language is the worker's, which
    # is the instance default -- and `snapshot_report` writes down the language it drew in,
    # so what was handed over says what it is either way (#283).
    html = render_to_string("applications/report_print.html", {"report": report})
    title = str(_("Job search report · %(period)s")) % {"period": report.period.label}
    try:
        document = snapshot_report(
            errand.owner,
            title=title,
            html=html,
            filename=reports.filename(report, "pdf"),
        )
    except PDFBackendUnavailable as unavailable:
        raise Refused(str(unavailable)) from unavailable

    return {
        "message": str(
            _("Filed under Sent documents, dated today, so what you handed over is kept.")
        ),
        "url": reverse("documents:rendered_download", args=[document.pk]),
    }
