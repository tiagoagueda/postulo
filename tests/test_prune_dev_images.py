"""`scripts/prune-dev-images.py`: old dev tags go, everything else stays (#190).

The registry is a fake with the same four methods the real one has, so what is tested is
the choosing and the order -- read every manifest first, then delete the tags, then only the
manifests nothing left refers to -- rather than urllib.
"""

from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[1]


@pytest.fixture(scope="module")
def tool():
    spec = importlib.util.spec_from_file_location(
        "prune_dev_images", REPO / "scripts" / "prune-dev-images.py"
    )
    module = importlib.util.module_from_spec(spec)
    sys.modules["prune_dev_images"] = module
    spec.loader.exec_module(module)
    return module


def version(name: str, created: str) -> dict:
    return {"name": "postulo", "version": name, "created_at": created}


def index(*digests: str) -> dict:
    return {"manifests": [{"digest": d, "platform": {"os": "linux"}} for d in digests]}


A, B, C, D, E, F = (f"sha256:{c * 64}" for c in "abcdef")


class FakeRegistry:
    """The listing and the manifests of a registry, and a record of what was asked."""

    def __init__(self, versions: list[dict], manifests: dict[str, dict]) -> None:
        self._versions = versions
        self._manifests = manifests
        self.deleted: list[str] = []
        self.read: list[str] = []

    def versions(self) -> list[dict]:
        return list(self._versions)

    def manifest(self, reference: str) -> dict:
        self.read.append(reference)
        if reference in self.deleted:
            raise AssertionError(f"{reference} was read after it was deleted")
        return self._manifests[reference]

    def delete(self, name: str) -> bool:
        self.deleted.append(name)
        return True


# -- choosing ----------------------------------------------------------------------------------


def test_only_dev_tags_are_ever_candidates(tool):
    versions = [
        version("latest", "2026-09-01T00:00:00Z"),
        version("0.2.1", "2026-09-01T00:00:00Z"),
        version("dev", "2026-09-02T00:00:00Z"),
        version(A, "2026-09-02T00:00:00Z"),
        version("0.2.1-dev.3ac8448", "2026-09-03T00:00:00Z"),
        version("0.3.0-rc.1-dev.0123abc", "2026-09-04T00:00:00Z"),
        version("0.2.1-dev", "2026-09-05T00:00:00Z"),
        version("0.2.1-dev.notahash", "2026-09-05T00:00:00Z"),
        version("1.0.0-dev.abc", "2026-09-05T00:00:00Z"),
    ]

    doomed, remaining = tool.select(versions, keep=0)

    assert doomed == ["0.3.0-rc.1-dev.0123abc", "0.2.1-dev.3ac8448"], "newest first"
    assert "latest" in remaining and "0.2.1" in remaining and "dev" in remaining
    assert A not in remaining, "a bare digest is not a tag"
    for odd in ("0.2.1-dev", "0.2.1-dev.notahash", "1.0.0-dev.abc"):
        assert odd in remaining, f"{odd} is not spelled like a dev tag, so it stays"


def test_the_newest_are_kept_by_creation_time_not_by_name(tool):
    versions = [
        version("0.2.1-dev.aaaaaaa", "2026-09-10T10:00:00Z"),
        version("0.2.1-dev.fffffff", "2026-09-01T10:00:00Z"),
        version("0.2.1-dev.bbbbbbb", "2026-09-05T10:00:00+00:00"),
    ]

    doomed, remaining = tool.select(versions, keep=2)

    assert doomed == ["0.2.1-dev.fffffff"]
    assert set(remaining) == {"0.2.1-dev.aaaaaaa", "0.2.1-dev.bbbbbbb"}


def test_keeping_more_than_exist_deletes_nothing(tool):
    versions = [version("0.2.1-dev.aaaaaaa", "2026-09-10T10:00:00Z")]
    assert tool.select(versions, keep=5) == ([], ["0.2.1-dev.aaaaaaa"])
    with pytest.raises(ValueError):
        tool.select(versions, keep=-1)


def test_a_single_architecture_manifest_has_no_children(tool):
    assert tool.children({"config": {"digest": A}, "layers": [{"digest": B}]}) == set()
    assert tool.children(index(A, B)) == {A, B}
    assert tool.children({"manifests": [{"digest": "not-a-digest"}]}) == set()


# -- pruning -----------------------------------------------------------------------------------


def test_a_pruned_tag_takes_only_the_manifests_nothing_else_names(tool):
    # Two builds: the old one (A, B) and the new one (C, D). `dev` is the new one too, and
    # the release shares the amd64 half with the old build (E is its arm64 half).
    registry = FakeRegistry(
        [
            version("0.2.1", "2026-09-01T00:00:00Z"),
            version("0.2.1-dev.1111111", "2026-09-02T00:00:00Z"),
            version("0.2.1-dev.2222222", "2026-09-03T00:00:00Z"),
            version("dev", "2026-09-03T00:00:00Z"),
            *(version(d, "2026-09-02T00:00:00Z") for d in (A, B, C, D, E)),
        ],
        {
            "0.2.1": index(A, E),
            "0.2.1-dev.1111111": index(A, B),
            "0.2.1-dev.2222222": index(C, D),
            "dev": index(C, D),
        },
    )

    deleted = tool.prune(registry, keep=1)

    assert deleted == ["0.2.1-dev.1111111", B]
    assert registry.deleted == deleted, "the tag first, then what only it held"
    assert A not in registry.deleted, "the release still names A"
    assert registry.read.index("0.2.1-dev.1111111") < len(registry.read) - 1
    assert "dev" in registry.read and "0.2.1" in registry.read, "every remaining tag is read"


def test_a_dry_run_reads_everything_and_deletes_nothing(tool, capsys):
    registry = FakeRegistry(
        [
            version("0.2.1-dev.1111111", "2026-09-02T00:00:00Z"),
            version("0.2.1-dev.2222222", "2026-09-03T00:00:00Z"),
        ],
        {"0.2.1-dev.1111111": index(A, B), "0.2.1-dev.2222222": index(C, D)},
    )

    would = tool.prune(registry, keep=1, dry_run=True)

    assert would == ["0.2.1-dev.1111111", A, B]
    assert registry.deleted == []
    assert "would delete 0.2.1-dev.1111111" in capsys.readouterr().out


def test_nothing_past_the_kept_means_nothing_is_read_or_deleted(tool):
    registry = FakeRegistry(
        [version("0.2.1-dev.1111111", "2026-09-02T00:00:00Z"), version(A, "2026-09-02T00:00:00Z")],
        {"0.2.1-dev.1111111": index(A)},
    )

    assert tool.prune(registry, keep=5) == []
    assert registry.read == [] and registry.deleted == []


def test_a_manifest_shared_by_two_pruned_tags_goes_once(tool):
    registry = FakeRegistry(
        [
            version("0.2.1-dev.1111111", "2026-09-01T00:00:00Z"),
            version("0.2.1-dev.2222222", "2026-09-02T00:00:00Z"),
            version("0.2.1-dev.3333333", "2026-09-03T00:00:00Z"),
        ],
        {
            "0.2.1-dev.1111111": index(A, B),
            "0.2.1-dev.2222222": index(A, C),
            "0.2.1-dev.3333333": index(D, E),
        },
    )

    deleted = tool.prune(registry, keep=1)

    assert deleted == ["0.2.1-dev.2222222", "0.2.1-dev.1111111", A, B, C]
    assert len(set(deleted)) == len(deleted)


def test_the_credential_never_comes_from_the_command_line(tool, monkeypatch, capsys):
    monkeypatch.delenv("FORGEJO_USER", raising=False)
    monkeypatch.delenv("FORGEJO_TOKEN", raising=False)

    code = tool.main(["--registry", "x", "--owner", "o", "--package", "p"])

    assert code == 2
    assert "FORGEJO_USER and FORGEJO_TOKEN" in capsys.readouterr().err
