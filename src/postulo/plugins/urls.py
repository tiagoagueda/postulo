from django.urls import path

from . import views

app_name = "connections"

urlpatterns = [
    path("", views.ConnectionListView.as_view(), name="list"),
    path("add/", views.ConnectionPickView.as_view(), name="pick"),
    path("add/<str:kind>/<str:name>/", views.ConnectionFormView.as_view(), name="create"),
    path("<int:pk>/", views.ConnectionFormView.as_view(), name="edit"),
    path("<int:pk>/test/", views.ConnectionTestView.as_view(), name="test"),
    path("<int:pk>/send-everything/", views.ConnectionBackfillView.as_view(), name="backfill"),
    path("<int:pk>/sync-now/", views.ConnectionSyncNowView.as_view(), name="sync_now"),
    path("<int:pk>/delete/", views.ConnectionDeleteView.as_view(), name="delete"),
]
