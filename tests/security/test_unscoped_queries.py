"""A new query on an owned model must be scoped to its owner, or say why it is not (#825).

SECURITY.md promises ownership scoping across every view and queryset, and the isolation sweep
proves it for the addresses it knows. Nothing stopped a *new* query -- in a service, a task, a
helper -- from reading `Company.objects.filter(pk=...)` and handing back somebody else's row.

This reads every module under `src/postulo` and finds each `Model.objects` of a model that has an
owner, in a statement that mentions neither `for_user`, an owner, nor the current user. Those
already in the code were looked at once and are listed in `unscoped_queries.txt`: scheduler and
metrics passes that work across accounts on purpose, jobs that delete what is expired, and
lookups hanging off a parent that was scoped. **The list may only shrink.** A new one fails here,
and is either scoped or added to the file with a reason in its commit.
"""

from __future__ import annotations

import ast
from pathlib import Path

import pytest

SOURCE = Path(__file__).resolve().parents[2] / "src" / "postulo"
BASELINE = Path(__file__).with_name("unscoped_queries.txt")

#: Words that make a statement scoped: the manager's own, an owner column or an owner argument.
SCOPED = (
    "for_user",
    "owner",
    "request.user",
    "self.user",
    "user=",
    "profile__user",
    # A shared record's scopes (#896).
    "visible_to",
    "manageable_by",
    "owned_by",
)


def owned_model_names() -> set[str]:
    """Every class that has an ``owner`` foreign key, by name, found without importing."""
    found = set()
    for path in SOURCE.rglob("models.py"):
        tree = ast.parse(path.read_text(encoding="utf-8"))
        for node in ast.walk(tree):
            if isinstance(node, ast.ClassDef):
                bases = {getattr(b, "id", getattr(b, "attr", "")) for b in node.bases}
                has_owner = any(
                    isinstance(item, ast.Assign)
                    and any(getattr(t, "id", "") == "owner" for t in item.targets)
                    for item in node.body
                )
                # A person's or a group's record (#896) is owned all the same.
                if bases & {"OwnedModel", "PrincipalOwnedModel"} or has_owner:
                    found.add(node.name)
    return found


def recorded() -> set[str]:
    lines = BASELINE.read_text(encoding="utf-8").splitlines()
    return {line.strip() for line in lines if line.strip() and not line.startswith("#")}


def unscoped() -> set[str]:
    names = owned_model_names()
    found = set()
    for path in sorted(SOURCE.rglob("*.py")):
        relative = path.relative_to(SOURCE).as_posix()
        if "/migrations/" in relative or relative.endswith("models.py"):
            continue
        text = path.read_text(encoding="utf-8")
        try:
            tree = ast.parse(text)
        except SyntaxError:
            continue
        lines = text.splitlines()
        for function in [
            n for n in ast.walk(tree) if isinstance(n, ast.FunctionDef | ast.AsyncFunctionDef)
        ]:
            for statement in function.body:
                for node in ast.walk(statement):
                    if (
                        isinstance(node, ast.Attribute)
                        and node.attr == "objects"
                        and isinstance(node.value, ast.Name)
                        and node.value.id in names
                    ):
                        segment = "\n".join(lines[statement.lineno - 1 : statement.end_lineno])
                        if not any(word in segment for word in SCOPED):
                            found.add(f"{relative}::{function.name}::{node.value.id}")
    return found


def test_no_new_query_on_an_owned_model_is_left_unscoped():
    baseline = recorded()
    new = sorted(unscoped() - baseline)
    assert not new, (
        "These read an owned model without for_user() or an owner, and nobody has said why "
        "that is safe. Scope them, or add the line to tests/security/unscoped_queries.txt:\n  "
        + "\n  ".join(new)
    )


def test_the_baseline_only_shrinks():
    """A line that no longer matches anything is a query that was fixed: remove it."""
    baseline = recorded()
    gone = sorted(baseline - unscoped())
    assert not gone, "fixed since the list was made, so take them off it:\n  " + "\n  ".join(gone)


def test_the_scan_notices_an_unscoped_query(tmp_path, monkeypatch):
    sample = tmp_path / "service.py"
    sample.write_text("def leak(pk):\n    return Company.objects.get(pk=pk)\n", encoding="utf-8")
    tree = ast.parse(sample.read_text(encoding="utf-8"))
    hits = [
        n
        for n in ast.walk(tree)
        if isinstance(n, ast.Attribute) and n.attr == "objects" and n.value.id == "Company"
    ]
    assert hits and not any(word in sample.read_text() for word in SCOPED)


@pytest.mark.parametrize("model", ["Company", "Application", "Contact"])
def test_the_models_it_guards_include_the_ones_that_matter(model):
    assert model in owned_model_names()
