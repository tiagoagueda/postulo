from django.urls import path

from . import views

app_name = "resume"

urlpatterns = [
    path("", views.ResumeOverviewView.as_view(), name="overview"),
    path("preview/", views.ResumePreviewView.as_view(), name="preview"),
    path("<slug:section>/new/", views.ResumeItemCreateView.as_view(), name="item_create"),
    path("<slug:section>/<int:pk>/edit/", views.ResumeItemUpdateView.as_view(), name="item_update"),
    path(
        "<slug:section>/<int:pk>/delete/", views.ResumeItemDeleteView.as_view(), name="item_delete"
    ),
    path(
        "<slug:section>/<int:pk>/move/<str:direction>/",
        views.ResumeItemMoveView.as_view(),
        name="item_move",
    ),
]
