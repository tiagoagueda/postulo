from django.urls import path

from . import views, views_export

app_name = "core"

urlpatterns = [
    path("", views.home, name="home"),
    path("healthz", views.healthz, name="healthz"),
    path("export/", views_export.export_overview, name="export"),
    path("export/download/", views_export.export_download, name="export_download"),
]
