"""The answers kept for application forms, under *Your career* (#1092).

A list, and the three ordinary pages of a record that belongs to one person. What an
application was told is a copy of one of these and is kept on the application
(`applications.views_answers`).
"""

from __future__ import annotations

from django.db.models import Q
from django.urls import reverse_lazy
from django.views.generic import CreateView, DeleteView, ListView, UpdateView

from postulo.applications.forms import SavedAnswerForm
from postulo.applications.models import SavedAnswer
from postulo.core.mixins import ConfirmDeleteMixin, OwnedObjectMixin, OwnerFormMixin
from postulo.core.models import Tag
from postulo.jobs.views import UserFormKwargsMixin


class SavedAnswerListView(OwnedObjectMixin, ListView):
    model = SavedAnswer
    template_name = "resume/answer_list.html"
    context_object_name = "answers"

    def get_queryset(self):
        found = super().get_queryset().prefetch_related("tags")
        query = self.request.GET.get("q", "").strip()
        if query:
            found = found.filter(Q(question__icontains=query) | Q(answer__icontains=query))
        tag = self.request.GET.get("tag", "").strip()
        if tag:
            found = found.filter(tags__slug=tag)
        return found

    def get_context_data(self, **kwargs) -> dict:
        context = super().get_context_data(**kwargs)
        context["query"] = self.request.GET.get("q", "").strip()
        context["tag"] = self.request.GET.get("tag", "").strip()
        context["tags"] = Tag.objects.for_user(self.request.user).filter(
            saved_answers__isnull=False
        )
        context["tags"] = context["tags"].distinct()
        return context


class SavedAnswerCreateView(OwnedObjectMixin, UserFormKwargsMixin, OwnerFormMixin, CreateView):
    model = SavedAnswer
    form_class = SavedAnswerForm
    template_name = "resume/answer_form.html"
    success_url = reverse_lazy("resume:answer_list")


class SavedAnswerUpdateView(OwnedObjectMixin, UserFormKwargsMixin, UpdateView):
    model = SavedAnswer
    form_class = SavedAnswerForm
    template_name = "resume/answer_form.html"
    success_url = reverse_lazy("resume:answer_list")


class SavedAnswerDeleteView(ConfirmDeleteMixin, OwnedObjectMixin, DeleteView):
    model = SavedAnswer
    template_name = "partials/confirm_delete.html"
    success_url = reverse_lazy("resume:answer_list")

    def get_cancel_url(self) -> str:
        return str(self.success_url)
