"""What `omh update` actually downloads, per channel.

The stable channel used to resolve the tag's repository archive. Measured on
2026-08-15 with `curl -sL -o /dev/null -w '%{size_download} %{time_total}'`:
the v1.0.6 tag archive is 45,921,555 bytes, the `main` branch archive is
46,012,605 bytes, and the published v1.0.6 wheel is 2,714,885 bytes. The
archive carries `assets/`, `tests/`, and `site/`; none of the three is needed
to run `omh`, and downloading them is the whole reason `omh update` took
minutes. These tests pin which artifact each channel names, and pin that the
heavy one still says so out loud.
"""

from __future__ import annotations

import re
import unittest
from pathlib import Path

from omh.release import (
    RELEASE_ARCHIVE_APPROX_SIZE,
    RELEASE_WHEEL_APPROX_SIZE,
    missing_release_asset_hint,
    package_url_for,
    release_artifact_note,
)

REPO_ROOT = Path(__file__).resolve().parents[1]
WHEEL_URL = (
    "https://github.com/rlaope/oh-my-hermes/releases/download/v1.0.6/"
    "oh_my_hermes-1.0.6-py3-none-any.whl"
)
ARCHIVE_URL = "https://github.com/rlaope/oh-my-hermes/archive/refs/tags/v1.0.6.zip"
PREVIEW_URL = "https://github.com/rlaope/oh-my-hermes/archive/refs/heads/main.zip"


class StableChannelArtifactTests(unittest.TestCase):
    def test_stable_resolves_the_published_wheel_asset(self) -> None:
        selection = package_url_for("stable", "1.0.6")

        self.assertEqual(selection.package_url, WHEEL_URL)
        self.assertEqual(selection.artifact_kind, "release-wheel")
        self.assertEqual(selection.source_label, "v1.0.6")

    def test_a_v_prefixed_version_resolves_to_the_same_asset(self) -> None:
        # `--version v1.0.6` and `--version 1.0.6` were interchangeable before
        # the wheel name entered the URL, and the wheel name carries no `v`.
        self.assertEqual(package_url_for("stable", "v1.0.6").package_url, WHEEL_URL)

    def test_a_version_outside_the_release_tag_shape_keeps_the_archive(self) -> None:
        # release.yml refuses to publish anything but `vX.Y.Z`, so there is no
        # asset name to predict here; guessing one would 404 on every install.
        selection = package_url_for("stable", "1.0.6rc1")

        self.assertEqual(
            selection.package_url,
            "https://github.com/rlaope/oh-my-hermes/archive/refs/tags/v1.0.6rc1.zip",
        )
        self.assertEqual(selection.artifact_kind, "repository-archive")

    def test_stable_still_requires_a_version(self) -> None:
        with self.assertRaises(ValueError) as raised:
            package_url_for("stable")

        self.assertIn("stable channel requires", str(raised.exception))


class PreviewAndLocalChannelTests(unittest.TestCase):
    def test_preview_still_tracks_the_branch_archive(self) -> None:
        # Preview tracks `main`, and GitHub publishes release assets per tag
        # only. There is no per-branch wheel to point at, so this stays the
        # heavy path rather than becoming an invented endpoint.
        selection = package_url_for("preview")

        self.assertEqual(selection.package_url, PREVIEW_URL)
        self.assertEqual(selection.artifact_kind, "repository-archive")
        self.assertEqual(selection.source_label, "main")

    def test_local_channel_is_unchanged(self) -> None:
        selection = package_url_for("local")

        self.assertEqual(selection.package_url, "local")
        self.assertEqual(selection.source_label, "local")
        self.assertEqual(selection.artifact_kind, "local")

    def test_an_explicit_package_url_still_wins_on_every_channel(self) -> None:
        for channel in ("stable", "preview", "local"):
            selection = package_url_for(channel, "1.0.6", "https://example.invalid/omh.whl")

            self.assertEqual(selection.package_url, "https://example.invalid/omh.whl")
            self.assertEqual(selection.artifact_kind, "custom-url")


class ArtifactCostDisclosureTests(unittest.TestCase):
    def test_the_heavy_path_names_its_size_before_the_download(self) -> None:
        note = release_artifact_note(package_url_for("preview"))

        self.assertIn(RELEASE_ARCHIVE_APPROX_SIZE, note)
        self.assertIn("minutes", note)

    def test_the_slim_path_names_its_size_too(self) -> None:
        note = release_artifact_note(package_url_for("stable", "1.0.6"))

        self.assertIn(RELEASE_WHEEL_APPROX_SIZE, note)
        self.assertNotIn("minutes", note)

    def test_local_and_custom_sources_have_nothing_to_disclose(self) -> None:
        self.assertEqual(release_artifact_note(package_url_for("local")), "")
        self.assertEqual(
            release_artifact_note(package_url_for("stable", "1.0.6", "https://example.invalid/x.whl")),
            "",
        )


class MissingAssetFallbackTests(unittest.TestCase):
    def test_a_wheel_failure_names_the_archive_that_does_exist(self) -> None:
        # v1.0.3 through v1.0.5 published no asset at all, so this is a real
        # state a user can reach, and pip reports it as a bare 404 against a
        # URL nobody typed.
        hint = missing_release_asset_hint(package_url_for("stable", "1.0.6"))

        self.assertIn("v1.0.6", hint)
        self.assertIn("--package-url", hint)
        self.assertIn(ARCHIVE_URL, hint)

    def test_paths_that_are_already_archives_offer_no_fallback(self) -> None:
        self.assertEqual(missing_release_asset_hint(package_url_for("preview")), "")
        self.assertEqual(missing_release_asset_hint(package_url_for("local")), "")


class InstallerParityTests(unittest.TestCase):
    """The installers resolve the same channels and must not fork from omh."""

    def setUp(self) -> None:
        self.shell = (REPO_ROOT / "install.sh").read_text(encoding="utf-8")
        self.powershell = (REPO_ROOT / "install.ps1").read_text(encoding="utf-8")

    def test_both_installers_build_the_stable_wheel_asset_name(self) -> None:
        for name, source in (("install.sh", self.shell), ("install.ps1", self.powershell)):
            with self.subTest(installer=name):
                self.assertIn("releases/download", source)
                self.assertIn("-py3-none-any.whl", source)
                self.assertRegex(
                    source,
                    re.compile(r"\^\[0-9\]\+\\\.\[0-9\]\+\\\.\[0-9\]\+\$"),
                    "the installer must gate the wheel name on the vX.Y.Z release-tag shape",
                )

    def test_both_installers_keep_the_archive_as_the_stable_fallback(self) -> None:
        self.assertIn('OMH_PACKAGE_URL="$OMH_REPO_ARCHIVE_ROOT/tags/$OMH_TAG.zip"', self.shell)
        self.assertIn('$OmhPackageUrl = "$OmhRepoArchiveRoot/tags/$OmhTag.zip"', self.powershell)

    def test_both_installers_default_to_stable(self) -> None:
        # A fresh install used to default to the `main` branch archive, which
        # GitHub generates per request rather than serving from a CDN: 46,012,605
        # bytes measured, and over five minutes on an ordinary connection. The
        # default is now the newest release wheel; tracking main is opt-in.
        self.assertIn('OMH_CHANNEL="${OMH_CHANNEL:-stable}"', self.shell)
        self.assertIn("Get-OmhEnv 'OMH_CHANNEL' 'stable'", self.powershell)

    def test_both_installers_resolve_latest_from_the_releases_redirect(self) -> None:
        # The installers are download scripts, so resolving "newest release" is
        # theirs to do -- `src/` stays offline, which INVARIANT 2 in
        # tests/test_handoff_safety_contract_enforcement.py enforces.
        for name, source in (("install.sh", self.shell), ("install.ps1", self.powershell)):
            with self.subTest(installer=name):
                self.assertIn("releases/latest", source)
                self.assertIn("/releases/tag/v", source)

    def test_a_failed_latest_lookup_names_both_ways_out(self) -> None:
        # An unresolvable redirect must not become a guessed URL.
        for name, source in (("install.sh", self.shell), ("install.ps1", self.powershell)):
            with self.subTest(installer=name):
                self.assertIn("could not resolve the latest release", source)
                self.assertIn("OMH_CHANNEL=preview", source)

    def test_the_installers_never_hand_omh_a_version_less_stable_channel(self) -> None:
        # `src/` refuses a version-less stable selection rather than reaching
        # the network, so the installer must always resolve a concrete version
        # before it forwards --channel stable to `omh setup`.
        self.assertIn('set -- setup --channel "$OMH_CHANNEL"', self.shell)
        self.assertIn('set -- "$@" --version "$OMH_VERSION"', self.shell)


if __name__ == "__main__":
    unittest.main()
