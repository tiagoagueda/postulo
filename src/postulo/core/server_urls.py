from django.urls import path

from . import server_views as views

app_name = "server"

urlpatterns = [
    path("", views.ServerIndexView.as_view(), name="index"),
    path("overview/", views.OverviewView.as_view(), name="overview"),
    path("people/", views.PeopleView.as_view(), name="people"),
    path("people/<int:pk>/administrator/", views.PersonAdminView.as_view(), name="person_admin"),
    path("people/<int:pk>/active/", views.PersonActiveView.as_view(), name="person_active"),
    path("people/<int:pk>/username/", views.PersonUsernameView.as_view(), name="person_username"),
    path("people/<int:pk>/delete/", views.PersonDeleteView.as_view(), name="person_delete"),
    path("sign-in/", views.SignInView.as_view(), name="signin"),
    path("email/", views.EmailView.as_view(), name="email"),
    path("email/test/", views.EmailTestView.as_view(), name="email_test"),
    path("plugins/", views.PluginsView.as_view(), name="plugins"),
    path("plugins/action/", views.PluginActionView.as_view(), name="plugin_action"),
    path("capture/", views.CaptureView.as_view(), name="capture"),
    path("defaults/", views.DefaultsView.as_view(), name="defaults"),
]
