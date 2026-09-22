"""Every plugin Postulo ships is its own package, and imports only the surface (#126, #129).

> and imperative is that a plugin, even a internal must be a self-contained as possible
> having is own manifest, is own locale, etc, and dont depende on the core

The measure of that imperative is not that some directories moved. It is this file: the
next `phone-numbers` cannot be written inside core without a test saying so. So the list of
plugins is **taken from the registry**, never written down here — a built-in added tomorrow
is checked tomorrow, including the one nobody remembered to add to a list.

Three things are asked of each of them: it is a package under `postulo.plugins`, it carries
its own catalogues, and it imports `postulo.plugins.api` and nothing else from Postulo.

The import check reads the source rather than importing it, so a module that reaches past
the surface fails here whether or not the import is ever executed — a lazy
`from postulo.core import site` inside a method is exactly as much of a dependency as one at
the top of the file, and is the shape most of the ones below take. Relative imports are
resolved, because a dependency spelled with a dot is still a dependency.

**The check itself lives in `postulo.plugins.testing`**, so that a plugin written outside
this repository can run the same one against itself (#229). It was here alone for long
enough that every official plugin drifted past the surface and a code audit, rather than a
test, is what found it.

**`REACHING_PAST` is not a list of exemptions.** Every entry is a reason a plugin has to
depend on Postulo rather than a failure of discipline, and says which. A new entry is a
decision somebody has to make on purpose, which is the whole point of the test failing on
one; a stale entry fails too, because one nobody removed hides the next real dependency.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from postulo.plugins.testing import (
    SURFACE,
    assert_imports_only_the_surface,
    imports_of,
    reaching_past_the_surface,
    unused_allowances,
)

ROOT = Path(__file__).resolve().parents[1] / "src" / "postulo"

#: Where a plugin Postulo ships has to live. Not a convention — the check below.
HOME = "postulo.plugins."

#: What each shipped plugin still reaches past the surface for, and why. Every line is
#: either a name that should become part of the surface, or work left to do.
REACHING_PAST: dict[str, dict[str, str]] = {
    "email": {
        "postulo.core": "`site`: the instance's name and from-address, for the message it sends",
        # `Notification` used to be reached past the surface as well — by this plugin and by
        # every notifier written outside the core, which is what #229 was about. It is on the
        # surface now, and this plugin imports it from there like anybody else's.
    },
    "browser": {
        "postulo.notifications": (
            "`inbox`: where a notification waits for an open tab when it cannot be pushed. A "
            "plugin holds no rows, and a notice has to outlive the send that left it, scoped "
            "to its owner by Postulo rather than by the plugin (#209)"
        ),
    },
    "own_mail": {
        "postulo.core": (
            "`mail`: the encryption choices, which are how TLS gets onto a session; and "
            "`mail_auth`: the identity-provider presets a person picks from to sign in with a "
            "token rather than a password (#151)"
        ),
        "postulo.core.mail": (
            "the connection check, and the backend that dials only where it is allowed to "
            "(#148). One guard for the instance's mail and the person's, rather than two "
            "that can drift (#149)"
        ),
    },
    "smtp": {
        "postulo.core": (
            "`mail` to open an SMTP connection and prove it, and `mail_auth` for the "
            "instance's own token when it signs in with XOAUTH2 (#151)"
        ),
        "postulo.core.mail": (
            "the guarded backend, which is where the server is allowed to dial (#148)"
        ),
    },
    "webhook": {
        "postulo.notifications": (
            "`webhooks`: where a delivery waits for the scheduler. A plugin holds no rows, and a "
            "delivery has to outlive the send that left it, scoped to its owner by Postulo rather "
            "than by the plugin -- the browser notifier's reason, for the same kind of row (#240)"
        ),
    },
    "localstore": {
        "postulo.documents.stores": (
            "`download_path`: where Postulo serves a document from, which is Postulo's to "
            "know and the store's to hand back"
        ),
    },
    "europass": {
        "postulo.accounts": (
            "`identifiers`: the schemes an ORCID in a Europass file is checked against. #109 "
            "would make this a registry of its own"
        ),
        # `Record` used to be reached past the surface as well. It is on it since #105,
        # because a third-party importer has to fill the same one.
    },
    "postal_rules": {
        "postulo.core": (
            "`phones.country_name`: the country table, which was built for dialling codes "
            "and is the same table an address needs. Two of them would be two things to "
            "keep current (#147)"
        ),
    },
    "identifiers": {
        "postulo.core.identifiers": (
            "`Scheme`, and the two subject names. This is the one plugin whose whole content "
            "*is* a core type: it contributes vocabulary rather than behaviour, and the shape "
            "of that vocabulary has to be Postulo's or the tables could not read it. #109 "
            "will put `Scheme` on the surface the day a third-party registry is a promise"
        ),
    },
    "builtin": {},
    "phone_numbers": {},
    "email_addresses": {},
    "social_profiles": {},
    "repositories": {},
    "websites": {},
}


def shipped() -> dict[str, str]:
    """Every plugin Postulo ships, by package, from the registry rather than from a list.

    A list here would be a list to forget to add to, and the plugin nobody added would be
    exactly the one written in the wrong place. `builtins()` is what the application itself
    believes it ships.
    """
    from postulo.plugins import registry

    found: dict[str, list[str]] = {}
    for kind, classes in registry.builtins().items():
        for plugin_class in classes:
            module = plugin_class.__module__
            if not module.startswith("postulo"):
                continue  # a plugin a test registered, which is not Postulo's to answer for
            found.setdefault(module, []).append(f"{plugin_class.__name__} ({kind})")
    return {module: ", ".join(sorted(names)) for module, names in sorted(found.items())}


def package_of(module: str) -> Path:
    return ROOT.joinpath(*module.removeprefix("postulo.").split("."))


def sources_of(module: str) -> list[Path]:
    """Every Python file in a plugin's package, because a plugin is more than its `__init__`."""
    return sorted(package_of(module).rglob("*.py"))


def reaching(module: str) -> set[str]:
    """What a whole plugin package imports from Postulo.

    `europass` keeping its reader beside its declaration is the point of #129 rather than a
    violation of it, so a plugin reaching into its own package does not count.
    """
    return imports_of(package_of(module), package=module)


def names() -> list[str]:
    return sorted(shipped())


def short_name(module: str) -> str:
    return module.rpartition(".")[2]


# ------------------------------------------------------- a plugin is its own package


def test_postulo_ships_the_plugins_it_says_it_does():
    """A sanity check on the check: an empty registry would make everything below pass."""
    assert len(shipped()) >= 6, shipped()


@pytest.mark.parametrize("module", names(), ids=short_name)
def test_every_plugin_postulo_ships_lives_in_its_own_package(module: str):
    """The measure of #129, and the reason it was worth doing at all.

    `phone-numbers` was written a week before that issue, entirely inside `core`, by somebody
    who had just read the plugin documentation. Nothing pulled the other way. This is what
    pulls the other way.
    """
    assert module.startswith(HOME), (
        f"{shipped()[module]} lives in {module}. A plugin Postulo ships belongs in its own "
        f"package under {HOME}* — its manifest, its catalogues and its code in one place, "
        f"the same as a plugin somebody else writes."
    )
    package = package_of(module)
    assert (package / "__init__.py").exists(), f"{module} is a module, not a package"


@pytest.mark.parametrize("module", names(), ids=short_name)
def test_every_plugin_postulo_ships_carries_its_own_catalogues(module: str):
    """*Writing a plugin* says a plugin's strings are never added to Postulo's catalogues.

    That was a rule third parties kept and every built-in broke, until #127 taught the
    tooling about several catalogue sets. `tests/test_translations.py` is what keeps each of
    these complete; this is what keeps one from quietly not existing.
    """
    locale = package_of(module) / "locale"

    assert locale.is_dir(), f"{module} has no locale/ of its own"
    assert list(locale.glob("*/LC_MESSAGES/django.po")), f"{module}: locale/ is empty"


# ----------------------------------------------------------- and imports the surface


@pytest.mark.parametrize("module", names(), ids=short_name)
def test_a_shipped_plugin_imports_the_surface_or_something_written_down(module: str):
    """The rule, and the only way past it is a line somebody wrote deliberately."""
    allowed = REACHING_PAST.get(short_name(module), {})

    past = reaching_past_the_surface(package_of(module), package=module, allowed=allowed)

    assert not past, (
        f"{shipped()[module]} reaches past the plugin surface: {past}. Either add the name to "
        f"postulo.plugins.api, or record the dependency in REACHING_PAST with the reason it "
        f"is needed — a new one is a decision, not an accident."
    )


@pytest.mark.parametrize("module", names(), ids=short_name)
def test_nothing_recorded_has_quietly_been_fixed(module: str):
    """A stale entry would let a real dependency back in behind it."""
    stale = unused_allowances(
        package_of(module), package=module, allowed=REACHING_PAST.get(short_name(module), {})
    )

    assert not stale, (
        f"{shipped()[module]} no longer imports {stale}. Delete those lines from "
        f"REACHING_PAST: the list is the map of what is left to do, and a stale entry hides "
        f"the next one."
    )


def test_nothing_is_recorded_for_a_plugin_that_no_longer_exists():
    recorded = set(REACHING_PAST)
    real = {short_name(module) for module in shipped()}

    assert recorded <= real, f"REACHING_PAST names plugins that are gone: {sorted(recorded - real)}"


def test_the_plugins_that_hold_no_data_need_only_the_surface():
    """The check that the surface is not so wide as to be meaningless.

    Two shapes reach it. A **source** is a URL and some HTML in, a `JobPostingData` out. A
    **feature** is a declaration and nothing else. Neither has a reason to know anything
    about Postulo beyond what it is handed, and neither does.
    """
    assert reaching("postulo.plugins.builtin") == {SURFACE}
    assert reaching("postulo.plugins.phone_numbers") == {SURFACE}
    for package in ("social_profiles", "repositories", "websites"):
        assert reaching(f"postulo.plugins.{package}") == {SURFACE}, package


def test_no_plugin_postulo_ships_reaches_for_a_model():
    """The deeper result of #129, and the one worth keeping.

    An importer turns bytes into a record, a store writes a file, a notifier sends a message,
    and Postulo does the scoping. Nothing Postulo ships queries the database itself — which
    matters because ownership scoping done wrong in a plugin is how one person sees another's
    data, and the way not to get it wrong in seven places is not to need it in seven places.
    """
    offenders = {
        module: sorted(name for name in reaching(module) if name.endswith(".models"))
        for module in shipped()
    }

    assert not any(offenders.values()), {k: v for k, v in offenders.items() if v}


# --------------------------------------------------------------- what the surface is


def test_every_promised_name_resolves():
    """A surface that names something it cannot hand over is worse than none."""
    from postulo.plugins import api

    missing = [name for name in api.__all__ if not hasattr(api, name)]

    assert not missing, missing


def test_asking_for_something_else_says_where_to_look():
    from postulo.plugins import api

    with pytest.raises(AttributeError) as raised:
        api.Connection  # noqa: B018

    assert "not part of the plugin surface" in str(raised.value)
    assert "/wiki/Writing-a-plugin" in str(raised.value)


def test_the_surface_holds_the_four_reasons_a_plugin_has_to_depend_on_postulo():
    """Ownership scoping, redirects, outbound requests, consent — each is a promise kept.

    Named individually rather than counted, because the argument for the surface existing at
    all is that these are unavoidable: a plugin that skips any of them breaks something the
    project promises rather than merely being untidy.
    """
    from postulo.plugins import api

    assert api.OwnedQuerySet is not None, "for_user(), or one person sees another's data"
    assert api.safe_next is not None, "or a plugin bounces somebody off the instance"
    assert api.client is not None, "or a plugin dials where the server should not"
    assert api.access_token is not None, "or a plugin keeps a token instead of refreshing it"


def test_the_surface_holds_the_store_contract():
    """What a store is handed and what it gives back, which used to live in `documents`.

    A store author writes against those three, so they belong where a plugin may import them
    from — and the local store now imports them from exactly there, like anybody else's.
    """
    from postulo.plugins import api

    assert api.DocumentMetadata is not None
    assert api.ExternalRef is not None
    assert api.StorePlugin is not None


def test_importing_the_surface_touches_no_database():
    """It is reachable before the app registry is ready, so it must cost nothing to import."""
    import importlib

    module = importlib.import_module("postulo.plugins.api")

    assert module.__all__, "and it still lists what it promises"


def test_the_surface_holds_the_notifier_contract():
    """What `send()` is handed, and what a person switches on per connection (#229).

    Every notifier that exists imported these from `postulo.notifications.base` — the
    built-in one, the three written outside the core, and the example in the wiki — because
    there was nowhere else to get them. A promise that the only working notifier is one that
    breaks the promise is not one.
    """
    from postulo.plugins import api

    assert api.NotifierPlugin is not None
    assert api.Notification is not None
    assert set(api.EVENTS) == {
        "reminder_due",
        "capture_received",
        "went_quiet",
        "posting_closing",
        "status_changed",
        "interview_scheduled",
        "offer_recorded",
    }


def test_the_surface_holds_what_a_sync_needs():
    """A sync is the one kind that cannot be handed its work: it goes and finds it (#229).

    The records, the link that ties one to its twin, the calls that write to a timeline, and
    the details that hang off a contact. Named individually because each was reached past
    the surface by `postulo-dav` or `postulo-imap`, and the list is what lets them stop.
    """
    from postulo.plugins import api

    for name in (
        "Application",
        "Contact",
        "EventKind",
        "Interview",
        "InterviewOutcome",
        "LinkKind",
        "Suggestion",
        "SyncLink",
        "calendar_status",
        "event_lines",
        "get_or_create_company",
        "phone_number_is_taken",
        "primary_phone_number",
        "primary_web_link",
        "record_event",
        "reschedule_interview",
        "save_phone_number",
        "save_web_link",
        "settle_interview",
        "suggest",
    ):
        assert getattr(api, name) is not None, name


def test_the_surface_hands_over_the_same_object_the_core_uses():
    """A re-export that is a copy would be a second thing to keep current.

    `SyncLink` reached from the surface and `SyncLink` reached from `plugins.models` have to
    be one class, or a plugin's rows and Postulo's would be different tables.
    """
    from postulo.applications.models import Application
    from postulo.notifications.base import Notification
    from postulo.plugins import api
    from postulo.plugins.models import SyncLink

    assert api.SyncLink is SyncLink
    assert api.Application is Application
    assert api.Notification is Notification


def test_a_plugin_written_elsewhere_can_run_the_same_check():
    """The check is published, not just performed here (#229).

    `postulo.plugins.testing` is what a plugin repository imports to assert the same thing
    about itself. Run against a shipped plugin, it has to agree with the parametrised tests
    above — otherwise there would be two checks and a plugin could pass the wrong one.
    """
    assert_imports_only_the_surface(
        package_of("postulo.plugins.phone_numbers"), package="postulo.plugins.phone_numbers"
    )


def test_the_published_check_reads_a_lazy_import_too(tmp_path):
    """The shape most real dependencies take: inside a method, so it never runs in a test."""
    package = tmp_path / "postulo_elsewhere"
    package.mkdir()
    (package / "__init__.py").write_text(
        "def send(self):\n    from postulo.core import site\n    return site\n",
        encoding="utf-8",
    )

    past = reaching_past_the_surface(package, package="postulo_elsewhere")

    assert past == ["postulo.core"]


def test_the_published_check_does_not_mistake_a_plugins_own_package_for_postulo(tmp_path):
    """`postulo_dav` starts with the same eight letters and is the plugin's own code."""
    package = tmp_path / "postulo_elsewhere"
    (package / "inner").mkdir(parents=True)
    (package / "__init__.py").write_text(
        "from postulo.plugins.api import FieldSpec\nfrom postulo_elsewhere import inner\n",
        encoding="utf-8",
    )
    (package / "inner" / "__init__.py").write_text("from .. import sibling\n", encoding="utf-8")

    assert_imports_only_the_surface(package, package="postulo_elsewhere")


def test_the_published_check_fails_an_allowance_nobody_removed(tmp_path):
    """A stale line would let the next real dependency in behind it."""
    package = tmp_path / "postulo_elsewhere"
    package.mkdir()
    (package / "__init__.py").write_text(
        "from postulo.plugins.api import FieldSpec\n", encoding="utf-8"
    )

    assert unused_allowances(
        package, package="postulo_elsewhere", allowed={"postulo.core": "it used to"}
    ) == ["postulo.core"]

    with pytest.raises(AssertionError, match="no longer imports"):
        assert_imports_only_the_surface(
            package, package="postulo_elsewhere", allowed={"postulo.core": "it used to"}
        )


def test_the_published_check_names_the_surface_it_is_checking_against():
    assert SURFACE == "postulo.plugins.api"
