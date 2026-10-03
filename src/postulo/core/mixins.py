"""View mixins that keep one person's data away from another's."""

from django.contrib.auth.mixins import LoginRequiredMixin, UserPassesTestMixin
from django.db.models import QuerySet
from django.utils.cache import patch_vary_headers


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


class PageOrFragmentMixin:
    """One address answered as a page, or as the part of the page that changes, and saying so.

    A table page answers an htmx request with its table alone -- the partial its template
    names `htmx` -- at the page's own address. That is what lets a filter, a sort or a search
    be put in the address bar as it is applied. So one address has two answers, and which is
    sent depends on a header of the request.

    A cache has to be told that, and was not (#646). The browser's own cache kept the
    fragment under the page's address, and when Back asked it for that address as a document
    it handed the fragment over: a table with no title, no masthead and no stylesheet. So
    every answer of such a view names, in `Vary`, the headers the choice reads:
    `HX-Request`, and `HX-History-Restore-Request`, with which htmx asks for a whole page it
    has no copy of. Every answer, not only the two shapes of the page: the redirect to a
    person's default view is not sent to an htmx request either, so it too is the header's
    doing.

    `HX-Trigger` is named with them (#626). The fragment is not the same for every control
    that asks for it: the column whose own filter asked is drawn with that filter open,
    whatever it holds (`Table.asked_by`), so the answer to one control is not the answer to
    another at the same address.

    The choice and the header are made in one place, so that a view cannot make the first
    without sending the second. First among a view's bases, so that the header is on what
    the bases after it answer as well, the redirect to the sign-in page among them.
    """

    def get_template_names(self) -> list[str]:
        # An htmx request wants the part alone; the back button's restore wants the page.
        if self.request.htmx and not self.request.htmx.history_restore_request:
            return [f"{self.template_name}#htmx"]
        return [self.template_name]

    def dispatch(self, request, *args, **kwargs):
        response = super().dispatch(request, *args, **kwargs)
        patch_vary_headers(response, ("HX-Request", "HX-History-Restore-Request", "HX-Trigger"))
        return response


class ConfirmDeleteMixin:
    """Where *Cancel* goes on a confirmation page, decided by the view.

    It used to be ``HTTP_REFERER``. That is a request header, not a fact about the page: it
    is absent for anybody who arrived from a bookmark, from a new tab, or with a browser
    that sends none, and the template's fallback was ``/`` — so somebody who changed their
    mind about deleting a company could be put on the dashboard instead of back where they
    were. It is also the one value on the page nobody chose (#227).

    The object's own page is where cancelling belongs, because it is the page the button was
    pressed on. Where the object has no page of its own — a CV entry, a career entry — the
    view already names where deleting leads, and that is the next best answer.
    """

    def get_cancel_url(self) -> str:
        obj = getattr(self, "object", None)
        if obj is not None and hasattr(obj, "get_absolute_url"):
            return obj.get_absolute_url()
        return self.get_success_url()

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        context.setdefault("cancel_url", self.get_cancel_url())
        return context


class GdprNoticeMixin:
    """The instance's privacy notice on a page that promises to keep data about another
    person (#297).

    The words are the operator's and sit on the policy row; the page only decides to show
    them. Nothing when the operator wrote nothing, and nothing when the feature is off,
    because a notice beside a page the feature no longer offers would be a promise the
    site is no longer keeping.
    """

    def get_context_data(self, **kwargs):
        from postulo.core import gdpr

        context = super().get_context_data(**kwargs)
        context.setdefault(
            "privacy_notice", gdpr.notice() if gdpr.is_offered(self.request.user) else ""
        )
        return context


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
        """Bind each block to the holder and write it. A new row takes its kind and its
        owner from its formset, which asks the holder (`core.formsets.owner_of`)."""
        for formset in formsets:
            if not formset.is_bound:
                continue
            formset.instance = holder
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
            "asked_by": self.request.user,
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
        did not exist when the formset was built. A new row takes its owner from the
        formset, which asks the holder (`core.formsets.owner_of`).
        """
        if formset is None or not formset.is_bound:
            return
        formset.instance = holder
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
