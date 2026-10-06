from django.urls import path

from . import capture_views, page_views, vcard_views, views

app_name = "jobs"

urlpatterns = [
    path("companies/", views.CompanyListView.as_view(), name="company_list"),
    path("companies/map/", views.CompanyMapView.as_view(), name="company_map"),
    path("companies/bulk/", views.CompanyBulkView.as_view(), name="company_bulk"),
    path("companies/new/", views.CompanyCreateView.as_view(), name="company_create"),
    path("companies/<int:pk>/", views.CompanyDetailView.as_view(), name="company_detail"),
    path("companies/<int:pk>/edit/", views.CompanyUpdateView.as_view(), name="company_update"),
    path(
        "companies/<int:pk>/cell/<slug:column>/",
        views.CompanyCellView.as_view(),
        name="company_cell",
    ),
    path("companies/<int:pk>/logo/", views.CompanyLogoView.as_view(), name="company_logo"),
    path(
        "companies/<int:pk>/logo/<str:action>/",
        views.CompanyLogoActionView.as_view(),
        name="company_logo_action",
    ),
    path("companies/<int:pk>/delete/", views.CompanyDeleteView.as_view(), name="company_delete"),
    path("companies/<int:pk>/merge/", views.CompanyMergeView.as_view(), name="company_merge"),
    path("industries/", views.IndustryListView.as_view(), name="industry_list"),
    path("industries/new/", views.IndustryCreateView.as_view(), name="industry_create"),
    path("industries/<int:pk>/edit/", views.IndustryUpdateView.as_view(), name="industry_update"),
    path("industries/<int:pk>/delete/", views.IndustryDeleteView.as_view(), name="industry_delete"),
    path("contacts/new/", views.ContactCreateView.as_view(), name="contact_create"),
    path("contacts/<int:pk>/edit/", views.ContactUpdateView.as_view(), name="contact_update"),
    path("contacts/<int:pk>/delete/", views.ContactDeleteView.as_view(), name="contact_delete"),
    path("contacts/<int:pk>/export/", views.ContactExportView.as_view(), name="contact_export"),
    path("contacts/<int:pk>/merge/", views.ContactMergeView.as_view(), name="contact_merge"),
    # Contacts as vCard 4.0 (#660): the page that offers the files and reads one, and the
    # downloads. None takes an id but a record's own, and that is looked up for the owner.
    path("contacts/vcard/", vcard_views.ContactVCardsView.as_view(), name="contact_vcards"),
    path("contacts/all.vcf", vcard_views.AllContactsVCardView.as_view(), name="contacts_vcard"),
    path("contacts/me.vcf", vcard_views.OwnCardView.as_view(), name="own_vcard"),
    path(
        "contacts/<int:pk>/card.vcf", vcard_views.ContactVCardView.as_view(), name="contact_vcard"
    ),
    path(
        "companies/<int:pk>/card.vcf",
        vcard_views.CompanyVCardView.as_view(),
        name="company_vcard",
    ),
    path(
        "companies/<int:pk>/contacts.vcf",
        vcard_views.CompanyContactsVCardView.as_view(),
        name="company_contacts_vcard",
    ),
    # Every capture waiting, and the discarded ones with the way to put each back (#380).
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
    path(
        "captures/<int:pk>/restore/",
        capture_views.CaptureRestoreView.as_view(),
        name="capture_restore",
    ),
    path(
        "captures/<int:pk>/delete/",
        capture_views.CaptureDeleteView.as_view(),
        name="capture_delete",
    ),
    path(
        "captures/discard/",
        capture_views.CaptureDiscardSelectedView.as_view(),
        name="capture_discard_selected",
    ),
    # A second capture of an advert already in the listings, added to that listing's
    # history rather than made into another listing (#270). POST only, from the review.
    path(
        "captures/<int:pk>/bind/",
        capture_views.CaptureBindView.as_view(),
        name="capture_bind",
    ),
    # What a capture kept of the page it was read from (#256). The source has two
    # addresses and neither answers it as a page: one shows it as text, one downloads it
    # as text.
    path(
        "captures/<int:pk>/page/",
        page_views.CapturedPageView.as_view(),
        name="capture_page",
    ),
    path(
        "captures/<int:pk>/page/source/",
        page_views.CapturedSourceView.as_view(),
        name="capture_page_source",
    ),
    path(
        "captures/<int:pk>/page/rendering/",
        page_views.CapturedRenderingView.as_view(),
        name="capture_page_rendering",
    ),
    path(
        "captures/<int:pk>/page/draw/",
        page_views.CapturedPageDrawView.as_view(),
        name="capture_page_draw",
    ),
    path(
        "captures/<int:pk>/page/forget/",
        page_views.CapturedPageForgetView.as_view(),
        name="capture_page_forget",
    ),
    path("postings/<int:pk>/", views.PostingDetailView.as_view(), name="posting_detail"),
    path("postings/<int:pk>/edit/", views.PostingUpdateView.as_view(), name="posting_update"),
    path("postings/<int:pk>/delete/", views.PostingDeleteView.as_view(), name="posting_delete"),
]
