"""The bar under each language on *Server settings -> Defaults* (#312).

Reviewed, draft and untranslated, drawn from `status.json` and said again in words. What a
bar gets wrong is the arithmetic -- parts that do not add up, a sliver rounded away, a
finished language drawn one point short -- so most of this file is about the sums, which
live in `languages.translation_progress` and `languages.whole_percents`. The rest is that
the page draws one per language and that the drawing needs neither a style nor a colour
to be understood.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest
from django.urls import reverse
from django.utils import translation

from postulo.core import languages
from postulo.core.languages import translation_progress, whole_percents

# ------------------------------------------------------------------ the sums


def test_a_normal_bar_rounds_by_the_largest_remainder():
    """812 and 1,061 of 1,873 are 43.35 % and 56.65 %: both round down, and the point
    left over goes to the one that rounding cost more."""
    assert whole_percents([812, 1061, 0]) == [43, 57, 0]


def test_the_widths_always_add_up_to_a_hundred_and_the_counts_to_the_total():
    """Every way of dividing a small total three ways, and a sample of large ones."""
    totals = [*range(1, 41), 997, 1873, 2000, 12345]
    for total in totals:
        step = 1 if total <= 40 else max(total // 37, 1)
        for reviewed in [*range(0, total + 1, step), total]:
            for draft in sorted({0, 1, total - reviewed, *range(0, total - reviewed + 1, step)}):
                untranslated = total - reviewed - draft
                if untranslated < 0:
                    continue
                counts = [reviewed, draft, untranslated]
                widths = whole_percents(counts)
                assert sum(widths) == 100, (counts, widths)
                for count, width in zip(counts, widths, strict=True):
                    assert (width == 0) == (count == 0), (counts, widths)
                    assert abs(width - 100 * count / total) < 2, (counts, widths)
                progress = translation_progress(
                    {"total": total, "translated": reviewed + draft, "reviewed": reviewed}
                )
                assert progress.reviewed + progress.draft + progress.untranslated == total


def test_a_sliver_is_never_rounded_away():
    """One string in 1,873 is 0.05 %, which rounds to nothing and would draw nothing."""
    assert whole_percents([1, 0, 1872]) == [1, 0, 99]
    assert whole_percents([1, 1, 1871]) == [1, 1, 98], "two slivers, paid for by the third"


def test_nothing_is_never_drawn():
    progress = translation_progress({"total": 1873, "translated": 1873, "reviewed": 0})

    assert [s.part for s in progress.segments] == ["draft"]
    assert progress.segments[0].width == 100
    assert progress.span == 100, "no gap without a second part"


def test_a_bar_with_anything_untranslated_never_reads_as_done():
    """1,872 of 1,873 is 99.95 %, which rounds to 100 and would hide the one left."""
    widths = whole_percents([0, 1872, 1])

    assert widths[0] + widths[1] == 99
    assert widths[2] == 1


def test_a_finished_bar_never_reads_as_99():
    """A single reviewed string among 1,873 drafts must not cost the bar its last point."""
    widths = whole_percents([1, 1872, 0])

    assert widths[0] + widths[1] == 100
    assert widths[0] >= 1


def test_nothing_at_all_draws_nothing():
    assert whole_percents([0, 0, 0]) == [0, 0, 0]
    assert translation_progress(None) is None
    assert translation_progress({}) is None
    assert translation_progress({"total": 0, "translated": 0}) is None


# ------------------------------------------------------------- what counts as what


def test_the_counts_are_read_the_way_the_status_writer_means_them():
    """`reviewed` is translated and neither draft nor fuzzy; the rest of what is translated
    is a draft, fuzzy included; what is not translated is untranslated."""
    progress = translation_progress(
        {"total": 20, "translated": 10, "drafts": 2, "fuzzy": 3, "reviewed": 5}
    )

    assert (progress.reviewed, progress.draft, progress.untranslated) == (5, 5, 10)


def test_every_line_of_the_shipped_file_adds_up():
    """Today every working language is complete and all draft, and the rest are empty."""
    for code, row in languages.translation_status().items():
        progress = translation_progress(row)
        assert progress is not None, code
        assert progress.reviewed + progress.draft + progress.untranslated == row["total"], code
        assert sum(s.width for s in progress.segments) == 100, code
        assert progress.reviewed == row["reviewed"], code


def test_a_file_without_a_reviewed_count_still_draws():
    progress = translation_progress({"total": 10, "translated": 8, "drafts": 3})

    assert (progress.reviewed, progress.draft, progress.untranslated) == (5, 3, 2)


def test_counts_out_of_range_are_held_inside_the_total():
    progress = translation_progress({"total": 10, "translated": 14, "reviewed": 20})

    assert (progress.reviewed, progress.draft, progress.untranslated) == (10, 0, 0)


# ------------------------------------------------------------------- the drawing


def test_the_parts_are_drawn_in_order_with_a_gap_between():
    progress = translation_progress({"total": 1873, "translated": 1873, "reviewed": 812})

    assert [(s.part, s.x, s.width) for s in progress.segments] == [
        ("reviewed", 0, 43),
        ("draft", 43 + languages.BAR_GAP, 57),
    ]
    assert progress.span == 100 + languages.BAR_GAP


def test_right_to_left_fills_from_the_right():
    """The same parts, mirrored: reviewed ends at the right-hand edge of the drawing."""
    ltr = translation_progress({"total": 100, "translated": 70, "reviewed": 20})
    rtl = translation_progress({"total": 100, "translated": 70, "reviewed": 20}, rtl=True)

    assert [s.part for s in rtl.segments] == ["reviewed", "draft", "untranslated"]
    for left, right in zip(ltr.segments, rtl.segments, strict=True):
        assert right.width == left.width
        assert right.x == rtl.span - left.x - left.width
    assert rtl.segments[0].x + rtl.segments[0].width == rtl.span
    assert rtl.segments[-1].x == 0


# ------------------------------------------------------------------- the page


@pytest.fixture
def administrator(db, django_user_model):
    return django_user_model.objects.create_user(
        email="admin@example.org", username="admin", password="x", is_staff=True
    )


def language_rows(html: str) -> dict[str, str]:
    rows = {}
    for row in re.findall(r"<label[^>]*>(.*?)</label>", html, re.S):
        code = re.search(r'name="offered_languages" value="([^"]+)"', row)
        if code:
            rows[code.group(1)] = row
    return rows


@pytest.mark.django_db
def test_every_language_on_the_defaults_page_has_its_bar(client, administrator):
    client.force_login(administrator)

    rows = language_rows(client.get(reverse("server:defaults")).content.decode())

    assert len(rows) > 30
    for code, row in rows.items():
        assert row.count("<svg") == 1, code
        assert 'class="translation-bar"' in row, code
        assert 'aria-hidden="true"' in row, code
        assert "style=" not in row, code


@pytest.mark.django_db
def test_the_bar_is_said_in_words_beside_it(client, administrator, monkeypatch):
    monkeypatch.setattr(
        languages,
        "translation_status",
        lambda: {
            "de": {
                "total": 1873,
                "translated": 1873,
                "drafts": 1061,
                "reviewed": 812,
                "percent": 100,
            }
        },
    )
    client.force_login(administrator)

    row = language_rows(client.get(reverse("server:defaults")).content.decode())["de"]

    assert "812 reviewed, 1,061 draft, 0 untranslated" in " ".join(row.split())
    parts = re.findall(r'<rect x="(\d+)" width="(\d+)" height="1" data-part="(\w+)"', row)
    assert parts == [("0", "43", "reviewed"), ("44", "57", "draft")]
    assert 'viewBox="0 0 101 1"' in row
    # The words sit outside the name, which is marked as being in another language.
    assert "reviewed" not in row.split('<span lang="de">', 1)[1].split("</span>", 1)[0]


@pytest.mark.django_db
def test_a_language_with_no_line_in_the_file_shows_its_name_alone(
    client, administrator, monkeypatch
):
    monkeypatch.setattr(languages, "translation_status", lambda: {})
    client.force_login(administrator)

    rows = language_rows(client.get(reverse("server:defaults")).content.decode())

    assert rows and not any("<svg" in row for row in rows.values())


@pytest.mark.django_db
def test_the_bar_follows_the_direction_of_the_page(administrator, monkeypatch):
    """Drawn for the language the administrator reads the page in, not for the row's."""
    from postulo.core.models import SiteSettings
    from postulo.core.server_forms import OfferedLanguagesForm

    monkeypatch.setattr(
        languages,
        "translation_status",
        lambda: {"de": {"total": 10, "translated": 10, "reviewed": 4, "percent": 100}},
    )
    with translation.override("ar"):
        rows = OfferedLanguagesForm(instance=SiteSettings.get()).rows()
    progress = next(row["progress"] for row in rows if row["code"] == "de")

    reviewed = progress.segments[0]
    assert reviewed.part == "reviewed"
    assert reviewed.x + reviewed.width == progress.span, "reviewed at the right-hand end"


@pytest.mark.parametrize(
    ("language", "expected"),
    [("en-GB", "1,873"), ("de", "1.873")],
)
def test_a_count_is_grouped_as_the_reader_groups_it(language, expected):
    """A comma in English and a full stop in German, from the same filter."""
    from postulo.core.templatetags.postulo import grouped

    with translation.override(language):
        assert grouped(1873) == expected


def test_the_counts_come_with_their_plural_forms():
    """Three counts, each its own plural, and the list around them a sentence of its own,
    so a language can put its own separators between them and its own order on them."""
    source = (
        Path(__file__).resolve().parents[1] / "src/postulo/templates/cotton/translation_bar.html"
    ).read_text(encoding="utf-8")

    for part in ("reviewed", "draft", "untranslated"):
        assert re.search(
            rf"blocktranslate count counter=progress\.{part}\b.*?{{% plural %}}", source, re.S
        ), part
    assert "{{ reviewed }}, {{ draft }}, {{ untranslated }}" in source
