"""Publications as BibTeX, BibLaTeX, RIS, CSL JSON, MODS and EndNote files, out and in (#879)."""

from __future__ import annotations

import json
import time

import pytest
from django.core.files.uploadedfile import SimpleUploadedFile
from django.urls import reverse

from postulo.plugins.models import PluginPolicy
from postulo.resume import references
from postulo.resume.models import Publication

pytestmark = pytest.mark.django_db

BIB = r"""
@string{nat = "Nature"}
% a comment
@article{harris2020,
  author  = {Harris, Charles R. and M{\"u}ller, Hans and {Smith and Sons} and others},
  title   = {Array programming with {NumPy} \& friends},
  journal = nat,
  year    = 2020,
  month   = sep,
  volume  = {585},
  number  = {7825},
  pages   = {357--362},
  doi     = {10.1038/s41586-020-2649-2},
  url     = {https://example.org/a_b},
  abstract = {A long abstract that has no place here.},
  keywords = {python, arrays},
  note    = {An <b>odd</b> note}
}
@thesis{kay2019,
  author = {Kay, Alan},
  title = {A master's work},
  type = {mastersthesis},
  institution = {University of Utah},
  date = {2019-06-01},
  location = {Salt Lake City}
}
@inproceedings{broken, title = {No close
"""

RIS = """TY  - JOUR
AU  - Harris, Charles R.
AU  - Millman, K. Jarrod
TI  - Array programming with NumPy
JO  - Nature
PY  - 2020/09/16/
VL  - 585
IS  - 7825
SP  - 357
EP  - 362
DO  - 10.1038/s41586-020-2649-2
UR  - https://example.org/paper
UR  - javascript:alert(1)
AB  - abstract
KW  - python
ER  -

TY  - CONF
TI  - Notebooks in class
T2  - Proceedings of Something
PY  - 2019///
ER  -
"""

CSL = [
    {
        "id": "harris2020",
        "type": "article-journal",
        "title": "Array programming with <i>NumPy</i>",
        "author": [{"family": "Harris", "given": "Charles R."}, {"literal": "NumPy Team"}],
        "editor": [{"family": "Ed", "given": "Ita"}],
        "container-title": "Nature",
        "issued": {"date-parts": [[2020, 9, 16]]},
        "volume": "585",
        "issue": "7825",
        "page": "357-362",
        "DOI": "10.1038/s41586-020-2649-2",
        "URL": "https://example.org/paper",
        "abstract": "hidden",
    },
    {"type": "thesis", "genre": "Master's thesis", "title": "Theory", "publisher": "Utah"},
    "nonsense",
]


def make(user, **values) -> Publication:
    values.setdefault("title", "A paper")
    return Publication.objects.create(owner=user, **values)


# ---------------------------------------------------------------------- reading


def test_bibtex_is_read_into_a_publications_fields():
    reading = references.read(BIB.encode(), "refs.bib")

    first, second = reading.entries
    assert first["entry_type"] == "article"
    assert first["title"] == "Array programming with NumPy & friends", "braces and escapes undone"
    assert first["authors"] == "Harris, Charles R.\nMüller, Hans\nSmith and Sons\nand others"
    assert first["container_title"] == "Nature", "a @string is resolved by the file's own"
    assert first["date"] == "2020-09"
    assert first["pages"] == "357–362"
    assert first["doi"] == "10.1038/s41586-020-2649-2"
    assert first["url"] == "https://example.org/a_b"
    assert first["cite_key"] == "harris2020"
    assert second["entry_type"] == "mastersthesis"
    assert second["institution"] == "University of Utah" and second["date"] == "2019-06-01"
    assert reading.skipped == 1, "the entry that never closes is counted, not guessed at"
    assert reading.left_out == ["abstract", "keywords"], "what has no place is named"


def test_ris_is_read_and_a_link_that_is_not_http_is_dropped():
    reading = references.read(RIS.encode(), "refs.ris")

    first, second = reading.entries
    assert first["entry_type"] == "article"
    assert first["authors"] == "Harris, Charles R.\nMillman, K. Jarrod"
    assert (first["container_title"], first["date"], first["pages"]) == (
        "Nature",
        "2020-09-16",
        "357–362",
    )
    assert first["url"] == "https://example.org/paper"
    assert second["entry_type"] == "proceedings" and second["title"] == "Notebooks in class"
    assert second["date"] == "2019"
    assert "abstract" in reading.left_out and "keywords" in reading.left_out


def test_csl_json_is_read_and_a_record_that_is_not_one_is_skipped():
    reading = references.read(json.dumps(CSL).encode(), "refs.json")

    first, second = reading.entries
    assert first["title"] == "Array programming with NumPy"
    assert first["authors"] == "Harris, Charles R.\nNumPy Team"
    assert first["editors"] == "Ed, Ita"
    assert (first["date"], first["number"], first["cite_key"]) == (
        "2020-09-16",
        "7825",
        "harris2020",
    )
    assert first["url"] == "https://example.org/paper"
    assert second["entry_type"] == "mastersthesis" and second["institution"] == "Utah"
    assert reading.skipped == 1
    assert "abstract" in reading.left_out


def test_a_file_that_is_none_of_them_is_refused_with_a_sentence():
    with pytest.raises(references.ReferencesRefused):
        references.read(b"just some text", "notes.txt")
    with pytest.raises(references.ReferencesRefused):
        references.read(b"{not json", "x.json")


# ------------------------------------------------------------- hostile input


def test_a_file_over_the_limit_is_refused_before_it_is_parsed():
    with pytest.raises(references.ReferencesRefused):
        references.read(b"@misc{a, title={x}}\n" * 200_000, "big.bib")


def test_more_entries_than_the_limit_are_counted_not_read():
    body = "".join(f"@misc{{k{n}, title={{T{n}}}}}\n" for n in range(references.MAX_ENTRIES + 7))

    reading = references.read(body.encode(), "many.bib")

    assert len(reading.entries) == references.MAX_ENTRIES and reading.beyond == 7


@pytest.mark.parametrize("depth", [50_000])
def test_deeply_nested_braces_and_json_are_refused_or_cut_and_never_crash(depth):
    started = time.monotonic()
    nested = "@misc{k, title = " + "{" * depth + "x" + "}" * depth + "}"
    try:
        references.read(nested.encode(), "nested.bib")
    except references.ReferencesRefused:
        pass
    # Whether Python's JSON reader refuses 50,000 nested lists depends on its patch version
    # (3.14.7 parses them, later ones may not): either is fine, so long as nothing crashes and
    # nothing is kept.
    try:
        reading = references.read(("[" * depth + "]" * depth).encode(), "nested.json")
    except references.ReferencesRefused:
        pass
    else:
        assert not reading.entries
    assert time.monotonic() - started < 20


def test_a_long_value_is_cut_to_its_column_and_markup_and_overrides_are_plain_text():
    long_title = "x" * 5000
    body = f"@misc{{k, title = {{{long_title}\u202e<script>alert(1)</script>}}}}"

    (entry,) = references.read(body.encode(), "x.bib").entries

    assert len(entry["title"]) <= 500
    assert "\u202e" not in entry["title"]
    (hostile,) = references.read(
        rb"@misc{k, title = {a \input{/etc/passwd} \write18{id} <img src=x>}}", "y.bib"
    ).entries
    assert "\n" not in hostile["title"], "read as text, never run or included"


def test_a_bibtex_string_is_only_ever_its_own_text():
    body = '@string{a = "x"}\n@misc{k, title = a}\n@misc{j, title = missing}'

    titles = [entry["title"] for entry in references.read(body.encode(), "s.bib").entries]

    assert titles[0] == "x"
    assert "etc" not in " ".join(titles)


def test_latin_1_bibtex_is_read_too():
    (entry,) = references.read("@misc{k, title = {Müller}}".encode("latin-1"), "l.bib").entries
    assert entry["title"] == "Müller"


# ------------------------------------------------------------------- writing


def test_bibtex_out_protects_the_title_escapes_and_drops_what_bibtex_cannot_say(user):
    item = make(
        user,
        entry_type="dataset",
        title="R&D {raw} 100%",
        authors="Smith and Sons Ltd\nand others",
        date="2024-05-17",
        pages="1–5",
        doi="10.1000/182",
        cite_key="rd2024",
    )

    out = references.write(references.BIBTEX, [item])

    assert out.startswith("@misc{rd2024,"), "BibTeX has no dataset"
    assert r"title = {{R\&D raw 100\%}}" in out
    assert "{Smith and Sons Ltd} and others" in out
    assert "year = {2024}" in out and "month = {may}" in out and "date" not in out
    assert "pages = {1--5}" in out
    assert "doi = {10.1000/182}" in out


def test_biblatex_out_keeps_the_type_and_the_whole_date(user):
    item = make(user, entry_type="dataset", date="2024-05-17", container_title="Zenodo")

    out = references.write(references.BIBLATEX, [item])

    assert out.startswith("@dataset{") and "date = {2024-05-17}" in out
    assert "titleaddon = {Zenodo}" in out


@pytest.mark.parametrize(
    "fmt",
    [
        references.BIBTEX,
        references.BIBLATEX,
        references.RIS,
        references.CSL_JSON,
        references.MODS,
    ],
)
def test_what_is_written_reads_back_the_same(user, fmt):
    item = make(
        user,
        entry_type="article",
        title="Array programming: a {survey} & more",
        authors="Harris, Charles R.\nMüller, Hans",
        editors="Ed, Ita",
        container_title="Nature",
        date="2020-09-16",
        volume="585",
        number="7825",
        pages="357–362",
        doi="10.1038/s41586-020-2649-2",
        url="https://example.org/a",
        cite_key="harris2020",
    )

    out = references.write(fmt, [item])
    (back,) = references.read(out.encode(), f"x.{references.FORMATS[fmt].extension}").entries

    expected = {
        "title": "Array programming: a survey & more",
        "authors": "Harris, Charles R.\nMüller, Hans",
        "container_title": "Nature",
        "date": "2020-09-16",
        "volume": "585",
        "number": "7825",
        "pages": "357–362",
        "doi": "10.1038/s41586-020-2649-2",
        "url": "https://example.org/a",
        "cite_key": "harris2020",
        "entry_type": "article",
    }
    if fmt == references.BIBTEX:
        expected["date"] = "2020-09"
    if fmt in (references.RIS, references.CSL_JSON, references.MODS):
        # Braces are the title's own here; only BibTeX's use of them is decoded.
        expected["title"] = "Array programming: a {survey} & more"
    for name, value in expected.items():
        assert back.get(name) == value, (fmt, name)
    if fmt != references.RIS:
        assert back["editors"] == "Ed, Ita"


MODS_FILE = """<?xml version="1.0" encoding="UTF-8"?>
<modsCollection xmlns="http://www.loc.gov/mods/v3" xmlns:xlink="http://www.w3.org/1999/xlink">
  <mods version="3.7" ID="harris2020">
    <titleInfo>
      <nonSort>The</nonSort><title>NumPy array</title><subTitle>a survey</subTitle>
    </titleInfo>
    <name type="personal">
      <namePart type="family">Harris</namePart><namePart type="given">Charles R.</namePart>
      <role><roleTerm type="text" authority="marcrelator">author</roleTerm></role>
    </name>
    <name type="personal"><namePart>Hans Muller</namePart></name>
    <name type="corporate"><namePart>NumPy Team</namePart>
      <role><roleTerm type="code" authority="marcrelator">aut</roleTerm></role></name>
    <name type="personal">
      <namePart type="family">Ed</namePart><namePart type="given">Ita</namePart>
      <role><roleTerm type="text">editor</roleTerm></role>
    </name>
    <typeOfResource>text</typeOfResource>
    <genre authority="marcgenre">journal article</genre>
    <originInfo><dateIssued encoding="w3cdtf">2020-09-16</dateIssued><publisher>Springer</publisher>
      <place><placeTerm type="text">London</placeTerm></place></originInfo>
    <abstract>Long text with no place here.</abstract>
    <subject><topic>arrays</topic></subject>
    <relatedItem type="host">
      <titleInfo><title>Nature</title></titleInfo>
      <part><detail type="volume"><number>585</number></detail>
        <detail type="issue"><number>7825</number></detail>
        <extent unit="page"><start>357</start><end>362</end></extent></part>
    </relatedItem>
    <identifier type="doi">https://doi.org/10.1038/s41586-020-2649-2</identifier>
    <identifier type="issn">0028-0836</identifier>
    <location><url>https://example.org/paper</url></location>
    <location><url>javascript:alert(1)</url></location>
  </mods>
  <mods version="3.7"><genre>thesis</genre><genre>Masters thesis</genre>
    <titleInfo><title>Theory</title></titleInfo>
    <originInfo><publisher>Utah</publisher></originInfo></mods>
  <mods version="3.7"><genre>book</genre></mods>
</modsCollection>
"""

MODS_WITH_ENTITY = (
    '<?xml version="1.0"?><!DOCTYPE m [<!ENTITY x "boom">]>'
    '<mods xmlns="http://www.loc.gov/mods/v3"><titleInfo><title>&x;</title></titleInfo></mods>'
)
MODS_CUT_OFF = '<modsCollection xmlns="http://www.loc.gov/mods/v3"><mods><titleInfo>'


def test_mods_is_read_into_a_publications_fields():
    reading = references.read(MODS_FILE.encode(), "refs.xml")

    first, second = reading.entries
    assert reading.format == references.MODS
    assert first["entry_type"] == "article"
    assert first["title"] == "The NumPy array: a survey"
    assert first["authors"] == "Harris, Charles R.\nHans Muller\nNumPy Team"
    assert first["editors"] == "Ed, Ita"
    assert (first["container_title"], first["volume"], first["number"]) == ("Nature", "585", "7825")
    assert first["pages"] == "357–362" and first["date"] == "2020-09-16"
    assert (first["publisher"], first["location"]) == ("Springer", "London")
    assert first["doi"] == "10.1038/s41586-020-2649-2"
    assert first["url"] == "https://example.org/paper", "a link that is not http is dropped"
    assert first["cite_key"] == "harris2020"
    assert second["entry_type"] == "mastersthesis" and second["institution"] == "Utah"
    assert reading.skipped == 1, "a record with no title is counted"
    assert reading.left_out == ["ISBN/ISSN", "abstract", "keywords"]


@pytest.mark.parametrize("body", [MODS_WITH_ENTITY, MODS_CUT_OFF])
def test_mods_with_entities_or_broken_markup_is_refused_not_expanded(body):
    with pytest.raises(references.ReferencesRefused):
        references.read_mods(body.encode())


def test_mods_out_is_well_formed_xml_whatever_the_title_holds(user):
    title = "A <b>&</b> " + chr(11) + " title"
    item = make(user, title=title, authors="Smith, Jo\nand others", entry_type="phdthesis")
    out = references.write(references.MODS, [item])

    assert out.startswith('<?xml version="1.0" encoding="UTF-8"?>')
    (back,) = references.read(out.encode(), "x.xml").entries
    assert back["title"] == "A <b>&</b> title"
    assert back["entry_type"] == "phdthesis" and back["authors"] == "Smith, Jo"


# ------------------------------------------------------------------- the page


def test_the_page_offers_each_format_and_a_download_is_the_callers_own(client, user, other_user):
    make(user, title="Mine", cite_key="mine1")
    make(other_user, title="Theirs", cite_key="theirs1")
    client.force_login(user)

    page = client.get(reverse("resume:references"))
    response = client.get(reverse("resume:references"), {"download": "ris"})

    assert page.status_code == 200
    for key in references.FORMATS:
        assert f'data-download="{key}"' in page.content.decode()
    assert response["Content-Type"].startswith("application/x-research-info-systems")
    assert (
        ".ris" in response["Content-Disposition"]
        and "attachment" in response["Content-Disposition"]
    )
    body = response.content.decode()
    assert "Mine" in body and "Theirs" not in body


def test_one_publication_can_be_exported_and_a_foreign_one_is_a_404(client, user, other_user):
    mine = make(user, title="Mine")
    make(user, title="Another")
    theirs = make(other_user, title="Theirs")
    client.force_login(user)

    one = client.get(reverse("resume:references"), {"download": "csl-json", "publication": mine.pk})
    foreign = client.get(
        reverse("resume:references"), {"download": "csl-json", "publication": theirs.pk}
    )
    unknown = client.get(reverse("resume:references"), {"download": "pdf"})

    assert [record["title"] for record in json.loads(one.content)] == ["Mine"]
    assert foreign.status_code == 404 and unknown.status_code == 404


def upload(client, body: str | bytes, name: str):
    data = body.encode() if isinstance(body, str) else body
    return client.post(
        reverse("resume:references"), {"file": SimpleUploadedFile(name, data)}, follow=True
    )


def test_a_file_is_listed_with_nothing_ticked_and_nothing_is_added_until_it_is(client, user):
    make(user, title="Array programming with NumPy & friends", date="2020-09")
    client.force_login(user)

    page = upload(client, BIB, "refs.bib")
    html = page.content.decode()

    assert "data-references-entries" in html
    assert (
        'name="entry" value="0"' in html
        and "checked" not in html.split("data-references-entries")[1]
    )
    assert html.count("data-references-held") == 1, "the one the person has is marked"
    assert "data-references-skipped" in html and "abstract, keywords" in html
    assert Publication.objects.for_user(user).count() == 1


def test_the_ticked_entries_are_added_with_their_keys_and_the_rest_are_not(client, user):
    make(user, title="Other", cite_key="harris2020")
    client.force_login(user)
    upload(client, BIB, "refs.bib")

    client.post(reverse("resume:references"), {"action": "confirm", "entry": ["0"]})

    added = Publication.objects.for_user(user).exclude(title="Other").get()
    assert added.title.startswith("Array programming")
    assert added.cite_key != "harris2020" and added.cite_key, "a key somebody holds is not taken"
    assert Publication.objects.for_user(user).count() == 2


def test_nothing_ticked_adds_nothing_and_forget_drops_the_list(client, user):
    client.force_login(user)
    upload(client, BIB, "refs.bib")

    client.post(reverse("resume:references"), {"action": "confirm"}, follow=True)
    assert Publication.objects.for_user(user).count() == 0
    client.post(reverse("resume:references"), {"action": "forget"})

    assert (
        "data-references-entries" not in client.get(reverse("resume:references")).content.decode()
    )


def test_an_account_cannot_add_from_anothers_held_list(client, user, other_user):
    client.force_login(user)
    upload(client, BIB, "refs.bib")
    client.logout()
    client.force_login(other_user)

    client.post(reverse("resume:references"), {"action": "confirm", "entry": ["0"]})

    assert Publication.objects.count() == 0


def test_a_hand_made_held_entry_is_held_to_the_rules_again(client, user):
    client.force_login(user)
    session = client.session
    session[references.SESSION_KEY] = [
        {
            "title": "T",
            "entry_type": "nonsense",
            "date": "soon",
            "doi": "x",
            "cite_key": "bad key!",
        },
        {"title": "", "entry_type": "article"},
        "junk",
    ]
    session.save()

    client.post(reverse("resume:references"), {"action": "confirm", "entry": ["0", "1", "9"]})

    item = Publication.objects.get()
    assert (item.entry_type, item.date, item.doi) == ("misc", "", "")
    assert item.cite_key != "bad key!"


def test_switched_off_the_page_answers_as_if_it_were_not_there(client, user):
    PluginPolicy.objects.create(plugin="format-bibliography", person=None, state="off")
    client.force_login(user)

    assert client.get(reverse("resume:references")).status_code == 404
    assert "references" not in client.get(reverse("resume:overview")).content.decode()


def test_the_career_page_links_each_publication_and_the_transfer_menu(client, user):
    item = make(user, title="Linked")
    client.force_login(user)

    html = client.get(reverse("resume:overview")).content.decode()

    assert f"?publication={item.pk}" in html and "data-publication-export" in html
    assert reverse("resume:references") in html


# -------------------------------------------------------- the online half, off by default


def test_the_instance_starts_with_the_doi_lookup_off_and_nothing_is_asked(client, user):
    from postulo.plugins.registry import find_any

    assert find_any("bibliography-lookups") is not None
    client.force_login(user)

    page = client.get(reverse("resume:references")).content.decode()
    refused = client.post(reverse("resume:references"), {"action": "dois", "dois": "10.1000/182"})

    assert "data-references-dois" not in page
    assert refused.status_code == 404


def test_switched_on_each_doi_is_one_request_and_the_answer_is_reviewed_not_saved(
    client, user, monkeypatch
):
    from postulo.resume import lookups

    PluginPolicy.objects.filter(plugin="bibliography-lookups", person=None).delete()
    asked = []

    def fake(doi):
        asked.append(doi)
        return lookups.Found(
            "Crossref", {"title": f"Paper {doi}", "doi": doi, "entry_type": "article"}
        )

    monkeypatch.setattr(lookups, "from_doi", fake)
    client.force_login(user)

    page = client.get(reverse("resume:references")).content.decode()
    client.post(
        reverse("resume:references"),
        {
            "action": "dois",
            "dois": "https://doi.org/10.1000/182\n10.1000/182\ndoi:10.1000/183 nope",
        },
    )
    listed = client.get(reverse("resume:references")).content.decode()

    assert "data-references-dois" in page
    assert asked == ["10.1000/182", "10.1000/183"], "typed twice, asked once; nonsense never sent"
    assert "Paper 10.1000/182" in listed and Publication.objects.count() == 0


def test_the_doi_lookup_needs_publication_lookups_and_the_bibliography(client, user):
    PluginPolicy.objects.filter(plugin="bibliography-lookups", person=None).delete()
    PluginPolicy.objects.create(plugin="publication-lookups", person=None, state="off")
    client.force_login(user)

    assert "data-references-dois" not in client.get(reverse("resume:references")).content.decode()
