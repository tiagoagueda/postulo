"""Postulo's modules import in one direction, and this is the direction (#248).

A cycle between two modules used to be held open by an import moved inside a function, where
nobody reading the top of the file sees it and nothing names the startup it breaks if it
moves back. This file is what names it. The graph is read from the source with `ast`, so an
import counts wherever it is written: at the top, inside a function, inside a method. Only an
`if TYPE_CHECKING:` import is left out, because it never runs -- and a test below holds every
such import outside the plugin surface to annotations, so that stays true.

**Two questions, two tests.** No cycles, except the ones in `ALLOWED_CYCLES`, each with the
reason it stays. And every import points *down* `LAYERS`: a module may import its own layer
or one below it, never one above. The second is the stronger promise -- a graph can be
acyclic today and still have a model reaching up into a view, which is the import that
closes tomorrow's cycle.

**`postulo.plugins.api` is read for what it is.** Its eager half is `plugins.base` again and
sits with the contract; its other names are looked up when a plugin asks for them
(`__getattr__`), so a module importing one of those depends on the module that provides it,
not on the facade. The providers are read from the facade's own `TYPE_CHECKING` block, and a
test below keeps that block, `_ELSEWHERE` and `__all__` agreeing, so the reading cannot drift
from what the facade really does. Nothing in Postulo imports the facade as a module, which is
what keeps every one of those dependencies visible here.

**Neither list is a list of exemptions.** A new entry is a decision somebody makes on purpose,
and a stale one fails, because an entry nobody removed hides the next real problem.
"""

from __future__ import annotations

import ast
import fnmatch
import functools
from collections import defaultdict
from pathlib import Path

import pytest

SRC = Path(__file__).resolve().parents[1] / "src"
PACKAGE = "postulo"
FACADE = "postulo.plugins.api"
#: Where the facade's own lazy imports are attributed: the top, since nothing imports it.
FACADE_LAZY = "postulo.plugins.api:lazy"

#: Bottom first. A module may import its own layer or any layer before it in this list.
#: Patterns are `fnmatch` over dotted names; a module matching none is a feature.
LAYERS: list[tuple[str, tuple[str, ...]]] = [
    (
        # What the settings import, and the small leaves every layer may use. Nothing here
        # imports anything above it, because the settings module imports some of it and
        # nothing Django sets up exists yet.
        "foundation",
        (
            "postulo",
            "postulo.config",
            "postulo.config.settings*",
            "postulo.config.formats*",
            "postulo.config.sqlite",
            "postulo.config.database_password",
            "postulo.accounts.tokens",
            "postulo.accounts.validators",
            "postulo.core.addresses",
            "postulo.core.country_sets",
            "postulo.core.currencies",
            "postulo.core.destinations",
            "postulo.core.brands",
            "postulo.core.flags",
            "postulo.core.language_field",
            "postulo.core.language_names",
            "postulo.core.media",
            "postulo.core.languages",
            "postulo.core.logs",
            "postulo.core.mail_choices",
            # Markdown to sanitised markup for a listing's description (#665): a parser, an
            # allowlist and nothing of Postulo's, read by the record that holds the text.
            "postulo.core.markdown",
            "postulo.core.memo",
            # What an option of a select draws beside its words (#301): a widget and the
            # name of an icon, read by the identifiers' registry among the records.
            "postulo.core.option_icons",
            "postulo.core.phone_field",
            "postulo.core.phones",
            "postulo.core.personal",
            "postulo.core.pictures",
            "postulo.core.proxy",
            "postulo.core.redirects",
            "postulo.core.slugs",
            "postulo.core.throttle",
            "postulo.documents.outline",
            "postulo.documents.themes",
            "postulo.jobs.esco",
            "postulo.jobs.industries",
            "postulo.jobs.places",
            "postulo.jobs.roles",
            "postulo.plugins.kinds",
            "postulo.plugins.locale",
            "postulo.plugins.record",
            "postulo.plugins.secrets",
            "postulo.resume.publications",
            "postulo.resume.translatable",
        ),
    ),
    (
        # What a plugin is, how one is found, and the network guard every plugin dials
        # through. Plain data and protocols: the models need it, so it holds no rows.
        "contract",
        (
            "postulo.plugins.base",
            FACADE,
            "postulo.plugins.http",
            "postulo.plugins.public_addresses",
            "postulo.plugins.themes",
            "postulo.plugins.registry",
            "postulo.notifications.base",
            # The shipped plugins Postulo's own code reads a name or a class from.
            "postulo.plugins.builtin*",
            "postulo.plugins.email_addresses*",
            "postulo.plugins.employer_structure*",
            "postulo.plugins.gdpr*",
            "postulo.plugins.maps*",
            "postulo.plugins.messaging_contacts*",
            "postulo.plugins.phone_numbers*",
            "postulo.plugins.postal_rules*",
            "postulo.plugins.repositories*",
            "postulo.plugins.social_profiles*",
            "postulo.plugins.websites*",
        ),
    ),
    (
        # The rows, the instance's own policy row, and what a model field is built from.
        "records",
        (
            "postulo.*.models",
            "postulo.core.site",
            # A person's own language and zone, asked of the policy row; the one place a sync
            # or an errand puts them in force outside a request (#335, #383).
            "postulo.core.preferences",
            "postulo.core.identifiers",
            "postulo.plugins.identifiers*",
            # What a web link's service is, which the model reads to name a row (#305). The
            # table Postulo ships is a feature like any other: nothing below imports it.
            "postulo.core.link_services",
            # And what a messaging handle's service is, for the same reason (#682).
            "postulo.core.messaging_services",
            "postulo.accounts.identifiers",
            "postulo.jobs.identifiers",
            "postulo.documents.kinds",
            "postulo.applications.endings",
        ),
    ),
    (
        # Deciding about plugins: installing, removing, trusting, switching per person.
        # Needs rows, and is asked by the features.
        "governance",
        (
            "postulo.plugins.catalogue",
            "postulo.plugins.consent",
            "postulo.plugins.data",
            "postulo.plugins.installing",
            "postulo.plugins.logos",
            "postulo.plugins.policy",
            "postulo.plugins.provenance",
            "postulo.plugins.syncing",
        ),
    ),
    # Every app's services, forms and the shipped plugins that do work. The default.
    ("features", ()),
    (
        # What nothing else imports.
        "interface",
        (
            "postulo.config.urls",
            "postulo.config.wsgi",
            "postulo.config.asgi",
            "postulo.*.views",
            "postulo.*.views_*",
            "postulo.*.*_views",
            "postulo.*.urls",
            "postulo.*.*_urls",
            "postulo.*.admin",
            "postulo.*.admin_site",
            "postulo.*.apps",
            "postulo.*.templatetags*",
            "postulo.*.context_processors",
            "postulo.*.middleware",
            "postulo.*.management*",
            "postulo.*.migrations*",
            "postulo.core.tasks",
            "postulo.api.api",
            "postulo.api.routers*",
            "postulo.plugins.testing",
            FACADE_LAZY,
        ),
    ),
]
ORDER = [name for name, _patterns in LAYERS]
#: Named layers are matched before the pattern-only ones, so a leaf listed by name wins
#: over a pattern that would also take it.
MATCH_FIRST = ("foundation", "contract", "interface", "records", "governance")

#: The cycles that stay, and why. Compared exactly: a new one fails, and so does one that
#: has gone and is still listed here.
ALLOWED_CYCLES: dict[frozenset[str], str] = {
    frozenset({"postulo.core.errands", "postulo.core.tasks"}): (
        "The queue stores a task by its dotted path, so `perform_errand` stays at "
        "postulo.core.tasks for every row already queued, and its body calls the errand it "
        "was handed. `errands.send` imports it when it enqueues rather than at the top, "
        "because defining a task builds and validates the task backend, and importing "
        "`errands` must not (#247)."
    ),
    frozenset({"postulo.notifications.webhooks", "postulo.plugins.webhook"}): (
        "A plugin holds no rows, so the webhook notifier's queue is Postulo's (#240), and the "
        "queue signs and posts each delivery in the plugin's own format. One feature on both "
        "sides of the plugin line, by the same decision `REACHING_PAST` records."
    ),
    frozenset({"postulo.applications.models", "postulo.applications.endings"}): (
        "`Application.ending` is model API, read from the timeline every time and stored "
        "nowhere (#239), and the reading needs the model's statuses and its events. Breaking "
        "the pair would mean moving the central `Status` enum, splitting the status sets "
        "across two modules and naming `ApplicationEvent` by string, which costs more than "
        "it buys."
    ),
}


# ------------------------------------------------------------------------ the graph


def _modules() -> dict[str, Path]:
    found: dict[str, Path] = {}
    for path in (SRC / PACKAGE).rglob("*.py"):
        if "__pycache__" in path.parts:
            continue
        parts = list(path.relative_to(SRC).with_suffix("").parts)
        if parts[-1] == "__init__":
            parts.pop()
        found[".".join(parts)] = path
    return found


def _parse(path: Path) -> ast.Module:
    return ast.parse(path.read_text(encoding="utf-8"), filename=str(path))


def _is_type_checking(test: ast.expr) -> bool:
    return (isinstance(test, ast.Name) and test.id == "TYPE_CHECKING") or (
        isinstance(test, ast.Attribute) and test.attr == "TYPE_CHECKING"
    )


class _Imports(ast.NodeVisitor):
    """Every import of a Postulo module: (target, names, line, deferred, typing)."""

    def __init__(self, module: str, path: Path, known: dict[str, Path]):
        self.known = known
        is_package = path.name == "__init__.py"
        self.package = module if is_package else module.rpartition(".")[0]
        self.depth = 0
        self.typing = 0
        self.found: list[tuple[str, tuple[str, ...], int, bool, bool]] = []

    def _add(self, target: str, names: tuple[str, ...], line: int) -> None:
        if target == PACKAGE or target.startswith(PACKAGE + "."):
            self.found.append((target, names, line, self.depth > 0, self.typing > 0))

    def _nearest(self, name: str) -> str:
        while name and name not in self.known:
            name = name.rpartition(".")[0]
        return name

    def visit_FunctionDef(self, node):
        defaults = [*node.args.defaults, *(d for d in node.args.kw_defaults if d is not None)]
        for expression in [*node.decorator_list, *defaults]:
            self.visit(expression)
        self.depth += 1
        for statement in node.body:
            self.visit(statement)
        self.depth -= 1

    visit_AsyncFunctionDef = visit_FunctionDef

    def visit_If(self, node):
        if not _is_type_checking(node.test):
            self.generic_visit(node)
            return
        self.typing += 1
        for statement in node.body:
            self.visit(statement)
        self.typing -= 1
        for statement in node.orelse:
            self.visit(statement)

    def visit_Import(self, node):
        for alias in node.names:
            self._add(self._nearest(alias.name), (), node.lineno)

    def visit_ImportFrom(self, node):
        source = node.module or ""
        if node.level:
            base = self.package.split(".")
            base = base[: len(base) - node.level + 1]
            source = ".".join([*base, *([node.module] if node.module else [])])
        rest = []
        for alias in node.names:
            if f"{source}.{alias.name}" in self.known:  # `from package import module`
                self._add(f"{source}.{alias.name}", (), node.lineno)
            else:
                rest.append(alias.name)
        if rest:
            self._add(self._nearest(source), tuple(rest), node.lineno)


@functools.cache
def _read() -> dict[str, list[tuple[str, tuple[str, ...], int, bool, bool]]]:
    """Every module's imports, parsed once."""
    known = _modules()
    found = {}
    for module, path in known.items():
        reader = _Imports(module, path, known)
        reader.visit(_parse(path))
        found[module] = reader.found
    return found


def _facade() -> tuple[dict[str, str], set[str], dict[str, str], list[str]]:
    """What the facade promises: lazy name -> provider, eager names, `_ELSEWHERE`, `__all__`."""
    tree = _parse(SRC / "postulo/plugins/api.py")
    lazy: dict[str, str] = {}
    eager: set[str] = set()
    elsewhere: dict[str, str] = {}
    promised: list[str] = []
    for node in tree.body:
        if isinstance(node, ast.If) and _is_type_checking(node.test):
            for statement in node.body:
                if isinstance(statement, ast.ImportFrom):
                    source = statement.module or ""
                    if statement.level:
                        source = "postulo.plugins" + (f".{source}" if source else "")
                    for alias in statement.names:
                        lazy[alias.asname or alias.name] = source
        elif isinstance(node, ast.ImportFrom):
            eager.update(alias.asname or alias.name for alias in node.names)
        elif isinstance(node, ast.AnnAssign | ast.Assign):
            targets = node.targets if isinstance(node, ast.Assign) else [node.target]
            names = [t.id for t in targets if isinstance(t, ast.Name)]
            if "_ELSEWHERE" in names:
                for key, value in zip(node.value.keys, node.value.values, strict=True):
                    elsewhere[key.value] = value.elts[0].value
            if "__all__" in names:
                promised = [element.value for element in node.value.elts]
    eager.discard("annotations")
    eager.discard("TYPE_CHECKING")
    return lazy, eager, elsewhere, promised


@functools.cache
def build(deferred: bool = True) -> tuple[dict[str, set[str]], dict[tuple[str, str], int]]:
    """Module -> the modules it imports, with the facade read through.

    With ``deferred=False``, only what runs when the module itself is imported.
    """
    lazy, _eager, _elsewhere, _promised = _facade()
    graph: dict[str, set[str]] = defaultdict(set)
    where: dict[tuple[str, str], int] = {}

    def edge(source: str, target: str, line: int) -> None:
        if source != target:
            graph[source].add(target)
            where.setdefault((source, target), line)

    for module, found in _read().items():
        graph[module]
        for target, names, line, is_deferred, typing in found:
            if typing or (is_deferred and not deferred):
                continue
            if module == FACADE and is_deferred:
                edge(FACADE_LAZY, target, line)
                continue
            if target == FACADE:
                asked = [name for name in names if name in lazy]
                for name in asked:
                    edge(module, lazy[name], line)
                if len(asked) < len(names) or not names:
                    edge(module, FACADE, line)
                continue
            edge(module, target, line)
    if deferred:
        for provider in set(lazy.values()):
            edge(FACADE_LAZY, provider, 0)
    return graph, where


def cycles(graph: dict[str, set[str]]) -> list[frozenset[str]]:
    """Strongly connected components with more than one module (Tarjan, iteratively)."""
    index: dict[str, int] = {}
    low: dict[str, int] = {}
    stack: list[str] = []
    on_stack: set[str] = set()
    found: list[frozenset[str]] = []
    for root in sorted(graph):
        if root in index:
            continue
        work = [(root, iter(sorted(graph[root])))]
        index[root] = low[root] = len(index)
        stack.append(root)
        on_stack.add(root)
        while work:
            node, children = work[-1]
            for child in children:
                if child not in index:
                    index[child] = low[child] = len(index)
                    stack.append(child)
                    on_stack.add(child)
                    work.append((child, iter(sorted(graph.get(child, ())))))
                    break
                if child in on_stack:
                    low[node] = min(low[node], index[child])
            else:
                work.pop()
                if work:
                    low[work[-1][0]] = min(low[work[-1][0]], low[node])
                if low[node] == index[node]:
                    component = set()
                    while True:
                        member = stack.pop()
                        on_stack.discard(member)
                        component.add(member)
                        if member == node:
                            break
                    if len(component) > 1:
                        found.append(frozenset(component))
    return found


def layer_of(module: str) -> str:
    table = dict(LAYERS)
    for name in MATCH_FIRST:
        if any(fnmatch.fnmatchcase(module, pattern) for pattern in table[name]):
            return name
    return "features"


# ------------------------------------------------------------------------ the tests


def test_the_graph_is_read_at_all():
    """A check on the check: a reader that saw nothing would pass everything below."""
    graph, _where = build()
    assert len(graph) > 300
    assert "postulo.core.models" in graph["postulo.core.site"], "a top-level import"
    assert "postulo.core.tasks" in graph["postulo.core.errands"], "an import inside a function"
    assert "postulo.plugins.base" in graph[FACADE], "the facade's eager half"
    assert "postulo.core.tasks" not in build(deferred=False)[0]["postulo.core.errands"]


def test_no_import_cycle_but_the_ones_that_stay():
    found = set(cycles(build()[0]))
    unexpected = [sorted(c) for c in found - set(ALLOWED_CYCLES)]
    stale = [sorted(c) for c in set(ALLOWED_CYCLES) - found]
    assert not unexpected, (
        "These modules import each other, counting imports inside functions: "
        f"{unexpected}. Move what both want into a module below both, rather than moving "
        "the import into a function."
    )
    assert not stale, f"No longer a cycle; take it out of ALLOWED_CYCLES: {stale}"


def test_module_level_imports_alone_are_acyclic():
    """What Python itself enforces at startup, asked here so that a failure reads as a sentence
    rather than as a half-initialised module in somebody's traceback."""
    found = [sorted(c) for c in cycles(build(deferred=False)[0])]
    assert not found, f"These modules import each other at the top: {found}"


def test_every_import_points_down_the_layers():
    graph, where = build()
    allowed = {(a, b) for cycle in ALLOWED_CYCLES for a in cycle for b in cycle if a != b}
    rank = {name: position for position, name in enumerate(ORDER)}
    upward = [
        f"{source} ({layer_of(source)}) imports {target} ({layer_of(target)}) "
        f"at line {where[(source, target)]}"
        for source in sorted(graph)
        for target in sorted(graph[source])
        if (source, target) not in allowed and rank[layer_of(target)] > rank[layer_of(source)]
    ]
    assert not upward, (
        "An import pointing up the layers is the one that closes the next cycle:\n  "
        + "\n  ".join(upward)
        + "\nMove the code down, or give the module its layer in LAYERS on purpose."
    )


@pytest.mark.parametrize("layer", [name for name, patterns in LAYERS if patterns])
def test_every_entry_in_the_layers_names_a_module(layer: str):
    modules = [module for module in build()[0] if module != FACADE_LAZY]
    patterns = dict(LAYERS)[layer]
    stale = [p for p in patterns if p != FACADE_LAZY and not fnmatch.filter(modules, p)]
    assert not stale, f"{layer} lists what no longer exists: {stale}"


def test_the_facade_is_read_from_what_it_really_imports():
    """`TYPE_CHECKING`, `_ELSEWHERE` and `__all__` have to agree, or the graph is wrong."""
    lazy, eager, elsewhere, promised = _facade()
    assert set(promised) == set(lazy) | eager, "every promised name is eager or lazy, once"
    wrong = {n: (m, lazy.get(n)) for n, m in elsewhere.items() if lazy.get(n) != m}
    assert not wrong, f"_ELSEWHERE and the TYPE_CHECKING block disagree: {wrong}"


def test_nothing_imports_the_facade_as_a_module():
    """`from postulo.plugins import api` would hide every lazy dependency behind one edge.

    The graph above follows a name imported *from* the facade to the module that provides
    it. An attribute read off the facade module (`api.Record`) is invisible to it, so inside
    Postulo the facade is only ever imported from, by name.
    """
    whole = [
        f"{module}:{line}"
        for module, found in sorted(_read().items())
        for target, names, line, _deferred, _typing in found
        if target == FACADE and (not names or "*" in names)
    ]
    assert not whole, f"Import the names you use from {FACADE} instead: {whole}"


def _type_checking_names(tree: ast.Module) -> tuple[set[str], set[int]]:
    """The names bound under `if TYPE_CHECKING:`, and the ids of the nodes inside such blocks."""
    names: set[str] = set()
    inside: set[int] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.If) and _is_type_checking(node.test):
            for statement in node.body:
                for child in ast.walk(statement):
                    inside.add(id(child))
                    if isinstance(child, ast.ImportFrom):
                        names.update(alias.asname or alias.name for alias in child.names)
                    elif isinstance(child, ast.Import):
                        names.update(
                            alias.asname or alias.name.partition(".")[0] for alias in child.names
                        )
    return names, inside


def _annotation_nodes(tree: ast.Module) -> set[int]:
    """The ids of every node that is part of an annotation, which never runs here."""
    found: set[int] = set()
    roots: list[ast.AST] = []
    for node in ast.walk(tree):
        if isinstance(node, ast.FunctionDef | ast.AsyncFunctionDef):
            if node.returns is not None:
                roots.append(node.returns)
            arguments = node.args
            for argument in [
                *arguments.posonlyargs,
                *arguments.args,
                *arguments.kwonlyargs,
                *([arguments.vararg] if arguments.vararg else []),
                *([arguments.kwarg] if arguments.kwarg else []),
            ]:
                if argument.annotation is not None:
                    roots.append(argument.annotation)
        elif isinstance(node, ast.AnnAssign):
            roots.append(node.annotation)
    for root in roots:
        found.update(id(child) for child in ast.walk(root))
    return found


def test_a_type_checking_import_is_for_annotations_only():
    """An import under `TYPE_CHECKING` never runs, so the graph above does not count it.

    That is only true while the name it binds is used in annotations alone. Used anywhere
    else it is a real dependency the graph cannot see -- and a `NameError` the day the line
    runs. The plugin surface is the one exception: its block declares the names
    `__getattr__` hands over, and the facade test above holds it to that.
    """
    misused = []
    for module, path in sorted(_modules().items()):
        if module == FACADE:
            continue
        tree = _parse(path)
        names, inside = _type_checking_names(tree)
        if not names:
            continue
        annotations = _annotation_nodes(tree)
        misused.extend(
            f"{module}:{node.lineno} uses {node.id}"
            for node in ast.walk(tree)
            if isinstance(node, ast.Name)
            and node.id in names
            and id(node) not in inside
            and id(node) not in annotations
        )
    assert not misused, (
        "A name imported only for type checking is used where it runs; import it for real, "
        f"or move what it names below this module: {misused}"
    )
