"""Ownership isolation.

Showing one person another person's job search is the single worst thing this
application could do, so the guarantee is tested directly rather than assumed from the
fact that views happen to call ``for_user``.
"""

import pytest
from django.contrib.auth.models import AnonymousUser
from django.views.generic import DetailView

from postulo.core.mixins import OwnedObjectMixin, OwnerFormMixin

from .testapp.models import Widget


@pytest.fixture
def widgets(user, other_user):
    return {
        "mine": Widget.objects.create(owner=user, name="Mine"),
        "theirs": Widget.objects.create(owner=other_user, name="Theirs"),
    }


def test_for_user_returns_only_that_users_rows(user, widgets):
    results = list(Widget.objects.for_user(user))
    assert results == [widgets["mine"]]


def test_for_user_excludes_other_accounts(other_user, widgets):
    assert widgets["mine"] not in Widget.objects.for_user(other_user)


def test_for_user_returns_nothing_for_anonymous(widgets):
    assert not Widget.objects.for_user(AnonymousUser()).exists()


def test_for_user_returns_nothing_for_none(widgets):
    """A missing user must fail closed, not fall through to every row."""
    assert not Widget.objects.for_user(None).exists()


class WidgetDetailView(OwnedObjectMixin, DetailView):
    model = Widget


def test_mixin_scopes_the_queryset(rf, user, widgets):
    request = rf.get("/")
    request.user = user
    view = WidgetDetailView()
    view.request = request

    assert list(view.get_queryset()) == [widgets["mine"]]


def test_mixin_hides_another_users_object(rf, other_user, widgets):
    """Another account's record must be absent from the queryset, which yields a 404.

    Returning 403 would confirm that the record exists, which is itself a disclosure.
    """
    request = rf.get("/")
    request.user = other_user
    view = WidgetDetailView()
    view.request = request

    assert widgets["mine"] not in view.get_queryset()


def test_owner_form_mixin_stamps_the_current_user(rf, user):
    class DummyForm:
        instance = Widget(name="New")

    class Base:
        def form_valid(self, form):
            return form.instance

    # The mixin must precede the base, so that its form_valid runs first.
    class DummyView(OwnerFormMixin, Base):
        pass

    request = rf.post("/")
    request.user = user
    view = DummyView()
    view.request = request

    instance = view.form_valid(DummyForm())
    assert instance.owner == user
