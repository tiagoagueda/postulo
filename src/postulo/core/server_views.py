"""Server settings: the instance, for administrators, in Postulo's own shell.

Policy an administrator can change — who may sign up, what new accounts start with, how
capture behaves — lives in the database and is edited here. Infrastructure stays in the
environment, and where an environment variable pins a policy value the page shows it
read-only and says so, so that a `.env` written for 0.1.0 goes on meaning what it meant.
"""

from __future__ import annotations

import logging
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
from postulo.plugins.forms import PluginRepositoryForm

from . import site
from .mixins import StaffRequiredMixin
from .models import SiteSettings
from .server_forms import (
    CaptureForm,
    DefaultsForm,
    EmailForm,
    OfferedLanguagesForm,
    SignInForm,
    TestEmailForm,
)

logger = logging.getLogger(__name__)


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
    # Both, because the page needs both and they are not interchangeable: `timesince`
    # takes the moment and works out the words, while "older than a week" is a question
    # about the span. Handing the span to `timesince` is what broke this page.
    made_at = timezone.datetime.fromtimestamp(
        newest.stat().st_mtime, tz=timezone.get_current_timezone()
    )
    return {"path": newest, "made_at": made_at, "age": timezone.now() - made_at}


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
                "admin_url": _admin_url(),
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


class PersonRecoveryView(StaffRequiredMixin, View):
    """Issue somebody a single-use way back into their account (#103).

    The route that needs no third party: an administrator makes a link and hands it over by
    whatever means they already trust. That is the whole design, and its smallness is the
    point — a family or small-team instance can now switch mail off, which was impossible
    while email was the only way back in.

    **The link is shown once, here, and nowhere else.** It is not mailed, not logged and not
    displayed again on a later visit: handing it over is the administrator's job, and doing
    it over the channel this exists to replace would be absurd. What survives is the row —
    who issued it, for whom, when, and what became of it.
    """

    def get(self, request: HttpRequest, pk: int) -> HttpResponse:
        return render(request, "server/person_recovery.html", self._context(request, pk))

    def post(self, request: HttpRequest, pk: int) -> HttpResponse:
        from postulo.accounts import recovery
        from postulo.core import throttle

        person = get_object_or_404(get_user_model(), pk=pk)
        if request.POST.get("revoke"):
            return self._revoke(request, person)

        try:
            throttle.consume(
                "recovery-link", request.user, throttle.rate_for("POSTULO_RECOVERY_RATE")
            )
        except throttle.TooOften:
            messages.error(
                request,
                _("That is a lot of recovery links. Try again shortly."),
            )
            return redirect("server:person_recovery", pk=person.pk)

        _link, token = recovery.issue(person, by=request.user)
        context = self._context(request, person.pk)
        # Handed to the template rather than to a message, because a message would survive
        # into the next page and the next request. This exists for exactly one render.
        context["token_url"] = request.build_absolute_uri(
            reverse("accounts:recovery_open", args=[token])
        )
        return render(request, "server/person_recovery.html", context)

    def _revoke(self, request: HttpRequest, person) -> HttpResponse:
        from django.utils import timezone

        from postulo.accounts.models import RecoveryLink

        stopped = (
            RecoveryLink.objects.live().filter(person=person).update(revoked_at=timezone.now())
        )
        if stopped:
            messages.success(request, _("That link will not work any more."))
        return redirect("server:person_recovery", pk=person.pk)

    def _context(self, request: HttpRequest, pk: int) -> dict:
        from postulo.accounts.models import RecoveryLink

        person = get_object_or_404(get_user_model(), pk=pk)
        return {
            "person": person,
            "section_title": _("People"),
            "links": RecoveryLink.objects.filter(person=person)[:10],
            "lifetime_minutes": int(RecoveryLink.LIFETIME.total_seconds() // 60),
            "is_yourself": person.pk == request.user.pk,
        }


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
    pinned_fields = ("registration_open", "sso_is_second_factor")

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
            "link_by_email": sso.link_by_email(),
            "callback_url": sso.callback_url(self.request) if sso.enabled() else "",
        }
        return context


class LogsView(ServerSectionMixin, TemplateView):
    """What the instance has been saying, newest first.

    Reading the log used to mean `docker logs` and a shell. The person administering a
    Postulo instance is usually the person using it, and is quite often on a phone when
    something stops working.
    """

    template_name = "server/logs.html"
    section_title = _("Logs")

    def get_context_data(self, **kwargs):
        from . import logs, metrics, views_logs

        context = super().get_context_data(**kwargs)
        level = self.request.GET.get("level", "")
        logger = self.request.GET.get("logger", "")
        search = self.request.GET.get("q", "")

        context.update(
            {
                "available": logs.available(),
                "records": logs.read(limit=200, level=level, logger=logger, search=search),
                "levels": logs.LEVELS,
                "loggers": logs.loggers(),
                "level": level,
                "logger": logger,
                "search": search,
                "size": logs.size_on_disk(),
                "path": logs.log_path(),
                "filtering": bool(level or logger or search),
                "endpoint_enabled": views_logs.enabled(),
                "endpoint_has_token": bool(views_logs.token()),
                "metrics_enabled": metrics.enabled(),
                "metrics_has_token": bool(metrics.token()),
            }
        )
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
        context.setdefault("languages_form", self.languages_form())
        context["offers_everything"] = not site.offered_languages()
        return context

    def languages_form(self, data=None) -> OfferedLanguagesForm:
        return OfferedLanguagesForm(data=data, instance=site.current())

    def post(self, request, *args, **kwargs):
        """Two forms, one page, saved separately.

        Together they would mean an administrator changing the instance name had to satisfy
        the language rules to do it — and the language list is thirty-nine checkboxes that
        would be re-posted on every unrelated save.
        """
        if "offered_languages_submit" not in request.POST:
            return super().post(request, *args, **kwargs)

        form = self.languages_form(data=request.POST)
        if not form.is_valid():
            self.object = None
            return self.render_to_response(self.get_context_data(languages_form=form))
        form.save()
        messages.success(request, _("Saved."))
        return redirect("server:defaults")


def _admin_url() -> str:
    """Where Django's admin is, or nothing when it is not mounted.

    Empty is the ordinary case now: the admin is off unless an operator asked for it (#116).
    Reversing a route that does not exist raises, so this answers rather than letting the
    Overview page fail because of a link at the bottom of it.
    """
    from django.urls import NoReverseMatch

    if not settings.POSTULO_ADMIN_URL:
        return ""
    try:
        return reverse("admin:index")
    except NoReverseMatch:  # pragma: no cover - mounted but unreversible is not a state
        return ""


def _security_label(value: str) -> str:
    from .mail import MailSecurity

    try:
        return str(MailSecurity(value).label)
    except ValueError:
        return value or str(_("Not set"))


def _mailer_summary() -> dict:
    """What is in force, resolved rather than read out of MAILERS.

    MAILERS no longer carries the values -- they are looked up per send, which is what lets
    the page below change them -- so a summary built from it would describe the shape of
    the configuration and none of its content.
    """
    from postulo.notifications import transport as transports

    backend = str(((getattr(settings, "MAILERS", {}) or {}).get("default", {})).get("BACKEND", ""))
    chosen = transports.selected()
    pluggable = backend.endswith("PluggableBackend")
    resolved = site.email_settings()
    return {
        # With a pluggable backend the class is always the same and says nothing, so the
        # line names the transport that is actually carrying the mail.
        "backend": (
            getattr(chosen, "label", chosen.name)
            if pluggable and chosen
            else ".".join(backend.split(".")[-2:]) or backend
        ),
        "is_smtp": (
            (chosen is not None and chosen.name == transports.DEFAULT_TRANSPORT)
            if pluggable
            else backend.endswith("smtp.EmailBackend")
        ),
        # Whether a transport is carrying anything at all. In development it is not: the
        # console backend is named directly, and saying "carried by SMTP" beside a line
        # reading `locmem.EmailBackend` would be two contradictory sentences.
        "pluggable": pluggable,
        "host": resolved["host"],
        "port": resolved["port"],
        "username": resolved["username"],
        "security": resolved["security"],
        # Named rather than shown as a code, because "ssl" on a settings page is a word
        # somebody has to look up and "TLS from the first byte" is the answer they wanted.
        "security_label": _security_label(str(resolved["security"])),
        "has_password": bool(resolved["password"]),
        "from_address": resolved["from_address"],
    }


class EmailView(PolicyView):
    """The email settings, and the two ways of proving them."""

    form_class = EmailForm
    template_name = "server/email.html"
    section_title = _("Email")
    pinned_fields = tuple(site.EMAIL_FIELDS)

    def get_context_data(self, **kwargs):
        from postulo.notifications import transport
        from postulo.plugins import base as plugin_base

        context = super().get_context_data(**kwargs)
        context["mailer"] = _mailer_summary()
        context["test_form"] = kwargs.get("test_form") or TestEmailForm(
            initial={"to": self.request.user.email}
        )
        context["shadowed"] = site.email_shadowed()
        row = SiteSettings.get()
        context["has_password"] = row.has_email_password
        context["has_client_secret"] = row.has_email_client_secret
        context["has_consent"] = row.has_email_consent
        # The address to register with the provider, shown rather than described: a wrong
        # one ends a consent screen in an error nobody can read (#148).
        from postulo.plugins import consent

        context["consent_callback"] = consent.callback_url(self.request)
        from . import mail_auth

        resolved = site.email_settings()
        context["oauth_fields"] = [
            "email_auth",
            "email_oauth_provider",
            "email_oauth_grant",
            "email_oauth_tenant",
            "email_oauth_client_id",
            "email_oauth_client_secret",
        ]
        context["uses_mailbox_grant"] = (
            resolved["auth"] == mail_auth.MailAuth.XOAUTH2
            and (resolved["oauth_grant"] or mail_auth.MailGrant.MAILBOX)
            == mail_auth.MailGrant.MAILBOX
        )
        context["mail_health"] = site.mail_health()
        # Mail failing matters at all times; it matters *urgently* when mail is the only
        # way back into an account, because then nobody who forgets a password can get in
        # until it is fixed. Counted here so the page can say how many people that is.
        context["stranded_by_broken_mail"] = (
            transport.accounts_needing_email() if not context["mail_health"]["delivers"] else 0
        )

        chosen = transport.selected()
        context["transports"] = transport.available()
        context["transport"] = chosen
        context["manifest"] = plugin_base.manifest_of(chosen) if chosen else None
        context["transport_is_smtp"] = (
            chosen is not None and chosen.name == transport.DEFAULT_TRANSPORT
        )
        # Said on the page rather than discovered at the moment somebody tries: this is why
        # the plugins page will refuse to switch the package off (#104).
        context["locked"] = (
            transport.refuse_switching_off(chosen.name)
            if chosen is not None and context["mailer"]["pluggable"]
            else ""
        )
        # The other half of the answer, and the one #103 said the lock has to be explicit
        # about: getting existing people back in and admitting new ones are different
        # questions. A recovery link answers the first; nothing answers the second, because
        # a new account has to verify an address before it exists.
        context["signup_needs_mail"] = not context["locked"] and site.registration_open()
        return context


class EmailConnectionTestView(StaffRequiredMixin, View):
    """Open a connection with what is on screen, and hang up without sending anything.

    On screen, not in the database, so a configuration can be tried before it replaces one
    that works. The password is the exception it has to be: it is never rendered, so a blank
    one means the stored one, exactly as saving does.

    A failure does not stop anybody saving. An administrator may be configuring a relay that
    is not up yet, and refusing to record what somebody typed because a machine elsewhere is
    down is rarely the right answer.
    """

    def post(self, request: HttpRequest) -> HttpResponse:
        from . import mail

        row = SiteSettings.get()
        resolved = site.email_settings()
        pinned = {f: v for f in site.EMAIL_FIELDS if (v := site.overridden_by(f))}

        def value(field: str, cast=str):
            key = site.EMAIL_FIELDS[field]
            if field in pinned:
                return resolved[key]
            raw = request.POST.get(field, "").strip()
            if raw == "":
                return resolved[key]
            try:
                return cast(raw)
            except (TypeError, ValueError):
                return resolved[key]

        typed = request.POST.get("email_password", "")
        if "email_password" in pinned:
            password = resolved["password"]
        elif typed:
            password = typed
        elif request.POST.get("forget_email_password"):
            password = ""
        else:
            password = row.email_password if row.has_email_password else resolved["password"]

        typed_security = request.POST.get("email_security", "")
        if "email_security" in pinned or typed_security == "":
            security = str(resolved["security"])
        else:
            security = typed_security

        from . import mail_auth

        # XOAUTH2 takes what is on screen for everything an operator types, and the stored
        # grant for the token -- the grant is the one thing a form cannot hold, because it
        # was issued by a provider after somebody agreed (#151).
        oauth = {
            key: value(field)
            for field, key in site.EMAIL_FIELDS.items()
            if key.startswith("oauth_") and field not in site.EMAIL_SECRET_FIELDS
        }
        typed_secret = request.POST.get("email_oauth_client_secret", "")
        oauth["oauth_client_secret"] = (
            typed_secret
            if typed_secret and "email_oauth_client_secret" not in pinned
            else resolved["oauth_client_secret"]
        )
        auth = str(value("email_auth"))

        try:
            token = mail_auth.instance_token(oauth) if auth == mail_auth.MailAuth.XOAUTH2 else ""
            report = mail.check_connection(
                host=str(value("email_host")),
                port=int(value("email_port", int)),
                username=str(value("email_username")),
                password=password,
                security=security,
                timeout=int(value("email_timeout", int)),
                token=token,
            )
        except (mail.ConnectionFailed, mail_auth.TokenUnavailable) as error:
            messages.error(request, _("No connection: %(why)s") % {"why": error})
        else:
            messages.success(request, report)
        return redirect("server:email")


class EmailConsentView(StaffRequiredMixin, View):
    """Sign in to the mail provider once, as the mailbox this instance sends from (#151).

    A POST rather than a link, because it starts a round trip that ends in a stored
    credential -- and this one sends the password resets.
    """

    def post(self, request: HttpRequest) -> HttpResponse:
        from . import mail_auth

        if request.POST.get("forget"):
            mail_auth.forget_consent()
            messages.success(
                request,
                _(
                    "Postulo has forgotten the grant. The provider still has it until you "
                    "withdraw it there as well."
                ),
            )
            return redirect("server:email")
        try:
            where = mail_auth.start_consent(request)
        except mail_auth.TokenUnavailable as error:
            messages.error(request, str(error))
            return redirect("server:email")
        return redirect(where)


class EmailTestView(StaffRequiredMixin, View):
    """Send one message, so a configuration can be proven before anyone depends on it."""

    def post(self, request: HttpRequest) -> HttpResponse:
        form = TestEmailForm(request.POST)
        if not form.is_valid():
            view = EmailView()
            view.request = request
            view.object = SiteSettings.get()
            context = view.get_context_data(form=EmailForm(instance=view.object), test_form=form)
            return render(request, EmailView.template_name, context)
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
        context["repositories"] = _repositories()
        context["repository_form"] = kwargs.get("repository_form") or PluginRepositoryForm()
        context["policy_rows"] = _policy_rows()
        from postulo.plugins.models import PluginPolicy

        context["policy_states"] = PluginPolicy.State.choices
        context["pending"] = self.request.session.get("plugin_pending")
        context["listings"] = self.request.session.get("plugin_listings", [])
        return context


def _repositories() -> list[dict]:
    """Every repository the page shows, internal first, with what may be done to each.

    The internal one is synthesised. The plugins that ship inside Postulo are not fetched
    from anywhere and there is no address to store, so a row would be a fact about a place
    that does not exist. It appears here so the list reads as one thing rather than two.
    """
    from postulo.plugins import catalogue
    from postulo.plugins.models import PluginRepository

    pinned = catalogue.pinned_names()
    rows = [
        {
            "name": "internal",
            "tier": "internal",
            "label": _("Internal"),
            "note": _("The plugins that ship inside Postulo. Nothing is fetched."),
            "url": "",
            "enabled": True,
            "usable": True,
            "pinned": "",
            "editable": False,
            "removable": False,
            "switchable": False,
        }
    ]
    for row in PluginRepository.objects.all():
        variable = "POSTULO_PLUGIN_CATALOGUES" if row.name in pinned else ""
        rows.append(
            {
                "name": row.name,
                "tier": row.tier,
                "label": row.get_tier_display(),
                "note": "",
                "url": row.url,
                "enabled": row.enabled,
                "usable": row.usable,
                "pinned": variable,
                "editable": not variable,
                # The official tier is a fixture of the interface: emptied and switched
                # off, never deleted, so the heading does not vanish along with it.
                "removable": not variable and not row.is_official,
                "switchable": not variable,
            }
        )
    return rows


def _governed_plugins() -> list:
    """Every plugin a person may hold an opinion about, in one list across the kinds."""
    from postulo.plugins import policy
    from postulo.plugins.registry import plugins

    found = []
    for kind in policy.GOVERNED_KINDS:
        found.extend(plugins(kind))
    return sorted(found, key=lambda item: (getattr(item, "kind", "source"), item.name))


def _policy_rows(person=None) -> list[dict]:
    """What is decided for each plugin, and by whom."""
    from postulo.plugins import base
    from postulo.plugins.models import PluginPolicy

    stored = {row.plugin: row for row in PluginPolicy.objects.filter(person=person)}
    rows = []
    for plugin in _governed_plugins():
        row = stored.get(plugin.name)
        # The whole manifest, not three fields off it: this is the page an administrator
        # is on when the question is "whose code is running here", and #97 exists because
        # the answer used to be visible on the day of installation and never again.
        manifest = base.manifest_of(plugin)
        rows.append(
            {
                "name": plugin.name,
                "label": manifest.label,
                "description": manifest.description,
                "kind": manifest.kind or "source",
                "manifest": manifest,
                # The instance itself, so the template can ask for a logo rather than
                # being handed one it has no way to fall back from (#106).
                "plugin": plugin,
                "state": row.state if row else PluginPolicy.State.AVAILABLE,
                "decided_by": row.decided_by if row else None,
                "decided_at": row.decided_at if row else None,
            }
        )
    return rows


def _save_policies(request: HttpRequest, person=None) -> int:
    """Write what was submitted, keeping only the decisions that are not the default.

    A row per plugin per person would be a table that grows with the product of two things
    that both grow. Absence means available, so the common answer is stored nowhere.
    """
    from postulo.plugins.models import PluginPolicy

    states = {value for value, _label in PluginPolicy.State.choices}
    changed = 0
    for plugin in _governed_plugins():
        wanted = request.POST.get(f"state:{plugin.name}", "")
        if wanted not in states:
            continue
        existing = PluginPolicy.objects.filter(plugin=plugin.name, person=person).first()
        if wanted == PluginPolicy.State.AVAILABLE:
            if existing:
                existing.delete()
                changed += 1
            continue
        if existing and existing.state == wanted:
            continue
        PluginPolicy.objects.update_or_create(
            plugin=plugin.name,
            person=person,
            defaults={"state": wanted, "decided_by": request.user},
        )
        changed += 1
        logger.warning(
            "Plugin %r set to %r for %s by %s",
            plugin.name,
            wanted,
            person.username if person else "every account",
            request.user.username,
        )
    return changed


class PluginPolicyView(StaffRequiredMixin, View):
    """The instance default for every plugin: available, unavailable, on, off.

    Nothing here deletes anybody's connections. Switching a plugin off for an account stops
    it being used; the credentials and the configuration stay exactly where they were, and
    reversing the decision brings them back unchanged. A policy that destroyed data on the
    way would be a delete button with a confusing name.
    """

    def post(self, request: HttpRequest) -> HttpResponse:
        changed = _save_policies(request)
        messages.success(
            request,
            _("Saved.") if changed else _("Nothing was different."),
        )
        return redirect("server:plugins")


class PersonPluginsView(StaffRequiredMixin, TemplateView):
    """One account's exceptions to the instance default.

    An administrator may compel as well as forbid, and this is where. The person is always
    told what was decided for them and by whom — a locked control on their own settings
    page rather than one that has quietly vanished (#96). That visibility is the whole
    reason this is acceptable at all: the concern was never that an administrator holds the
    power, it was that it could be held invisibly.
    """

    template_name = "server/person_plugins.html"

    def dispatch(self, request, *args, **kwargs):
        self.person = get_object_or_404(get_user_model(), pk=kwargs["pk"])
        return super().dispatch(request, *args, **kwargs)

    def get_context_data(self, **kwargs) -> dict:
        from postulo.plugins.models import PluginPolicy

        context = super().get_context_data(**kwargs)
        context["person"] = self.person
        context["rows"] = _policy_rows(self.person)
        context["defaults"] = {row["name"]: row["state"] for row in _policy_rows()}
        context["states"] = PluginPolicy.State.choices
        context["section_title"] = _("Plugins for %(name)s") % {"name": self.person.username}
        return context

    def post(self, request: HttpRequest, pk: int) -> HttpResponse:
        changed = _save_policies(request, self.person)
        messages.success(request, _("Saved.") if changed else _("Nothing was different."))
        return redirect("server:person_plugins", pk=self.person.pk)


class PluginRepositoryView(StaffRequiredMixin, View):
    """Add, edit, switch and remove the catalogues an instance may install from.

    Every one of these is an administrator changing where code may come from, which is why
    each writes a line to the log. There is no audit trail in *Server settings* yet — no
    action on that page records anything — and building one is #95's problem rather than
    this view's, but a key change that left no trace at all would be the wrong place to
    wait for it.
    """

    def post(self, request: HttpRequest) -> HttpResponse:
        from postulo.plugins import catalogue
        from postulo.plugins.models import PluginRepository

        action = request.POST.get("action", "")
        name = (request.POST.get("name") or "").strip()
        row = PluginRepository.objects.filter(name=name).first()

        if action == "add":
            return self._save(request, PluginRepositoryForm(request.POST))

        if row is None:
            messages.error(request, _("There is no repository by that name."))
            return redirect("server:plugins")
        if row.name in catalogue.pinned_names():
            messages.error(
                request,
                _("The environment sets “%(name)s”, so it cannot be changed here.")
                % {"name": row.name},
            )
            return redirect("server:plugins")

        if action == "save":
            return self._save(request, PluginRepositoryForm(request.POST, instance=row))
        if action in {"enable", "disable"}:
            row.enabled = action == "enable"
            row.save(update_fields=["enabled"])
            logger.warning(
                "Plugin repository %r switched %s by %s",
                row.name,
                "on" if row.enabled else "off",
                request.user.username,
            )
            messages.success(
                request,
                _("“%(name)s” is on. Plugins may be installed and updated from it.")
                % {"name": row.name}
                if row.enabled
                else _(
                    "“%(name)s” is off. Nothing new is installed or updated from it; what "
                    "was already installed goes on working."
                )
                % {"name": row.name},
            )
            return redirect("server:plugins")
        if action == "remove":
            if row.is_official:
                messages.error(request, _("The official repository is emptied, not removed."))
                return redirect("server:plugins")
            logger.warning("Plugin repository %r removed by %s", row.name, request.user.username)
            row.delete()
            messages.success(
                request,
                _("“%(name)s” is gone. Anything installed from it stays installed.")
                % {"name": row.name},
            )
            return redirect("server:plugins")

        messages.error(request, _("That is not something this page does."))
        return redirect("server:plugins")

    def _save(self, request: HttpRequest, form) -> HttpResponse:
        if not form.is_valid():
            return PluginsView.as_view()(request, repository_form=form)
        changed_key = form.key_changed
        row = form.save()
        if changed_key:
            logger.warning(
                "Plugin repository %r had its public key replaced by %s",
                row.name,
                request.user.username,
            )
            messages.warning(
                request,
                _(
                    "The key for “%(name)s” was replaced. That key is the only thing "
                    "standing between its index and code running here, so make sure it "
                    "came from somewhere you trust."
                )
                % {"name": row.name},
            )
        else:
            messages.success(request, _("Saved."))
        return redirect("server:plugins")


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
            "source_url": info.source_url,
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
        from postulo.notifications import transport
        from postulo.plugins.installing import InstallError, set_disabled
        from postulo.plugins.registry import GROUPS
        from postulo.plugins.registry import plugins as registry_plugins

        name = request.POST.get("name", "")
        # Switching off a package that carries the mail is switching off the mail (#104).
        if disabled and (refusal := transport.refuse_removing_distribution(name)):
            messages.error(request, refusal)
            return redirect("server:plugins")
        try:
            entry = set_disabled(name, disabled)
        except InstallError as error:
            messages.error(request, str(error))
            return redirect("server:plugins")
        for kind in GROUPS:
            registry_plugins(kind, refresh=True)
        messages.success(
            request,
            _("%(name)s is switched off; its files are still here.") % {"name": entry.name}
            if disabled
            else _("%(name)s is switched on again.") % {"name": entry.name},
        )
        return redirect("server:plugins")

    def _remove(self, request: HttpRequest) -> HttpResponse:
        from postulo.notifications import transport
        from postulo.plugins import data as plugin_data
        from postulo.plugins.installing import InstallError, remove

        name = request.POST.get("name", "")
        # Two refusals, both named after what they protect: the mail this instance sends
        # (#104), and the records this package still holds (#128).
        for refusal in (
            transport.refuse_removing_distribution(name),
            plugin_data.refuse_removing(name),
        ):
            if refusal:
                messages.error(request, refusal)
                return redirect("server:plugins")
        try:
            entry = remove(name)
        except InstallError as error:
            messages.error(request, str(error))
            return redirect("server:plugins")
        messages.success(
            request,
            _("%(name)s is removed. Restart Postulo to be sure nothing of it is left loaded.")
            % {"name": entry.name},
        )
        return redirect("server:plugins")
