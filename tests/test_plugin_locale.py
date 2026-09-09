"""Every plugin holds its own translations, and the registry reads them.

Including the plugins Postulo ships, since #127: `docs/PLUGINS.md` has always said a
plugin's strings are never added to Postulo's catalogues, and that was a rule every
built-in broke. What decides the two of them is the order of `LOCALE_PATHS`, so the order
is asserted here rather than assumed.
"""

import struct
import sys
from importlib.metadata import EntryPoint
from pathlib import Path

import pytest
from django.conf import settings as django_settings
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


def test_postulos_own_translation_wins_a_string_a_plugin_also_translates(tmp_path, settings):
    """The whole reason a plugin's catalogue is appended rather than prepended.

    Django merges `reversed(LOCALE_PATHS)` and each merge overrides the last, so the first
    path wins. Two catalogues can define the same English string -- "Name", "Send", "Draft"
    -- and with one catalogue that could not happen. With several it can, silently, and the
    string that changes is one somebody reads. Postulo's own goes first, so a plugin can add
    a word to the interface but never change one (#127).
    """
    from django.utils.translation import trans_real

    for owner, rendering in (("core", "Le nom de Postulo"), ("plugin", "Le nom du greffon")):
        write_mo(
            tmp_path / owner / "fr_FR" / "LC_MESSAGES" / "django.mo", {"Whose name": rendering}
        )
    settings.LOCALE_PATHS = [str(tmp_path / "core")]
    trans_real._translations = {}
    trans_real._default = None

    assert plugin_locale.register_locale_dir(tmp_path / "plugin") is True

    assert settings.LOCALE_PATHS[0] == str(tmp_path / "core"), "appended, never prepended"
    with translation.override("fr-fr"):
        assert translation.gettext("Whose name") == "Le nom de Postulo"
    plugin_locale._registered.remove(str((tmp_path / "plugin").resolve()))
    trans_real._translations = {}
    trans_real._default = None


def test_the_instance_reads_the_built_ins_catalogue_without_being_asked():
    """End to end: the app registry put it there while the apps were loading.

    Not a request, not a first `gettext` call -- `AppConfig.ready`, once, before anything is
    served. `register_locale_dir` throws away Django's merged-catalogue cache each time it
    adds a path, which is free at start-up and would not be during a request (#127).
    """
    from pathlib import Path as _Path

    import postulo

    package = _Path(postulo.__file__).resolve().parent
    paths = [str(_Path(p).resolve()) for p in django_settings.LOCALE_PATHS]

    assert str(package / "plugins" / "builtin" / "locale") in paths
    assert paths[0] == str(package / "locale"), "and Postulo's own is still read first"


def test_a_built_in_plugin_finds_the_catalogue_beside_it_rather_than_postulos():
    """A plugin Postulo ships lives inside `postulo`, so "the top-level package's locale"
    would have been Postulo's own and a built-in could never have had one of its own (#127).
    """
    from pathlib import Path as _Path

    import postulo

    package = _Path(postulo.__file__).resolve().parent
    assert (
        plugin_locale.locale_dir_of("postulo.plugins.builtin")
        == package / "plugins" / "builtin" / "locale"
    )


def test_a_module_with_no_catalogue_beside_it_falls_back_to_postulos():
    """Which is what let #129 move the built-ins out one at a time rather than all at once.

    Every plugin Postulo ships has its own catalogue now, so the example here is an ordinary
    core module -- but the fallback is the behaviour that made the staging possible, and the
    next plugin to be extracted from core relies on it again on its way out.
    """
    from pathlib import Path as _Path

    import postulo

    package = _Path(postulo.__file__).resolve().parent
    assert plugin_locale.locale_dir_of("postulo.notifications.base") == package / "locale"


def test_a_package_without_a_locale_directory_registers_nothing(settings):
    settings.LOCALE_PATHS = list(settings.LOCALE_PATHS)
    before = list(settings.LOCALE_PATHS)
    assert plugin_locale.register_plugin_locale("json") is False
    assert settings.LOCALE_PATHS == before
