"""The services a messaging handle can be on, and what a handle on each looks like (#682).

A registry plugins supply, kept apart from the web-link one: a handle is not an address on
the web. Each service Postulo ships accepts its own example and refuses a near miss; each
normalises as it says; a pattern reads a handle once, so none takes time that grows with
what is typed; a package installed beside Postulo adds its own services and cannot
redefine a shipped key; and there is always an *Other*.
"""

from __future__ import annotations

import re
import time
from pathlib import Path

import pytest
from django.core.exceptions import ValidationError

from postulo.core import messaging_services
from postulo.plugins import registry

ROOT = Path(__file__).resolve().parents[1]


# ------------------------------------------------------------------------ what ships


def test_five_services_ship_and_each_accepts_its_own_example():
    found = messaging_services.registry()

    assert list(found) == ["matrix", "xmpp", "signal", "telegram", "threema"]
    for key, service in found.items():
        assert service.example, key
        stored = service.normalise(service.example)
        assert service.accepts(stored), f"{key} refuses its own example {service.example!r}"
        assert messaging_services.settle(key, service.example) == (key, "", stored)


#: For each service, what is nearly a handle there and is not.
NEAR_MISSES = {
    "matrix": ["@name", "@name:", "@name:exa_mple.org", "@na me:example.org", "@:example.org"],
    "xmpp": ["name", "name@", "@example.org", "na me@example.org", "a@b@c", "name@exa mple.org"],
    "signal": ["name", "name.4", "name.", "1name.42", "na.me.42", "ab.42", "name.4x"],
    "telegram": ["abcd", "x" * 33, "na me", "na-me-ok", "name!"],
    "threema": ["ABCD123", "ABCD12345", "ABCD-123", "*ABCD12", "*ABCD1234", "abc d123"],
}


@pytest.mark.parametrize(
    ("key", "typed"), [(key, typed) for key, typeds in NEAR_MISSES.items() for typed in typeds]
)
def test_a_near_miss_is_refused_with_what_one_looks_like(key, typed):
    service = messaging_services.find(key)

    with pytest.raises(ValidationError) as refused:
        messaging_services.settle(key, typed)

    assert refused.value.code == "handle"
    assert service.example in refused.value.messages[0]
    assert not service.accepts(service.normalise(typed))


@pytest.mark.parametrize(
    ("key", "typed", "stored"),
    [
        # Matrix: the sigil is put back where it was left off, and the case is folded.
        ("matrix", "@Alex:Example.ORG", "@alex:example.org"),
        ("matrix", "alex:example.org", "@alex:example.org"),
        ("matrix", "@alex:example.org:8448", "@alex:example.org:8448"),
        ("matrix", "@alex:[::1]", "@alex:[::1]"),
        # XMPP: a bare JID. The resource names one device and is dropped; so is a typed
        # `xmpp:`; the case is folded.
        ("xmpp", "Alex@Example.org/laptop", "alex@example.org"),
        ("xmpp", "xmpp:alex@example.org", "alex@example.org"),
        ("xmpp", "  alex@example.org  ", "alex@example.org"),
        # Telegram and Signal keep what was typed, less a leading @.
        ("telegram", "@Alex_Morgan", "Alex_Morgan"),
        ("signal", "Alex_M.42", "Alex_M.42"),
        # Threema folds up, and a Gateway ID begins with an asterisk.
        ("threema", "abcd1234", "ABCD1234"),
        ("threema", "*abcdefg", "*ABCDEFG"),
    ],
)
def test_a_handle_is_stored_as_the_service_writes_it(key, typed, stored):
    assert messaging_services.settle(key, typed) == (key, "", stored)


def test_a_handle_that_is_not_text_a_service_can_read_is_not_a_handle():
    for typed in ("", "   ", "@alex:example.org\x00", "alex@example.org\n@b:c.d"):
        with pytest.raises(ValidationError):
            messaging_services.settle("matrix", typed)
        with pytest.raises(ValidationError):
            messaging_services.settle("xmpp", typed)


# ------------------------------------------------------------------------------- Other


def test_other_takes_a_name_and_any_text_and_nothing_chosen_is_other():
    for chosen in (messaging_services.OTHER, ""):
        assert messaging_services.settle(chosen, "irc.example/#room nick", "Our IRC") == (
            "",
            "Our IRC",
            "irc.example/#room nick",
        )


def test_other_has_to_be_named_and_is_bounded():
    with pytest.raises(ValidationError) as unnamed:
        messaging_services.settle("other", "somebody")
    assert unnamed.value.code == "label"
    with pytest.raises(ValidationError):
        messaging_services.settle("other", "x" * 256, "Long")
    with pytest.raises(ValidationError):
        messaging_services.settle("other", "somebody", "n" * 61)
    with pytest.raises(ValidationError):
        messaging_services.settle("other", "some\x07body", "Bell")
    assert messaging_services.settle("other", "x" * 255, "Long")[2] == "x" * 255


def test_a_service_nobody_knows_is_refused_when_chosen_by_hand():
    with pytest.raises(ValidationError) as refused:
        messaging_services.settle("no-such-service", "@a:b.c")
    assert refused.value.code == "service"


def test_a_row_whose_service_nothing_knows_reads_as_other_and_keeps_its_key():
    from postulo.core.models import MessagingHandle

    row = MessagingHandle(service="gone", label="Old chat", handle="somebody")

    assert row.service == "gone"
    assert row.service_label == "Old chat"
    assert row.display == "Old chat somebody"
    assert row.icon == messaging_services.OTHER_ICON
    assert messaging_services.label_for("gone") == "Other"
    assert messaging_services.find("gone") is None


# ------------------------------------------------------- a pattern reads a handle once

#: How long one pattern may take over one handle, in seconds. A pattern that reads once
#: takes microseconds over 255 characters; this is thousands of times that.
PATIENCE = 0.1
LENGTHS = (*range(8, 33, 2), 64, 125, messaging_services.MAX_HANDLE_LENGTH)


def awkward(length: int, start: str):
    """Handles of ``length`` characters that almost match: a long run of what a pattern
    repeats, then a character that is nobody's."""
    for unit in ("a", ".", "-", "a.", "a-", "@", ":", "a:", "_", "a@a", "[", "9"):
        run = (start + unit * length)[: length - 1]
        for last in ("a", " ", "\n", "/", "@"):
            yield run + last


def seconds(pattern: re.Pattern, text: str) -> float:
    fastest = float("inf")
    for _attempt in range(3):
        began = time.perf_counter()
        pattern.fullmatch(text)
        fastest = min(fastest, time.perf_counter() - began)
        if fastest <= PATIENCE:
            break
    return fastest


def first_slow(pattern: re.Pattern, starts) -> str:
    for length in LENGTHS:
        for start in starts:
            for text in awkward(length, start):
                took = seconds(pattern, text)
                if took > PATIENCE:
                    return f"{took:.2f}s over {length} characters: {text[:40]!r}"
    return ""


def test_the_check_catches_a_pattern_that_goes_back_over_what_it_read():
    assert first_slow(re.compile(r"(a+)+b"), [""])
    assert first_slow(re.compile(r"(?:[a-z]+\.?)+-"), [""])
    assert not first_slow(re.compile(r"[a-z.]+@[a-z.]+"), ["", "a@"])


def test_every_shipped_pattern_reads_a_handle_once():
    for key, service in messaging_services.registry().items():
        starts = ["", "@", "@a:", service.example[:3], service.example.split("@")[0] + "@"]
        assert not first_slow(service.pattern, starts), key
        assert not first_slow(re.compile(service.pattern.pattern), starts), key


def test_a_handle_longer_than_the_column_is_not_read_at_all():
    for service in messaging_services.registry().values():
        assert not service.accepts("a" * (messaging_services.MAX_HANDLE_LENGTH + 1))
        assert service.normalise("a" * 5000) == "", "too long to be tidied either"


# ------------------------------------------------------------------------ the registry


class Elsewhere:
    """What a package registered under `postulo.messaging_services` looks like once loaded."""

    name = "elsewhere"
    version = "1.0"
    kind = "messaging-service"
    label = "Elsewhere"

    def __init__(self):
        from postulo.plugins.api import MessagingService

        self.services = (
            MessagingService(
                "irc-example",
                "Example IRC",
                re.compile(r"[a-z][a-z0-9_]{1,15}@example"),
                normaliser=str.lower,
                example="alex@example",
                icon="no-such-icon",
            ),
            # Postulo's own key: not this package's to redefine.
            MessagingService("matrix", "Not Matrix", re.compile(r".*")),
            # And four that cannot be used at all.
            MessagingService("other", "Other", re.compile(r".*")),
            MessagingService("Bad Key", "Bad", re.compile(r".*")),
            MessagingService("bytes", "Bytes", re.compile(rb".*")),
            MessagingService("loud", "Loud", re.compile(r".*"), normaliser="not callable"),
            "not a service",
        )


@pytest.fixture
def elsewhere(monkeypatch):
    monkeypatch.setattr(
        registry,
        "_load_third_party",
        lambda kind: [Elsewhere()] if kind == "messaging-service" else [],
    )
    registry._cache.pop("messaging-service", None)
    yield Elsewhere
    registry._cache.pop("messaging-service", None)


def test_the_services_come_from_a_plugin():
    found = registry.plugins("messaging-service")

    assert [plugin.name for plugin in found] == ["messaging-services"]
    assert len(found[0].services) == len(messaging_services.registry())


def test_the_group_is_open_and_the_installer_knows_it():
    from postulo.plugins import base, installing

    assert base.MESSAGING_SERVICE_GROUP == "postulo.messaging_services"
    assert registry.GROUPS["messaging-service"] == "postulo.messaging_services"
    assert "postulo.messaging_services" in installing.plugin_groups()


def test_a_package_installed_beside_postulo_adds_its_own_services(elsewhere):
    found = registry.plugins("messaging-service")
    assert [plugin.name for plugin in found] == ["elsewhere", "messaging-services"]

    offered = messaging_services.registry()

    assert list(offered)[-1] == "irc-example", "after the ones Postulo ships"
    assert messaging_services.settle("irc-example", "ALEX@example") == (
        "irc-example",
        "",
        "alex@example",
    )
    with pytest.raises(ValidationError):
        messaging_services.settle("irc-example", "not a handle")


def test_a_key_postulo_ships_is_not_another_packages_to_redefine(elsewhere, caplog):
    """Or installing a package could change what `matrix` means on every row stored."""
    with caplog.at_level("WARNING", logger="postulo.core.messaging_services"):
        found = messaging_services.registry()

    assert found["matrix"].label == "Matrix"
    assert not found["matrix"].accepts("anything")
    assert "offered twice" in caplog.text


def test_a_service_that_cannot_be_used_is_left_out_and_logged(elsewhere, caplog):
    with caplog.at_level("WARNING", logger="postulo.core.messaging_services"):
        found = messaging_services.registry()

    assert not {"other", "Bad Key", "bytes", "loud"} & set(found)
    assert caplog.text.count("cannot be used") == 5


def test_an_icon_a_plugin_got_wrong_costs_a_picture_and_not_a_page(elsewhere):
    assert messaging_services.find("irc-example").icon_name == messaging_services.DEFAULT_ICON


def test_a_class_built_on_the_shipped_one_is_not_one_postulo_ships(monkeypatch, caplog):
    from postulo.plugins.api import MessagingService
    from postulo.plugins.messaging_services import MessagingServices

    class Imposter(MessagingServices):
        name = "imposter"
        services = (MessagingService("matrix", "Not Matrix", re.compile(r".*")),)

    monkeypatch.setattr(
        registry,
        "_load_third_party",
        lambda kind: [Imposter()] if kind == "messaging-service" else [],
    )
    registry._cache.pop("messaging-service", None)
    try:
        with caplog.at_level("WARNING", logger="postulo.core.messaging_services"):
            found = messaging_services.registry()
    finally:
        registry._cache.pop("messaging-service", None)

    assert found["matrix"].label == "Matrix"
    assert "offered twice" in caplog.text


def test_every_icon_a_row_can_draw_is_one_postulo_ships():
    """`{% icon %}` raises on a name it has no file for, and these names come from a table
    and not from a template, so an icon nobody vendored would be a 500 on *Your details*."""
    listed = {
        line.split("#", 1)[0].strip()
        for line in (ROOT / "assets" / "icons.txt").read_text(encoding="utf-8").splitlines()
    }
    drawn = {service.icon for service in messaging_services.registry().values()}
    drawn |= {messaging_services.DEFAULT_ICON, messaging_services.OTHER_ICON}

    assert drawn <= listed, f"add {sorted(drawn - listed)} to assets/icons.txt"
    assert all(messaging_services.icon_exists(name) for name in drawn)
    # No brand marks (TRADEMARKS.md): a generic Lucide icon for every service.
    assert not any(name in ("signal", "telegram", "matrix") for name in drawn)


def test_a_registry_is_not_somebodys_to_switch_off():
    from postulo.plugins.policy import GOVERNED_KINDS, UNGOVERNED_KINDS

    assert "messaging-service" in UNGOVERNED_KINDS
    assert "messaging-service" not in GOVERNED_KINDS


def test_the_list_is_not_frozen_into_the_model():
    from postulo.core.models import MessagingHandle

    field = MessagingHandle._meta.get_field("service")

    assert field.choices is None and field.blank
    assert field.max_length == messaging_services.MAX_KEY_LENGTH
    migration = (ROOT / "src/postulo/core/migrations/0030_messaginghandle.py").read_text(
        encoding="utf-8"
    )
    assert "matrix" not in migration


def test_the_plugin_carries_its_own_translations_and_core_does_not_translate_them():
    own = ROOT / "src/postulo/plugins/messaging_services/locale/pt_PT/LC_MESSAGES/django.po"
    core = ROOT / "src/postulo/locale/pt_PT/LC_MESSAGES/django.po"
    text = own.read_text(encoding="utf-8")

    assert 'msgid "Services for messaging handles"' in text
    assert 'msgid "Services for messaging handles"' not in core.read_text(encoding="utf-8")


def test_the_surface_holds_what_a_registry_of_messaging_services_needs():
    """One entry, what holds the entries, and the read of one by its key (#682)."""
    from postulo.plugins import api, base

    for name in ("MessagingService", "MessagingServicePlugin", "messaging_service_of"):
        assert name in api.__all__, name
    assert api.MessagingService is messaging_services.Service
    assert api.MessagingServicePlugin is base.MessagingServicePlugin
    assert api.messaging_service_of is messaging_services.find

    made = api.MessagingService("x-chat", "X chat", re.compile(r"[a-z]+"))
    assert made.normalise("  @abc ") == "abc"
    assert made.accepts("abc") and not made.accepts("ab c")
    assert api.messaging_service_of("signal").label == "Signal"
    assert api.messaging_service_of("") is None, "Other is not a service"
