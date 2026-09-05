from django.urls import path

from . import views, views_export, views_search, views_tables

app_name = "core"

urlpatterns = [
    path("", views.home, name="home"),
    path("healthz", views.healthz, name="healthz"),
    path("search/", views_search.search_page, name="search"),
    path("export/", views_export.export_overview, name="export"),
    path("export/download/", views_export.export_download, name="export_download"),
    path("tables/<slug:name>/settings/", views_tables.table_settings, name="table_settings"),
]
