from django.urls import path

from . import settings_views as views

app_name = "settings"

urlpatterns = [
    path("", views.SettingsIndexView.as_view(), name="index"),
    path("appearance/", views.AppearanceView.as_view(), name="appearance"),
    path("accessibility/", views.AccessibilityView.as_view(), name="accessibility"),
    path(
        "<slug:section>/field/<slug:name>/",
        views.SaveFieldView.as_view(),
        name="save_field",
    ),
    path("language/", views.LocaleView.as_view(), name="locale"),
    path("account/", views.AccountView.as_view(), name="account"),
    path("capture/", views.CaptureView.as_view(), name="capture"),
    path(
        "capture/remembered/forget/",
        views.ForgetRememberedPlacesView.as_view(),
        name="capture_forget",
    ),
    path("plugins/", views.PluginsView.as_view(), name="plugins"),
]
