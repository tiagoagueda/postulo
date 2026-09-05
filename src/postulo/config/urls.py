"""Root URL configuration."""

from django.conf import settings
from django.conf.urls.static import static
from django.contrib import admin
from django.urls import include, path

from postulo.api.api import api

urlpatterns = [
    path("", include("postulo.core.urls")),
    # Postulo's own account pages come first: Django resolves in order, so these
    # take precedence over any allauth route sharing a path.
    path("accounts/", include("postulo.accounts.urls")),
    path("accounts/", include("allauth.urls")),
    path("settings/", include("postulo.accounts.settings_urls")),
    path("applications/", include("postulo.applications.urls")),
    path("listings/", include("postulo.jobs.listing_urls")),
    path("jobs/", include("postulo.jobs.urls")),
    path("career/", include("postulo.resume.urls")),
    path("documents/", include("postulo.documents.urls")),
    path("capture-tokens/", include("postulo.api.urls")),
    # The capture API. Deliberately the only machine-readable surface Postulo has.
    path("api/v1/", api.urls),
    path(settings.POSTULO_ADMIN_URL, admin.site.urls),
]

if settings.DEBUG:
    urlpatterns += static(settings.STATIC_URL, document_root=settings.STATIC_ROOT)
