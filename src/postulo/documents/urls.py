from django.urls import path

from . import views

app_name = "documents"

urlpatterns = [
    path("cvs/", views.CVListView.as_view(), name="cv_list"),
    path("cvs/new/", views.CVCreateView.as_view(), name="cv_create"),
    path("cvs/<int:pk>/", views.CVDetailView.as_view(), name="cv_detail"),
    path("cvs/<int:pk>/edit/", views.CVUpdateView.as_view(), name="cv_update"),
    path("cvs/<int:pk>/delete/", views.CVDeleteView.as_view(), name="cv_delete"),
    path("cvs/<int:pk>/entries/add/", views.CVAddItemsView.as_view(), name="cv_add_items"),
    path("cvs/<int:pk>/preview/", views.CVPreviewView.as_view(), name="cv_preview"),
    path("cvs/<int:pk>/export/", views.CVExportView.as_view(), name="cv_export"),
    path("cv-entries/<int:pk>/edit/", views.CVItemUpdateView.as_view(), name="cv_item_update"),
    path("cv-entries/<int:pk>/delete/", views.CVItemDeleteView.as_view(), name="cv_item_delete"),
    path(
        "cv-entries/<int:pk>/move/<str:direction>/",
        views.CVItemMoveView.as_view(),
        name="cv_item_move",
    ),
    path("letters/", views.CoverLetterListView.as_view(), name="letter_list"),
    path("letters/new/", views.CoverLetterCreateView.as_view(), name="letter_create"),
    path("letters/<int:pk>/", views.CoverLetterDetailView.as_view(), name="letter_detail"),
    path("letters/<int:pk>/edit/", views.CoverLetterUpdateView.as_view(), name="letter_update"),
    path("letters/<int:pk>/delete/", views.CoverLetterDeleteView.as_view(), name="letter_delete"),
    path(
        "letters/<int:pk>/preview/", views.CoverLetterPreviewView.as_view(), name="letter_preview"
    ),
    path("files/", views.UploadListView.as_view(), name="upload_list"),
    path("files/new/", views.UploadCreateView.as_view(), name="upload_create"),
    path("files/<int:pk>/edit/", views.UploadUpdateView.as_view(), name="upload_update"),
    path("files/<int:pk>/delete/", views.UploadDeleteView.as_view(), name="upload_delete"),
    path("files/<int:pk>/download/", views.UploadDownloadView.as_view(), name="upload_download"),
    path("sent/", views.RenderedListView.as_view(), name="rendered_list"),
    path("sent/<int:pk>/download/", views.RenderedDownloadView.as_view(), name="rendered_download"),
    path("applications/<int:pk>/send/", views.SendDocumentsView.as_view(), name="send"),
    path(
        "applications/<int:pk>/documents/",
        views.ApplicationDocumentsView.as_view(),
        name="application_documents",
    ),
]
