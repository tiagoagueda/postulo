"""Every module under `src/postulo` imports first, alone, in a fresh interpreter (#248).

`test_import_layers.py` reads the imports; this one runs them. A cycle among module-level
imports is a module that works when something else happened to be imported before it and
fails when it is the first -- the shape a worker, a management command or a plugin's own
test suite meets and this repository's tests, which import half of Postulo before the first
one runs, never would. So each module is imported in an interpreter that has imported
nothing of Postulo's yet.

**How "alone" is arranged, and why not one interpreter per module.** Four hundred Python
start-ups, each setting Django up, cost minutes. Instead one interpreter imports the modules
one after another and, before each, forgets every `postulo.*` module the last one loaded, so
each is imported as the first of Postulo's. Two passes:

- **Before Django is set up**, every module is tried with nothing loaded at all. Most need
  nothing more; one that touches a model raises `AppRegistryNotReady`, and is tried again in
  the second pass. One that sets Django up itself (`wsgi`, `asgi`) ends its interpreter, and
  the rest carry on in a fresh one.
- **After `django.setup()`**, which imports the apps, their models and what those import in
  the order Django chooses -- the only order a model is ever imported in -- each remaining
  module is imported first among the rest. What setup itself loaded is never forgotten,
  because importing a model twice registers it twice.

The interpreter reads no `.env` and writes nothing into the checkout: the settings are
pointed at a temporary directory, and the production settings get the key they insist on.
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "src"

#: What runs in the fresh interpreter. It is handed the phase and the module names, and
#: prints one line of JSON: each module and what happened to it.
CHILD = r"""
import importlib, json, os, sys, traceback

import environ

# The repository's .env belongs to whoever is developing here; what is under test is what the
# modules say, so the file is taken out of the picture rather than trusted to be absent.
environ.Env.read_env = lambda *args, **kwargs: None

phase, names = sys.argv[1], json.loads(sys.stdin.read())
results = {}


def ours(name):
    return name == "postulo" or name.startswith("postulo.")


def forget(keep):
    for name in [name for name in list(sys.modules) if ours(name) and name not in keep]:
        del sys.modules[name]


def tried(name):
    try:
        importlib.import_module(name)
    except Exception as error:
        return "".join(traceback.format_exception(error))
    return ""


if phase == "before":
    from django.apps import apps
    from django.core.exceptions import AppRegistryNotReady

    keep = {name for name in sys.modules if ours(name)}
    for name in names:
        forget(keep)
        try:
            importlib.import_module(name)
            outcome = "ok"
        except AppRegistryNotReady:
            outcome = "needs the app registry"
        except Exception as error:
            outcome = "".join(traceback.format_exception(error))
        if apps.apps_ready:
            results[name] = "sets Django up" if outcome == "ok" else outcome
            break
        results[name] = outcome
else:
    import django

    django.setup()
    keep = {name for name in sys.modules if ours(name)}
    for name in names:
        if name in keep:
            results[name] = "loaded by django.setup()"
            continue
        forget(keep)
        results[name] = tried(name) or "ok"

print("\n" + json.dumps(results))
"""

#: What each outcome may be, other than a traceback.
FINE = {"ok", "sets Django up", "loaded by django.setup()"}


def modules() -> list[str]:
    found = []
    for path in sorted((SRC / "postulo").rglob("*.py")):
        if "__pycache__" in path.parts:
            continue
        parts = list(path.relative_to(SRC).with_suffix("").parts)
        if parts[-1] == "__init__":
            parts.pop()
        found.append(".".join(parts))
    return found


def environment(scratch: Path) -> dict[str, str]:
    kept = {key: value for key, value in os.environ.items() if not key.startswith("POSTULO_")}
    return {
        **kept,
        "DJANGO_SETTINGS_MODULE": "postulo.config.settings.test",
        # A key the production settings accept, so importing them is a test of the import.
        "POSTULO_SECRET_KEY": "kQ7vN2xR9wT4yU6iO8pA3sD5fG1hJ0kL2zX4cV6bN8mQ7wE9rT5yU3iO1pA6sD4f",
        "POSTULO_ALLOWED_HOSTS": "postulo.example.org",
        "POSTULO_DATABASE_URL": f"sqlite:///{(scratch / 'db' / 'postulo.sqlite3').as_posix()}",
        "POSTULO_LOG_DIR": str(scratch / "logs"),
        "POSTULO_MEDIA_ROOT": str(scratch / "media"),
        "POSTULO_PLUGINS_DIR": str(scratch / "plugins"),
    }


def run(phase: str, names: list[str], scratch: Path) -> dict[str, str]:
    finished = subprocess.run(  # noqa: S603 - this interpreter, and a script written here
        [sys.executable, "-c", CHILD, phase],
        input=json.dumps(names),
        capture_output=True,
        text=True,
        env=environment(scratch),
        cwd=ROOT,
        timeout=600,
    )
    assert finished.returncode == 0, finished.stderr[-4000:]
    return json.loads(finished.stdout.strip().splitlines()[-1])


def test_every_module_imports_first_and_alone(tmp_path):
    names = modules()
    outcomes: dict[str, str] = {}
    pending = list(names)
    while pending:
        answered = run("before", pending, tmp_path)
        assert answered, f"nothing was tried of {pending[:3]}..."
        outcomes.update(answered)
        pending = [name for name in pending if name not in answered]
    later = [name for name, outcome in outcomes.items() if outcome == "needs the app registry"]
    outcomes.update(run("after", later, tmp_path))

    assert set(outcomes) == set(names), "every module was tried"
    assert sum(outcome == "ok" for outcome in outcomes.values()) > 300, "and most imported"
    broken = {name: outcome for name, outcome in outcomes.items() if outcome not in FINE}
    assert not broken, "These modules fail when they are the first imported:\n\n" + "\n\n".join(
        f"{name}:\n" + "\n".join(outcome.strip().splitlines()[-6:])
        for name, outcome in sorted(broken.items())
    )
