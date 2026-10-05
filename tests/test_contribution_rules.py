"""The contribution rules, held to the tree (#653).

Two rules from the maintainer sit in prose: look for a library before writing one, and
which format data leaves in. Prose is where a rule goes missing, so these check that the
sections exist, that the files which point at them point at headings that are really there,
and that the pull request template asks the three questions.
"""

from __future__ import annotations

import re
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
CONTRIBUTING = (REPO / "CONTRIBUTING.md").read_text(encoding="utf-8")
CLAUDE = (REPO / "CLAUDE.md").read_text(encoding="utf-8")
TEMPLATE = REPO / ".forgejo" / "PULL_REQUEST_TEMPLATE.md"


def anchors(markdown: str) -> set[str]:
    """The fragment each `##` heading gets: lower-case, words joined by hyphens."""
    found = set()
    for heading in re.findall(r"^#{1,6}\s+(.+?)\s*$", markdown, flags=re.MULTILINE):
        slug = re.sub(r"[^\w\s-]", "", heading.lower())
        found.add(re.sub(r"\s+", "-", slug.strip()))
    return found


def test_contributing_has_both_sections():
    found = anchors(CONTRIBUTING)
    assert "look-for-a-library-before-you-write-one" in found
    assert "formats" in found


def test_the_sections_say_what_the_rule_is():
    flat = " ".join(CONTRIBUTING.split())
    assert "unpatched severe advisory" in flat
    for licence in ("MIT", "Apache-2.0", "MPL-2.0", "GPL-2.0-only"):
        assert licence in flat
    assert "vCard 4.0" in flat and "iCalendar" in flat and "PDF" in flat


def test_claude_md_points_at_both_sections():
    flat = " ".join(CLAUDE.split())
    assert "*Look for a library before you write one*" in flat
    assert "*Formats*" in flat


def test_the_pull_request_template_asks_the_three_questions():
    assert TEMPLATE.is_file(), ".forgejo/PULL_REQUEST_TEMPLATE.md is gone"
    text = TEMPLATE.read_text(encoding="utf-8")
    for question in ("What library was looked at?", "licence line", "advisory check"):
        assert question in text, f"the template no longer asks about: {question}"


def test_every_heading_the_template_links_to_is_in_contributing():
    text = TEMPLATE.read_text(encoding="utf-8")
    linked = re.findall(r"\(\.\./CONTRIBUTING\.md#([\w-]+)\)", text)
    assert linked, "the template links to nothing"
    missing = set(linked) - anchors(CONTRIBUTING)
    assert not missing, f"CONTRIBUTING.md has no heading for {sorted(missing)}"
