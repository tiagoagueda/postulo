"""Views for CVs, cover letters, uploads and sent documents."""

from __future__ import annotations

from django.contrib import messages
from django.db import models
from django.http import HttpRequest, HttpResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse_lazy
from django.utils.translation import gettext_lazy as _
from django.views import View
from django.views.generic import CreateView, DeleteView, DetailView, ListView, UpdateView

from postulo.applications.models import Application
from postulo.applications.services import record_event
from postulo.core import languages
from postulo.core.files import serve_private_file
from postulo.core.mixins import ConfirmDeleteMixin, OwnedObjectMixin, OwnerFormMixin
from postulo.core.redirects import safe_next
from postulo.jobs.views import UserFormKwargsMixin
from postulo.resume import ordering, translating

from . import rendering
from .forms import (
    AddCVItemsForm,
    CoverLetterForm,
    CVForm,
    CVItemForm,
    SendDocumentsForm,
    UploadedDocumentForm,
)
from .models import CV, CoverLetter, CVItem, LetterKind, RenderedDocument, UploadedDocument
from .pdf import PDFBackendUnavailable
from .rendering import (
    document_language,
    render_cv_html,
    render_letter_html,
    snapshot_cv,
    snapshot_letter,
)


def _as_pk(value) -> int | None:
    """A primary key out of something typed into a URL, or nothing at all."""
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


class PDFErrorMixin:
    """Turn a missing PDF backend into a message rather than a stack trace.

    Postulo is usable without PDF export, so failing to have a renderer installed is an
    inconvenience to explain, not an error to crash on.
    """

    def handle_pdf_error(self, request: HttpRequest, error: PDFBackendUnavailable) -> None:
        messages.error(request, str(error))


# --------------------------------------------------------------------------- CVs


class CVListView(OwnedObjectMixin, ListView):
    model = CV
    template_name = "documents/cv_list.html"
    context_object_name = "cvs"

    def get_queryset(self):
        return super().get_queryset().prefetch_related("items")


class CVDetailView(OwnedObjectMixin, DetailView):
    model = CV
    template_name = "documents/cv_detail.html"
    context_object_name = "cv"

    def get_context_data(self, **kwargs) -> dict:
        context = super().get_context_data(**kwargs)
        context["items"] = self.object.items.select_related("content_type").order_by("order", "pk")
        context["add_form"] = AddCVItemsForm(cv=self.object)
        context["renders"] = self.object.renders.all()[:10]
        # Which entries will print their original text, said here rather than discovered in
        # the PDF an employer already has (#131).
        context["fell_back"] = translating.fallen_back(self.object)
        context["document_language"] = languages.NATIVE_NAMES.get(
            translating.normalise(document_language(self.object)), ""
        )
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
            _("Added %(count)s entries.") % {"count": added} if added else _("Nothing was added."),
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


class CVPreviewView(OwnedObjectMixin, View):
    """The CV as HTML, exactly as the PDF renderer will see it."""

    def get_queryset(self):
        return CV.objects.for_user(self.request.user)

    def get(self, request: HttpRequest, pk: int) -> HttpResponse:
        cv = get_object_or_404(self.get_queryset(), pk=pk)
        return HttpResponse(render_cv_html(cv))


class CVExportView(OwnedObjectMixin, PDFErrorMixin, View):
    """Render a CV to PDF and keep the result as a snapshot."""

    def get_queryset(self):
        return CV.objects.for_user(self.request.user)

    def post(self, request: HttpRequest, pk: int) -> HttpResponse:
        cv = get_object_or_404(self.get_queryset(), pk=pk)
        try:
            document = snapshot_cv(cv)
        except PDFBackendUnavailable as error:
            self.handle_pdf_error(request, error)
            return redirect(cv.get_absolute_url())

        messages.success(request, _("PDF created."))
        return redirect("documents:rendered_download", pk=document.pk)


# ------------------------------------------------------------------ cover letters


class CoverLetterListView(OwnedObjectMixin, ListView):
    """Every letter, of every kind, with a filter for when there are several kinds."""

    model = CoverLetter
    template_name = "documents/letter_list.html"
    context_object_name = "letters"

    def get_queryset(self):
        queryset = super().get_queryset()
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
        context["renders"] = self.object.renders.all()[:10]
        # The preview was reachable only without an application, which is the one version of
        # a letter nobody sends: every placeholder was blank in it. Choosing one here is how
        # you read the letter the way the employer will (#235).
        context["applications"] = (
            Application.objects.for_user(self.request.user)
            .select_related("posting", "posting__company")
            .order_by("-created_at")[:50]
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


class CoverLetterPreviewView(OwnedObjectMixin, View):
    """Preview a letter, optionally as it would read for one application.

    The placeholders are marked here and nowhere else: this is the page somebody reads
    *before* deciding, so a gap should look like a gap rather than like a sentence with a
    word missing (#235).
    """

    def get_queryset(self):
        return CoverLetter.objects.for_user(self.request.user)

    def get(self, request: HttpRequest, pk: int) -> HttpResponse:
        letter = get_object_or_404(self.get_queryset(), pk=pk)
        application = None
        # A query parameter is typed by whoever sends the link. `abc` reached `filter(pk=…)`
        # and came back out as a 500; what it means is "no application", which is what the
        # page already draws when none is chosen (#235).
        application_id = _as_pk(request.GET.get("application"))
        if application_id is not None:
            application = (
                Application.objects.for_user(request.user)
                .select_related("posting", "posting__company", "contact")
                .filter(pk=application_id)
                .first()
            )
        return HttpResponse(render_letter_html(letter, application, mark_empty=True))


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
        return super().get_queryset().prefetch_related("replaced_by")


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


class SendCopiesNowView(OwnedObjectMixin, View):
    """*Send now*: try every store this document is still missing from, at once.

    The one place a store is called inside a request. It is what the person asked for,
    with the button in front of them, and the outcome is told to them in a sentence.
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
        sent, failed = send_now(document)
        if failed and not sent:
            messages.error(request, _("The copy could not be sent; the document says why."))
        elif failed:
            messages.warning(
                request,
                _("%(sent)d copies sent, %(failed)d failed; each document says which.")
                % {"sent": sent, "failed": failed},
            )
        elif sent:
            messages.success(
                request,
                _("%(sent)d copies sent.") % {"sent": sent},
            )
        else:
            messages.info(request, _("Every store already has this document."))
        return redirect(safe_next(request, str(fallback)))


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


class SendDocumentsView(OwnedObjectMixin, PDFErrorMixin, View):
    """Freeze the documents being sent with an application.

    This is the moment the snapshot exists for. Everything chosen here is rendered as it
    stands now and attached to the application, so the record survives every later edit
    to the CV it came from.
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
            {"application": application, "form": SendDocumentsForm(user=request.user)},
        )

    def post(self, request: HttpRequest, pk: int) -> HttpResponse:
        application = self.get_application(pk)
        form = SendDocumentsForm(request.POST, user=request.user)
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

        created: list[str] = []
        try:
            if form.cleaned_data["cv"]:
                document = snapshot_cv(form.cleaned_data["cv"], application=application)
                created.append(document.title)
            if form.cleaned_data["cover_letter"]:
                document = snapshot_letter(
                    form.cleaned_data["cover_letter"], application=application
                )
                created.append(document.title)
        except PDFBackendUnavailable as error:
            self.handle_pdf_error(request, error)
            return render(request, self.template_name, {"application": application, "form": form})

        uploads = form.cleaned_data["uploads"]
        if uploads:
            application.sent_uploads.add(*uploads)
            created.extend(str(upload) for upload in uploads)

        links = form.cleaned_data["links"]
        if links:
            application.sent_links.add(*links)
            created.extend(f"{link.title} — {link.url}" for link in links)

        if created:
            record_event(
                application,
                summary=str(_("Documents sent")),
                body="\n".join(created),
            )
            messages.success(request, _("Recorded what you sent."))
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
        attach_copies([*rendered, *uploads])
        context["rendered"] = rendered
        context["uploads"] = uploads
        context["links"] = list(self.object.sent_links.all())
        context["has_stores"] = store_connections(self.request.user).exists()
        return context
