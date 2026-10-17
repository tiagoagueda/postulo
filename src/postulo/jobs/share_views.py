"""The pages that share a record, take a share back, and keep a person's own note (#896)."""

from __future__ import annotations

from django.contrib import messages
from django.contrib.auth.mixins import LoginRequiredMixin
from django.contrib.auth.models import Group
from django.http import Http404, HttpRequest, HttpResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.utils.translation import gettext as _
from django.views import View

from postulo.core import throttle
from postulo.core.mixins import MANAGE, READ, shared_scope
from postulo.core.redirects import safe_next

from . import sharing
from .models import Share


def record_or_404(request, kind: str, pk: int, access: str):
    model = sharing.KINDS.get(kind)
    if model is None:
        raise Http404
    return get_object_or_404(shared_scope(model.objects, request.user, access), pk=pk)


class ShareView(LoginRequiredMixin, View):
    """A record's grants, and the form that adds one. Its managers' page only."""

    template_name = "jobs/share.html"

    def context(self, request, record, **extra) -> dict:
        return {
            "record": record,
            "kind": sharing.kind_of(record),
            "grants": sharing.grants_for(record),
            "groups": request.user.groups.order_by("name"),
            **extra,
        }

    def get(self, request: HttpRequest, kind: str, pk: int) -> HttpResponse:
        record = record_or_404(request, kind, pk, MANAGE)
        return render(request, self.template_name, self.context(request, record))

    def post(self, request: HttpRequest, kind: str, pk: int) -> HttpResponse:
        record = record_or_404(request, kind, pk, MANAGE)
        value = (request.POST.get("grantee") or request.POST.get("person", "")).strip()
        if value and not value.startswith("group:"):
            # Whom a typed name or address belongs to is how an account is found out; the
            # number of tries is bounded, and choosing one's own group costs nothing (#943).
            try:
                throttle.consume(
                    "share-lookup", request.user, throttle.rate_for("POSTULO_SHARE_LOOKUP_RATE")
                )
            except throttle.TooOften as too_often:
                messages.error(request, str(too_often))
                return redirect(request.path)
        grantee = sharing.grantee_from(request.user, value)
        if grantee is None:
            messages.error(
                request,
                _("Nobody on this instance answers to that, or it is you; nothing was shared."),
            )
            return redirect(request.path)
        scope = request.POST.get("scope", "read")
        also = request.POST.get("also") == "1"
        # A person is named back as they were typed, never by the username an address maps to.
        typed = "" if isinstance(grantee, Group) else value
        label = sharing.grantee_label(grantee) if isinstance(grantee, Group) else value
        plan = sharing.plan_share(record, grantee, scope, request.user)
        if plan.possible and plan.also and not also:
            # Asked, and nothing done until it is answered.
            grantee_value = f"group:{grantee.pk}" if isinstance(grantee, Group) else value
            return render(
                request,
                self.template_name,
                self.context(
                    request,
                    record,
                    plan=plan,
                    grantee_label=label,
                    grantee_value=grantee_value,
                ),
            )
        try:
            sharing.share(record, grantee, scope, request.user, also_share=also, addressed_as=typed)
        except sharing.ShareRefused as refusal:
            messages.error(request, str(refusal))
            return redirect(request.path)
        messages.success(request, _("Shared with %(who)s.") % {"who": label})
        return redirect(request.path)


class RevokeView(LoginRequiredMixin, View):
    """Take one grant back, at once."""

    def post(self, request: HttpRequest, pk: int) -> HttpResponse:
        grant = get_object_or_404(Share, pk=pk)
        record = grant.record
        if not sharing.may_manage(record, request.user):
            raise Http404
        try:
            sharing.revoke(grant, request.user)
        except sharing.ShareRefused as refusal:
            messages.error(request, str(refusal))
        else:
            messages.success(
                request,
                _("No longer shared with %(who)s.") % {"who": sharing.grantee_label(grant.grantee)},
            )
        return redirect("jobs:share", kind=sharing.kind_of(record), pk=record.pk)


class OwnNoteView(LoginRequiredMixin, View):
    """Keep one's own note on a company or a contact one can see, edit rights or not."""

    def post(self, request: HttpRequest, kind: str, pk: int) -> HttpResponse:
        if kind not in ("company", "contact"):
            raise Http404
        record = record_or_404(request, kind, pk, READ)
        record.write_note(request.user, request.POST.get("notes", ""))
        messages.success(request, _("Your note is kept."))
        return redirect(safe_next(request, record.get_absolute_url()))


class MoveView(LoginRequiredMixin, View):
    """Give a record to another owner: one of the giver's groups, or the member asking."""

    template_name = "jobs/move_owner.html"

    def choices(self, request, record) -> list[tuple[str, str]]:
        found = []
        if record.owner_group_id is not None or request.user.is_superuser:
            if record.owner_id != request.user.pk:
                found.append(("me", _("Me, %(name)s") % {"name": request.user.get_username()}))
        groups = (
            Group.objects.order_by("name")
            if request.user.is_superuser
            else request.user.groups.order_by("name")
        )
        for group in groups:
            if group.pk != record.owner_group_id:
                found.append((f"group:{group.pk}", _("The group %(name)s") % {"name": group.name}))
        return found

    def principal_from(self, request, value: str, person: str):
        from django.contrib.auth import get_user_model

        if person and request.user.is_superuser:
            # A deactivated account cannot sign in, so a record given to it is out of
            # everyone's reach: sharing refuses it too (#947).
            return (
                get_user_model()
                .objects.filter(username__iexact=person.strip(), is_active=True)
                .first()
            )
        if value == "me":
            return request.user
        if value.startswith("group:"):
            try:
                pk = int(value.split(":", 1)[1])
            except ValueError:
                return None
            return Group.objects.filter(pk=pk).first()
        return None

    def base(self, request, record, **extra) -> dict:
        from postulo.core.sharing import principal_label

        return {
            "record": record,
            "owner_label": principal_label(record),
            "choices": self.choices(request, record),
            "superuser": request.user.is_superuser,
            **extra,
        }

    def get(self, request: HttpRequest, kind: str, pk: int) -> HttpResponse:
        record = record_or_404(request, kind, pk, MANAGE)
        return render(request, self.template_name, self.base(request, record))

    def post(self, request: HttpRequest, kind: str, pk: int) -> HttpResponse:
        from . import ownership

        record = record_or_404(request, kind, pk, MANAGE)
        to = self.principal_from(
            request, request.POST.get("to", ""), request.POST.get("person", "")
        )
        if to is None or not ownership.may_move_to(record, to, request.user):
            messages.error(request, _("It cannot be given to them."))
            return redirect(request.path)
        if request.POST.get("confirmed") != "1":
            also = []
            if isinstance(to, Group):
                plan = sharing.plan_share(record, to, Share.Scope.READ, request.user)
                if not plan.possible:
                    messages.error(request, sharing.why_refused(plan))
                    return redirect(request.path)
                also = plan.also
            to_value = (
                f"group:{to.pk}"
                if isinstance(to, Group)
                else ("me" if to.pk == request.user.pk else to.get_username())
            )
            return render(
                request,
                self.template_name,
                self.base(
                    request,
                    record,
                    confirm=True,
                    to_value=to_value,
                    to_label=ownership.label(to),
                    to_group=isinstance(to, Group),
                    losing=ownership.losing(record, to),
                    also=also,
                ),
            )
        try:
            ownership.move(record, to, request.user, also_share=True)
        except (ownership.MoveRefused, sharing.ShareRefused) as refusal:
            messages.error(request, str(refusal))
            return redirect(request.path)
        messages.success(request, _("It belongs to %(name)s now.") % {"name": ownership.label(to)})
        if (
            type(record)
            .objects.visible_to(request.user, everyone=True)
            .filter(pk=record.pk)
            .exists()
        ):
            return redirect(record.get_absolute_url())
        return redirect("jobs:company_list")


class ConflictsView(LoginRequiredMixin, View):
    """What wants deciding between owners, for whoever can decide it (#896)."""

    template_name = "jobs/conflicts.html"

    def get(self, request: HttpRequest) -> HttpResponse:
        from . import conflicts

        everyone = request.GET.get("everyone") == "1" and request.user.is_superuser
        return render(
            request,
            self.template_name,
            {"found": conflicts.find(request.user, everyone=everyone), "everyone": everyone},
        )

    def post(self, request: HttpRequest) -> HttpResponse:
        """Keep two records apart: the page stops asking about them."""
        from . import conflicts

        kind = request.POST.get("kind", "")
        model = conflicts.KINDS.get(kind)
        try:
            first, second = int(request.POST.get("first", "")), int(request.POST.get("second", ""))
        except ValueError:
            raise Http404 from None
        if model is None:
            raise Http404
        seen = shared_scope(model.objects, request.user, READ).filter(pk__in=[first, second])
        managed = shared_scope(model.objects, request.user, MANAGE).filter(pk__in=[first, second])
        if seen.count() != 2 or not managed.exists():
            raise Http404
        conflicts.keep_apart(kind, first, second, request.user)
        messages.success(request, _("Kept apart: they will not be asked about again."))
        return redirect("jobs:conflicts")
