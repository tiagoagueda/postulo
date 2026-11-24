"""The placeholder checks as the first writing checker, and the one list they are drawn in (#1258).

What `rendering.unfilled_placeholders` said in one sentence before a letter was frozen is a
`writing-checker` now, bundled and always on, and its findings are lines of the list the
letter's own page and the freezing page both draw. One list, not two; read out with its
count; whole with scripts off, which is the only way the unit suite ever sees it.
"""

from __future__ import annotations

import re

import pytest
from django.urls import reverse

from postulo.applications.models import Application, Status
from postulo.core import writing_checks
from postulo.documents import checking, rendering
from postulo.documents.models import CoverLetter, RenderedDocument
from postulo.jobs.models import Company, JobPosting
from postulo.plugins import policy, registry
from postulo.plugins.testing import assert_is_a_writing_checker, writing_findings
from tests import writing


@pytest.fixture(autouse=True)
def _fresh_registry():
    writing.forget()
    yield
    writing.forget()


@pytest.fixture
def application(db, user):
    company = Company.objects.create(owner=user, name="Black Mesa")
    posting = JobPosting.objects.create(owner=user, company=company, title="Research Engineer")
    return Application.objects.create(owner=user, posting=posting, status=Status.APPLIED)


@pytest.fixture
def letter(db, user):
    return CoverLetter.objects.create(
        owner=user,
        name="General",
        subject="About {{ role }}",
        body="Dear {{ company }},\r\n\r\nThe role in {{ location }} suits me.\r\n\r\n{{ name }}",
    )


def checks_of(listing) -> list[tuple[str, str]]:
    return [
        (line.finding.check, line.finding.severity) for line in (*listing.problems, *listing.hints)
    ]


# ---------------------------------------------------------------------- the plugin


def test_the_placeholder_checks_ship_always_on_and_keep_the_contract(db):
    plugin = registry.find_plugin("writing-checker", "placeholder-checks")

    assert plugin is not None and plugin.kind == "writing-checker"
    assert policy.is_required("placeholder-checks"), "no switch moves it"
    assert_is_a_writing_checker(plugin)
    (checker,) = plugin.checkers
    assert checker.offline and not checker.hosts and not checker.sends


def test_no_switch_turns_the_placeholder_checks_off(user):
    from postulo.core.memo import forget_decisions
    from postulo.plugins.models import PluginPolicy

    PluginPolicy.objects.create(
        plugin="placeholder-checks", person=None, state=PluginPolicy.State.FORCED_OFF
    )
    forget_decisions()

    assert [c.key for c in writing_checks.checkers("letter", user)] == ["placeholder-checks"]


def test_the_checker_reports_what_the_function_before_it_reported(letter, application, user):
    """The step before freezing is made of the same answer it always was."""
    for body in (
        letter.body,
        "{{ location }} twice: {{ location }}, and {{ contact }} once. {{ compnay }}",
        "Nothing to fill in here.",
    ):
        letter.body = body
        listing = checking.letter_findings(letter, application, user)
        empty = [
            line.finding.message
            for line in listing.problems
            if line.finding.check == "placeholder-empty"
        ]

        expected = rendering.unfilled_placeholders(letter, application)
        assert empty == [
            f"Nothing fills in {{{{ {name} }}}}, so it goes out blank." for name in expected
        ]


def test_with_no_application_a_template_is_not_told_its_company_is_missing(letter, user):
    listing = checking.letter_findings(letter, None, user)

    assert rendering.unfilled_placeholders(letter) == ["role", "company", "location"]
    assert checks_of(listing) == [], "what only an application fills in is not asked about"
    assert listing.unasked, "and the list says it was not"


def test_a_misspelt_placeholder_is_a_problem_with_the_real_name_proposed():
    from postulo.plugins.placeholder_checks import PlaceholderChecks

    (found,) = writing_findings(PlaceholderChecks(), "Dear {{ compnay }}, about {{rol}}.")[:1]
    second = writing_findings(PlaceholderChecks(), "Dear {{ compnay }}, about {{rol}}.")[1]

    assert found["check"] == "placeholder-unknown" and found["severity"] == "problem"
    assert found["text"] == "{{ compnay }}", "the whole of what was typed, braces and all"
    assert found["replacements"] == ("{{ company }}",)
    assert found["message"] == "{{ compnay }} is not a placeholder, so it goes out as typed."
    assert second["text"] == "{{rol}}" and second["replacements"] == ("{{ role }}",)


def test_a_name_like_none_of_the_six_is_a_problem_with_nothing_proposed():
    from postulo.plugins.placeholder_checks import PlaceholderChecks

    (found,) = writing_findings(PlaceholderChecks(), "Yours, {{ xyzzy }}")

    assert found["check"] == "placeholder-unknown" and found["replacements"] == ()


def test_the_checker_is_handed_the_persons_own_stored_text_and_plain_values(
    letter, application, user, monkeypatch
):
    """The words as stored -- placeholders as typed, line endings as kept -- and nothing that
    reaches a model: a checker cannot follow anything it is handed back to the database."""
    handed = []
    writing.install(
        monkeypatch, writing.plugin("mine", writing.checker(lambda d: handed.append(d) or []))
    )

    checking.letter_findings(letter, application, user)

    (document,) = handed
    assert document.passage("body").text == letter.body, "CRLF and braces exactly as stored"
    assert document.passage("subject").text == letter.subject
    assert "Black Mesa" not in document.passage("body").text, "not filled in"
    company = document.passage("body").placeholders[0]
    assert (company.name, company.start, company.end) == ("company", 5, 18)
    assert company.known and company.value == "Black Mesa"
    assert document.facts["company"] == "Black Mesa" and document.facts["role"]
    assert document.placeholder_names == tuple(CoverLetter.PLACEHOLDERS)

    def plain(value) -> bool:
        if isinstance(value, str | int | bool | type(None)):
            return True
        if isinstance(value, tuple | list):
            return all(plain(item) for item in value)
        if hasattr(value, "items"):
            return all(plain(k) and plain(v) for k, v in value.items())
        if type(value).__module__ == "postulo.core.writing_checks":
            return all(plain(getattr(value, name)) for name in value.__dataclass_fields__)
        return False

    assert plain(document)


# ------------------------------------------------------------------ the letter's page


def test_the_letters_page_draws_one_list_and_says_how_many(client, user, letter, application):
    client.force_login(user)

    page = client.get(letter.get_absolute_url(), {"application": application.pk}).content.decode()
    drawn = writing.section(page)

    assert page.count('id="writing-findings"') == 1, "one list, not two"
    assert 'aria-labelledby="writing-findings-heading"' in drawn and 'tabindex="-1"' in drawn
    assert re.search(r'<p role="status"[^>]*>1 thing will go out wrong\.</p>', drawn)
    assert "Nothing fills in {{ location }}, so it goes out blank." in drawn
    assert "Goes out wrong" in drawn, "how serious, in a word and not only a colour"
    assert "Placeholder checks" in drawn, "and who said it"


def test_the_quoted_words_carry_the_letters_language_and_direction(client, user, application):
    theirs = CoverLetter.objects.create(
        owner=user, name="رسالة", language="ar", body="السيدة {{ contact }}، أكتب إليكم"
    )
    client.force_login(user)

    page = client.get(theirs.get_absolute_url(), {"application": application.pk}).content.decode()

    assert '<bdi lang="ar" dir="rtl"' in writing.section(page)
    assert "<mark>{{ contact }}</mark>" in writing.section(page)


def test_a_letter_with_nothing_wrong_says_so_in_a_line(client, user, application):
    fine = CoverLetter.objects.create(owner=user, name="Fine", body="Dear {{ company }},")
    client.force_login(user)

    page = client.get(fine.get_absolute_url(), {"application": application.pk}).content.decode()

    assert re.search(r'<p role="status"[^>]*>Nothing to point out\.</p>', writing.section(page))
    assert "data-finding" not in writing.section(page).replace("data-writing-findings", "")


def test_with_no_application_chosen_the_page_says_what_was_not_checked(client, user, letter):
    client.force_login(user)

    drawn = writing.section(client.get(letter.get_absolute_url()).content.decode())

    assert "Nothing to point out." in drawn
    assert "No application is chosen" in drawn


def test_the_chooser_draws_the_whole_page_for_the_application_picked(
    client, user, letter, application
):
    """One choice, one truth: the frame, the PDF and the list follow the same application."""
    client.force_login(user)
    address = letter.get_absolute_url()

    plain = client.get(address).content.decode()
    chosen = client.get(address, {"application": application.pk}).content.decode()

    preview = reverse("documents:letter_preview", args=[letter.pk])
    draft = reverse("documents:letter_draft", args=[letter.pk])
    assert f'action="{address}#writing-findings" id="letter-chooser"' in plain
    assert 'target="letter-preview"' not in plain, "the chooser no longer re-targets the frame"
    assert f'<iframe src="{preview}" name="letter-preview"' in plain
    assert f'data-src="{draft}?inline=1"' in plain
    assert f'<iframe src="{preview}?application={application.pk}"' in chosen
    assert f'data-src="{draft}?inline=1&amp;application={application.pk}"' in chosen
    assert chosen.count(f'<option value="{application.pk}" selected>') == 2
    assert " selected>" not in plain.split('id="preview-application"')[1].split("</select>")[0]


def test_the_chooser_keeps_the_paper_picked_and_the_pdf_is_drawn_on_it(client, user, letter):
    """The chooser carries the paper as well; the page that comes back keeps it, and leaves
    out one the letter's theme does not print on."""
    from postulo.documents.forms import PaperForm
    from postulo.documents.themes import Kind

    client.force_login(user)
    address = letter.get_absolute_url()
    offered = PaperForm(theme=letter.theme, kind=Kind.LETTER, owner=user).fields["paper"]
    paper = [key for key, _label in offered.choices][-1]

    kept = client.get(address, {"paper": paper}).content.decode()
    refused = client.get(address, {"paper": "no-such-paper"}).content.decode()

    draft = reverse("documents:letter_draft", args=[letter.pk])
    assert f'data-src="{draft}?inline=1&amp;paper={paper}"' in kept
    assert f'<option value="{paper}" selected>' in kept
    assert f'data-src="{draft}?inline=1"' in refused
    assert 'paper=no-such-paper"' not in refused.split("letter-chooser")[1]


def test_somebody_elses_application_draws_the_list_for_none(client, user, other_user, letter):
    company = Company.objects.create(owner=other_user, name="Aperture Science")
    posting = JobPosting.objects.create(owner=other_user, company=company, title="Tester")
    theirs = Application.objects.create(owner=other_user, posting=posting)
    client.force_login(user)

    page = client.get(letter.get_absolute_url(), {"application": theirs.pk}).content.decode()

    assert "Aperture" not in page
    assert "No application is chosen" in writing.section(page)


def test_what_a_plugin_says_is_a_line_of_the_same_list_and_is_escaped(
    client, user, letter, application, monkeypatch
):
    def check(document):
        yield writing.finding(
            check="shouting", message="<b>Bold</b> & <script>alert(1)</script>", start=0, end=4
        )

    writing.install(
        monkeypatch,
        writing.plugin("mine", writing.checker(check, label="<i>Their</i> checks")),
    )
    client.force_login(user)

    page = client.get(letter.get_absolute_url(), {"application": application.pk}).content.decode()
    drawn = writing.section(page)

    assert page.count('id="writing-findings"') == 1
    assert 'data-check="placeholder-empty"' in drawn and 'data-check="shouting"' in drawn
    assert "&lt;b&gt;Bold&lt;/b&gt; &amp; &lt;script&gt;" in drawn
    assert "<script>alert" not in drawn and "<i>Their</i>" not in drawn
    assert "1 thing will go out wrong. 1 suggestion." in drawn
    assert "<mark>Dear</mark> {{ company }}," in drawn


def test_a_passage_too_long_to_check_is_said(client, user):
    long = CoverLetter.objects.create(owner=user, name="Long", body="word " * 4001)
    client.force_login(user)

    drawn = writing.section(client.get(long.get_absolute_url()).content.decode())

    assert "too long to be checked" in drawn


def test_with_no_checker_at_all_the_page_draws_no_list(client, user, letter, monkeypatch):
    """With every checker gone the page is what it was before there was a list."""
    writing.only(monkeypatch)
    client.force_login(user)

    response = client.get(letter.get_absolute_url())

    assert response.status_code == 200
    assert "writing-findings" not in response.content.decode().replace("#writing-findings", ""), (
        "no section, and nothing that points at one but the chooser's landing place"
    )
    assert response.context["findings"] is None


def test_looking_at_the_page_changes_nothing(client, user, letter, application):
    """A suggestion is never applied without the person's press: a page view is not one."""
    before = (letter.subject, letter.body, letter.updated_at)
    client.force_login(user)

    client.get(letter.get_absolute_url(), {"application": application.pk})
    client.post(reverse("documents:send", args=[application.pk]), {"cover_letter": letter.pk})

    letter.refresh_from_db()
    assert (letter.subject, letter.body, letter.updated_at) == before


# ---------------------------------------------------------------- before freezing


def test_the_step_before_freezing_draws_the_same_list(client, user, letter, application):
    client.force_login(user)

    response = client.post(
        reverse("documents:send", args=[application.pk]), {"cover_letter": str(letter.pk)}
    )
    page = response.content.decode()
    drawn = writing.section(page)

    assert response.status_code == 200, "nothing was frozen yet"
    assert page.count('id="writing-findings"') == 1
    assert "Nothing fills in {{ location }}, so it goes out blank." in drawn
    assert "[ location ]" in drawn, "and the letter as it will be frozen, marked"
    assert "Freeze it anyway" in page
    assert "data-writing-apply" not in page, "nothing is replaced from here"
    assert f'href="{letter.get_absolute_url()}?application={application.pk}#writing-findings"' in (
        drawn
    )
    assert not RenderedDocument.objects.filter(owner=user).exists()


def test_a_misspelt_placeholder_puts_the_step_in_front_of_freezing_too(client, user, application):
    typo = CoverLetter.objects.create(owner=user, name="Typo", body="Dear {{ compnay }},")
    client.force_login(user)

    response = client.post(
        reverse("documents:send", args=[application.pk]), {"cover_letter": str(typo.pk)}
    )

    assert response.status_code == 200
    assert "{{ compnay }} is not a placeholder" in writing.section(response.content.decode())


def test_only_a_problem_from_a_checker_postulo_ships_adds_the_step(
    client, user, application, monkeypatch
):
    """An installed plugin adds lines, never a step everybody has to press through (#235)."""
    fine = CoverLetter.objects.create(owner=user, name="Fine", body="Dear {{ company }},")
    alarm = writing.checker(
        lambda d: [writing.finding(severity="problem", message="A plugin's alarm.")]
    )
    writing.install(monkeypatch, writing.plugin("mine", alarm))
    listing = checking.letter_findings(fine, application, user)
    assert [line.finding.message for line in listing.problems] == ["A plugin's alarm."]
    assert checking.blocking(listing) == ()
    client.force_login(user)

    response = client.post(
        reverse("documents:send", args=[application.pk]), {"cover_letter": str(fine.pk)}
    )

    assert response.status_code == 302, "frozen without an extra press"


def test_a_shipped_checkers_hint_adds_no_step(client, user, application, monkeypatch):
    fine = CoverLetter.objects.create(owner=user, name="Fine", body="Dear {{ company }},")
    writing.ship(
        monkeypatch, writing.plugin("ours", writing.checker(lambda d: [writing.finding()]))
    )
    client.force_login(user)

    response = client.post(
        reverse("documents:send", args=[application.pk]), {"cover_letter": str(fine.pk)}
    )

    assert response.status_code == 302


def test_before_freezing_the_suggestions_are_folded(client, user, letter, application, monkeypatch):
    writing.ship(
        monkeypatch,
        writing.plugin(
            "ours", writing.checker(lambda d: [writing.finding(replacements=("Hello",))])
        ),
    )
    client.force_login(user)

    gate = client.post(
        reverse("documents:send", args=[application.pk]), {"cover_letter": str(letter.pk)}
    ).content.decode()
    own = client.get(letter.get_absolute_url(), {"application": application.pk}).content.decode()

    assert re.search(r"<details[^>]*data-writing-hints", writing.section(gate))
    assert not re.search(r"<details[^>]* open [^>]*data-writing-hints", writing.section(gate))
    assert re.search(r"<details[^>]* open [^>]*data-writing-hints", writing.section(own))
    assert "data-writing-apply" in own and "data-writing-apply" not in gate


def test_with_no_checker_at_all_nothing_stands_before_freezing(
    client, user, letter, application, monkeypatch
):
    writing.only(monkeypatch)
    client.force_login(user)

    response = client.post(
        reverse("documents:send", args=[application.pk]), {"cover_letter": str(letter.pk)}
    )

    assert response.status_code == 302


# ---------------------------------------------------------- the administrator's page


def test_the_plugins_page_lists_the_checks_as_always_on(client, django_user_model):
    admin = django_user_model.objects.create_user(
        email="admin@example.org",
        username="admin",
        password="a-long-enough-password-42",
        is_staff=True,
        is_superuser=True,
    )
    client.force_login(admin)

    page = client.get("/server/plugins/").content.decode()

    assert "Placeholder checks" in page
    assert "Writing checks" in page, "the kind, in words"
