"""Writes ``.env.example`` from the registry of environment variables (#1186).

    uv run python scripts/configuration_docs.py           # rewrite .env.example
    uv run python scripts/configuration_docs.py --check   # fail if it is out of date
    uv run python scripts/configuration_docs.py --wiki    # print the wiki's tables

Every variable is declared once, in ``src/postulo/core/configuration.py``, with its type,
default, syntax and a line saying what it does. ``.env.example`` is written from that, grouped
by area, each variable commented out unless an instance needs it set; and ``--wiki`` prints the
same as the Markdown tables of the wiki's *Configuration* page, one per area. Neither is edited
by hand, and ``tests/test_configuration.py`` fails when the file and the registry disagree.
"""

from __future__ import annotations

import sys
import textwrap
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO / "src"))

from postulo.core import configuration  # noqa: E402 - after the path is set
from postulo.core.configuration import Kind, Scope, Variable  # noqa: E402

ENV_EXAMPLE = REPO / ".env.example"
WIDTH = 92

HEADER = """\
# Postulo's configuration. Copy this file to .env and adjust it. Every variable is optional
# in development; production needs POSTULO_SECRET_KEY and POSTULO_ALLOWED_HOSTS.
#
# Written from src/postulo/core/configuration.py by scripts/configuration_docs.py: change the
# registry and run the script, rather than editing this file. The wiki's Configuration page
# says the same, and `manage.py settings` prints what an instance is actually running with.
#
# How a value is written:
#   boolean   true or false (also yes/no, on/off, 1/0), in any case
#   size      a whole number in the unit the name gives, or 25MB, 2GB (counted in 1024s)
#   duration  a whole number in the unit the name gives, or 90s, 15m, 2h, 7d
#   list      values separated by commas
#   rate      <count>/<s|m|h|d>, such as 30/h; empty or 0 switches the limit off
#   secret    the value itself, or NAME_FILE=/path naming a file that holds it (Docker and
#             Kubernetes secrets); set one or the other, not both
"""

#: The scopes `.env.example` covers: what an instance and its container read.
ENV_FILE_SCOPES = (Scope.INSTANCE, Scope.CONTAINER, Scope.SERVER)


def _comment(text: str) -> list[str]:
    """Text as comment lines, wrapped, keeping the lines it breaks itself."""
    lines = []
    for paragraph in text.split("\n"):
        if paragraph.startswith("  "):
            lines.append(f"# {paragraph}")
            continue
        lines.extend(
            f"# {line}" for line in textwrap.wrap(paragraph, WIDTH - 2, break_on_hyphens=False)
        )
    return lines


def env_value(variable: Variable) -> str:
    """The value the line in `.env.example` shows: an example, or the default as written."""
    if variable.example:
        return variable.example
    if variable.kind is Kind.PATH:
        return variable.image
    default = variable.default
    if default is None:
        return ""
    if isinstance(default, bool):
        return "true" if default else "false"
    if isinstance(default, (list, tuple)):
        return ",".join(str(item) for item in default)
    return str(default)


#: The kinds whose syntax says nothing the kind's name does not.
PLAIN = (Kind.TEXT, Kind.URL, Kind.PATH)


def kind_line(variable: Variable) -> str:
    """The variable's type, the syntax its value takes and its default, in one sentence."""
    kind = str(variable.kind)
    kind = kind if kind.isupper() else kind.capitalize()
    syntax = "" if variable.kind in PLAIN else f": {configuration.syntax(variable)}"
    return f"{kind}{syntax}. Default: {configuration.display_default(variable)}."


def in_env_file(variable: Variable) -> bool:
    return variable.scope in ENV_FILE_SCOPES and variable.in_env_file


def render_env_example(variables=None) -> str:
    variables = configuration.VARIABLES if variables is None else variables
    out = [HEADER.rstrip("\n")]
    for area in configuration.AREAS:
        members = [v for v in configuration.in_area(area.key, variables) if in_env_file(v)]
        if not members:
            continue
        out.append("")
        title = f"# --- {area.title} "
        out.append(title + "-" * max(3, WIDTH - len(title)))
        if area.notes:
            out.extend(_comment(area.notes))
        for variable in members:
            out.append("")
            out.extend(_comment(variable.description))
            if variable.notes:
                out.extend(_comment(variable.notes))
            if variable.image:
                out.extend(_comment(f"The image sets {variable.image}."))
            out.extend(_comment(kind_line(variable)))
            line = f"{variable.name}={env_value(variable)}"
            out.append(line if variable.required else f"# {line}")
    return "\n".join(out) + "\n"


def _cell(text: str) -> str:
    return text.replace("|", "\\|").replace("\n", " ")


def render_wiki(variables=None) -> str:
    """The wiki's Configuration tables: one per area, then what each needs said at length."""
    variables = configuration.VARIABLES if variables is None else variables
    out = [
        "<!-- Generated by scripts/configuration_docs.py --wiki from the registry in "
        "src/postulo/core/configuration.py; do not edit by hand. -->"
    ]
    for area in configuration.AREAS:
        members = [v for v in configuration.in_area(area.key, variables) if v.documented]
        if not members:
            continue
        out += ["", f"### {area.title}", ""]
        if area.notes:
            out += [area.notes, ""]
        out += [
            "| Variable | Type | Default | Value | What it does |",
            "| --- | --- | --- | --- | --- |",
        ]
        for variable in members:
            default = configuration.display_default(variable)
            if variable.image:
                default += f" (the image: {variable.image})"
            out.append(
                f"| `{variable.name}` | {variable.kind} | {_cell(default)} | "
                f"{_cell(configuration.syntax(variable))} | {_cell(variable.description)} |"
            )
        noted = [variable for variable in members if variable.notes]
        if noted:
            out.append("")
            out += [f"- `{v.name}`: {_cell(v.notes)}" for v in noted]
    return "\n".join(out) + "\n"


def main(argv: list[str]) -> int:
    if "--wiki" in argv:
        sys.stdout.write(render_wiki())
        return 0
    wanted = render_env_example()
    if "--check" in argv:
        current = ENV_EXAMPLE.read_text(encoding="utf-8") if ENV_EXAMPLE.is_file() else ""
        if current != wanted:
            print(".env.example is out of date: run scripts/configuration_docs.py")
            return 1
        return 0
    ENV_EXAMPLE.write_text(wanted, encoding="utf-8", newline="\n")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
