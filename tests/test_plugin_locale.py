"""Every plugin holds its own translations, and the registry reads them."""

import struct
import sys
from importlib.metadata import EntryPoint
from pathlib import Path

import pytest
from django.utils import translation

from postulo.plugins import locale as plugin_locale
from postulo.plugins import registry


def write_mo(path: Path, messages: dict[str, str]) -> None:
    """A minimal GNU .mo file, so the test needs no gettext binaries on the machine."""
    entries = [("", "Content-Type: text/plain; charset=UTF-8\n"), *sorted(messages.items())]
    ids = [key.encode() for key, _ in entries]
    strs = [value.encode() for _, value in entries]
    count = len(entries)
    header_size = 7 * 4
    table_size = count * 8
    ids_offset = header_size + 2 * table_size
    strs_offset = ids_offset + sum(len(i) + 1 for i in ids)

    id_table, str_table, offset = [], [], ids_offset
    for item in ids:
        id_table.append((len(item), offset))
        offset += len(item) + 1
    offset = strs_offset
    for item in strs:
        str_table.append((len(item), offset))
        offset += len(item) + 1

    out = struct.pack("<7I", 0x950412DE, 0, count, header_size, header_size + table_size, 0, 0)
    for length, position in id_table:
        out += struct.pack("<2I", length, position)
    for length, position in str_table:
        out += struct.pack("<2I", length, position)
    out += b"".join(item + b"\0" for item in ids)
    out += b"".join(item + b"\0" for item in strs)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(out)


@pytest.fixture
def plugin_package(tmp_path, monkeypatch):
    """A throwaway plugin package on sys.path with a French catalogue beside it."""
    package = tmp_path / "echoplug"
    package.mkdir()
    (package / "__init__.py").write_text(
        "from django.utils.translation import gettext_lazy as _\n"
        "from postulo import __version__\n"
        "class EchoNotifier:\n"
        "    name = 'echoplug'\n"
        "    version = __version__\n"
        "    kind = 'notifier'\n"
        "    label = _('Echo it back')\n"
        "    def config_fields(self):\n"
        "        return []\n"
        "    def test(self, config):\n"
        "        from postulo.plugins.base import TestResult\n"
        "        return TestResult(True, 'ok')\n"
        "    def send(self, notification, config, user):\n"
        "        pass\n",
        encoding="utf-8",
    )
    write_mo(
        package / "locale" / "fr_FR" / "LC_MESSAGES" / "django.mo",
        {"Echo it back": "Renvoyer en écho"},
    )
    monkeypatch.syspath_prepend(str(tmp_path))
    yield package
    sys.modules.pop("echoplug", None)


def test_a_plugins_locale_directory_is_read_once_registered(plugin_package, settings):
    settings.LOCALE_PATHS = list(settings.LOCALE_PATHS)
    assert plugin_locale.register_plugin_locale("echoplug") is True
    assert plugin_locale.register_plugin_locale("echoplug") is False, "once is enough"
    assert str(plugin_package / "locale") in settings.LOCALE_PATHS

    with translation.override("fr-fr"):
        assert translation.gettext("Echo it back") == "Renvoyer en écho"
    with translation.override("en-gb"):
        assert translation.gettext("Echo it back") == "Echo it back"


def test_the_registry_registers_the_locale_of_every_plugin_it_loads(
    plugin_package, settings, monkeypatch
):
    settings.LOCALE_PATHS = list(settings.LOCALE_PATHS)
    fake = EntryPoint("echoplug", "echoplug:EchoNotifier", "postulo.notifiers")
    monkeypatch.setattr(
        registry, "entry_points", lambda group: [fake] if group == "postulo.notifiers" else []
    )

    names = [plugin.name for plugin in registry.plugins("notifier", refresh=True)]
    assert "echoplug" in names
    assert str(plugin_package / "locale") in settings.LOCALE_PATHS
    with translation.override("fr-fr"):
        plugin = registry.find_plugin("notifier", "echoplug")
        assert str(plugin.label) == "Renvoyer en écho"
    registry._cache.clear()


def test_a_package_without_a_locale_directory_registers_nothing(settings):
    settings.LOCALE_PATHS = list(settings.LOCALE_PATHS)
    before = list(settings.LOCALE_PATHS)
    assert plugin_locale.register_plugin_locale("json") is False
    assert settings.LOCALE_PATHS == before
