"""Root URL configuration."""

from django.conf import settings
from django.conf.urls.static import static
from django.contrib import admin
from django.urls import include, path

from postulo.api.api import urls as api_urls

# The 500 page needs the person's language, which Django's own view does not pass (#421).
handler500 = "postulo.core.views.server_error"

urlpatterns = [
    path("", include("postulo.core.urls")),
    # The service worker has to be served from the root to receive pushes for the whole site.
    path("", include("postulo.notifications.urls")),
    # Postulo's own account pages come first: Django resolves in order, so these
    # take precedence over any allauth route sharing a path.
    path("accounts/", include("postulo.accounts.urls")),
    path("accounts/", include("allauth.urls")),
    path("settings/connections/", include("postulo.plugins.urls")),
    path("settings/", include("postulo.accounts.settings_urls")),
    path("server/", include("postulo.core.server_urls")),
    path("applications/", include("postulo.applications.urls")),
    path("listings/", include("postulo.jobs.listing_urls")),
    path("jobs/", include("postulo.jobs.urls")),
    path("career/", include("postulo.resume.urls")),
    path("documents/", include("postulo.documents.urls")),
    path("capture-tokens/", include("postulo.api.urls")),
    # The capture API. Deliberately the only machine-readable surface Postulo has. Its
    # addresses come through `urls()`, which takes the slow calls out of the request's
    # transaction (#256).
    path("api/v1/", api_urls()),
]

# Only when an operator asked for it. Empty is the default, and an admin that is not mounted
# is the one thing nobody can brute-force (#116).
if settings.POSTULO_ADMIN_URL:
    urlpatterns.append(path(settings.POSTULO_ADMIN_URL, admin.site.urls))

if settings.DEBUG:
    urlpatterns += static(settings.STATIC_URL, document_root=settings.STATIC_ROOT)
