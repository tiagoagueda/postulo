"""Server settings: the instance, for administrators, in Postulo's own shell.

Policy an administrator can change — who may sign up, what new accounts start with, how
capture behaves — lives in the database and is edited here. Infrastructure stays in the
environment, and where an environment variable pins a policy value the page shows it
read-only and says so, so that a `.env` written for 0.1.0 goes on meaning what it meant.
"""

from __future__ import annotations

import platform
import secrets
import shutil
import sys
from pathlib import Path

import django
from django.conf import settings
from django.contrib import messages
from django.contrib.auth import get_user_model
from django.core.mail import send_mail
from django.db import connection
from django.http import HttpRequest, HttpResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse
from django.utils import timezone
from django.utils.translation import gettext_lazy as _
from django.views import View
from django.views.generic import RedirectView, TemplateView, UpdateView

from postulo import __version__
from postulo.accounts import deletion

from . import site
from .mixins import StaffRequiredMixin
from .models import SiteSettings
from .server_forms import CaptureForm, DefaultsForm, SignInForm, TestEmailForm


class ServerIndexView(StaffRequiredMixin, RedirectView):
    pattern_name = "server:overview"


class ServerSectionMixin(StaffRequiredMixin):
    section_title: str = ""

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        context["section_title"] = self.section_title
        return context


def _directory_size(root: Path) -> tuple[int, int]:
    files = 0
    size = 0
    if root.is_dir():
        for entry in root.rglob("*"):
            if entry.is_file():
                files += 1
                size += entry.stat().st_size
    return files, size


def _newest_backup(root: Path):
    if not root.is_dir():
        return None
    archives = sorted(root.glob("*.tar.gz"), key=lambda p: p.stat().st_mtime, reverse=True)
    if not archives:
        return None
    newest = archives[0]
    return {
        "path": newest,
        "age": timezone.now()
        - timezone.datetime.fromtimestamp(
            newest.stat().st_mtime, tz=timezone.get_current_timezone()
        ),
    }


def _pdf_backend_name() -> str | None:
    from postulo.documents.pdf import PDFBackendUnavailable, get_pdf_backend

    try:
        return get_pdf_backend().name
    except PDFBackendUnavailable:
        return None


def _queued_tasks() -> int | None:
    try:
        from django_tasks_db.models import DBTaskResult
    except Exception:
        return None
    try:
        return DBTaskResult.objects.filter(status="READY").count()
    except Exception:
        return None


class OverviewView(ServerSectionMixin, TemplateView):
    template_name = "server/overview.html"
    section_title = _("Overview")

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        media_root = Path(settings.MEDIA_ROOT)
        media_files, media_bytes = _directory_size(media_root)
        database = settings.DATABASES["default"]
        context.update(
            {
                "version": __version__,
                "python_version": platform.python_version(),
                "django_version": django.get_version(),
                "database_engine": connection.vendor,
                "database_name": str(database.get("NAME", "")),
                "pdf_backend": _pdf_backend_name(),
                "media_root": media_root,
                "media_files": media_files,
                "media_bytes": media_bytes,
                "backup_dir": Path(settings.POSTULO_BACKUP_DIR),
                "newest_backup": _newest_backup(Path(settings.POSTULO_BACKUP_DIR)),
                "queued_tasks": _queued_tasks(),
                "admin_url": reverse("admin:index"),
                "health_url": reverse("core:healthz"),
                "platform": platform.platform(),
                "executable": sys.executable,
            }
        )
        return context


class PeopleView(ServerSectionMixin, TemplateView):
    template_name = "server/people.html"
    section_title = _("People")

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        User = get_user_model()
        people = User.objects.order_by("username")
        context["people"] = people
        context["administrators"] = people.filter(is_staff=True, is_active=True).count()
        return context


def _last_administrator(user) -> bool:
    """Whether ``user`` is the only active administrator left."""
    return deletion.is_last_administrator(user)


class PersonAdminView(StaffRequiredMixin, View):
    """Make somebody an administrator, or stop them being one. Never the last one."""

    def post(self, request: HttpRequest, pk: int) -> HttpResponse:
        person = get_object_or_404(get_user_model(), pk=pk)
        if person.is_staff:
            if _last_administrator(person):
                messages.error(request, _("That is the last administrator. Appoint another first."))
            else:
                person.is_staff = False
                person.is_superuser = False
                person.save(update_fields=["is_staff", "is_superuser"])
                messages.success(
                    request, _("%(name)s is no longer an administrator.") % {"name": person}
                )
        else:
            person.is_staff = True
            person.is_superuser = True
            person.save(update_fields=["is_staff", "is_superuser"])
            messages.success(request, _("%(name)s is now an administrator.") % {"name": person})
        return redirect("server:people")


class PersonActiveView(StaffRequiredMixin, View):
    """Deactivate an account — it keeps its data, cannot sign in — or reactivate it."""

    def post(self, request: HttpRequest, pk: int) -> HttpResponse:
        person = get_object_or_404(get_user_model(), pk=pk)
        if person == request.user:
            messages.error(request, _("You cannot deactivate the account you are signed in with."))
        elif person.is_active:
            if _last_administrator(person):
                messages.error(request, _("That is the last administrator. Appoint another first."))
            else:
                person.is_active = False
                person.save(update_fields=["is_active"])
                messages.success(
                    request,
                    _("%(name)s is deactivated: nothing deleted, no sign-in.") % {"name": person},
                )
        else:
            person.is_active = True
            person.save(update_fields=["is_active"])
            messages.success(request, _("%(name)s can sign in again.") % {"name": person})
        return redirect("server:people")


class PersonDeleteView(StaffRequiredMixin, View):
    """Delete somebody's account, files included, on their behalf or after they left.

    The same service as the person's own *Delete my account*, so nothing is left behind
    either way. Not oneself (that path is under Settings, with reauthentication), and
    never the last administrator.
    """

    template_name = "server/person_delete.html"

    def _blocked(self, request: HttpRequest, person) -> str:
        if person == request.user:
            return str(_("Delete your own account from Settings, not from here."))
        if deletion.is_last_administrator(person):
            return str(_("That is the last administrator. Appoint another first."))
        return ""

    def get(self, request: HttpRequest, pk: int) -> HttpResponse:
        person = get_object_or_404(get_user_model(), pk=pk)
        return render(
            request,
            self.template_name,
            {
                "section_title": _("People"),
                "person": person,
                "blocked": self._blocked(request, person),
            },
        )

    def post(self, request: HttpRequest, pk: int) -> HttpResponse:
        person = get_object_or_404(get_user_model(), pk=pk)
        blocked = self._blocked(request, person)
        if blocked:
            messages.error(request, blocked)
            return redirect("server:people")
        if request.POST.get("confirm_username", "").strip().casefold() != person.username:
            return render(
                request,
                self.template_name,
                {
                    "section_title": _("People"),
                    "person": person,
                    "blocked": "",
                    "error": _("That is not the username."),
                },
            )
        report = deletion.delete_account(person)
        messages.success(
            request,
            _("%(name)s is gone: the account, its records and %(files)s files on disk.")
            % {"name": report.username, "files": report.files_removed},
        )
        return redirect("server:people")


class PersonUsernameView(StaffRequiredMixin, UpdateView):
    """Change somebody's username on their behalf.

    The person can do it themselves under Settings > Account; this is the same form, with
    the same rules, for the administrator who is asked to. A username is unique across the
    instance whoever changes it: the model refuses a duplicate, and the form refuses one
    first, in words, whatever the capitals.
    """

    template_name = "server/person_username.html"
    section_title = _("People")

    def get_queryset(self):
        return get_user_model().objects.all()

    def get_form_class(self):
        from postulo.accounts.forms import AccountForm

        return AccountForm

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        context["section_title"] = self.section_title
        context["person"] = self.object
        return context

    def form_valid(self, form):
        response = super().form_valid(form)
        messages.success(
            self.request,
            _("%(name)s is now @%(username)s.")
            % {
                "name": self.object.get_full_name() or self.object.email,
                "username": self.object.username,
            },
        )
        return response

    def get_success_url(self) -> str:
        return reverse("server:people")


class PolicyView(ServerSectionMixin, UpdateView):
    """One form over the policy row, plus which of its fields the environment pins."""

    model = SiteSettings
    pinned_fields: tuple[str, ...] = ()

    def get_object(self, queryset=None) -> SiteSettings:
        return SiteSettings.get()

    def get_success_url(self) -> str:
        return self.request.path

    def form_valid(self, form):
        form.instance.updated_by = self.request.user
        messages.success(self.request, _("Saved."))
        return super().form_valid(form)

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        context["pinned"] = {
            field: site.overridden_by(field)
            for field in self.pinned_fields
            if site.overridden_by(field)
        }
        return context


class SignInView(PolicyView):
    form_class = SignInForm
    template_name = "server/signin.html"
    section_title = _("Sign-in")
    pinned_fields = ("registration_open",)

    def get_context_data(self, **kwargs):
        from postulo.accounts import sso

        context = super().get_context_data(**kwargs)
        context["effective_registration_open"] = site.registration_open()
        context["is_empty"] = site.is_empty()
        context["sso"] = {
            "enabled": sso.enabled(),
            "name": sso.name(),
            "server_url": sso.server_url(),
            "auto_signup": sso.auto_signup(),
            "callback_url": sso.callback_url(self.request) if sso.enabled() else "",
        }
        return context


class CaptureView(PolicyView):
    form_class = CaptureForm
    template_name = "server/capture.html"
    section_title = _("Capture")
    pinned_fields = ("capture_ignore_robots",)

    def get_context_data(self, **kwargs):
        from postulo.plugins import fetching

        context = super().get_context_data(**kwargs)
        context["effective_ignore_robots"] = site.capture_ignore_robots()
        context["limits"] = {
            "max_bytes": fetching.MAX_BYTES,
            "timeout_seconds": fetching.TIMEOUT_SECONDS,
            "max_redirects": fetching.MAX_REDIRECTS,
            "user_agent": fetching.USER_AGENT,
        }
        return context


class DefaultsView(PolicyView):
    form_class = DefaultsForm
    template_name = "server/defaults.html"
    section_title = _("Defaults")
    pinned_fields = ("default_time_zone",)

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        context["effective_time_zone"] = site.default_time_zone()
        context["effective_language"] = site.default_language()
        return context


def _mailer_summary() -> dict:
    mailer = (getattr(settings, "MAILERS", {}) or {}).get("default", {})
    options = mailer.get("OPTIONS", {}) or {}
    backend = str(mailer.get("BACKEND", ""))
    return {
        "backend": ".".join(backend.split(".")[-2:]) or backend,
        "is_smtp": backend.endswith("smtp.EmailBackend"),
        "host": options.get("host", ""),
        "port": options.get("port", ""),
        "username": options.get("username", ""),
        "use_tls": options.get("use_tls"),
        "from_address": settings.DEFAULT_FROM_EMAIL,
    }


class EmailView(ServerSectionMixin, TemplateView):
    template_name = "server/email.html"
    section_title = _("Email")

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        context["mailer"] = _mailer_summary()
        context["form"] = kwargs.get("form") or TestEmailForm(
            initial={"to": self.request.user.email}
        )
        return context


class EmailTestView(StaffRequiredMixin, View):
    """Send one message, so a configuration can be proven before anyone depends on it."""

    def post(self, request: HttpRequest) -> HttpResponse:
        form = TestEmailForm(request.POST)
        if not form.is_valid():
            view = EmailView()
            view.request = request
            return render(request, EmailView.template_name, view.get_context_data(form=form))
        to = form.cleaned_data["to"]
        try:
            sent = send_mail(
                subject=str(_("A test message from %(name)s") % {"name": site.instance_name()}),
                message=str(
                    _(
                        "If you are reading this, the email settings of your Postulo "
                        "instance work. Nothing else to do."
                    )
                ),
                from_email=None,
                recipient_list=[to],
                fail_silently=False,
            )
        except Exception as error:
            messages.error(request, _("Sending failed: %(error)s") % {"error": error})
        else:
            if sent:
                messages.success(request, _("Sent to %(to)s.") % {"to": to})
            else:
                messages.error(request, _("The mail backend accepted nothing."))
        return redirect("server:email")


#: Where a wheel waits between "read it" and "install it". Under the plugins directory,
#: which is on the data volume and writable; cleaned out as soon as it is used.
PENDING_DIRNAME = ".pending"


def _pending_dir() -> Path:
    from postulo.plugins.installing import plugins_dir

    return plugins_dir() / PENDING_DIRNAME


class PluginsView(ServerSectionMixin, TemplateView):
    """What is installed, what can be, and the plain warning about what installing means."""

    template_name = "server/plugins.html"
    section_title = _("Plugins")

    def get_context_data(self, **kwargs):
        from postulo.plugins import catalogue, installing
        from postulo.plugins.registry import ENTRY_POINT_GROUP, available_sources

        context = super().get_context_data(**kwargs)
        context["sources"] = [
            {
                "name": source.name,
                "version": getattr(source, "version", ""),
                "module": type(source).__module__,
                "builtin": type(source).__module__.startswith("postulo."),
            }
            for source in available_sources(refresh=True)
        ]
        context["entry_point_group"] = ENTRY_POINT_GROUP
        context["installed"] = installing.status()
        context["plugins_dir"] = str(installing.plugins_dir())
        context["catalogues_configured"] = sorted(catalogue.configured())
        context["pending"] = self.request.session.get("plugin_pending")
        context["listings"] = self.request.session.get("plugin_listings", [])
        return context


class PluginActionView(StaffRequiredMixin, View):
    """Upload, confirm, install from a catalogue, switch off, remove.

    Uploading only *reads* the package: what it says about itself is shown for
    confirmation, and the wheel waits in a scratch directory until an administrator says
    yes. Nothing about that is decorative — the confirmation is where a person sees the
    entry points, the licence and the dependencies before somebody else's code runs.
    """

    def post(self, request: HttpRequest) -> HttpResponse:
        action = request.POST.get("action", "")
        handler = getattr(self, f"_{action}", None)
        if handler is None:
            messages.error(request, _("That is not something this page does."))
            return redirect("server:plugins")
        return handler(request)

    # ------------------------------------------------------------- upload

    def _upload(self, request: HttpRequest) -> HttpResponse:
        from postulo.plugins.installing import InstallError, check, read_wheel

        upload = request.FILES.get("package")
        if upload is None:
            messages.error(request, _("Choose a package first."))
            return redirect("server:plugins")

        scratch = _pending_dir()
        shutil.rmtree(scratch, ignore_errors=True)
        scratch.mkdir(parents=True, exist_ok=True)
        token = secrets.token_urlsafe(16)
        target = scratch / f"{token}.whl"
        with target.open("wb") as handle:
            for chunk in upload.chunks():
                handle.write(chunk)

        try:
            info = read_wheel(target)
            check(info)
        except InstallError as error:
            target.unlink(missing_ok=True)
            messages.error(request, str(error))
            return redirect("server:plugins")

        request.session["plugin_pending"] = {
            "token": token,
            "name": info.name,
            "version": info.version,
            "summary": info.summary,
            "licence": info.licence,
            "author": info.author,
            "home_page": info.home_page,
            "requires": info.requires,
            "entry_points": info.entry_points,
            "sha256": info.sha256,
            "filename": upload.name,
        }
        return redirect("server:plugins")

    def _cancel(self, request: HttpRequest) -> HttpResponse:
        request.session.pop("plugin_pending", None)
        shutil.rmtree(_pending_dir(), ignore_errors=True)
        messages.info(request, _("Nothing was installed."))
        return redirect("server:plugins")

    def _confirm(self, request: HttpRequest) -> HttpResponse:
        from postulo.plugins.installing import InstallError, install_wheel

        pending = request.session.get("plugin_pending") or {}
        token = request.POST.get("token", "")
        if not pending or token != pending.get("token"):
            messages.error(request, _("That package is no longer waiting; upload it again."))
            return redirect("server:plugins")
        wheel = _pending_dir() / f"{token}.whl"
        if not wheel.is_file():
            request.session.pop("plugin_pending", None)
            messages.error(request, _("That package is no longer waiting; upload it again."))
            return redirect("server:plugins")
        try:
            entry = install_wheel(
                wheel,
                origin="upload",
                source=pending.get("filename", ""),
                by=request.user.get_username(),
            )
        except InstallError as error:
            messages.error(request, str(error))
            return redirect("server:plugins")
        finally:
            request.session.pop("plugin_pending", None)
            shutil.rmtree(_pending_dir(), ignore_errors=True)
        messages.success(
            request,
            _(
                "%(name)s %(version)s is installed. Restart Postulo if it adds pages of "
                "its own; anything else is available now."
            )
            % {"name": entry.name, "version": entry.version},
        )
        return redirect("server:plugins")

    # ---------------------------------------------------------- catalogue

    def _refresh(self, request: HttpRequest) -> HttpResponse:
        from postulo.plugins import catalogue, installing

        catalogues, problems = catalogue.fetch_all()
        for problem in problems:
            messages.error(request, problem)
        listings = []
        for one in catalogues:
            for listing in one.listings:
                release = listing.latest
                if release is None:
                    continue
                listings.append(
                    {
                        "name": listing.name,
                        "version": release.version,
                        "summary": listing.summary,
                        "licence": listing.licence,
                        "maintainer": listing.maintainer,
                        "catalogue": one.name,
                        "installed": installing.installed(listing.name) is not None,
                    }
                )
        request.session["plugin_listings"] = listings
        if catalogues and not listings:
            messages.info(request, _("The catalogues answered, and list nothing yet."))
        return redirect("server:plugins")

    def _install(self, request: HttpRequest) -> HttpResponse:
        from postulo.plugins import catalogue
        from postulo.plugins.installing import InstallError

        name = request.POST.get("name", "")
        try:
            entry = catalogue.install(name, by=request.user.get_username())
        except (catalogue.CatalogueError, InstallError) as error:
            messages.error(request, str(error))
            return redirect("server:plugins")
        messages.success(
            request,
            _("%(name)s %(version)s is installed from the catalogue.")
            % {"name": entry.name, "version": entry.version},
        )
        return redirect("server:plugins")

    # ------------------------------------------------- switching, removing

    def _disable(self, request: HttpRequest) -> HttpResponse:
        return self._switch(request, True)

    def _enable(self, request: HttpRequest) -> HttpResponse:
        return self._switch(request, False)

    def _switch(self, request: HttpRequest, disabled: bool) -> HttpResponse:
        from postulo.plugins.installing import InstallError, set_disabled
        from postulo.plugins.registry import plugins as registry_plugins

        try:
            entry = set_disabled(request.POST.get("name", ""), disabled)
        except InstallError as error:
            messages.error(request, str(error))
            return redirect("server:plugins")
        for kind in ("source", "notifier", "store", "sync"):
            registry_plugins(kind, refresh=True)
        messages.success(
            request,
            _("%(name)s is switched off; its files are still here.") % {"name": entry.name}
            if disabled
            else _("%(name)s is switched on again.") % {"name": entry.name},
        )
        return redirect("server:plugins")

    def _remove(self, request: HttpRequest) -> HttpResponse:
        from postulo.plugins.installing import InstallError, remove

        try:
            entry = remove(request.POST.get("name", ""))
        except InstallError as error:
            messages.error(request, str(error))
            return redirect("server:plugins")
        messages.success(
            request,
            _("%(name)s is removed. Restart Postulo to be sure nothing of it is left loaded.")
            % {"name": entry.name},
        )
        return redirect("server:plugins")
