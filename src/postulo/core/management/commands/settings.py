"""`manage.py settings`: each variable's effective value and where it came from (#1186).

Every variable is declared in `postulo.core.configuration`; this prints, for each one an
instance or its image reads, the value it has and whether that came from the environment,
from a `_FILE`, from an old name or from the default. A secret is shown as set or unset and
never printed, and a password inside an address is hidden. `--json` gives the same as one
document, for a script; `--all` adds what only the test suite and the maintainers' scripts
read.
"""

from __future__ import annotations

import json
from urllib.parse import urlsplit, urlunsplit

from django.conf import settings
from django.core.exceptions import ImproperlyConfigured
from django.core.management.base import BaseCommand

from postulo.core import configuration
from postulo.core.configuration import Kind, Variable

#: What a hidden value is shown as.
HIDDEN = "********"


def _hide_password(address: str) -> str:
    """An address with the password in it, if any, replaced."""
    parts = urlsplit(address)
    if not parts.password:
        return address
    host = parts.hostname or ""
    if parts.port:
        host = f"{host}:{parts.port}"
    netloc = f"{parts.username or ''}:{HIDDEN}@{host}"
    return urlunsplit(parts._replace(netloc=netloc))


def _settings_module() -> str:
    return getattr(settings, "SETTINGS_MODULE", "") or ""


def _effective(variable: Variable):
    """What the running settings hold for it, where they hold it under a name; else the reading."""
    module = _settings_module().rsplit(".", 1)[-1]
    reads_it = variable.module == "base" or variable.module == module
    name = variable.setting or variable.name
    if variable.scope is configuration.Scope.INSTANCE and reads_it and hasattr(settings, name):
        return getattr(settings, name)
    return configuration.read(variable.name)


def describe(variable: Variable) -> dict:
    """One variable: where its value came from, and the value as it may be shown."""
    entry = {
        "name": variable.name,
        "area": variable.area,
        "kind": str(variable.kind),
        "scope": str(variable.scope),
        "restart": variable.restart,
        "default": configuration.display_default(variable),
    }
    try:
        found = configuration.source(variable.name)
        value = _effective(variable)
    except ImproperlyConfigured as exc:
        entry.update(source="error", via="", value=None, shown=str(exc))
        return entry
    entry.update(source=found.source, via=found.via)
    if variable.kind is Kind.SECRET:
        has_value = bool(value)
        entry.update(value="set" if has_value else "unset", shown="set" if has_value else "unset")
        return entry
    shown = configuration.display(variable, value if not hasattr(value, "root") else str(value))
    if variable.kind is Kind.URL and isinstance(value, str):
        shown = _hide_password(shown)
    entry.update(value=shown, shown=shown)
    return entry


class Command(BaseCommand):
    help = (
        "Print each configuration variable's effective value and where it came from: the "
        "environment, a _FILE, an old name, or the default. Secrets are shown as set or unset."
    )
    # It is how a configuration the checks refuse is looked at, so it does not wait on them.
    requires_system_checks = ()

    def add_arguments(self, parser):
        parser.add_argument("--json", action="store_true", help="print one JSON document")
        parser.add_argument(
            "--all",
            action="store_true",
            help="include the variables only the tests and the maintainers' scripts read",
        )

    def handle(self, *args, **options):
        variables = [
            variable
            for variable in configuration.VARIABLES.values()
            if options["all"] or variable.documented
        ]
        entries = [describe(variable) for variable in variables]
        if options["json"]:
            self.stdout.write(json.dumps(entries, indent=2, ensure_ascii=False))
            return
        width = max(len(entry["name"]) for entry in entries)
        for area in configuration.AREAS:
            rows = [entry for entry in entries if entry["area"] == area.key]
            if not rows:
                continue
            self.stdout.write(f"\n{area.title}")
            for entry in rows:
                source = entry["source"]
                if source in ("file", "alias"):
                    source = f"{source} ({entry['via']})"
                self.stdout.write(f"  {entry['name']:<{width}}  {source:<12}  {entry['shown']}")
