"""The environment a child process is started with: an allow-list, not a copy (#814, #820).

Postulo starts a few processes to handle what it does not trust: one that draws a stranger's page,
and one that imports a plugin to see whether it loads. Each inherits, by default, everything this
process was started with -- the secret key, the database address and password, an API token, a
mail password -- and none of them needs any of it. They get what a program needs to find its
libraries, its fonts and its temporary files, and nothing else.
"""

from __future__ import annotations

import os

ENVIRONMENT_KEPT = (
    "PATH",
    "LANG",
    "LANGUAGE",
    "LC_ALL",
    "LC_CTYPE",
    "TZ",
    "SYSTEMROOT",
    "WINDIR",
    "TEMP",
    "TMP",
    "TMPDIR",
    "HOME",
    "XDG_CACHE_HOME",
    "XDG_DATA_DIRS",
    "FONTCONFIG_PATH",
    "FONTCONFIG_FILE",
    "LD_LIBRARY_PATH",
    "DYLD_LIBRARY_PATH",
    "PYTHONPATH",
    "VIRTUAL_ENV",
)


def child_environment() -> dict[str, str]:
    """This process's environment, cut to what ``ENVIRONMENT_KEPT`` names."""
    return {name: os.environ[name] for name in ENVIRONMENT_KEPT if name in os.environ}
