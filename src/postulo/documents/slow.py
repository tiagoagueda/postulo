"""The slow work this app asks for: drawing PDFs (#247).

On the WeasyPrint backend a render is seconds; on the Chromium one it is a browser launch
and then a render, which is why a container allows a worker two minutes rather than thirty
(#220). Two buttons do it -- *Export as PDF* on a CV, and *Record what you sent* on an
application, which may draw two documents -- and both held a request open for the whole of
it.

**Nothing is retried.** Rendering a CV files a snapshot, and a snapshot is a record of what
somebody handed over; a second attempt made on the person's behalf would file a second one.
A failure here says why, and the button is still there.

**What a person has to see is already kept.** Whatever these draw is in *Sent documents*
before the errand finishes, so the answer to *where is it if I closed the tab* is the answer
it always was.
"""

from __future__ import annotations

from django.utils.translation import gettext_lazy as _

from postulo.core.errands import Refused, handler


@handler("cv_pdf", working=_("Drawing the PDF"))
def render_a_cv(errand) -> dict:
    """Render one CV and file the snapshot."""
    from django.urls import reverse

    from .models import CV
    from .pdf import PDFBackendUnavailable
    from .rendering import snapshot_cv

    cv = CV.objects.filter(pk=errand.payload.get("cv_id"), owner=errand.owner).first()
    if cv is None:
        raise Refused(_("That CV is no longer here."))
    try:
        document = snapshot_cv(cv)
    except PDFBackendUnavailable as unavailable:
        raise Refused(str(unavailable)) from unavailable
    return {
        "message": str(_("PDF created.")),
        "url": reverse("documents:rendered_download", args=[document.pk]),
    }


@handler("sent_documents", working=_("Freezing what you sent"))
def freeze_what_was_sent(errand) -> dict:
    """Draw whatever was chosen, attach it all to the application, note it on the timeline.

    The whole of `SendDocumentsView.post` after the form said yes, moved here unchanged in
    what it does and in what order: one renderer for both documents, each snapshot saved as
    it is made, and the one transaction at the end that ties the lot to the application. A
    render that fails half way still keeps the document it managed to file, which is a
    document that genuinely exists (#220).
    """
    from django.db import transaction

    from postulo.applications.models import Application
    from postulo.applications.services import record_event
    from postulo.resume.models import Link

    from .models import UploadedDocument
    from .pdf import PDFBackendUnavailable, pdf_session
    from .rendering import snapshot_cv, snapshot_letter

    application = Application.objects.filter(
        pk=errand.payload.get("application_id"), owner=errand.owner
    ).first()
    if application is None:
        raise Refused(_("That application is no longer here."))

    cv = _one("CV", errand.payload.get("cv_id"), errand.owner)
    letter = _one("CoverLetter", errand.payload.get("letter_id"), errand.owner)
    uploads = list(
        UploadedDocument.objects.for_user(errand.owner).filter(
            pk__in=errand.payload.get("upload_ids") or []
        )
    )
    links = list(
        Link.objects.for_user(errand.owner).filter(pk__in=errand.payload.get("link_ids") or [])
    )

    created: list[str] = []
    if cv or letter:
        try:
            # Opened once, and only where there is something to draw: a *Send* of uploads and
            # links alone should not start a browser. Opening it also settles whether there is
            # a usable backend before the first document is written down.
            with pdf_session() as backend:
                if cv:
                    created.append(snapshot_cv(cv, application=application, backend=backend).title)
                if letter:
                    created.append(
                        snapshot_letter(letter, application=application, backend=backend).title
                    )
        except PDFBackendUnavailable as unavailable:
            raise Refused(str(unavailable)) from unavailable

    created.extend(str(upload) for upload in uploads)
    created.extend(f"{link.title} — {link.url}" for link in links)

    # Everything the application is told, in one transaction. The two lists and the timeline
    # entry naming what went with them are one act: a timeline saying documents were sent,
    # beside an application nothing is attached to, is a record of nothing. Nothing slow is
    # inside it -- the rendering is done, and already filed.
    if created:
        with transaction.atomic():
            if uploads:
                application.sent_uploads.add(*uploads)
            if links:
                application.sent_links.add(*links)
            record_event(
                application,
                summary=str(_("Documents sent")),
                body="\n".join(created),
            )

    return {
        "message": str(_("Recorded what you sent.")),
        "url": application.get_absolute_url(),
    }


def _one(model_name: str, pk, owner):
    """One of this person's documents by id, or nothing. Re-scoped in the worker.

    The ids were the person's when the form was posted; they are read again here because the
    worker is a different process at a later moment, and *whose is this* is a question Postulo
    answers at the point of use rather than once at the door.
    """
    from django.apps import apps

    if not pk:
        return None
    model = apps.get_model("documents", model_name)
    return model.objects.filter(pk=pk, owner=owner).first()
