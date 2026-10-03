"""Django's admin, mounted only when asked for (#116) and with no login of its own (#367).

Postulo has its own *Server settings* — people, sign-in policy, plugins, email, logs,
defaults — so the admin is a developer's convenience rather than something the application
needs. On a self-hosted instance it is mostly a second, less careful way into the same data,
and it used to sit at ``/admin/`` on every instance whose operator had not read one line of
the settings file.

**It holds the instance, not the people on it (#368).** Accounts, invitations and the
metadata of API tokens are registered; nobody's applications, documents, contacts or career
record are, and ``tests/security/test_admin_exposure.py`` fails on a model that is. A
registration with no owner rule showed every member's job search to whoever held the
``is_superuser`` flag, and edits made there skipped ``change_status`` and the event log.
Appointing an administrator from *People* no longer sets that flag; it stays with the
operator's own account.

Two more things follow, and only the second one lives here.

**It is not mounted unless an operator says so.** ``POSTULO_ADMIN_URL`` is empty by default
and ``config/urls.py`` adds nothing when it is. Choosing to run the admin and choosing where
it lives are then the same decision, made once, on purpose.

**When it is mounted, nobody signs in to it.** ``django.contrib.admin`` has a login view of
its own, which checks a username and a password and makes the same session the rest of
Postulo uses. allauth is never involved, so nothing asked for the code from an authenticator
app, nothing asked whether the address had been confirmed, and an administrator with a
second factor was one password away from every table. Counting the attempts, which is what
this module did first, limits the guessing and does nothing about a password that is right.

So the login here is a signpost. Whoever is not signed in is sent to Postulo's sign-in,
which has every stage and every limit already, and comes back afterwards.
"""

from __future__ import annotations

from django.contrib.admin.apps import AdminConfig
from django.contrib.admin.sites import AdminSite
from django.contrib.auth import REDIRECT_FIELD_NAME
from django.contrib.auth.decorators import login_not_required
from django.core.exceptions import PermissionDenied
from django.http import HttpResponseRedirect
from django.urls import reverse
from django.utils.decorators import method_decorator
from django.utils.http import url_has_allowed_host_and_scheme
from django.views.decorators.cache import never_cache


class PostuloAdminSite(AdminSite):
    """The admin, reached through Postulo's own sign-in and no other way."""

    @method_decorator(never_cache)
    @login_not_required
    def login(self, request, extra_context=None):
        """Send the visitor where they can sign in, or on to where they were going.

        Django's view is never called, and not merely wrapped. allauth ships a decorator
        for wrapping it, which lets the view run for somebody already signed in as staff;
        and the view reads a POST from them, so an administrator holding a colleague's
        password would still become that colleague with no second factor asked for.
        """
        # Here and not at the top: this module is an app config's, read while the apps are
        # still loading, and the auth views bring the user model in with them.
        from django.contrib.auth.views import redirect_to_login

        wanted = request.POST.get(REDIRECT_FIELD_NAME) or request.GET.get(REDIRECT_FIELD_NAME)
        if not wanted or not url_has_allowed_host_and_scheme(
            wanted, allowed_hosts={request.get_host()}, require_https=request.is_secure()
        ):
            wanted = reverse("admin:index", current_app=self.name)

        if self.has_permission(request):
            return HttpResponseRedirect(wanted)
        if request.user.is_authenticated:
            # Signed in, and not staff. Django shows them its form to sign in as somebody
            # else; there is no form here, and signing in again would change nothing.
            raise PermissionDenied
        return redirect_to_login(wanted, reverse("account_login"))


class PostuloAdminConfig(AdminConfig):
    """Replaces ``django.contrib.admin`` in ``INSTALLED_APPS`` to install the site above."""

    default_site = "postulo.core.admin_site.PostuloAdminSite"
