from django.urls import path

from . import views

app_name = "applications"

urlpatterns = [
    path("", views.ApplicationListView.as_view(), name="list"),
    path("board/", views.ApplicationBoardView.as_view(), name="board"),
    path("insights/", views.InsightsView.as_view(), name="insights"),
    path("new/", views.ApplicationCreateView.as_view(), name="create"),
    path("<int:pk>/", views.ApplicationDetailView.as_view(), name="detail"),
    path("<int:pk>/edit/", views.ApplicationUpdateView.as_view(), name="update"),
    path("<int:pk>/delete/", views.ApplicationDeleteView.as_view(), name="delete"),
    path("<int:pk>/status/", views.ApplicationStatusView.as_view(), name="status"),
    path("<int:pk>/events/new/", views.EventCreateView.as_view(), name="event_create"),
    path("reminders/", views.ReminderListView.as_view(), name="reminder_list"),
    path("reminders/new/", views.ReminderCreateView.as_view(), name="reminder_create"),
    path(
        "reminders/<int:pk>/done/", views.ReminderCompleteView.as_view(), name="reminder_complete"
    ),
    path("tags/", views.TagListView.as_view(), name="tag_list"),
    path("tags/new/", views.TagCreateView.as_view(), name="tag_create"),
    path("tags/<int:pk>/edit/", views.TagUpdateView.as_view(), name="tag_update"),
    path("tags/<int:pk>/delete/", views.TagDeleteView.as_view(), name="tag_delete"),
]
