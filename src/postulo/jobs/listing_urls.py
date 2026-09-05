from django.urls import path

from . import listing_views as views

app_name = "listings"

urlpatterns = [
    path("", views.ListingListView.as_view(), name="list"),
    path("new/", views.ListingCreateView.as_view(), name="create"),
    path(
        "<int:pk>/shortlist/",
        views.ListingStateView.as_view(action="shortlist"),
        name="shortlist",
    ),
    path("<int:pk>/discard/", views.ListingStateView.as_view(action="discard"), name="discard"),
    path("<int:pk>/restore/", views.ListingStateView.as_view(action="restore"), name="restore"),
    path("<int:pk>/apply/", views.ListingApplyView.as_view(), name="apply"),
]
