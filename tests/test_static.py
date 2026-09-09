"""The static files collect the way the container image collects them.

The image runs ``collectstatic`` with WhiteNoise's manifest storage, which rewrites every
reference inside CSS and JavaScript to a hashed name and refuses a reference that points
at a file that does not exist. A vendored script ending in a source-map pointer once
failed the image build while every test passed; this is the test that would have caught
it.
"""

from django.core.management import call_command


def test_collectstatic_with_the_manifest_storage_succeeds(tmp_path, settings):
    settings.STATIC_ROOT = tmp_path / "static"
    settings.STORAGES = {
        **settings.STORAGES,
        "staticfiles": {
            "BACKEND": "whitenoise.storage.CompressedManifestStaticFilesStorage",
        },
    }
    call_command("collectstatic", interactive=False, verbosity=0, clear=True)
    manifest = tmp_path / "static" / "staticfiles.json"
    assert manifest.is_file()
    assert (tmp_path / "static" / "js" / "vendor" / "zxcvbn" / "core.js").is_file()


def test_the_committed_stylesheet_is_one_a_person_can_read_a_diff_of():
    """#159: it is committed, so every rebuild lands in somebody's review.

    Minified it was a single line of 76 kB, which meant every rebuild produced a two-line
    diff a hundred kilobytes wide: unreadable in a terminal, truncated by a review tool,
    and useless to `git add -p`. That is not a cosmetic complaint — a Tailwind upgrade
    quietly changing a base rule is exactly what a diff should catch, and no one could see
    one.

    Written out, it costs 819 bytes of the 11 kB WhiteNoise actually sends, because
    compression removes almost everything minification does. The number of lines is not
    pinned; that it is not *one* line is the whole point.
    """
    from pathlib import Path

    stylesheet = Path(__file__).resolve().parents[1] / "src" / "postulo" / "static" / "css"
    text = (stylesheet / "app.css").read_text(encoding="utf-8")
    lines = text.splitlines()

    assert len(lines) > 100, "app.css is minified again; drop --minify from npm run build:css"
    longest = max(len(line) for line in lines)
    assert longest < 2000, f"a {longest}-character line still cannot be reviewed"
