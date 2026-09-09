from django.urls import path

from . import views

app_name = "accounts"

urlpatterns = [
    # Postulo's own sign-in page and its by-code door, both shadowing allauth's paths so
    # that whether the instance offers a code is a decision read at request time rather than
    # one frozen into the URL table at import (#153). allauth's names still reverse here.
    path("login/", views.SignInView.as_view(), name="login"),
    path("login/code/", views.RequestLoginCodeView.as_view(), name="login_code"),
    path("profile/", views.ProfileView.as_view(), name="profile"),
    path("theme/", views.ThemeView.as_view(), name="theme"),
    path("avatar/<int:pk>/", views.AvatarView.as_view(), name="avatar"),
    path("avatar/refresh/", views.GravatarRefreshView.as_view(), name="avatar_refresh"),
    path("delete/", views.DeleteAccountView.as_view(), name="delete"),
    path("invitations/", views.InviteListView.as_view(), name="invite_list"),
    path("invitations/new/", views.InviteCreateView.as_view(), name="invite_create"),
    path("invitations/<int:pk>/revoke/", views.InviteRevokeView.as_view(), name="invite_revoke"),
    # Deliberately singular and distinct from the management URLs above, so that a
    # token can never be mistaken for a primary key.
    path("invitation/<str:token>/", views.InviteAcceptView.as_view(), name="invite_accept"),
    # Singular and separate for the same reason the invitation URL is: a token must never
    # be able to arrive where a primary key is expected. The form the link leads to has no
    # token in its address, so the secret does not travel in a Referer header (#103).
    path("recover/<str:token>/", views.RecoveryLinkView.as_view(), name="recovery_open"),
    path("recover/", views.RecoverySetPasswordView.as_view(), name="recovery_set"),
]
