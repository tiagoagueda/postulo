"""Scripts that need more of the page than a font: Urdu's Nastaliq (#1054), and the scripts
that break their lines without spaces (#1052).

Urdu is written in the Arabic script, in Nastaliq, a calligraphic style whose words descend
on a diagonal and whose letters stack higher and drop lower than Latin ones. Drawn in the
Naskh that Arabic uses it is legible and looks foreign, so the interface names Noto
Nastaliq Urdu first for anything marked Urdu, and opens the line up for it.

What can be checked anywhere is what the stylesheet asks for: the computed family and line
height of a page in Urdu. Whether a box clips the words drawn in it can only be measured
where the face is installed -- the image installs it, a Windows desktop and CI's browser job
mostly do not -- and that walk skips, saying so, where the browser cannot draw it.

Chinese breaks between almost any two characters and Thai only between words, which the
browser finds with ICU's dictionary. Neither depends on the font a character is drawn in --
a box is broken around where the letter would have been -- so those checks run on every
machine.
"""

from __future__ import annotations

import pytest
from playwright.sync_api import Page

from .test_accessibility import (  # noqa: F401
    axe_source,
    describe,
    furnished,
    sign_in,
    violations_on,
)
from .test_reflow import NARROW, SCROLLS_SIDEWAYS
from .test_text_spacing import CLIPPED

pytestmark = pytest.mark.e2e

#: Pages with every shape that clips or squeezes a line: buttons and chips in a heading
#: row, a table, the board's cards, a form with one-line selects, a long settings list.
PAGES = (
    "/",
    "/applications/",
    "/applications/?view=board",
    "/jobs/companies/",
    "/documents/cvs/",
    "/career/",
    "/settings/language/",
)

#: The suite draws every page in its own copy of DejaVu Sans on a machine whose system font
#: is something else (`conftest._drawn_in_the_suites_own_font`), with a rule that names that
#: family for every element. What is measured here is the application's own choice of face,
#: so the rule is taken out of the page that is being asked; on CI it was never there.
UNFORCED = r"""() => {
  for (const sheet of document.styleSheets) {
    let rules;
    try { rules = sheet.cssRules; } catch (e) { continue; }
    for (let i = rules.length - 1; i >= 0; i--) {
      const style = rules[i].style;
      if (style && style.fontFamily.includes('Postulo E2E')) sheet.deleteRule(i);
    }
  }
}"""

#: The body's computed family list, split into families, and its line height in ems.
TYPE = r"""() => {
  const style = getComputedStyle(document.body);
  return {
    families: style.fontFamily.split(',').map((f) => f.trim().replace(/^["']|["']$/g, '')),
    lineHeight: parseFloat(style.lineHeight) / parseFloat(style.fontSize),
  };
}"""

#: Whether the browser has a face of this family to draw with: a word set in it, with two
#: different generic families behind it, comes out the same width both times only when
#: neither generic family was reached.
HAS_FAMILY = r"""([family, sample]) => {
  const width = (stack) => {
    const span = document.createElement('span');
    span.textContent = sample;
    span.style.fontFamily = stack;
    span.style.fontSize = '48px';
    span.style.position = 'absolute';
    document.body.append(span);
    const measured = span.getBoundingClientRect().width;
    span.remove();
    return measured;
  };
  const serif = width(`"${family}", serif`);
  const mono = width(`"${family}", monospace`);
  return serif === mono && serif !== width('monospace');
}"""


def reads_in(furnished, language: str) -> None:  # noqa: F811
    from postulo.accounts.models import Profile

    Profile.objects.filter(user=furnished["applicant"]).update(language=language)


def test_urdu_asks_for_nastaliq_and_arabic_does_not(live_server, page: Page, furnished):  # noqa: F811
    base = live_server.url
    sign_in(page, base)

    reads_in(furnished, "ur")
    page.goto(f"{base}/applications/")
    page.evaluate(UNFORCED)
    urdu = page.evaluate(TYPE)
    assert urdu["families"][0] == "Noto Nastaliq Urdu", urdu["families"]
    assert urdu["lineHeight"] >= 1.8, "Nastaliq needs the line opened up to 1.8 or more"

    # A table row, a button and a chip read the same variables as the body does.
    for selector in ("main table td", "main .btn", "main .badge"):
        found = page.locator(selector).first
        assert found.count(), f"nothing on the page is {selector}"
        ratio = found.evaluate(
            "el => parseFloat(getComputedStyle(el).lineHeight)"
            " / parseFloat(getComputedStyle(el).fontSize)"
        )
        assert ratio >= 1.8, f"{selector} keeps a line of {ratio:.2f}"

    reads_in(furnished, "ar")
    page.goto(f"{base}/")
    page.evaluate(UNFORCED)
    arabic = page.evaluate(TYPE)
    assert "Noto Nastaliq Urdu" not in arabic["families"], "Arabic keeps its own stack"
    assert arabic["lineHeight"] < 1.8


@pytest.mark.parametrize("width", [1280, NARROW])
def test_urdu_in_nastaliq_clips_nothing(
    live_server,
    page: Page,
    axe_source,  # noqa: F811
    furnished,  # noqa: F811
    width,
):
    base = live_server.url
    sign_in(page, base)
    reads_in(furnished, "ur")
    page.set_viewport_size({"width": width, "height": 900})
    page.goto(f"{base}/")
    page.evaluate(UNFORCED)
    if not page.evaluate(HAS_FAMILY, ["Noto Nastaliq Urdu", "اردو"]):
        pytest.skip("the browser here has no Noto Nastaliq Urdu to draw with")

    failures = []
    for path in PAGES:
        page.goto(f"{base}{path}")
        page.evaluate(UNFORCED)
        for clipped in page.evaluate(CLIPPED):
            # Cut sideways is `truncate` doing its job, with an ellipsis to say so; cut top
            # or bottom is a Nastaliq tail lost to a box measured for Latin.
            if clipped["axis"] != "tall":
                continue
            failures.append(
                f"{path}: words {clipped['needs']}px {clipped['axis']} in a box of "
                f"{clipped['box']}px  {clipped['what']}"
            )
        sideways = page.evaluate(SCROLLS_SIDEWAYS)
        if sideways["reached"]:
            failures.append(f"{path}: scrolls {sideways['reached']}px sideways at {width}")
        found = violations_on(page, axe_source)
        if found:
            failures.append(describe(f"{path} in Urdu at {width}", found))
    assert not failures, "\n".join(failures)


# ------------------------------------------------- lines without spaces (#1052)

#: What may not start a line in Chinese or Japanese, and what may not end one.
CLOSING = "，。、；：？！）」』】〉》〕”’"
OPENING = "（「『【〈《〔“‘"

#: A title, a company and a paragraph in each language, the paragraph long enough to fold
#: many times at 320 pixels, with a kinsoku mark every few characters in the Chinese.
WRITTEN = {
    "zh-Hans": {
        "title": "高级软件工程师（后端），负责服务器、数据库和部署。",
        "company": "黑山研究所（剑桥）",
        "summary": (
            "我们维护了服务器和数据库，部署时间从四十分钟缩短到四分钟。"
            "（另外）我们还指导了三名工程师，「效果」很好！"
        )
        * 4,
    },
    "th": {
        "title": "วิศวกรซอฟต์แวร์อาวุโสของบริษัท",
        "company": "สถาบันวิจัยแบล็กเมซา",
        "summary": "ผมรักแมวและหมาของเขาในบ้าน" * 8,
    },
}

#: Every line start inside a run of Han or Thai on the page: the character it starts with and
#: the one before it, and, in Thai, whether ICU's word segmenter -- the dictionary the line
#: breaker itself uses -- puts a word boundary there. A line start is a character whose box
#: sits a line lower than the character before it in the same text.
LINE_STARTS = r"""() => {
  const spaceless = /[฀-๿　-ヿ一-鿿＀-￯]/;
  const thai = /[฀-๿]/;
  const segmenter = new Intl.Segmenter('th', {granularity: 'word'});
  const out = [];
  const walker = document.createTreeWalker(document.body, NodeFilter.SHOW_TEXT);
  for (let node = walker.nextNode(); node; node = walker.nextNode()) {
    const text = node.textContent;
    if (!spaceless.test(text)) continue;
    if (!node.parentElement || !node.parentElement.checkVisibility()) continue;
    const boundaries = new Set([...segmenter.segment(text)].map((s) => s.index));
    const range = document.createRange();
    let line = null;
    let previous = -1;
    for (let i = 0; i < text.length; i++) {
      if (/\s/.test(text[i])) continue;
      range.setStart(node, i);
      range.setEnd(node, i + 1);
      const box = range.getBoundingClientRect();
      if (!box.height) continue;
      if (line !== null && box.top > line + box.height / 2 && previous >= 0) {
        out.push({
          starts: text[i],
          after: text[previous],
          thai: thai.test(text[i]) && thai.test(text[previous]),
          between_words: boundaries.has(i),
          where: text.slice(Math.max(0, i - 8), i) + '|' + text.slice(i, i + 8),
        });
      }
      line = box.top;
      previous = i;
    }
  }
  return out;
}"""


@pytest.fixture
def spaceless(furnished):  # noqa: F811
    """The furnished account with a title, a company, a capture and a CV in a language
    written without spaces, by the language asked for."""

    def written_in(language: str) -> dict:
        words = WRITTEN[language]
        posting = furnished["application"].posting
        posting.title = words["title"]
        posting.save()
        company = posting.company
        company.name = words["company"]
        company.save()
        capture = furnished["capture"]
        capture.data = {"title": words["title"], "company_name": words["company"]}
        capture.save()
        experience = furnished["experience"]
        experience.summary = words["summary"]
        experience.role = words["title"]
        experience.save()
        cv = furnished["cv"]
        cv.language = language
        cv.headline = words["title"]
        cv.save()
        return furnished

    return written_in


@pytest.mark.parametrize("language", ["zh-Hans", "th"])
def test_spaceless_scripts_wrap_at_legal_points_at_320(
    live_server, page: Page, spaceless, language
):
    """The review page, a table and a CV's preview at 320 pixels: nothing pushes the page
    sideways, no line starts with a closing mark or ends with an opening one, and Thai
    folds only between words.

    Chinese is offered (#728), so the account reads the interface in it. Thai is not yet, so
    the interface stays in English around Thai content, and the CV says it is in Thai.
    """
    things = spaceless(language)
    if language == "zh-Hans":
        reads_in(things, language)
    base = live_server.url
    sign_in(page, base)
    page.set_viewport_size({"width": NARROW, "height": 900})

    failures = []
    starts = 0
    for path in (
        f"/jobs/captures/{things['capture'].pk}/review/",
        "/applications/",
        f"/documents/cvs/{things['cv'].pk}/preview/",
    ):
        page.goto(f"{base}{path}")
        sideways = page.evaluate(SCROLLS_SIDEWAYS)
        if sideways["reached"]:
            culprits = "; ".join(culprit["what"] for culprit in sideways["culprits"][:3])
            failures.append(f"{path}: scrolls {sideways['reached']}px sideways  {culprits}")
        for start in page.evaluate(LINE_STARTS):
            starts += 1
            if start["starts"] in CLOSING:
                failures.append(f"{path}: a line starts with {start['starts']}  {start['where']}")
            if start["after"] in OPENING:
                failures.append(f"{path}: a line ends with {start['after']}  {start['where']}")
            if start["thai"] and not start["between_words"]:
                failures.append(f"{path}: Thai broken inside a word  {start['where']}")

    assert starts > 5, "the paragraphs were meant to fold, and the check to see them fold"
    assert not failures, "\n".join(failures)


def test_korean_keeps_its_words_whole_and_chinese_is_set_strictly(live_server, page: Page, db):
    """`:lang(ko)` breaks Hangul at its spaces rather than between syllables, and
    `:lang(zh)` asks for the strict kinsoku set. Korean is not offered yet, so the page is
    told it is Korean the way a passage in Korean would say so."""
    page.goto(f"{live_server.url}/accounts/login/")
    styles = page.evaluate(
        """() => {
          const ask = (lang) => {
            document.documentElement.lang = lang;
            const style = getComputedStyle(document.body);
            return {wordBreak: style.wordBreak, lineBreak: style.lineBreak};
          };
          return {ko: ask('ko'), zh: ask('zh-Hans'), ja: ask('ja'), en: ask('en-GB')};
        }"""
    )
    assert styles["ko"]["wordBreak"] == "keep-all"
    assert styles["zh"]["lineBreak"] == "strict"
    assert styles["ja"]["lineBreak"] == "strict"
    assert styles["en"] == {"wordBreak": "normal", "lineBreak": "auto"}
