from django.urls import path

from . import views

app_name = "accounts"

urlpatterns = [
    path("profile/", views.ProfileView.as_view(), name="profile"),
    path("theme/", views.ThemeView.as_view(), name="theme"),
    path("avatar/<int:pk>/", views.AvatarView.as_view(), name="avatar"),
    path("avatar/refresh/", views.GravatarRefreshView.as_view(), name="avatar_refresh"),
    path("invitations/", views.InviteListView.as_view(), name="invite_list"),
    path("invitations/new/", views.InviteCreateView.as_view(), name="invite_create"),
    path("invitations/<int:pk>/revoke/", views.InviteRevokeView.as_view(), name="invite_revoke"),
    # Deliberately singular and distinct from the management URLs above, so that a
    # token can never be mistaken for a primary key.
    path("invitation/<str:token>/", views.InviteAcceptView.as_view(), name="invite_accept"),
]
