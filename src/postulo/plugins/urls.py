from django.urls import path

from . import views

app_name = "connections"

urlpatterns = [
    path("", views.ConnectionListView.as_view(), name="list"),
    # Addressed by the plugin's name rather than by a number: a logo belongs to the plugin
    # rather than to any row, and the name is what every other page already has (#106).
    path("logo/<str:name>/", views.PluginLogoView.as_view(), name="logo"),
    path("add/", views.ConnectionPickView.as_view(), name="pick"),
    path("add/<str:kind>/<str:name>/", views.ConnectionFormView.as_view(), name="create"),
    path("<int:pk>/", views.ConnectionFormView.as_view(), name="edit"),
    path("<int:pk>/test/", views.ConnectionTestView.as_view(), name="test"),
    # One callback for the whole instance: the address an operator registers with a provider
    # by hand, once, rather than once per plugin (#150).
    path("consent/", views.ConnectionConsentCallbackView.as_view(), name="consent_callback"),
    path("<int:pk>/consent/", views.ConnectionConsentView.as_view(), name="consent"),
    path("<int:pk>/send-everything/", views.ConnectionBackfillView.as_view(), name="backfill"),
    path("<int:pk>/sync-now/", views.ConnectionSyncNowView.as_view(), name="sync_now"),
    path("<int:pk>/delete/", views.ConnectionDeleteView.as_view(), name="delete"),
]
