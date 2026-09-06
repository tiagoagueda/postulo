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
