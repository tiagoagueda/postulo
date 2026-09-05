from django.urls import path

from . import settings_views as views

app_name = "settings"

urlpatterns = [
    path("", views.SettingsIndexView.as_view(), name="index"),
    path("appearance/", views.AppearanceView.as_view(), name="appearance"),
    path("language/", views.LocaleView.as_view(), name="locale"),
    path("account/", views.AccountView.as_view(), name="account"),
]
