"""Views for CVs, cover letters, uploads and sent documents."""

from __future__ import annotations

from django.contrib import messages
from django.http import HttpRequest, HttpResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse_lazy
from django.utils.translation import gettext_lazy as _
from django.views import View
from django.views.generic import CreateView, DeleteView, DetailView, ListView, UpdateView

from postulo.applications.models import Application
from postulo.applications.services import record_event
from postulo.core.files import serve_private_file
from postulo.core.mixins import OwnedObjectMixin, OwnerFormMixin
from postulo.core.redirects import safe_next
from postulo.jobs.views import UserFormKwargsMixin

from .forms import (
    AddCVItemsForm,
    CoverLetterForm,
    CVForm,
    CVItemForm,
    SendDocumentsForm,
    UploadedDocumentForm,
)
from .models import CV, CoverLetter, CVItem, RenderedDocument, UploadedDocument
from .pdf import PDFBackendUnavailable
from .rendering import render_cv_html, render_letter_html, snapshot_cv, snapshot_letter


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
        return context


class CVCreateView(OwnedObjectMixin, UserFormKwargsMixin, OwnerFormMixin, CreateView):
    model = CV
    form_class = CVForm
    template_name = "documents/cv_form.html"

    def form_valid(self, form):
        messages.success(self.request, _("CV created. Now choose what goes on it."))
        return super().form_valid(form)


class CVUpdateView(OwnedObjectMixin, UserFormKwargsMixin, UpdateView):
    model = CV
    form_class = CVForm
    template_name = "documents/cv_form.html"


class CVDeleteView(OwnedObjectMixin, DeleteView):
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

        highest = cv.items.count()
        added = 0
        for content_type, object_id in form.selected():
            CVItem.objects.get_or_create(
                cv=cv,
                content_type=content_type,
                object_id=object_id,
                defaults={"owner": request.user, "order": highest + added},
            )
            added += 1

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


class CVItemDeleteView(OwnedObjectMixin, DeleteView):
    model = CVItem
    template_name = "partials/confirm_delete.html"

    def get_success_url(self) -> str:
        return self.object.cv.get_absolute_url()


class CVItemMoveView(OwnedObjectMixin, View):
    def get_queryset(self):
        return CVItem.objects.for_user(self.request.user)

    def post(self, request: HttpRequest, pk: int, direction: str) -> HttpResponse:
        item = get_object_or_404(self.get_queryset(), pk=pk)
        item.order = max(0, item.order + (-1 if direction == "up" else 1))
        item.save(update_fields=["order", "updated_at"])
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
    model = CoverLetter
    template_name = "documents/letter_list.html"
    context_object_name = "letters"


class CoverLetterDetailView(OwnedObjectMixin, DetailView):
    model = CoverLetter
    template_name = "documents/letter_detail.html"
    context_object_name = "letter"

    def get_context_data(self, **kwargs) -> dict:
        context = super().get_context_data(**kwargs)
        context["placeholders"] = CoverLetter.PLACEHOLDERS
        context["renders"] = self.object.renders.all()[:10]
        return context


class CoverLetterCreateView(OwnedObjectMixin, UserFormKwargsMixin, OwnerFormMixin, CreateView):
    model = CoverLetter
    form_class = CoverLetterForm
    template_name = "documents/letter_form.html"


class CoverLetterUpdateView(OwnedObjectMixin, UserFormKwargsMixin, UpdateView):
    model = CoverLetter
    form_class = CoverLetterForm
    template_name = "documents/letter_form.html"


class CoverLetterDeleteView(OwnedObjectMixin, DeleteView):
    model = CoverLetter
    template_name = "partials/confirm_delete.html"
    success_url = reverse_lazy("documents:letter_list")


class CoverLetterPreviewView(OwnedObjectMixin, View):
    """Preview a letter, optionally as it would read for one application."""

    def get_queryset(self):
        return CoverLetter.objects.for_user(self.request.user)

    def get(self, request: HttpRequest, pk: int) -> HttpResponse:
        letter = get_object_or_404(self.get_queryset(), pk=pk)
        application = None
        application_id = request.GET.get("application")
        if application_id:
            application = (
                Application.objects.for_user(request.user).filter(pk=application_id).first()
            )
        return HttpResponse(render_letter_html(letter, application))


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


class UploadDeleteView(OwnedObjectMixin, DeleteView):
    model = UploadedDocument
    template_name = "partials/confirm_delete.html"
    success_url = reverse_lazy("documents:upload_list")


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
            request, document.file, download_name=f"{document.title}.pdf", as_attachment=True
        )


class RenderedDownloadView(OwnedObjectMixin, View):
    """Hand over a snapshot: the document exactly as it was sent."""

    def get_queryset(self):
        return RenderedDocument.objects.for_user(self.request.user)

    def get(self, request: HttpRequest, pk: int) -> HttpResponse:
        document = get_object_or_404(self.get_queryset(), pk=pk)
        return serve_private_file(request, document.file, download_name=f"{document.title}.pdf")


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
            .select_related("application", "application__posting", "cv", "cover_letter")
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
        context["has_stores"] = store_connections(self.request.user).exists()
        return context
