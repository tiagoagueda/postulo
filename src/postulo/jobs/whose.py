"""Whose records a list shows: all one can see, one's own, one's groups', those shared with
one -- and, for a superuser, everybody's (#896)."""

from __future__ import annotations

from django.db.models import Exists, OuterRef, Q
from django.utils.translation import gettext_lazy as _

from postulo.core.models import group_ids

ALL, MINE, GROUPS, SHARED, EVERYONE = "", "mine", "groups", "shared", "everyone"


def offered(user) -> list[tuple[str, str]]:
    """The choices worth offering ``user``: none at all for somebody with nothing but their
    own, which is everybody on an instance nobody shares on."""
    from .models import Share

    choices = []
    in_groups = user.groups.exists()
    has_shares = Share.objects.filter(
        Q(grantee_user=user) | Q(grantee_group__in=group_ids(user))
    ).exists()
    if in_groups or has_shares or user.is_superuser:
        choices = [(ALL, _("All you can see")), (MINE, _("Yours"))]
        if in_groups:
            choices.append((GROUPS, _("Your groups'")))
        if has_shares:
            choices.append((SHARED, _("Shared with you")))
        if user.is_superuser:
            choices.append((EVERYONE, _("Everybody's")))
    return choices


def chosen(request) -> str:
    wanted = request.GET.get("whose", ALL)
    return wanted if wanted in dict(offered(request.user)) else ALL


def base(model, user, whose: str):
    """What a list starts from: everybody's for a superuser who asked, else what ``user`` can
    see, narrowed to ``whose``."""
    if whose == EVERYONE and user.is_superuser:
        return model.objects.all()
    queryset = model.objects.visible_to(user)
    if whose == MINE:
        return queryset.filter(owner=user)
    if whose == GROUPS:
        return queryset.filter(owner_group__in=group_ids(user))
    if whose == SHARED:
        from .models import Share

        field = {"company": "company", "contact": "contact", "jobposting": "posting"}[
            model._meta.model_name
        ]
        grants = Share.objects.filter(**{field: OuterRef("pk")}).filter(
            Q(grantee_user=user) | Q(grantee_group__in=group_ids(user))
        )
        return queryset.filter(Exists(grants))
    return queryset
