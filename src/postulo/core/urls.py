from django.urls import path

from . import (
    kind_files,
    views,
    views_errands,
    views_export,
    views_help,
    views_import,
    views_kind_files,
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
    # A card's help as a page of its own: where its question mark leads with scripts off,
    # and what a script opens in a drawer instead (#302).
    path("help/<slug:slug>/", views_help.topic_page, name="help_topic"),
    path("export/", views_export.export_overview, name="export"),
    path("export/download/", views_export.export_download, name="export_download"),
    path(
        "export/archive/<int:pk>/",
        views_export.export_archive,
        name="export_archive",
    ),
    # One kind of record as a file of its own, and back (#659). Four pages and four
    # downloads, written out by kind rather than with a slug in the address: none of them
    # names a record, and a kind that is not one of the four is a 404 by not being a route.
    *(
        route
        for kind in kind_files.FORMATS
        for route in (
            path(
                f"export/{kind}/",
                views_kind_files.KindFileView.as_view(),
                {"kind": kind},
                name=f"file_{kind}",
            ),
            path(
                f"export/{kind}/download/",
                views_kind_files.KindDownloadView.as_view(),
                {"kind": kind},
                name=f"file_{kind}_download",
            ),
        )
    ),
    # Where a button that sends work off lands, and the fragment that page polls (#247).
    path("working/<int:pk>/", views_errands.errand_page, name="errand"),
    path("working/<int:pk>/state/", views_errands.errand_state, name="errand_state"),
    path("import/", views_import.import_csv, name="import_csv"),
    path("import/template.csv", views_import.import_csv_template, name="import_csv_template"),
    path("import/done/<int:pk>/", views_import.import_csv_done, name="import_csv_done"),
    path("import/forget/", views_import.import_csv_forget, name="import_csv_forget"),
    path("tables/<slug:name>/settings/", views_tables.table_settings, name="table_settings"),
    path("tables/<slug:name>/views/", views_tables.table_views, name="table_views"),
]
