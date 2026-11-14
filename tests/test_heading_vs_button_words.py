"""A heading and a button are different words in many languages (#1107).

"Export" and "Schedule" each stood for a place and an action in one string, so a translator
could give only one. Each use now has its own context; this keeps a bare one from coming back.
"""

import re
from pathlib import Path

import pytest

SRC = Path(__file__).resolve().parents[1] / "src" / "postulo"
SPLIT = ("Export", "Schedule")


@pytest.mark.parametrize("word", SPLIT)
def test_the_word_is_never_translated_without_saying_which_use_it_is(word):
    quoted = '"' + word + '"'
    bare = re.compile(
        r"\{%\s*translate\s+" + quoted + r"\s*%\}|(?:_|gettext|gettext_lazy)\(" + quoted + r"\)"
    )
    found = [
        str(path.relative_to(SRC))
        for path in [*SRC.rglob("*.html"), *SRC.rglob("*.py")]
        if "locale" not in path.parts and bare.search(path.read_text(encoding="utf-8"))
    ]
    assert not found, f'"{word}" needs a context in: {found}'
