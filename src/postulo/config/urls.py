"""Root URL configuration."""

from django.conf import settings
from django.conf.urls.static import static
from django.contrib import admin
from django.urls import include, path

urlpatterns = [
    path("", include("postulo.core.urls")),
    # Postulo's own account pages come first: Django resolves in order, so these
    # take precedence over any allauth route sharing a path.
    path("accounts/", include("postulo.accounts.urls")),
    path("accounts/", include("allauth.urls")),
    path("applications/", include("postulo.applications.urls")),
    path("jobs/", include("postulo.jobs.urls")),
    path(settings.POSTULO_ADMIN_URL, admin.site.urls),
]

if settings.DEBUG:
    urlpatterns += static(settings.STATIC_URL, document_root=settings.STATIC_ROOT)
