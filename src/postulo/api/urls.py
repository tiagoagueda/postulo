from django.urls import path

from . import views

app_name = "api"

urlpatterns = [
    path("", views.ApiTokenListView.as_view(), name="token_list"),
    path("new/", views.ApiTokenCreateView.as_view(), name="token_create"),
    path("<int:pk>/revoke/", views.ApiTokenRevokeView.as_view(), name="token_revoke"),
]
