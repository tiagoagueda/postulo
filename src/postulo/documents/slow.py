"""The slow work this app asks for: drawing PDFs (#247).

On the WeasyPrint backend a render is seconds; on the Chromium one it is a browser launch
and then a render, which is why a container allows a worker two minutes rather than thirty
(#220). Two buttons do it -- *Export as PDF* on a CV, and *Record what you sent* on an
application, which may draw two documents -- and both held a request open for the whole of
it.

**Nothing is retried.** Rendering a CV files a snapshot, and a snapshot is a record of what
somebody handed over; a second attempt made on the person's behalf would file a second one.
A failure here says why, and the button is still there.

**A draft is not here at all** (#236). *Download draft PDF* is a GET that hands the bytes
back and files nothing, for the reason the report's is (`applications/slow.py`): the draft
cache is in the web process, and the answer has to be a file download anyway.

**What a person has to see is already kept.** Whatever these draw is in *Sent documents*
before the errand finishes, so the answer to *where is it if I closed the tab* is the answer
it always was.
"""

from __future__ import annotations

from django.utils.translation import gettext_lazy as _

from postulo.core.errands import Refused, handler


@handler("cv_pdf", working=_("Drawing the PDF"))
def render_a_cv(errand) -> dict:
    """Render one CV and file the snapshot, with the properties it was asked to carry (#480)."""
    from django.urls import reverse

    from .models import CV
    from .pdf import PDFBackendUnavailable
    from .properties import Properties
    from .rendering import snapshot_cv

    cv = CV.objects.filter(pk=errand.payload.get("cv_id"), owner=errand.owner).first()
    if cv is None:
        raise Refused(_("That CV is no longer here."))
    try:
        document = snapshot_cv(
            cv, properties=Properties.from_data(errand.payload.get("properties"))
        )
    except PDFBackendUnavailable as unavailable:
        raise Refused(str(unavailable)) from unavailable
    # The same CV exported twice is one version, and saying "created" of a PDF that was
    # filed last week would be the page telling somebody something that did not happen.
    if getattr(document, "already_filed", False):
        message = _("Nothing on it has changed, so this is the PDF already filed.")
    else:
        message = _("PDF created.")
    return {
        "message": str(message),
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
    from postulo.applications.models import Application
    from postulo.resume.models import Link

    from .models import UploadedDocument
    from .pdf import PDFBackendUnavailable
    from .properties import Properties

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

    try:
        freeze(
            application,
            cv=cv,
            letter=letter,
            uploads=uploads,
            links=links,
            properties=Properties.from_data(errand.payload.get("properties")),
        )
    except PDFBackendUnavailable as unavailable:
        raise Refused(str(unavailable)) from unavailable

    return {
        "message": str(_("Recorded what you sent.")),
        "url": application.get_absolute_url(),
    }


NEWLINE = "\n"


def freeze(
    application,
    *,
    cv,
    letter,
    uploads,
    links,
    drawn: dict | None = None,
    emailed_to: str = "",
    properties=None,
) -> None:
    """File what was sent with an application and tell its timeline.

    The one place both ways of sending end up: the errand above, which draws the PDFs
    itself, and *Email these* (#361), which has already drawn them to attach to the message
    and hands the same bytes over in ``drawn`` (``{"cv": bytes, "letter": bytes}``), so the
    copy that is kept is the copy that was mailed. ``emailed_to`` says to whom, on the
    timeline; it is never logged. ``properties`` is what the files were told to say about
    themselves, which is part of what is recorded (#480).
    """
    from django.db import transaction

    from postulo.applications.models import EventKind
    from postulo.applications.services import record_event

    from .pdf import pdf_session

    drawn = drawn or {}
    created: list[str] = []
    if cv or letter:
        # Opened once, and only where there is something to draw: a *Send* of uploads and
        # links alone should not start a browser. Opening it also settles whether there is
        # a usable backend before the first document is written down. Nothing is opened
        # when every PDF was drawn already.
        needs_drawing = (cv and "cv" not in drawn) or (letter and "letter" not in drawn)
        if needs_drawing:
            with pdf_session() as backend:
                created.extend(_snapshots(application, cv, letter, drawn, backend, properties))
        else:
            created.extend(_snapshots(application, cv, letter, drawn, None, properties))

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
            if emailed_to:
                record_event(
                    application,
                    kind=EventKind.EMAIL_SENT,
                    summary=str(_("Documents emailed")),
                    body=NEWLINE.join(
                        [str(_("Emailed to %(address)s.")) % {"address": emailed_to}, *created]
                    ),
                )
            else:
                record_event(
                    application,
                    summary=str(_("Documents sent")),
                    body=NEWLINE.join(created),
                )


def _snapshots(application, cv, letter, drawn, backend, properties=None) -> list[str]:
    from .rendering import snapshot_cv, snapshot_letter

    titles: list[str] = []
    if cv:
        titles.append(
            snapshot_cv(
                cv,
                application=application,
                backend=backend,
                content=drawn.get("cv"),
                properties=properties,
            ).title
        )
    if letter:
        titles.append(
            snapshot_letter(
                letter,
                application=application,
                backend=backend,
                content=drawn.get("letter"),
                properties=properties,
            ).title
        )
    return titles


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
