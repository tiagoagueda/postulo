"""Views for CVs, cover letters, uploads and sent documents."""

from __future__ import annotations

import logging
from pathlib import Path

from django.conf import settings
from django.contrib import messages
from django.db import models, transaction
from django.http import Http404, HttpRequest, HttpResponse
from django.middleware.csp import get_nonce
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse_lazy
from django.utils.csp import CSP
from django.utils.decorators import method_decorator
from django.utils.http import content_disposition_header
from django.utils.translation import gettext_lazy as _
from django.utils.translation import ngettext
from django.views import View
from django.views.decorators.clickjacking import xframe_options_sameorigin
from django.views.decorators.csp import csp_override
from django.views.generic import CreateView, DeleteView, DetailView, ListView, UpdateView

from postulo.applications.models import Application
from postulo.core.files import FILE_POLICY, serve_private_file
from postulo.core.mixins import ConfirmDeleteMixin, OwnedObjectMixin, OwnerFormMixin
from postulo.core.params import as_pk
from postulo.core.redirects import safe_next
from postulo.jobs.views import UserFormKwargsMixin
from postulo.resume import ordering, translating

from . import comparing, formats, kinds, printing, rendering, themes
from . import pdf as renderers
from .forms import (
    AddCVItemsForm,
    CoverLetterForm,
    CVForm,
    CVItemForm,
    FilePropertiesForm,
    SendDocumentsForm,
    UploadedDocumentForm,
)
from .models import (
    CV,
    CoverLetter,
    CVItem,
    LetterKind,
    RenderedDocument,
    UploadedDocument,
    with_entries,
)
from .pdf import PDFBackendUnavailable
from .properties import Properties
from .rendering import (
    render_cv_html,
    render_letter_html,
)

logger = logging.getLogger(__name__)

#: How many of a document's versions its own page lists.
VERSIONS_SHOWN = 10


def handed_over(content: bytes, *, content_type: str, name: str) -> HttpResponse:
    """A file made for this request: handed to whoever asked, and kept nowhere (#236).

    What `serve_private_file` says of a file on disk, said of one that never reaches it,
    for the same reasons. This is somebody's CV, so it has no business in a shared cache;
    a browser is told what it is rather than left to guess; and the policy a private file
    carries goes with it, so that a format somebody else wrote cannot be a document that
    runs anything if it is ever opened where it was served from (#264).

    The name is written by Django, which escapes what a quoted name cannot hold and spells
    a name with an accent or a dash in it the way RFC 6266 asks -- and the title of a
    document always has the dash.
    """
    response = HttpResponse(content, content_type=content_type)
    response["Content-Disposition"] = content_disposition_header(True, name)
    response["Cache-Control"] = "private, max-age=0, no-store"
    response["X-Content-Type-Options"] = "nosniff"
    response["Content-Security-Policy"] = FILE_POLICY
    return response


def chosen_properties(request: HttpRequest) -> Properties:
    """What a file is to say about itself, as the person chose it (#480).

    From the form on the page, which posts every button's choice to its own address, or from
    a link's query string. Neither says anything on a link from before this existed, and
    then it is what it always was.
    """
    return Properties.from_data(request.POST if request.method == "POST" else request.GET)


def written_file(request: HttpRequest, document, outline, key: str):
    """A document in one of the registry's formats, handed over, or a sentence about why not.

    The one place a CV and a letter are written out: the record is already the person's, the
    format is looked up, and a format that fails -- it may be a plugin's -- is a message on
    the document's own page rather than a traceback about it (#236).
    """
    chosen = formats.get(key)
    if chosen is None:
        raise Http404("No such format.")
    try:
        content = chosen.write(outline)
    except Exception:
        logger.exception(
            "Format %r could not write %s %s", chosen.key, type(document).__name__, document.pk
        )
        messages.error(
            request,
            _("That file could not be written. The server log has the details."),
        )
        return redirect(document.get_absolute_url())
    return handed_over(
        content,
        content_type=chosen.content_type,
        name=f"{outline.title}.{chosen.extension}",
    )


def newest_versions(renders, *, shown: int = VERSIONS_SHOWN) -> list:
    """The newest PDFs made from one document, each knowing whether one came before it.

    One more than is shown is fetched, which is what answers the question for the last
    row without a query a row: a version has an earlier one exactly when it is not the
    oldest, and only the tenth of ten cannot tell from the list it is in.
    """
    newest = list(renders.select_related("application__posting")[: shown + 1])
    for place, version in enumerate(newest):
        version.has_earlier = place + 1 < len(newest)
    return newest[:shown]


class PDFErrorMixin:
    """Turn a missing PDF backend into a message rather than a stack trace.

    Postulo is usable without PDF export, so failing to have a renderer installed is an
    inconvenience to explain, not an error to crash on.
    """

    def handle_pdf_error(self, request: HttpRequest, error: PDFBackendUnavailable) -> None:
        messages.error(request, str(error))


class DraftMixin(PDFErrorMixin):
    """A document as the PDF it would be, handed back and filed nowhere (#236).

    The preview is HTML, and HTML cannot show where a page ends. Until this the only way
    to find out was *Export PDF*, and every press of that is a record: a row under
    *Versions you have sent*, a line in *Sent documents*, and a copy queued for every store
    somebody had connected -- for a look at a page break.

    **A GET, because it changes nothing**, and served from the request for the reason the
    report's draft is (`applications/slow.py`): the answer has to be a file, and the cache
    that spares a second render is in this process. `pdf.draft_pdf` is that cache -- the
    same address asked for twice is drawn once, which is what a browser reloading a
    download does.

    **Outside a transaction of its own (#220)**, like every view that draws: a render is
    seconds, and on SQLite a request's transaction holds the write lock for all of them.
    """

    def draft(self, request: HttpRequest, document, html) -> HttpResponse:
        """``html`` is called rather than passed, so that a theme which cannot set this
        document is refused in the same sentence as a renderer that is not there."""
        try:
            content = renderers.draft_pdf(html())
        except (PDFBackendUnavailable, themes.CannotRender) as refusal:
            messages.error(request, str(refusal))
            return redirect(document.get_absolute_url())
        return handed_over(
            content,
            content_type="application/pdf",
            name=f"{rendering.draft_name(document)}.pdf",
        )


# --------------------------------------------------------------------------- CVs


class CVListView(OwnedObjectMixin, ListView):
    model = CV
    template_name = "documents/cv_list.html"
    context_object_name = "cvs"

    def get_queryset(self):
        # `owner__profile` because a card asks each CV what language it is in, and a blank
        # field means "follow your profile" -- without the join that is two queries a row
        # rather than one for the page (#280).
        return super().get_queryset().select_related("owner__profile").prefetch_related("items")


class CVDetailView(OwnedObjectMixin, DetailView):
    model = CV
    template_name = "documents/cv_detail.html"
    context_object_name = "cv"

    def get_context_data(self, **kwargs) -> dict:
        context = super().get_context_data(**kwargs)
        context["items"] = with_entries(self.object.items.all()).order_by("order", "pk")
        context["add_form"] = AddCVItemsForm(cv=self.object)
        context["renders"] = newest_versions(self.object.renders)
        # What it can leave as beside the PDF: the registry's list, so a format a plugin
        # brings is on the page without the page being edited (#236).
        context["formats"] = formats.all_formats()
        # What the file will say about itself, before anybody exports it (#480).
        context["properties_form"] = FilePropertiesForm(
            initial=rendering.file_defaults(self.object)
        )
        # Which entries will print their original text, said here rather than discovered in
        # the PDF an employer already has (#131).
        context["fell_back"] = translating.fallen_back(self.object)
        # And which skills print the ESCO classification's name for them rather than one the
        # person wrote, said in the same place for the same reason (#266).
        context["classified"] = translating.named_by_classification(self.object)
        context["classified_language"] = rendering.document_language(self.object)
        # The same answer the card and the header draw, asked once of the document
        # itself rather than computed a second way here (#280).
        context["document_language"] = self.object.language_name
        # A detail this CV had chosen that is no longer in the profile: it prints none of
        # that kind, and that is said beside the preview, before the export (#308). Only
        # while the contact block is printed at all.
        context["gone"] = printing.gone(self.object) if self.object.show_contact_details else ()
        return context


class CVCreateView(OwnedObjectMixin, UserFormKwargsMixin, OwnerFormMixin, CreateView):
    model = CV
    form_class = CVForm
    template_name = "documents/cv_form.html"

    def form_valid(self, form):
        messages.success(
            self.request,
            _("%(kind)s created. Now choose what goes on it.")
            % {"kind": form.instance.get_kind_display()},
        )
        return super().form_valid(form)


class CVUpdateView(OwnedObjectMixin, UserFormKwargsMixin, UpdateView):
    model = CV
    form_class = CVForm
    template_name = "documents/cv_form.html"


class CVDeleteView(ConfirmDeleteMixin, OwnedObjectMixin, DeleteView):
    model = CV
    template_name = "partials/confirm_delete.html"
    success_url = reverse_lazy("documents:cv_list")


class CVAddItemsView(OwnedObjectMixin, View):
    """Put career entries onto a CV."""

    def get_queryset(self):
        return CV.objects.for_user(self.request.user)

    def post(self, request: HttpRequest, pk: int) -> HttpResponse:
        cv = get_object_or_404(self.get_queryset(), pk=pk)
        form = AddCVItemsForm(request.POST, cv=cv)
        if not form.is_valid():
            messages.error(request, _("Nothing was added."))
            return redirect(cv.get_absolute_url())

        # The number after the last one, not the *count*: removing an entry leaves the
        # numbers above it where they were, so counting produced a number something on the
        # CV already had and the new entry landed in the middle of the page (#235). The
        # whole list is renumbered densely afterwards, so what is stored is what is shown.
        highest = cv.items.aggregate(models.Max("order"))["order__max"]
        start = 0 if highest is None else highest + 1
        added = 0
        for content_type, object_id in form.selected():
            CVItem.objects.get_or_create(
                cv=cv,
                content_type=content_type,
                object_id=object_id,
                defaults={"owner": request.user, "order": start + added},
            )
            added += 1
        ordering.renumber(list(cv.items.order_by("order", "pk")))

        messages.success(
            request,
            ngettext("Added %(count)s entry.", "Added %(count)s entries.", added) % {"count": added}
            if added
            else _("Nothing was added."),
        )
        return redirect(cv.get_absolute_url())


class CVItemUpdateView(OwnedObjectMixin, UserFormKwargsMixin, UpdateView):
    model = CVItem
    form_class = CVItemForm
    template_name = "documents/cv_item_form.html"

    def get_success_url(self) -> str:
        return self.object.cv.get_absolute_url()


class CVItemDeleteView(ConfirmDeleteMixin, OwnedObjectMixin, DeleteView):
    model = CVItem
    template_name = "partials/confirm_delete.html"

    def get_success_url(self) -> str:
        return self.object.cv.get_absolute_url()


class CVItemMoveView(OwnedObjectMixin, View):
    """Move an entry past its neighbour on this CV, up or down.

    The same fix as #203, which this row of arrows was left out of: nudging the number by one
    left *up* at the top doing nothing, let one *down* jump past every entry that shared a
    number, and needed two presses -- the first of them invisible -- to pass an entry two
    numbers away. A swap with the neighbour the page actually drew, and a dense renumbering
    afterwards, is what makes a press do exactly what it looks like it does (#235).

    The neighbours are *this CV's* entries, hidden ones included, because that is the list
    on the page; `ordering.siblings` would have answered with every `CVItem` the person owns
    across every CV.
    """

    def get_queryset(self):
        return CVItem.objects.for_user(self.request.user)

    def post(self, request: HttpRequest, pk: int, direction: str) -> HttpResponse:
        item = get_object_or_404(self.get_queryset(), pk=pk)
        ordering.move(item, direction, among=item.cv.items.order_by("order", "pk"))
        return redirect(item.cv.get_absolute_url())


def _nonce(request: HttpRequest) -> str | None:
    """The request's content-security-policy nonce, generated.

    Django's nonce is lazy and reaches the header only once something has read it, so a
    template that asks `{% if csp_nonce %}` before using it would never see one. Reading it
    here is what puts the same value in the header and on the `<style>` element (#232).
    """
    nonce = get_nonce(request)
    return str(nonce) if nonce is not None else None


#: The one policy under which a preview may be framed by its own editing page (#293).
#:
#: A framed document is refused by *its own* headers, not by the page framing it: the
#: instance-wide policy says `frame-ancestors 'none'` and `X-Frame-Options: DENY`, which is
#: right for every page a person operates and wrong for the two documents that exist to be
#: looked at from beside their controls. The override is the instance policy with that one
#: directive turned to `'self'`, so the nonce the preview's `<style>` needs is still in it;
#: the `X-Frame-Options` decorator below says the same thing in the older header for the
#: browsers that still read it. Both are on the preview views alone.
FRAMEABLE_BY_OURSELVES = {**settings.SECURE_CSP, "frame-ancestors": [CSP.SELF]}


@method_decorator(csp_override(FRAMEABLE_BY_OURSELVES), name="dispatch")
@method_decorator(xframe_options_sameorigin, name="dispatch")
class CVPreviewView(OwnedObjectMixin, View):
    """The CV as HTML, exactly as the PDF renderer will see it.

    Framed on the CV's own page since #293, and still a page of its own: the frame fetches
    this address, so the response, its policy header and the nonce on its `<style>` stay
    together. A `srcdoc` frame would inherit the parent's policy and render unstyled, which
    is the bug #232 fixed.
    """

    def get_queryset(self):
        return CV.objects.for_user(self.request.user)

    def get(self, request: HttpRequest, pk: int) -> HttpResponse:
        cv = get_object_or_404(self.get_queryset(), pk=pk)
        return HttpResponse(render_cv_html(cv, nonce=_nonce(request)))


@method_decorator(transaction.non_atomic_requests, name="dispatch")
class CVExportView(OwnedObjectMixin, PDFErrorMixin, View):
    """Render a CV to PDF and keep the result as a snapshot.

    **Outside a transaction of its own (#220).** Rendering is seconds of WeasyPrint or a
    whole Chromium, and on SQLite a request's transaction takes the write lock before the
    view body runs and keeps it until the response — so one CV on a small machine stalled
    every other worker, the scheduler and `db_worker` with it. The only write here is the
    snapshot row, and `rendering` wraps that in a transaction lasting a statement.
    """

    def get_queryset(self):
        return CV.objects.for_user(self.request.user)

    def post(self, request: HttpRequest, pk: int) -> HttpResponse:
        from postulo.core import errands

        cv = get_object_or_404(self.get_queryset(), pk=pk)
        # Sent off rather than waited for: on the Chromium backend this is a browser launch
        # and then a render, and it was holding a request open for all of it (#247).
        errand = errands.send(
            "cv_pdf",
            request.user,
            subject=cv,
            cv_id=cv.pk,
            properties=chosen_properties(request).as_data(),
        )
        return redirect("core:errand", pk=errand.pk)


@method_decorator(transaction.non_atomic_requests, name="dispatch")
class CVDraftView(OwnedObjectMixin, DraftMixin, View):
    """*Download draft PDF*: the CV as it would print, and no record of having looked."""

    def get_queryset(self):
        return CV.objects.for_user(self.request.user)

    def get(self, request: HttpRequest, pk: int) -> HttpResponse:
        cv = get_object_or_404(self.get_queryset(), pk=pk)
        chosen = chosen_properties(request)
        return self.draft(request, cv, lambda: render_cv_html(cv, properties=chosen))

    #: The page's form posts, so that what it chose is not in an address (#480).
    post = get


class CVTextView(OwnedObjectMixin, View):
    """*Copy as plain text*, as a page: the words of a CV in a box to copy them from.

    A page and not a button, because copying needs a script and this has to work without
    one. What the page holds is what `Download .txt` writes and what a snapshot keeps
    beside its PDF -- one text, built once (`rendering.cv_text`), so what somebody pastes
    into a portal is what the comparison will later say they sent (#236).
    """

    template_name = "documents/cv_text.html"

    def get_queryset(self):
        return CV.objects.for_user(self.request.user)

    def get(self, request: HttpRequest, pk: int) -> HttpResponse:
        cv = get_object_or_404(self.get_queryset(), pk=pk)
        outline = rendering.cv_outline(cv)
        return render(
            request,
            self.template_name,
            {
                "cv": cv,
                "text": formats.as_text(outline),
                # The document's, not the reader's: the box holds the CV's words, and a
                # screen reader should say them in the language they are in.
                "language": outline.language,
                "direction": outline.direction,
                "text_format": formats.get("txt"),
            },
        )


class CVDownloadView(OwnedObjectMixin, View):
    """A CV in one of the formats the registry holds: `.txt`, `.odt`, `.docx`, or a plugin's.

    Written for the request and kept nowhere. This is not a version somebody sent -- a
    Word file is for a portal to read and a person to restyle, and what reached the
    employer is whatever they did with it next -- so nothing is filed and no store is told.

    Not sent off as an errand, as the PDF is: there is no renderer here, only a list of
    paragraphs written into a string or a zip, and it is done before a page watching it
    could have been drawn.
    """

    def get_queryset(self):
        return CV.objects.for_user(self.request.user)

    def get(self, request: HttpRequest, pk: int, format: str) -> HttpResponse:
        # The record first, so that somebody else's CV is a 404 whatever was asked of it.
        cv = get_object_or_404(self.get_queryset(), pk=pk)
        chosen = formats.get(format)
        if chosen is None:
            raise Http404("No such format.")
        # A format may be a plugin's, and its failing is not a reason to show somebody a
        # traceback about their own CV: `written_file` says it in a sentence.
        return written_file(
            request, cv, rendering.cv_outline(cv, chosen_properties(request)), format
        )

    #: The page's form posts what it chose (#480).
    post = get


# ------------------------------------------------------------------ cover letters


class CoverLetterListView(OwnedObjectMixin, ListView):
    """Every letter, of every kind, with a filter for when there are several kinds."""

    model = CoverLetter
    template_name = "documents/letter_list.html"
    context_object_name = "letters"

    def get_queryset(self):
        # `owner__profile` for the language each row shows; see CVListView (#280).
        queryset = super().get_queryset().select_related("owner__profile")
        kind = self.request.GET.get("kind", "")
        if kind in LetterKind.values:
            queryset = queryset.filter(kind=kind)
        return queryset

    def get_context_data(self, **kwargs) -> dict:
        context = super().get_context_data(**kwargs)
        context["kinds"] = LetterKind.choices
        context["current_kind"] = self.request.GET.get("kind", "")
        return context


class CoverLetterDetailView(OwnedObjectMixin, DetailView):
    model = CoverLetter
    template_name = "documents/letter_detail.html"
    context_object_name = "letter"

    def get_context_data(self, **kwargs) -> dict:
        context = super().get_context_data(**kwargs)
        context["placeholders"] = CoverLetter.PLACEHOLDERS
        context["renders"] = newest_versions(self.object.renders)
        # The preview was reachable only without an application, which is the one version of
        # a letter nobody sends: every placeholder was blank in it. Choosing one here is how
        # you read the letter the way the employer will (#235).
        context["applications"] = (
            Application.objects.for_user(self.request.user)
            .select_related("posting", "posting__company")
            .order_by("-created_at")[:50]
        )
        # What the file will say about itself, and the formats it can leave as (#480).
        context["formats"] = formats.all_formats()
        context["properties_form"] = FilePropertiesForm(
            initial=rendering.file_defaults(self.object)
        )
        return context


class CoverLetterCreateView(OwnedObjectMixin, UserFormKwargsMixin, OwnerFormMixin, CreateView):
    model = CoverLetter
    form_class = CoverLetterForm
    template_name = "documents/letter_form.html"

    def get_initial(self) -> dict:
        """A kind chosen on the way in decides the starter text and the theme."""
        initial = super().get_initial()
        kind = self.request.GET.get("kind", "")
        if kind in LetterKind.values:
            initial["kind"] = kind
        return initial

    def get_context_data(self, **kwargs) -> dict:
        context = super().get_context_data(**kwargs)
        context["kinds"] = LetterKind.choices
        return context


class CoverLetterUpdateView(OwnedObjectMixin, UserFormKwargsMixin, UpdateView):
    model = CoverLetter
    form_class = CoverLetterForm
    template_name = "documents/letter_form.html"


class CoverLetterDeleteView(ConfirmDeleteMixin, OwnedObjectMixin, DeleteView):
    model = CoverLetter
    template_name = "partials/confirm_delete.html"
    success_url = reverse_lazy("documents:letter_list")


@method_decorator(csp_override(FRAMEABLE_BY_OURSELVES), name="dispatch")
@method_decorator(xframe_options_sameorigin, name="dispatch")
class CoverLetterPreviewView(OwnedObjectMixin, View):
    """Preview a letter, optionally as it would read for one application.

    Framed on the letter's own page as the CV preview is (#293); the application chooser
    beside the frame points the frame at the version for that application.

    The placeholders are marked here and nowhere else: this is the page somebody reads
    *before* deciding, so a gap should look like a gap rather than like a sentence with a
    word missing (#235).
    """

    def get_queryset(self):
        return CoverLetter.objects.for_user(self.request.user)

    def get(self, request: HttpRequest, pk: int) -> HttpResponse:
        letter = get_object_or_404(self.get_queryset(), pk=pk)
        application = chosen_application(request)
        return HttpResponse(
            render_letter_html(letter, application, mark_empty=True, nonce=_nonce(request))
        )


def chosen_application(request: HttpRequest):
    """The application a letter is being read against, or none: the requester's own.

    A query parameter is typed by whoever sends the link. `abc` reached `filter(pk=…)` and
    came back out as a 500; what it means is "no application", which is what the page
    already draws when none is chosen (#235). Somebody else's application is no
    application either, and fills in nothing.
    """
    # The page's form posts what it chose, and a link carries it in the address (#480).
    application_id = as_pk(
        (request.POST if request.method == "POST" else request.GET).get("application")
    )
    if application_id is None:
        return None
    return (
        Application.objects.for_user(request.user)
        .select_related("posting", "posting__company", "contact")
        .filter(pk=application_id)
        .first()
    )


@method_decorator(transaction.non_atomic_requests, name="dispatch")
class CoverLetterDraftView(OwnedObjectMixin, DraftMixin, View):
    """*Download draft PDF* for a letter, as it would read for one application or for none.

    The preview's words, on the PDF's pages: the placeholders are filled from the
    application that was chosen, and one with nothing behind it is marked, as it is in the
    preview and for the same reason -- this is something somebody reads *before* deciding,
    and a gap should look like a gap (#235). What is frozen with an application is the
    letter without the marks, and a draft is never that: nothing here is filed (#236).
    """

    def get_queryset(self):
        return CoverLetter.objects.for_user(self.request.user)

    def get(self, request: HttpRequest, pk: int) -> HttpResponse:
        letter = get_object_or_404(self.get_queryset(), pk=pk)
        application = chosen_application(request)
        chosen = chosen_properties(request)
        return self.draft(
            request,
            letter,
            lambda: render_letter_html(letter, application, mark_empty=True, properties=chosen),
        )

    #: The page's form posts what it chose (#480).
    post = get


@method_decorator(transaction.non_atomic_requests, name="dispatch")
class CoverLetterDownloadView(OwnedObjectMixin, View):
    """A letter in one of the formats the registry holds, as it would read for an application.

    The counterpart of `CVDownloadView` (#480): written for the request and kept nowhere, with
    the placeholders filled in from the application that was chosen and nothing marked, and
    with whatever the person chose to leave out or edit in the file's properties.
    """

    def get_queryset(self):
        return CoverLetter.objects.for_user(self.request.user)

    def get(self, request: HttpRequest, pk: int, format: str) -> HttpResponse:
        letter = get_object_or_404(self.get_queryset(), pk=pk)
        if formats.get(format) is None:
            raise Http404("No such format.")
        outline = rendering.letter_outline(
            letter, chosen_application(request), chosen_properties(request)
        )
        return written_file(request, letter, outline, format)

    post = get


# ----------------------------------------------------------------------- uploads


class CopiesContextMixin:
    """Give a list page each document's copies and whether *Send now* makes sense."""

    def get_context_data(self, **kwargs):
        from .archiving import attach_copies, store_connections

        context = super().get_context_data(**kwargs)
        documents = list(context.get(self.context_object_name) or [])
        attach_copies(documents)
        context[self.context_object_name] = documents
        context["has_stores"] = store_connections(self.request.user).exists()
        return context


class UploadListView(CopiesContextMixin, OwnedObjectMixin, ListView):
    model = UploadedDocument
    template_name = "documents/upload_list.html"
    context_object_name = "documents"

    def get_queryset(self):
        queryset = super().get_queryset().prefetch_related("replaced_by")
        # An unknown kind is ignored, as the letters list ignores one (#667).
        if self.request.GET.get("kind", "") in dict(kinds.choices()):
            queryset = queryset.filter(kind=self.request.GET["kind"])
        return queryset

    def get_context_data(self, **kwargs) -> dict:
        context = super().get_context_data(**kwargs)
        # From the registry, so a kind a plugin brings is offered; only those somebody has a
        # file of, so the row is not a list of empty answers.
        held = set(
            UploadedDocument.objects.filter(owner=self.request.user).values_list("kind", flat=True)
        )
        current = self.request.GET.get("kind", "")
        current = current if current in dict(kinds.choices()) else ""
        context["kinds"] = [
            (key, label) for key, label in kinds.choices() if key in held or key == current
        ]
        context["current_kind"] = current
        return context


class UploadCreateView(OwnedObjectMixin, UserFormKwargsMixin, OwnerFormMixin, CreateView):
    model = UploadedDocument
    form_class = UploadedDocumentForm
    template_name = "documents/upload_form.html"
    success_url = reverse_lazy("documents:upload_list")

    def form_valid(self, form):
        messages.success(self.request, _("File saved."))
        return super().form_valid(form)


class UploadUpdateView(OwnedObjectMixin, UserFormKwargsMixin, UpdateView):
    model = UploadedDocument
    form_class = UploadedDocumentForm
    template_name = "documents/upload_form.html"
    success_url = reverse_lazy("documents:upload_list")


class UploadDeleteView(ConfirmDeleteMixin, OwnedObjectMixin, DeleteView):
    model = UploadedDocument
    template_name = "partials/confirm_delete.html"
    success_url = reverse_lazy("documents:upload_list")

    def get_context_data(self, **kwargs):
        """Warn when applications say this file was sent with them (#217).

        Deleting it removes it from those applications without a word, which makes the record
        of what was sent quietly untrue. The file goes from disk as well now, so this is the
        last chance to say so.
        """
        from django.utils.translation import ngettext

        context = super().get_context_data(**kwargs)
        number = self.object.applications.count()
        if number:
            context["consequences"] = [
                ngettext(
                    "It is recorded as sent with %(count)d application, which will no longer "
                    "say it was sent.",
                    "It is recorded as sent with %(count)d applications, which will no longer "
                    "say it was sent.",
                    number,
                )
                % {"count": number}
            ]
        return context


class UploadDownloadView(OwnedObjectMixin, View):
    """Hand over an uploaded file, once ownership is established.

    The queryset is narrowed to the requester first, so a file belonging to someone else
    is simply not found. Media is never served by the web server directly.
    """

    def get_queryset(self):
        return UploadedDocument.objects.for_user(self.request.user)

    def get(self, request: HttpRequest, pk: int) -> HttpResponse:
        document = get_object_or_404(self.get_queryset(), pk=pk)
        return serve_private_file(
            request, document.file, download_name=document.download_name, as_attachment=True
        )


class RenderedDownloadView(OwnedObjectMixin, View):
    """Hand over a snapshot: the document exactly as it was sent."""

    def get_queryset(self):
        return RenderedDocument.objects.for_user(self.request.user)

    def get(self, request: HttpRequest, pk: int) -> HttpResponse:
        document = get_object_or_404(self.get_queryset(), pk=pk)
        return serve_private_file(request, document.file, download_name=document.download_name)


@method_decorator(transaction.non_atomic_requests, name="dispatch")
class SendCopiesNowView(OwnedObjectMixin, View):
    """*Send now*: try every store this document is still missing from, at once.

    The one place a store is called inside a request. It is what the person asked for,
    with the button in front of them, and the outcome is told to them in a sentence.

    Out of the request's transaction (#357): `send_now` schedules in a block of its own, and
    each copy is claimed and recorded by statements that commit themselves, as the
    scheduler's pass does.
    """

    models = {"upload": UploadedDocument, "render": RenderedDocument}

    def get_queryset(self):
        model = self.models[self.kwargs["origin"]]
        return model.objects.for_user(self.request.user)

    def post(self, request: HttpRequest, origin: str, pk: int) -> HttpResponse:
        from .archiving import send_now, store_connections

        document = get_object_or_404(self.get_queryset(), pk=pk)
        fallback = reverse_lazy(
            "documents:upload_list" if origin == "upload" else "documents:rendered_list"
        )
        if not store_connections(request.user).exists():
            messages.info(
                request,
                _("No document store is connected. Add one under Settings → Connections."),
            )
            return redirect(safe_next(request, str(fallback)))
        if not document.goes_to_stores:
            # The page offers no button for one of these, so this is a form kept open from
            # before, or an address typed by hand. "Every store already has this document"
            # is what `send_now` would lead to, and it would be untrue (#236).
            messages.info(
                request,
                _("Kept here only: a store is sent what went with an application."),
            )
            return redirect(safe_next(request, str(fallback)))
        sent, failed = send_now(document)
        if failed and not sent:
            messages.error(request, _("The copy could not be sent; the document says why."))
        elif failed:
            messages.warning(
                request,
                _("%(sent)s, %(failed)s; each document says which.")
                % {
                    "sent": ngettext("%(count)d copy sent", "%(count)d copies sent", sent)
                    % {"count": sent},
                    "failed": ngettext("%(count)d failed", "%(count)d failed", failed)
                    % {"count": failed},
                },
            )
        elif sent:
            messages.success(
                request,
                ngettext("%(count)d copy sent.", "%(count)d copies sent.", sent) % {"count": sent},
            )
        else:
            messages.info(request, _("Every store already has this document."))
        return redirect(safe_next(request, str(fallback)))


class RenderedCompareView(OwnedObjectMixin, View):
    """*Compare with previous*: what changed between this version and the one before it.

    The earlier one is found, not named: it is the render of the same CV or letter that
    came before this one, among this person's own, so there is no second id in the address
    for somebody to point at a document that is not theirs (#236).

    Three ways of there being nothing to show, and the page says which. Nothing earlier was
    sent from the same document. One of the two was frozen before a CV's words were kept
    beside its PDF, and what is kept of it is markup. Or the words are the same, and what
    changed is the setting.
    """

    template_name = "documents/rendered_compare.html"

    def get_queryset(self):
        return RenderedDocument.objects.for_user(self.request.user).select_related(
            "application__posting", "source_type"
        )

    def get(self, request: HttpRequest, pk: int) -> HttpResponse:
        document = get_object_or_404(self.get_queryset(), pk=pk)
        earlier = document.previous()
        comparison = None
        if earlier is not None and document.text_to_compare and earlier.text_to_compare:
            comparison = comparing.compare(
                earlier.text_to_compare,
                document.text_to_compare,
                # The words of a CV are written by `formats.as_text`, so a line beginning
                # with its bullet is a list item; a letter's are the person's own.
                bullet=formats.BULLET if document.plain_text and earlier.plain_text else "",
            )
        source = document.source
        return render(
            request,
            self.template_name,
            {
                "document": document,
                "earlier": earlier,
                "comparison": comparison,
                "source_url": source.get_absolute_url() if source is not None else "",
            },
        )


class RenderedListView(CopiesContextMixin, OwnedObjectMixin, ListView):
    model = RenderedDocument
    template_name = "documents/rendered_list.html"
    context_object_name = "documents"
    paginate_by = 50

    def get_queryset(self):
        return (
            super()
            .get_queryset()
            # `source` is a generic link with no join to follow, so the content type
            # is what is worth fetching: the label a row shows comes from it (#130).
            .select_related("application", "application__posting", "source_type")
        )


# ------------------------------------------------- sending documents with an application


@method_decorator(transaction.non_atomic_requests, name="dispatch")
class SendDocumentsView(OwnedObjectMixin, PDFErrorMixin, View):
    """Freeze the documents being sent with an application.

    This is the moment the snapshot exists for. Everything chosen here is rendered as it
    stands now and attached to the application, so the record survives every later edit
    to the CV it came from.

    **The slowest button in Postulo, and it no longer holds the database while it runs
    (#220).** Two documents meant two renders inside one request-long transaction — two
    Chromium launches, on the backend where that is what rendering means. One renderer draws
    both now, and the writes are short transactions of their own: each snapshot saves its row
    as it is made, and what the application is told about all of them is one block at the end.
    A render that fails half way therefore keeps the document it managed to file, which is a
    document that genuinely exists, instead of throwing it away with the seconds spent on it.
    """

    template_name = "documents/send.html"

    def get_queryset(self):
        return Application.objects.for_user(self.request.user)

    def get_application(self, pk: int) -> Application:
        return get_object_or_404(self.get_queryset().select_related("posting"), pk=pk)

    def get(self, request: HttpRequest, pk: int) -> HttpResponse:
        application = self.get_application(pk)
        return render(
            request,
            self.template_name,
            {
                "application": application,
                "form": SendDocumentsForm(user=request.user, application=application),
            },
        )

    def post(self, request: HttpRequest, pk: int) -> HttpResponse:
        application = self.get_application(pk)
        form = SendDocumentsForm(request.POST, user=request.user, application=application)
        if not form.is_valid():
            return render(request, self.template_name, {"application": application, "form": form})

        # Freezing a letter used to show nobody the text being frozen, so a placeholder with
        # nothing behind it -- a posting with no location, a name never filled in -- became a
        # gap in a PDF an employer already had. Where there is one, the filled letter is put
        # in front of the person first, with the gaps marked, and the button says so. Where
        # there is not, nothing changes: an extra step everybody has to press through would
        # be read once and clicked past for ever after (#235).
        letter = form.cleaned_data["cover_letter"]
        gaps = rendering.unfilled_placeholders(letter, application) if letter else []
        if gaps and not request.POST.get("confirmed"):
            return render(
                request,
                self.template_name,
                {
                    "application": application,
                    "form": form,
                    "gaps": gaps,
                    "letter_preview": rendering.letter_text(letter, application, mark_empty=True),
                },
            )

        if form.cleaned_data.get("send_email"):
            return self.email_then_freeze(request, application, form)

        # The slowest button in Postulo, and it no longer waits for itself (#247). What
        # gets drawn, what is attached and what the timeline is told are all in the handler,
        # in the order they were in here: one renderer for both documents, each snapshot
        # saved as it is made, and one transaction at the end tying the lot together.
        from postulo.core import errands

        errand = errands.send(
            "sent_documents",
            request.user,
            subject=application,
            application_id=application.pk,
            cv_id=form.cleaned_data["cv"].pk if form.cleaned_data["cv"] else None,
            letter_id=letter.pk if letter else None,
            upload_ids=[upload.pk for upload in form.cleaned_data["uploads"]],
            link_ids=[link.pk for link in form.cleaned_data["links"]],
            properties=Properties(include=form.cleaned_data["with_properties"]).as_data(),
        )
        return redirect("core:errand", pk=errand.pk)

    def email_then_freeze(self, request: HttpRequest, application, form) -> HttpResponse:
        """Mail the chosen documents from the person's own address; freeze only if it went (#361).

        **Sent first.** A snapshot is the record of what somebody handed over, so one filed
        for an email that never left would be a record of nothing. Each PDF is drawn once
        here, mailed, and the very same bytes are what is kept afterwards: what was sent is
        what is on file. Whatever refuses -- no outbox, a sender that is not the outbox's,
        the limit, the server, a lapsed grant -- puts the form back with the server's own
        words and what was typed, and files and records nothing.

        This runs in the request, not as an errand: the answer to *did it go* has to be on
        the page the person is looking at. The view is not atomic (#220), so no write lock is
        held while the renderer and the mail server are waited on.
        """
        import smtplib

        from django.core.mail import EmailMessage
        from django.utils.text import slugify

        from postulo.core import correspondence, destinations, throttle
        from postulo.core.mail import ConnectionFailed
        from postulo.plugins.consent import ConsentFailed

        from .slow import freeze

        data = form.cleaned_data
        cv, letter = data["cv"], data["cover_letter"]
        uploads = list(data["uploads"])
        recipient = data["recipient"]

        def refuse(words) -> HttpResponse:
            form.add_error(None, str(words))
            return render(request, self.template_name, {"application": application, "form": form})

        # Which of the two the files are written as is recorded with them (#480).
        carried = Properties(include=data["with_properties"])
        drawn: dict[str, bytes] = {}
        attachments: list[tuple[str, bytes, str]] = []
        try:
            if cv or letter:
                with renderers.pdf_session() as backend:
                    if cv:
                        drawn["cv"] = renderers.html_to_pdf(
                            rendering.render_cv_html(cv, properties=carried), backend=backend
                        )
                        name = slugify(rendering.file_title(cv, carried)) or "cv"
                        attachments.append((f"{name}.pdf", drawn["cv"], "application/pdf"))
                    if letter:
                        drawn["letter"] = renderers.html_to_pdf(
                            rendering.render_letter_html(letter, application, properties=carried),
                            backend=backend,
                        )
                        name = slugify(rendering.file_title(letter, carried)) or "cover-letter"
                        attachments.append((f"{name}.pdf", drawn["letter"], "application/pdf"))
        except PDFBackendUnavailable as unavailable:
            return refuse(unavailable)
        try:
            for upload in uploads:
                with upload.file.open("rb") as handle:
                    attachments.append((Path(upload.file.name).name, handle.read(), ""))
        except OSError:
            return refuse(_("One of the files you chose could not be read, so nothing was sent."))

        body = data["body"] or (rendering.letter_text(letter, application) if letter else "")
        # No sender: `correspondence.send` puts the outbox's own address on it, and refuses
        # rather than rewrites if a caller ever claims another.
        message = EmailMessage(subject=data["subject"], body=body, to=[recipient])
        for filename, content, mimetype in attachments:
            message.attach(filename, content, mimetype or None)
        try:
            correspondence.send(request.user, message)
        except (correspondence.NoOutbox, correspondence.WrongSender) as refused:
            return refuse(refused)
        except throttle.TooOften as often:
            return refuse(often)
        except (ConnectionFailed, ConsentFailed, destinations.Refused) as failed:
            return refuse(failed)
        except (smtplib.SMTPException, OSError):
            # Never the exception's text: a server's reply can quote the recipient.
            logger.warning("An outbox for account %s failed to send", request.user.pk)
            return refuse(
                _("The mail server did not take the message, so nothing was sent or recorded.")
            )

        freeze(
            application,
            cv=cv,
            letter=letter,
            uploads=uploads,
            links=list(data["links"]),
            drawn=drawn,
            emailed_to=recipient,
            properties=carried,
        )
        messages.success(request, _("Emailed, and recorded what you sent."))
        return redirect(application.get_absolute_url())


class ApplicationDocumentsView(OwnedObjectMixin, DetailView):
    """Everything attached to one application."""

    model = Application
    template_name = "documents/application_documents.html"
    context_object_name = "application"

    def get_context_data(self, **kwargs) -> dict:
        from .archiving import attach_copies, store_connections

        context = super().get_context_data(**kwargs)
        rendered = list(self.object.rendered_documents.all())
        uploads = list(self.object.sent_uploads.all())
        # Whether each has a version before it to be compared with. A query a document,
        # and an application is sent a CV and a letter, not fifty (#236).
        for document in rendered:
            document.has_earlier = document.previous() is not None
        attach_copies([*rendered, *uploads])
        context["rendered"] = rendered
        context["uploads"] = uploads
        context["links"] = list(self.object.sent_links.all())
        context["has_stores"] = store_connections(self.request.user).exists()
        return context
