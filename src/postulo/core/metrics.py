"""What an operator can see about a running instance, and nothing about the people on it.

Postulo is a thing somebody runs on a server, and running something means wanting to know
whether it is working: is the scheduler still going round, is a store refusing every
document, is anything piling up. A Prometheus endpoint answers that without a shell.

**Off unless asked for**, and while it is off the address is a plain 404 rather than a
403 — a refusal would confirm that something is there, and there is no reason to tell a
stranger which endpoints an instance has.

**Clean means what it says.** Every number here is about the instance: how many
applications exist, not whose; how many document copies are waiting, not for what. There
is no label anywhere carrying a person, a company, an application or a URL. A metric with
somebody's identifier in a label is a record of what they are doing, exported to somewhere
else, and calling it monitoring does not change that.

**No per-request counters, deliberately.** The obvious thing to export is a request rate
and a latency histogram, and it is the one thing this cannot do honestly: the image runs
three workers, a counter in one of them sees a third of the traffic, and a graph quietly
showing a third of the traffic is worse than no graph at all. Making them shared would
mean a write to the database on every request. The reverse proxy in front already has
these numbers, sees every request, and is where they belong — *Hardening* says so.

Everything below is computed at scrape time from the database, so it is the same answer
whichever worker replies.
"""

from __future__ import annotations

import platform
from dataclasses import dataclass, field

import django
from django.conf import settings
from django.db import connection

from postulo import __version__


@dataclass
class Metric:
    """One metric, in the text exposition format Prometheus reads."""

    name: str
    kind: str  # gauge or counter
    help_text: str
    samples: list[tuple[dict[str, str], float]] = field(default_factory=list)

    def render(self) -> str:
        lines = [f"# HELP {self.name} {self.help_text}", f"# TYPE {self.name} {self.kind}"]
        for labels, value in self.samples:
            rendered = ",".join(f'{key}="{_escape(str(text))}"' for key, text in labels.items())
            suffix = f"{{{rendered}}}" if rendered else ""
            lines.append(f"{self.name}{suffix} {_number(value)}")
        return "\n".join(lines)


def _escape(value: str) -> str:
    return value.replace("\\", "\\\\").replace('"', '\\"').replace("\n", "\\n")


def _number(value: float) -> str:
    if value == int(value):
        return str(int(value))
    return repr(float(value))


def enabled() -> bool:
    return bool(getattr(settings, "POSTULO_METRICS_ENABLED", False))


def token() -> str:
    return str(getattr(settings, "POSTULO_METRICS_TOKEN", "") or "")


def _database_is_reachable() -> bool:
    try:
        with connection.cursor() as cursor:
            cursor.execute("SELECT 1")
        return True
    except Exception:
        return False


def _migrations_are_applied() -> bool:
    from django.db.migrations.executor import MigrationExecutor

    try:
        executor = MigrationExecutor(connection)
        targets = executor.loader.graph.leaf_nodes()
        return not executor.migration_plan(targets)
    except Exception:
        return False


def collect() -> list[Metric]:
    """Every metric, read from the database at the moment of the scrape."""
    from django.contrib.auth import get_user_model

    from postulo.applications.models import Application, Reminder, Suggestion, SuggestionStatus
    from postulo.documents.models import CopyStatus, DocumentCopy, RenderedDocument
    from postulo.jobs.models import Capture, CaptureStatus, Company, JobPosting
    from postulo.plugins import installing

    metrics: list[Metric] = [
        Metric(
            "postulo_info",
            "gauge",
            "What this instance is running. Always 1; the labels carry the answer.",
            [
                (
                    {
                        "version": __version__,
                        "python": platform.python_version(),
                        "django": django.get_version(),
                    },
                    1,
                )
            ],
        )
    ]

    reachable = _database_is_reachable()
    metrics.append(
        Metric(
            "postulo_database_reachable",
            "gauge",
            "1 when the database answered, 0 when it did not.",
            [({}, 1 if reachable else 0)],
        )
    )
    if not reachable:
        # Nothing below can be read. Say so rather than failing the whole scrape: the two
        # metrics above are exactly what an operator needs at that moment.
        return metrics

    metrics.append(
        Metric(
            "postulo_migrations_applied",
            "gauge",
            "1 when every migration has been applied, 0 when some are outstanding.",
            [({}, 1 if _migrations_are_applied() else 0)],
        )
    )

    # ---- how much is in here. Counts only; nothing says whose.
    metrics.append(
        Metric(
            "postulo_records",
            "gauge",
            "How many of each kind of record exist on this instance.",
            [
                ({"kind": "people"}, get_user_model().objects.count()),
                ({"kind": "applications"}, Application.objects.count()),
                ({"kind": "listings"}, JobPosting.objects.count()),
                ({"kind": "companies"}, Company.objects.count()),
                ({"kind": "documents"}, RenderedDocument.objects.count()),
            ],
        )
    )

    # ---- what is waiting, which is what an operator actually watches.
    metrics.append(
        Metric(
            "postulo_pending",
            "gauge",
            "Work waiting to be done or looked at.",
            [
                (
                    {"kind": "document_copies"},
                    DocumentCopy.objects.filter(status=CopyStatus.PENDING).count(),
                ),
                (
                    {"kind": "captures"},
                    Capture.objects.filter(status=CaptureStatus.PENDING).count(),
                ),
                (
                    {"kind": "suggestions"},
                    Suggestion.objects.filter(status=SuggestionStatus.PENDING).count(),
                ),
                ({"kind": "reminders"}, Reminder.objects.filter(done_at__isnull=True).count()),
            ],
        )
    )

    # ---- what has gone wrong, which is what an operator wants alerting on.
    metrics.append(
        Metric(
            "postulo_failures",
            "gauge",
            "Things that tried and did not succeed, and are still in that state.",
            [
                (
                    {"kind": "document_copies"},
                    DocumentCopy.objects.filter(status=CopyStatus.FAILED).count(),
                ),
            ],
        )
    )

    installed = installing.read_record()
    metrics.append(
        Metric(
            "postulo_plugins",
            "gauge",
            "Plugins on this instance, by whether they are switched on.",
            [
                ({"state": "enabled"}, sum(1 for entry in installed if not entry.disabled)),
                ({"state": "disabled"}, sum(1 for entry in installed if entry.disabled)),
            ],
        )
    )

    return metrics


def render() -> str:
    """Everything, in the text format, ending with the newline Prometheus expects."""
    return "\n".join(metric.render() for metric in collect()) + "\n"
