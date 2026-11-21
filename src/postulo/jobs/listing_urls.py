from django.urls import path

from . import listing_views as views
from . import search_views

app_name = "listings"

urlpatterns = [
    path("", views.ListingListView.as_view(), name="list"),
    path("new/", views.ListingCreateView.as_view(), name="create"),
    path("bulk/", views.ListingBulkView.as_view(), name="bulk"),
    # Searching a job service, on a press, while Listing search is on (#1253).
    path("search/", search_views.ListingSearchView.as_view(), name="search"),
    path(
        "<int:pk>/cell/<slug:column>/",
        views.ListingCellView.as_view(),
        name="cell",
    ),
    path(
        "<int:pk>/shortlist/",
        views.ListingStateView.as_view(action="shortlist"),
        name="shortlist",
    ),
    path("<int:pk>/discard/", views.ListingStateView.as_view(action="discard"), name="discard"),
    path("<int:pk>/restore/", views.ListingStateView.as_view(action="restore"), name="restore"),
    path("<int:pk>/apply/", views.ListingApplyView.as_view(), name="apply"),
    # Adding to what arrived about it (#270). POST only, from the listing's own page.
    path("<int:pk>/history/", views.ListingEventCreateView.as_view(), name="event_create"),
]
