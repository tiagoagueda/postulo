"""What an application was told, and the answers kept for reuse (#1092).

Two sets of pages. An application's own answers are copies, made on its page and edited there.
The saved answers they may begin as are listed and edited under *Your career*
(`resume.views_answers`).
"""

from __future__ import annotations

from django.contrib import messages
from django.contrib.auth.mixins import LoginRequiredMixin
from django.http import HttpRequest, HttpResponse, HttpResponseRedirect
from django.shortcuts import get_object_or_404, render
from django.urls import reverse
from django.utils.translation import gettext as _
from django.views import View
from django.views.generic import DeleteView, UpdateView

from postulo.core.mixins import ConfirmDeleteMixin, OwnedObjectMixin
from postulo.core.params import as_pk
from postulo.jobs.views import UserFormKwargsMixin

from . import answers
from .forms import ApplicationAnswerForm
from .models import Application, ApplicationAnswer, SavedAnswer


class AnswerGivenCreateView(LoginRequiredMixin, View):
    """Keep an answer on an application: begun from a saved one (`?from=`) or from nothing."""

    template_name = "applications/answer_form.html"

    def application(self, request: HttpRequest, pk: int) -> Application:
        return get_object_or_404(
            Application.objects.for_user(request.user).select_related(
                "posting", "posting__company", "contact"
            ),
            pk=pk,
        )

    def context(self, request, application, form, saved) -> dict:
        return {
            "form": form,
            "application": application,
            "saved": saved,
            "saved_answers": SavedAnswer.objects.for_user(request.user),
        }

    def chosen(self, request: HttpRequest, value) -> SavedAnswer | None:
        pk = as_pk(value)
        if not pk:
            return None
        return SavedAnswer.objects.for_user(request.user).filter(pk=pk).first()

    def get(self, request: HttpRequest, pk: int) -> HttpResponse:
        application = self.application(request, pk)
        saved = self.chosen(request, request.GET.get("from"))
        initial = answers.initial_from(saved, application) if saved else {}
        form = ApplicationAnswerForm(user=request.user, initial=initial)
        return render(request, self.template_name, self.context(request, application, form, saved))

    def post(self, request: HttpRequest, pk: int) -> HttpResponse:
        application = self.application(request, pk)
        saved = self.chosen(request, request.POST.get("saved_answer"))
        form = ApplicationAnswerForm(request.POST, user=request.user)
        if not form.is_valid():
            return render(
                request, self.template_name, self.context(request, application, form, saved)
            )
        answers.give(application, saved=saved, **form.cleaned_data)
        messages.success(request, _("Answer kept."))
        return HttpResponseRedirect(f"{application.get_absolute_url()}#answers")


class AnswerGivenUpdateView(OwnedObjectMixin, UserFormKwargsMixin, UpdateView):
    model = ApplicationAnswer
    form_class = ApplicationAnswerForm
    template_name = "applications/answer_form.html"

    def get_queryset(self):
        return (
            super()
            .get_queryset()
            .select_related("application", "application__posting", "application__posting__company")
        )

    def get_context_data(self, **kwargs) -> dict:
        context = super().get_context_data(**kwargs)
        context["application"] = self.object.application
        return context

    def get_success_url(self) -> str:
        return self.object.get_absolute_url()

    def form_valid(self, form):
        messages.success(self.request, _("Answer updated."))
        return super().form_valid(form)


class AnswerGivenDeleteView(ConfirmDeleteMixin, OwnedObjectMixin, DeleteView):
    model = ApplicationAnswer
    template_name = "partials/confirm_delete.html"

    def get_queryset(self):
        return super().get_queryset().select_related("application")

    def get_success_url(self) -> str:
        return reverse("applications:detail", args=[self.object.application_id]) + "#answers"
