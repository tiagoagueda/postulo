from django.urls import path

from . import views, views_pairing

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
    # *Look on the server* (#332): a POST from the connection's own form, which draws it again.
    path("<int:pk>/look/", views.ConnectionDiscoverView.as_view(), name="discover"),
    # One callback for the whole instance: the address an operator registers with a provider
    # by hand, once, rather than once per plugin (#150).
    path("consent/", views.ConnectionConsentCallbackView.as_view(), name="consent_callback"),
    path("<int:pk>/consent/", views.ConnectionConsentView.as_view(), name="consent"),
    path("<int:pk>/send-everything/", views.ConnectionBackfillView.as_view(), name="backfill"),
    path("<int:pk>/sync-now/", views.ConnectionSyncNowView.as_view(), name="sync_now"),
    # *Pairing* (#327): a sync connection's items paired by hand with the person's records.
    path("<int:pk>/pairing/", views_pairing.PairingView.as_view(), name="pairing"),
    path("<int:pk>/pairing/item/", views_pairing.PairingItemView.as_view(), name="pairing_item"),
    path("<int:pk>/pairing/apply/", views_pairing.PairingApplyView.as_view(), name="pairing_apply"),
    path(
        "<int:pk>/pairing/unpair/", views_pairing.PairingUnpairView.as_view(), name="pairing_unpair"
    ),
    path("<int:pk>/pairing/renew/", views_pairing.PairingRenewView.as_view(), name="pairing_renew"),
    path("<int:pk>/delete/", views.ConnectionDeleteView.as_view(), name="delete"),
]
