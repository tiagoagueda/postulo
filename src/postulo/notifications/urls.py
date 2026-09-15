from django.urls import path

from . import views

app_name = "notifications"

urlpatterns = [
    path("sw.js", views.worker, name="worker"),
    path("notifications/waiting/", views.waiting, name="waiting"),
]
