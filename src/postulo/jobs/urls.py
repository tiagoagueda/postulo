from django.urls import path, reverse_lazy
from django.views.generic import RedirectView

from . import capture_views, views

app_name = "jobs"

urlpatterns = [
    path("companies/", views.CompanyListView.as_view(), name="company_list"),
    path("companies/new/", views.CompanyCreateView.as_view(), name="company_create"),
    path("companies/<int:pk>/", views.CompanyDetailView.as_view(), name="company_detail"),
    path("companies/<int:pk>/edit/", views.CompanyUpdateView.as_view(), name="company_update"),
    path("companies/<int:pk>/delete/", views.CompanyDeleteView.as_view(), name="company_delete"),
    path("industries/", views.IndustryListView.as_view(), name="industry_list"),
    path("industries/new/", views.IndustryCreateView.as_view(), name="industry_create"),
    path("industries/<int:pk>/edit/", views.IndustryUpdateView.as_view(), name="industry_update"),
    path("industries/<int:pk>/delete/", views.IndustryDeleteView.as_view(), name="industry_delete"),
    path("contacts/new/", views.ContactCreateView.as_view(), name="contact_create"),
    path("contacts/<int:pk>/edit/", views.ContactUpdateView.as_view(), name="contact_update"),
    path("contacts/<int:pk>/delete/", views.ContactDeleteView.as_view(), name="contact_delete"),
    # Captures waiting for review now sit at the top of the listings page.
    path(
        "captures/",
        RedirectView.as_view(url=reverse_lazy("listings:list"), permanent=False),
        name="capture_list",
    ),
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
