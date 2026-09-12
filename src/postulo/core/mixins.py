"""View mixins that keep one person's data away from another's."""

from django.contrib.auth.mixins import LoginRequiredMixin, UserPassesTestMixin
from django.db.models import QuerySet


class OwnedObjectMixin(LoginRequiredMixin):
    """Restrict a view's queryset to objects owned by the person making the request.

    Use this on every view exposing an :class:`~postulo.core.models.OwnedModel`. It
    narrows the queryset rather than checking ownership after lookup, so a request for
    someone else's object returns 404 instead of 403 — the existence of another user's
    records is not something worth confirming.
    """

    def get_queryset(self) -> QuerySet:
        return super().get_queryset().for_user(self.request.user)


class OwnerFormMixin:
    """Stamp the current user onto objects created through a form."""

    def form_valid(self, form):
        form.instance.owner = self.request.user
        return super().form_valid(form)


class StaffRequiredMixin(LoginRequiredMixin, UserPassesTestMixin):
    """Restrict a view to staff members.

    Issuing invitations is an operator decision, not something every account holder
    should be able to do on a shared instance.
    """

    def test_func(self) -> bool:
        return self.request.user.is_staff


class WebLinksMixin:
    """The link blocks on a view whose object holds them, for the reason the telephone
    mixin below gives: one place for "the kind's feature may be off", "a create view has
    no holder yet" and "saved inside the same transaction as the form or not at all".

    ``links`` in the context is the list of formsets for the kinds that are on -- empty
    while every kind is off, which is the template's signal that the boxes on the form
    are the whole control (#189).
    """

    def link_holder(self):
        return getattr(self, "object", None)

    def get_web_links(self) -> list:
        from postulo.core import web_links

        data = self.request.POST if self.request.method == "POST" else None
        return web_links.formsets_for(self.link_holder(), self.request.user, data=data)

    def web_links_invalid(self, formsets: list) -> bool:
        return any(formset.is_bound and not formset.is_valid() for formset in formsets)

    def save_web_links(self, formsets: list, holder) -> None:
        for formset in formsets:
            if not formset.is_bound:
                continue
            formset.instance = holder
            for form in formset.forms:
                form.instance.owner = holder.owner if hasattr(holder, "owner") else holder.user
            formset.save()

    def get_context_data(self, **kwargs) -> dict:
        from postulo.core import web_links

        context = super().get_context_data(**kwargs)
        context.setdefault("links", self.get_web_links())
        holder = self.link_holder()
        context["links_kept_back"] = (
            web_links.kept_back(holder, self.request.user) if holder is not None else []
        )
        return context


class PhoneNumbersMixin:
    """The telephone rows on a view whose object holds them.

    One place, because the alternative is each view remembering three things: that the
    feature may be off, that a create view has no holder to bind rows to until its object
    exists, and that the rows are saved inside the same transaction as the form or not at
    all. The view that forgets the first offers a person a control an administrator took
    away; the one that forgets the third leaves a contact saved and its numbers not.
    """

    #: ``None`` while the feature is off, which is the template's signal to show the one
    #: box on the form instead. Both at once would be two controls writing one row.
    phone_numbers_prefix = "phone_numbers"

    def phone_holder(self):
        return getattr(self, "object", None)

    def get_phone_numbers(self):
        from postulo.core import phone_numbers, phones

        if not phone_numbers.several_allowed(self.request.user):
            return None
        holder = self.phone_holder()
        profile = getattr(self.request.user, "profile", None)
        kwargs = {
            "holder": holder,
            "prefix": self.phone_numbers_prefix,
            "default_country": phones.default_country(getattr(profile, "language", "")),
        }
        posted = f"{self.phone_numbers_prefix}-TOTAL_FORMS" in self.request.POST
        if self.request.method == "POST" and posted:
            kwargs["data"] = self.request.POST
        return phone_numbers.formset_for(**kwargs)

    def phone_numbers_invalid(self, formset) -> bool:
        return formset is not None and formset.is_bound and not formset.is_valid()

    def save_phone_numbers(self, formset, holder) -> None:
        """Bind the rows to the holder and write them.

        The holder is passed in rather than read back, because on a create view the object
        did not exist when the formset was built.
        """
        if formset is None or not formset.is_bound:
            return
        formset.instance = holder
        for form in formset.forms:
            form.instance.owner = holder.owner if hasattr(holder, "owner") else holder.user
        formset.save()

    def get_context_data(self, **kwargs) -> dict:
        from postulo.core import phone_numbers

        context = super().get_context_data(**kwargs)
        context.setdefault("numbers", self.get_phone_numbers())
        holder = self.phone_holder()
        context["numbers_kept_back"] = (
            phone_numbers.kept_back(holder, self.request.user) if holder is not None else 0
        )
        return context
