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
