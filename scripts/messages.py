"""Postulo's own catalogues: the tool in `postulo.core.messages_tool`, pointed at this
repository. A plugin repository runs the same tool as `postulo-messages` (#187).

    uv run python scripts/messages.py extract          # refresh every .po from the source
    uv run python scripts/messages.py extract --check  # fail if a .po is out of date
    uv run python scripts/messages.py compile          # write the .mo files Django loads
    uv run python scripts/messages.py stats [--write]  # how far along each language is
    uv run python scripts/messages.py check            # placeholders and plural forms agree
"""

from __future__ import annotations

import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO / "src"))

from postulo.core import messages_tool  # noqa: E402

if __name__ == "__main__":
    messages_tool.use(REPO)
    sys.exit(messages_tool.main())
