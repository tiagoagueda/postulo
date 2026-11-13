"""Message templates, and writing one to an application (#1094).

A follow-up, a thank-you, a withdrawal: the same few messages, written many times. A template
is a subject and a body with the letter's placeholders. Writing one is a page of its own, off
an application or an interview: the template filled in, edited, and then sent from the
person's own address, or copied, or said to have been sent -- and the timeline is told in
whichever case.
"""

from __future__ import annotations

import logging
import smtplib

from django.contrib import messages
from django.contrib.auth.mixins import LoginRequiredMixin
from django.http import HttpRequest, HttpResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse_lazy
from django.utils.translation import gettext_lazy as _
from django.views import View
from django.views.generic import CreateView, DeleteView, ListView, UpdateView

from postulo.applications.models import Application, EventKind, Interview
from postulo.applications.services import record_event
from postulo.core.mixins import ConfirmDeleteMixin, OwnedObjectMixin, OwnerFormMixin
from postulo.core.params import as_pk
from postulo.jobs.views import UserFormKwargsMixin

from . import message_templates
from .forms import MessageChoiceForm, MessageTemplateForm, MessageWriteForm
from .models import MessagePurpose, MessageTemplate

logger = logging.getLogger(__name__)


class MessageTemplateListView(OwnedObjectMixin, ListView):
    """Every message template, by what it is for."""

    model = MessageTemplate
    template_name = "documents/message_list.html"
    context_object_name = "message_templates"

    def get_queryset(self):
        return super().get_queryset().select_related("owner__profile")


class ShowsPlaceholders:
    """The form says which placeholders it fills in, and what each is."""

    def get_context_data(self, **kwargs) -> dict:
        context = super().get_context_data(**kwargs)
        context["placeholders"] = MessageTemplate.PLACEHOLDERS
        return context


class MessageTemplateCreateView(
    ShowsPlaceholders, OwnedObjectMixin, UserFormKwargsMixin, OwnerFormMixin, CreateView
):
    model = MessageTemplate
    form_class = MessageTemplateForm
    template_name = "documents/message_form.html"
    success_url = reverse_lazy("documents:message_list")

    def get_initial(self) -> dict:
        initial = super().get_initial()
        purpose = self.request.GET.get("purpose", "")
        if purpose in MessagePurpose.values:
            initial["purpose"] = purpose
        return initial


class MessageTemplateUpdateView(
    ShowsPlaceholders, OwnedObjectMixin, UserFormKwargsMixin, UpdateView
):
    model = MessageTemplate
    form_class = MessageTemplateForm
    template_name = "documents/message_form.html"
    success_url = reverse_lazy("documents:message_list")


class MessageTemplateDeleteView(ConfirmDeleteMixin, OwnedObjectMixin, DeleteView):
    model = MessageTemplate
    template_name = "partials/confirm_delete.html"
    success_url = reverse_lazy("documents:message_list")


class MessageStartersView(LoginRequiredMixin, View):
    """Add the starter messages the person does not have yet. Never done unasked."""

    def post(self, request: HttpRequest) -> HttpResponse:
        added = message_templates.add_starters(request.user)
        if added:
            messages.success(request, _("The starter messages are added. Change them as you like."))
        else:
            messages.info(request, _("You already have a message for each of them."))
        return redirect("documents:message_list")


class MessageWriteView(OwnedObjectMixin, View):
    """Write a message about one application, from a template, and say what became of it.

    Three ends, because not everybody has an outbox: *Send* goes from the person's own
    address, *Copy* shows the text to copy into their own mail program, and *Record as sent*
    notes that it went. Each of the first and the last leaves a line on the timeline; the
    copy leaves nothing, since nothing has happened yet.
    """

    template_name = "documents/message_write.html"

    def get_queryset(self):
        return Application.objects.for_user(self.request.user).select_related(
            "posting", "posting__company", "contact"
        )

    def get_application(self, pk: int) -> Application:
        return get_object_or_404(self.get_queryset(), pk=pk)

    def interview_for(self, request: HttpRequest, application: Application):
        """The interview the message is about, when it is one of this application's."""
        pk = as_pk((request.POST if request.method == "POST" else request.GET).get("interview"))
        if pk is None:
            return None
        return (
            Interview.objects.for_user(request.user)
            .filter(pk=pk, application=application)
            .prefetch_related("contacts")
            .first()
        )

    def chosen_template(self, request: HttpRequest):
        """The template asked for by its id, or else the first one for the purpose asked for."""
        pk = as_pk(request.GET.get("template"))
        if pk is not None:
            found = MessageTemplate.objects.for_user(request.user).filter(pk=pk).first()
            if found is not None:
                return found
        return message_templates.starter_for(request.user, request.GET.get("purpose", ""))

    def page(self, request, application, interview, form, **extra) -> HttpResponse:
        from postulo.core import correspondence

        chosen = extra.pop("template", None)
        context = {
            "application": application,
            "interview": interview,
            "form": form,
            "choose": MessageChoiceForm(
                user=request.user, initial={"template": chosen.pk if chosen else None}
            ),
            "has_templates": MessageTemplate.objects.for_user(request.user).exists(),
            "can_send": correspondence.can_send_as_themselves(request.user),
            "chosen": chosen,
            **extra,
        }
        return render(request, self.template_name, context)

    def get(self, request: HttpRequest, pk: int) -> HttpResponse:
        application = self.get_application(pk)
        interview = self.interview_for(request, application)
        chosen = self.chosen_template(request)
        subject, body = ("", "")
        if chosen is not None:
            subject, body = message_templates.written_for(chosen, application)
        form = MessageWriteForm(
            initial={
                "recipient": message_templates.recipient_for(application, interview),
                "subject": subject,
                "body": body,
                "purpose": chosen.purpose if chosen else request.GET.get("purpose", ""),
            }
        )
        return self.page(request, application, interview, form, template=chosen)

    def post(self, request: HttpRequest, pk: int) -> HttpResponse:
        application = self.get_application(pk)
        interview = self.interview_for(request, application)
        form = MessageWriteForm(request.POST)
        action = request.POST.get("action", "")
        if action not in ("send", "copy", "record") or not form.is_valid():
            if action not in ("send", "copy", "record"):
                form.add_error(None, _("Choose what to do with the message."))
            return self.page(request, application, interview, form)

        data = form.cleaned_data
        if action == "copy":
            text = f"{data['subject']}\n\n{data['body']}"
            return self.page(request, application, interview, form, copied=text)
        if action == "send":
            refusal = self.send(request, data)
            if refusal is not None:
                form.add_error(None, str(refusal))
                return self.page(request, application, interview, form)
            messages.success(request, _("Sent, and recorded on the timeline."))
        else:
            messages.success(request, _("Recorded on the timeline."))
        self.record(application, interview, data)
        return redirect(application.get_absolute_url())

    def send(self, request: HttpRequest, data: dict):
        """Mail the message from the person's own address; what went wrong, in words, or none.

        Sent first and recorded only if it went, so the timeline never says a message left
        that did not. Never the server's own reply, which can quote the recipient.
        """
        from django.core.mail import EmailMessage

        from postulo.core import correspondence, destinations, throttle
        from postulo.core.mail import ConnectionFailed
        from postulo.plugins.consent import ConsentFailed

        if not data["recipient"]:
            return _("Say who to send it to.")
        # No sender: `correspondence.send` puts the outbox's own address on it, and refuses
        # rather than rewrites if a caller ever claims another.
        message = EmailMessage(subject=data["subject"], body=data["body"], to=[data["recipient"]])
        try:
            correspondence.send(request.user, message)
        except (correspondence.NoOutbox, correspondence.WrongSender) as refused:
            return refused
        except throttle.TooOften as often:
            return often
        except (ConnectionFailed, ConsentFailed, destinations.Refused) as failed:
            return failed
        except (smtplib.SMTPException, OSError):
            logger.warning("An outbox for account %s failed to send", request.user.pk)
            return _("The mail server did not take the message, so nothing was sent or recorded.")
        return None

    def record(self, application: Application, interview, data: dict) -> None:
        """The message as a line on the timeline: its subject, and its words under the address.

        A follow-up is what the quiet action's own button records, so it is that kind here;
        everything else is a message sent.
        """
        kind = (
            EventKind.FOLLOW_UP
            if data["purpose"] == MessagePurpose.FOLLOW_UP
            else EventKind.EMAIL_SENT
        )
        body = data["body"]
        if data["recipient"]:
            body = f"{data['recipient']}\n\n{body}"
        record_event(
            application,
            kind=kind,
            summary=data["subject"],
            body=body,
            interview=interview,
        )
