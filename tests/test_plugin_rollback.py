"""An install that can be undone, and a removal that takes what it brought (#246).

`run_install` used to write over the working version of a plugin before anything had
checked that the new one imports, and there was nothing kept to go back to: an upgrade that
failed left an instance with a plugin that could not load and no way back but finding the
old wheel again. Removing one deleted the files its own ``RECORD`` listed and left
everything it had dragged in on the volume for ever. And the constraint file held only
Postulo's own pins, so installing one plugin could move a dependency another was using,
with nothing refusing it and nothing saying it had happened.

The installer itself is never run here, as in `test_plugin_install.py`: shelling out to pip
would be slow and would reach the network. Everything around it is real -- real wheels, the
real record, the real directory, and the import check really does start a subprocess.
"""

from __future__ import annotations

import importlib
import importlib.metadata
import sys
import zipfile
from pathlib import Path

import pytest
from django.core.management import call_command
from django.core.management.base import CommandError

from postulo.plugins import installing
from postulo.plugins.installing import InstallError

pytestmark = pytest.mark.django_db


def a_wheel(path: Path, *, name="postulo-example", version="1.0", body="", requires=()) -> Path:
    """A real wheel whose module body is whatever the test needs it to be."""
    package = name.replace("-", "_")
    wheel = path / f"{package}-{version}-py3-none-any.whl"
    dist_info = f"{package}-{version}.dist-info"
    metadata = [
        "Metadata-Version: 2.1",
        f"Name: {name}",
        f"Version: {version}",
        "Summary: An example plugin",
        "License: MIT",
        "Requires-Python: >=3.12",
        *[f"Requires-Dist: {requirement}" for requirement in requires],
    ]
    with zipfile.ZipFile(wheel, "w") as archive:
        archive.writestr(f"{dist_info}/METADATA", "\n".join(metadata) + "\n\n")
        archive.writestr(f"{dist_info}/WHEEL", "Wheel-Version: 1.0\nTag: py3-none-any\n")
        archive.writestr(
            f"{dist_info}/entry_points.txt", f"[postulo.sources]\nexample = {package}:Source\n"
        )
        archive.writestr(
            f"{package}/__init__.py",
            body
            or (
                "class Source:\n"
                "    name = 'example'\n"
                f"    version = '{version}'\n"
                "    def can_handle(self, url):\n"
                "        return False\n"
                "    def parse(self, url, html):\n"
                "        return None\n"
            ),
        )
    return wheel


@pytest.fixture
def plugins_dir(tmp_path, settings):
    directory = tmp_path / "plugins"
    settings.POSTULO_PLUGINS_DIR = directory
    yield directory
    while str(directory) in sys.path:
        sys.path.remove(str(directory))
    importlib.invalidate_caches()
    importlib.metadata.MetadataPathFinder.invalidate_caches()


@pytest.fixture
def installer(monkeypatch, plugins_dir):
    """Stand in for pip: unpack the wheel where pip would have put it."""

    def fake(target: Path, wheel: Path, constraint_file: Path) -> str:
        with zipfile.ZipFile(wheel) as archive:
            names = archive.namelist()
            archive.extractall(target)
        for dist_info in Path(target).glob("*.dist-info"):
            (dist_info / "RECORD").write_text(
                "\n".join(f"{member},," for member in names if not member.endswith("RECORD"))
                + "\n",
                encoding="utf-8",
            )
        return "installed"

    monkeypatch.setattr(installing, "run_install", fake)


def pretend_dependency(plugins_dir: Path, name: str, version: str = "1.0") -> None:
    """Lay down what an installer would have left for a dependency of a plugin."""
    package = name.replace("-", "_")
    (plugins_dir / package).mkdir(parents=True, exist_ok=True)
    (plugins_dir / package / "__init__.py").write_text("", encoding="utf-8")
    dist_info = plugins_dir / f"{package}-{version}.dist-info"
    dist_info.mkdir(parents=True, exist_ok=True)
    (dist_info / "METADATA").write_text(
        f"Metadata-Version: 2.1\nName: {name}\nVersion: {version}\n", encoding="utf-8"
    )
    (dist_info / "RECORD").write_text(
        f"{package}/__init__.py,,\n{dist_info.name}/METADATA,,\n", encoding="utf-8"
    )


# ------------------------------------------------------- the check before the swap


def test_a_plugin_that_cannot_be_imported_is_put_back_rather_than_left(
    tmp_path, plugins_dir, installer
):
    """The whole of #246 in one test: the working version survives a bad upgrade."""
    installing.install_wheel(a_wheel(tmp_path, version="1.0"))
    assert installing.installed("postulo-example").version == "1.0"

    broken = a_wheel(tmp_path, version="2.0", body="raise RuntimeError('no good')\n")
    with pytest.raises(InstallError, match="could not be loaded"):
        installing.install_wheel(broken)

    assert installing.installed("postulo-example").version == "1.0", "the record is unmoved"
    text = (plugins_dir / "postulo_example" / "__init__.py").read_text(encoding="utf-8")
    assert "RuntimeError" not in text and "version = '1.0'" in text, "and so are the files"


def test_the_refusal_says_what_the_import_said(tmp_path, plugins_dir, installer):
    """An administrator with a plugin that will not load needs the reason, not the fact."""
    with pytest.raises(InstallError) as raised:
        installing.install_wheel(a_wheel(tmp_path, body="raise RuntimeError('missing a key')\n"))

    assert "missing a key" in str(raised.value)
    assert installing.installed("postulo-example") is None, "and nothing was recorded"


def test_a_plugin_that_kills_its_process_at_import_is_caught_too(tmp_path, plugins_dir, installer):
    """`SystemExit` is not an `Exception`, and a module is allowed to raise one.

    This is why the check is a subprocess: the same import in the worker would have taken
    the worker with it, and there is no unimporting what a half-loaded module did.
    """
    with pytest.raises(InstallError, match="could not be loaded"):
        installing.install_wheel(a_wheel(tmp_path, body="import sys\nsys.exit(3)\n"))

    assert installing.installed("postulo-example") is None


def test_checking_that_it_imports_leaves_nothing_behind(tmp_path, plugins_dir, installer):
    """An import writes `__pycache__` beside the source unless the process is told not to."""
    installing.install_wheel(a_wheel(tmp_path))

    assert not list(plugins_dir.rglob("__pycache__")), "the check ran with -B"


def test_a_good_install_is_recorded_and_loadable(tmp_path, plugins_dir, installer):
    """The check must not refuse the ordinary case, which is the one it is never tested on."""
    entry = installing.install_wheel(a_wheel(tmp_path))

    assert entry.version == "1.0"
    assert entry.entry_points == ["postulo.sources:example"]


# ------------------------------------------------------------------- going back


def test_the_previous_state_is_kept_and_can_be_returned_to(tmp_path, plugins_dir, installer):
    """For the upgrade that installed cleanly and turned out to be wrong.

    Nothing about a wheel says it is the wrong version; only using it says that. So the
    check at install time cannot be the whole answer, and this is the rest of it.
    """
    installing.install_wheel(a_wheel(tmp_path, version="1.0"))
    installing.install_wheel(a_wheel(tmp_path, version="2.0"))
    assert installing.installed("postulo-example").version == "2.0"

    assert installing.can_roll_back()
    installing.roll_back()

    assert installing.installed("postulo-example").version == "1.0"
    text = (plugins_dir / "postulo_example" / "__init__.py").read_text(encoding="utf-8")
    assert "version = '1.0'" in text, "the files went back too, not only the record"


def test_rolling_back_the_first_install_of_all_takes_it_away(tmp_path, plugins_dir, installer):
    """The state before the first install is no plugin at all, and that is a state."""
    installing.install_wheel(a_wheel(tmp_path))

    installing.roll_back()

    assert installing.read_record() == []
    assert not (plugins_dir / "postulo_example").exists()


def test_there_is_one_way_back_and_it_is_spent_once_used(tmp_path, plugins_dir, installer):
    """One snapshot, not a chain: a chain would be a decision about how much volume to
    spend, and undoing the thing you just did is what anybody actually asks for."""
    installing.install_wheel(a_wheel(tmp_path, version="1.0"))
    installing.roll_back()

    assert not installing.can_roll_back()
    with pytest.raises(InstallError, match="nothing to go back to"):
        installing.roll_back()


def test_a_failed_install_leaves_the_way_back_where_it_was(tmp_path, plugins_dir, installer):
    """It already used the snapshot to put things back; it must not claim one remains."""
    installing.install_wheel(a_wheel(tmp_path, version="1.0"))
    with pytest.raises(InstallError):
        installing.install_wheel(a_wheel(tmp_path, version="2.0", body="raise RuntimeError('x')\n"))

    assert installing.installed("postulo-example").version == "1.0"
    assert installing.can_roll_back(), "the attempt must not have spent it"

    installing.roll_back()
    assert installing.read_record() == [], "back to before 1.0, which is where it pointed"


def test_the_snapshot_is_not_mistaken_for_something_installed(tmp_path, plugins_dir, installer):
    """It sits inside the plugins directory, where everything else looks for packages."""
    installing.install_wheel(a_wheel(tmp_path, version="1.0"))
    installing.install_wheel(a_wheel(tmp_path, version="2.0"))

    assert installing.previous_dir().is_dir(), "it is there"
    assert installing.distributions_in(plugins_dir) == {"postulo-example": "2.0"}
    names = [row["name"] for row in installing.status()]
    assert names.count("postulo-example") == 1, "once, not once per copy of it on the volume"
    assert not [name for name in names if name.startswith(".")], "and the snapshot is not one"


def test_the_command_can_go_back_and_says_what_moved(tmp_path, plugins_dir, installer, capsys):
    installing.install_wheel(a_wheel(tmp_path, version="1.0"))
    installing.install_wheel(a_wheel(tmp_path, version="2.0"))

    call_command("plugins", "rollback")

    said = capsys.readouterr().out
    assert "postulo-example: 2.0 to 1.0" in said
    with pytest.raises(CommandError, match="nothing to go back to"):
        call_command("plugins", "rollback")


# --------------------------------------------------- what a removal takes with it


def test_removing_takes_what_the_plugin_brought_with_it(tmp_path, plugins_dir, installer):
    """A dependency nothing else uses goes; the volume does not grow with every trial."""
    installing.install_wheel(a_wheel(tmp_path))
    pretend_dependency(plugins_dir, "leftpad")
    entry = installing.installed("postulo-example")
    entry.dependencies = ["leftpad==1.0"]
    installing.write_record([entry])

    installing.remove("postulo-example")

    assert not (plugins_dir / "leftpad").exists()
    assert not list(plugins_dir.glob("leftpad-*.dist-info"))


def test_a_dependency_another_plugin_uses_is_kept(tmp_path, plugins_dir, installer):
    """The reference count, which is the point of counting rather than deleting."""
    installing.install_wheel(a_wheel(tmp_path, name="postulo-example"))
    installing.install_wheel(a_wheel(tmp_path, name="postulo-other"))
    pretend_dependency(plugins_dir, "leftpad")
    first, second = sorted(installing.read_record(), key=lambda entry: entry.name)
    first.dependencies = ["leftpad==1.0"]
    second.dependencies = ["leftpad==1.0"]
    installing.write_record([first, second])

    installing.remove("postulo-example")

    assert (plugins_dir / "leftpad").exists(), "postulo-other is still using it"

    installing.remove("postulo-other")
    assert not (plugins_dir / "leftpad").exists(), "and now nothing is"


def test_what_is_shared_is_worked_out_from_the_record(plugins_dir):
    """The counting itself, without the filesystem, because it is the part that is subtle."""
    mine = installing.Installed(name="a", version="1", dependencies=["shared==1", "mine==1"])
    theirs = installing.Installed(name="b", version="1", dependencies=["shared==1"])
    installing.write_record([mine, theirs])

    assert installing.unshared_dependencies(mine) == ["mine"]
    assert installing.unshared_dependencies(theirs) == []


def test_a_plugin_is_never_removed_as_another_plugins_dependency(plugins_dir):
    """A record listing a plugin by name protects it, whatever another plugin claims."""
    mine = installing.Installed(name="a", version="1", dependencies=["b==1"])
    theirs = installing.Installed(name="b", version="1")
    installing.write_record([mine, theirs])

    assert installing.unshared_dependencies(mine) == []


def test_removing_takes_the_compiled_copies_a_worker_left(tmp_path, plugins_dir, installer):
    """`__pycache__` is in no `RECORD`, and every worker that imported the plugin wrote one.

    Without this the package directory stayed on the volume after a removal, looking
    installed to anyone who looked.
    """
    installing.install_wheel(a_wheel(tmp_path))
    cache = plugins_dir / "postulo_example" / "__pycache__"
    cache.mkdir(parents=True, exist_ok=True)
    (cache / "__init__.cpython-312.pyc").write_bytes(b"\x00")

    installing.remove("postulo-example")

    assert not (plugins_dir / "postulo_example").exists()


# ------------------------------------------------------- one plugin against another


def test_the_constraint_file_does_not_pin_the_plugin_being_installed(
    tmp_path, plugins_dir, monkeypatch
):
    """Its own pin would refuse the upgrade it exists to perform.

    The plugins directory goes on `sys.path` when a plugin is installed, so from the second
    install onwards `postulo-example==1.0` was in the environment the constraint file is
    built from -- while 1.0 was being upgraded to 2.0.
    """
    seen: list[str] = []

    def fake(target, wheel, constraint_file):
        seen.append(constraint_file.read_text(encoding="utf-8"))
        with zipfile.ZipFile(wheel) as archive:
            names = archive.namelist()
            archive.extractall(target)
        for dist_info in Path(target).glob("*.dist-info"):
            (dist_info / "RECORD").write_text(
                "\n".join(f"{m},," for m in names if not m.endswith("RECORD")) + "\n",
                encoding="utf-8",
            )
        return "installed"

    monkeypatch.setattr(installing, "run_install", fake)
    installing.install_wheel(a_wheel(tmp_path, version="1.0"))
    installing.install_wheel(a_wheel(tmp_path, version="2.0"))

    assert "postulo-example==" not in seen[-1]


def test_another_plugins_pins_are_in_the_constraint_file(tmp_path, plugins_dir):
    """What one plugin brought is what the next one may not move."""
    other = installing.Installed(name="postulo-other", version="3.0", dependencies=["leftpad==1.0"])
    installing.write_record([other])

    pins = installing.constraints(exclude="postulo-example")

    assert "leftpad==1.0" in pins
    assert "postulo-other==3.0" in pins


def test_a_plugins_own_dependency_may_move_when_it_is_upgraded(plugins_dir):
    """Nobody else is using it, so nothing is broken by moving it."""
    mine = installing.Installed(
        name="postulo-example", version="1.0", dependencies=["leftpad==1.0"]
    )
    installing.write_record([mine])

    pins = installing.constraints(exclude="postulo-example")

    assert not [pin for pin in pins if pin.startswith("leftpad==")]


def test_a_refusal_names_the_plugin_whose_pin_it_is_about(plugins_dir):
    """A resolver says a package name and stops. An administrator needs the plugin."""
    other = installing.Installed(name="postulo-other", version="3.0", dependencies=["leftpad==1.0"])
    installing.write_record([other])

    said = installing.explain_conflict(
        "cannot install leftpad==2.0 and leftpad==1.0", exclude="postulo-example"
    )

    assert "postulo-other" in said


def test_a_refusal_about_nothing_recognised_is_left_alone(plugins_dir):
    """Guessing at an owner would be worse than saying only what the resolver said."""
    installing.write_record([])

    assert installing.explain_conflict("something went wrong") == "something went wrong"


def test_a_conflict_with_another_plugins_pin_does_not_blame_postulo(tmp_path, plugins_dir):
    """ "Postulo has 1.0" used to be true by definition, because Postulo was the whole
    constraint. Another plugin can hold a pin now, and saying Postulo does would send an
    administrator looking in the wrong place."""
    other = installing.Installed(name="postulo-other", version="3.0", dependencies=["leftpad==1.0"])
    installing.write_record([other])
    wheel = a_wheel(tmp_path, requires=("leftpad==2.0",))

    with pytest.raises(InstallError) as raised:
        installing.check(installing.read_wheel(wheel))

    said = str(raised.value)
    assert "postulo-other has 1.0" in said
    assert "Postulo has" not in said
