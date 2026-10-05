"""Server settings > Backups: the instance's own copies, for administrators (#242).

Every address here is for an administrator and answers **404** to anybody else, signed in or
not -- a refusal would confirm that the page exists, and this page is the way to a file
holding everybody's data. Anonymous visitors are sent to sign in, as everywhere.

**Downloading and deleting are the two sensitive actions, and both need a recent
authentication** (allauth's, as *Delete my account* does): the password again or the second
factor, so a session left open on a shared screen is not a way to carry the database away.
Both write a line to the log naming who and which file.

**An archive is only ever addressed by name**, through `backups.archive_path`, which refuses
anything that is not a file of the shape Postulo writes, directly inside the directory.

**Restoring stays on the command line** (#242, option *a*). The page verifies the archive,
shows what it holds and writes out the commands for this installation with the path filled
in. Replacing the database under running workers is not something a web request can do
safely, and the reasons are in `docs/PLAN.md`.
"""

from __future__ import annotations

import datetime as dt
import logging
import os
import shlex
import tarfile
import tempfile
from pathlib import Path

from allauth.account.decorators import reauthentication_required
from django.conf import settings
from django.contrib import messages
from django.db import transaction
from django.http import FileResponse, Http404, HttpRequest, HttpResponse
from django.shortcuts import redirect, render
from django.urls import reverse
from django.utils import timezone
from django.utils.decorators import method_decorator
from django.utils.translation import gettext as _
from django.utils.translation import gettext_lazy
from django.views import View
from django.views.generic import UpdateView

from . import backups, errands, site
from .backup import (
    BackupError,
    _newer_than_installed,
    database_vendor,
    read_manifest,
    verify_backup,
)
from .models import Errand, ErrandState, SiteSettings
from .server_forms import BackupScheduleForm
from .server_views import ServerSectionMixin
from .views_errands import state_context

logger = logging.getLogger(__name__)

SECTION = gettext_lazy("Backups")


class BackupsMixin(ServerSectionMixin):
    """Administrators only, and a 404 rather than a 403 for everybody else."""

    section_title = SECTION

    def handle_no_permission(self):
        if self.request.user.is_authenticated:
            raise Http404
        return super().handle_no_permission()


def _archive(name: str):
    try:
        return backups.archive_path(name)
    except backups.NotAnArchive as missing:
        raise Http404 from missing


def _a_backup_is_running() -> bool:
    if backups.running():
        return True
    return Errand.objects.filter(
        kind="backup",
        state__in=(ErrandState.WAITING, ErrandState.WORKING),
        created_at__gt=timezone.now() - dt.timedelta(hours=24),
    ).exists()


class BackupsView(BackupsMixin, UpdateView):
    """The list, the button, and the schedule: one page, one form (the schedule's)."""

    model = SiteSettings
    form_class = BackupScheduleForm
    template_name = "server/backups.html"

    def get_object(self, queryset=None) -> SiteSettings:
        return SiteSettings.get()

    def get_success_url(self) -> str:
        return reverse("server:backups")

    def form_valid(self, form):
        form.instance.updated_by = self.request.user
        # Any change to the schedule starts its clock afresh. Otherwise switching it on at
        # two in the afternoon would take a backup at once for a slot that was at three
        # that morning, and moving the hour earlier would do the same.
        if form.has_changed() and form.instance.backup_schedule != backups.OFF:
            form.instance.backup_last_run_at = timezone.now()
            form.instance.backup_last_run_ok = None
        messages.success(self.request, _("Saved."))
        return super().form_valid(form)

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        row = self.object
        listed = backups.archives()
        free = backups.free_space()
        errand = None
        asked = self.request.GET.get("errand", "")
        if asked.isdigit():
            errand = Errand.objects.for_user(self.request.user).filter(pk=int(asked)).first()
        context.update(
            {
                "archives": listed,
                "backup_dir": backups.directory(),
                "free_bytes": free,
                "low_on_room": backups.room_warning(listed, free),
                "running": _a_backup_is_running(),
                "errand_state": state_context(errand) if errand else None,
                "next_slot": backups.next_slot(row),
                "scheduled": row.backup_schedule != backups.OFF,
                "missed": backups.is_missed(row),
                "upload_max_mb": settings.POSTULO_BACKUP_UPLOAD_MAX_MB,
                "background_work": errands.worker_expected(),
            }
        )
        return context


@method_decorator(transaction.non_atomic_requests, name="dispatch")
class BackupRunView(BackupsMixin, View):
    """*Back up now*: an errand, never the request itself.

    Not atomic: with no worker configured the errand runs where it stands, and SQLite's
    backup API refuses a connection inside a transaction rather than waiting for ever.
    """

    def post(self, request: HttpRequest) -> HttpResponse:
        if _a_backup_is_running():
            messages.info(request, _("A backup is already running. Wait for it to finish."))
            return redirect("server:backups")
        errand = errands.send("backup", request.user)
        logger.info("Backup started by %s (errand %s).", request.user.get_username(), errand.pk)
        return redirect(f"{reverse('server:backups')}?errand={errand.pk}")


class BackupVerifyView(BackupsMixin, View):
    """Open one archive and compare its database to the checksum, now."""

    def post(self, request: HttpRequest, name: str) -> HttpResponse:
        path = _archive(name)
        ok, problem = backups.verify(path)
        if ok:
            messages.success(
                request, _("%(name)s opens and matches its checksum.") % {"name": name}
            )
        else:
            messages.error(
                request,
                _("%(name)s does not verify: %(problem)s") % {"name": name, "problem": problem},
            )
        return redirect("server:backups")


class BackupDownloadView(BackupsMixin, View):
    """Send an archive to the browser: everybody's data, unencrypted.

    A recent authentication first, a log line naming who, and a streamed attachment. The
    archive is not encrypted by Postulo: this is served over the connection the instance is
    reached by, and the file on disk is the operator's to protect (see the wiki).
    """

    @method_decorator(reauthentication_required)
    def get(self, request: HttpRequest, name: str) -> HttpResponse:
        path = _archive(name)
        logger.warning("Backup %s downloaded by %s.", name, request.user.get_username())
        response = FileResponse(
            path.open("rb"), as_attachment=True, filename=name, content_type="application/gzip"
        )
        response["Cache-Control"] = "no-store"
        return response


class BackupDeleteView(BackupsMixin, View):
    template_name = "server/backup_delete.html"

    def _show(self, request: HttpRequest, name: str) -> HttpResponse:
        path = _archive(name)
        archive = next((a for a in backups.archives() if a.name == path.name), None)
        return render(
            request,
            self.template_name,
            {
                "section_title": SECTION,
                "archive": archive,
                "blocked": backups.delete_blocked(name),
            },
        )

    @method_decorator(reauthentication_required)
    def get(self, request: HttpRequest, name: str) -> HttpResponse:
        return self._show(request, name)

    @method_decorator(reauthentication_required)
    def post(self, request: HttpRequest, name: str) -> HttpResponse:
        _archive(name)
        blocked = backups.delete_blocked(name)
        if blocked:
            messages.error(request, blocked)
            return redirect("server:backups")
        backups.delete(name)
        logger.warning("Backup %s deleted by %s.", name, request.user.get_username())
        messages.success(request, _("%(name)s is deleted.") % {"name": name})
        return redirect("server:backups")


class BackupUploadView(BackupsMixin, View):
    """Keep an archive somebody brings, to restore from, if it is one of ours.

    Refused **before it is kept**: the size against the limit first, then the whole file
    opened and checked into a temporary name that no listing matches. Only an archive that
    verifies is renamed into the directory.
    """

    def post(self, request: HttpRequest) -> HttpResponse:
        sent = request.FILES.get("archive")
        if sent is None:
            messages.error(request, _("Choose an archive to upload."))
            return redirect("server:backups")
        limit = settings.POSTULO_BACKUP_UPLOAD_MAX_MB * 1024 * 1024
        if sent.size > limit:
            messages.error(
                request,
                _("That file is larger than the %(mb)s MB this instance accepts.")
                % {"mb": settings.POSTULO_BACKUP_UPLOAD_MAX_MB},
            )
            return redirect("server:backups")

        root = backups.directory()
        root.mkdir(parents=True, exist_ok=True)
        handle, temporary = tempfile.mkstemp(dir=root, prefix=".upload-", suffix=".part")
        scratch = Path(temporary)
        try:
            with os.fdopen(handle, "wb") as writing:
                for chunk in sent.chunks():
                    writing.write(chunk)
            try:
                verify_backup(scratch)
            except BackupError as error:
                messages.error(
                    request,
                    _("That is not a Postulo backup that opens cleanly: %(problem)s")
                    % {"problem": error},
                )
                return redirect("server:backups")
            stamp = timezone.now().strftime("%Y%m%d-%H%M%S")
            final = root / f"postulo-upload-{stamp}.tar.gz"
            counter = 1
            while final.exists():
                final = root / f"postulo-upload-{stamp}-{counter}.tar.gz"
                counter += 1
            os.replace(scratch, final)
            backups.remember_check(final, True)
        finally:
            scratch.unlink(missing_ok=True)
        logger.warning("Backup archive %s uploaded by %s.", final.name, request.user.get_username())
        messages.success(request, _("%(name)s is kept and opens cleanly.") % {"name": final.name})
        return redirect("server:backup_restore", name=final.name)


class BackupRestoreView(BackupsMixin, View):
    """Verify an archive, show what it holds, and say exactly how to restore it.

    Opening this page verifies the archive: it is what the person asked for by pressing
    *Restore*, and a restore that starts from an archive that does not open is the one
    mistake this page exists to prevent.
    """

    template_name = "server/backup_restore.html"

    def get(self, request: HttpRequest, name: str) -> HttpResponse:
        path = _archive(name)
        ok, problem = backups.verify(path)
        manifest: dict = {}
        if ok:
            try:
                with tarfile.open(path, "r:gz") as opened:
                    manifest = read_manifest(opened)
            except (BackupError, tarfile.TarError, OSError) as error:
                ok, problem = False, str(error)
        engine = (manifest.get("database") or {}).get("engine", "")
        quoted = shlex.quote(str(path))
        return render(
            request,
            self.template_name,
            {
                "section_title": SECTION,
                "name": name,
                "path": path,
                "verified": ok,
                "problem": problem,
                "manifest": manifest,
                "created": (manifest.get("postulo") or {}).get("created_at", ""),
                "version": (manifest.get("postulo") or {}).get("version", ""),
                "engine": engine,
                "engine_matches": bool(engine) and engine == database_vendor(),
                "this_engine": database_vendor(),
                "newer": _newer_than_installed(manifest) if manifest else None,
                "counts": (manifest.get("counts") or {}).items(),
                "media": manifest.get("media") or {},
                "plugins": manifest.get("plugins") or {},
                "quoted_path": quoted,
                "instance": site.instance_name(),
            },
        )
