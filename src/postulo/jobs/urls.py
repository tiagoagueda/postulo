from django.urls import path

from . import capture_views, views

app_name = "jobs"

urlpatterns = [
    path("companies/", views.CompanyListView.as_view(), name="company_list"),
    path("companies/new/", views.CompanyCreateView.as_view(), name="company_create"),
    path("companies/<int:pk>/", views.CompanyDetailView.as_view(), name="company_detail"),
    path("companies/<int:pk>/edit/", views.CompanyUpdateView.as_view(), name="company_update"),
    path("companies/<int:pk>/delete/", views.CompanyDeleteView.as_view(), name="company_delete"),
    path("contacts/new/", views.ContactCreateView.as_view(), name="contact_create"),
    path("contacts/<int:pk>/edit/", views.ContactUpdateView.as_view(), name="contact_update"),
    path("contacts/<int:pk>/delete/", views.ContactDeleteView.as_view(), name="contact_delete"),
    path("captures/", capture_views.CaptureListView.as_view(), name="capture_list"),
    path("captures/new/", capture_views.CaptureCreateView.as_view(), name="capture_create"),
    path(
        "captures/<int:pk>/review/",
        capture_views.CaptureReviewView.as_view(),
        name="capture_review",
    ),
    path(
        "captures/<int:pk>/discard/",
        capture_views.CaptureDiscardView.as_view(),
        name="capture_discard",
    ),
    path("postings/new/", views.PostingCreateView.as_view(), name="posting_create"),
    path("postings/<int:pk>/", views.PostingDetailView.as_view(), name="posting_detail"),
    path("postings/<int:pk>/edit/", views.PostingUpdateView.as_view(), name="posting_update"),
    path("postings/<int:pk>/delete/", views.PostingDeleteView.as_view(), name="posting_delete"),
]
