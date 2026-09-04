from django.urls import path

from . import views

app_name = "api"

urlpatterns = [
    path("", views.CaptureTokenListView.as_view(), name="token_list"),
    path("new/", views.CaptureTokenCreateView.as_view(), name="token_create"),
    path("<int:pk>/revoke/", views.CaptureTokenRevokeView.as_view(), name="token_revoke"),
]
