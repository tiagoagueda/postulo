from django.urls import path

from . import (
    views,
    views_errands,
    views_export,
    views_import,
    views_logs,
    views_metrics,
    views_search,
    views_tables,
)

app_name = "core"

urlpatterns = [
    path("", views.home, name="home"),
    path("healthz", views.healthz, name="healthz"),
    path("manifest.webmanifest", views.manifest, name="manifest"),
    # Off unless an operator turns it on, and a 404 rather than a 403 when it is off.
    path("logs", views_logs.collect, name="logs_endpoint"),
    path("metrics", views_metrics.scrape, name="metrics"),
    path("search/", views_search.search_page, name="search"),
    path("export/", views_export.export_overview, name="export"),
    path("export/download/", views_export.export_download, name="export_download"),
    path(
        "export/archive/<int:pk>/",
        views_export.export_archive,
        name="export_archive",
    ),
    # Where a button that sends work off lands, and the fragment that page polls (#247).
    path("working/<int:pk>/", views_errands.errand_page, name="errand"),
    path("working/<int:pk>/state/", views_errands.errand_state, name="errand_state"),
    path("import/", views_import.import_csv, name="import_csv"),
    path("import/template.csv", views_import.import_csv_template, name="import_csv_template"),
    path("import/forget/", views_import.import_csv_forget, name="import_csv_forget"),
    path("tables/<slug:name>/settings/", views_tables.table_settings, name="table_settings"),
]
