import hashlib
import os
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from scripts.update_github_releases import (
    GitHubReleaseUpdater,
    Release,
    ReleaseAsset,
    Status,
    UpdateResult,
    _write_github_summary,
)


RIPGREP_MANIFEST = """name = "ripgrep"
version = "15.1.0"
description = "Recursively searches directories for a regex pattern."
homepage = "https://github.com/BurntSushi/ripgrep"
license = "MIT"
type = "archive"
bin = ["rg.exe"]

[architecture.x64]
url = "https://github.com/BurntSushi/ripgrep/releases/download/15.1.0/ripgrep-15.1.0-x86_64-pc-windows-msvc.zip"
hash = "sha256:old"
extract_dir = "ripgrep-15.1.0-x86_64-pc-windows-msvc"
"""


class FakeGitHubClient:
    def __init__(self, releases, downloads):
        self.releases = releases
        self.downloads = downloads
        self.downloaded_urls = []

    def latest_release(self, repository):
        return self.releases[repository]

    def download_sha256(self, url):
        self.downloaded_urls.append(url)
        return hashlib.sha256(self.downloads[url]).hexdigest()


class FailingGitHubClient:
    def latest_release(self, repository):
        raise RuntimeError(f"api failed for {repository}")

    def download_sha256(self, url):
        raise AssertionError("download should not be reached")


class GitHubReleaseUpdaterTests(unittest.TestCase):
    def test_updates_github_release_manifest_with_matching_asset(self):
        with tempfile.TemporaryDirectory() as tmp:
            manifest_path = Path(tmp) / "terminal" / "ripgrep.toml"
            manifest_path.parent.mkdir()
            manifest_path.write_text(RIPGREP_MANIFEST, encoding="utf-8")
            artifact_url = "https://github.com/BurntSushi/ripgrep/releases/download/15.2.0/ripgrep-15.2.0-x86_64-pc-windows-msvc.zip"
            client = FakeGitHubClient(
                {
                    "BurntSushi/ripgrep": Release(
                        tag_name="15.2.0",
                        assets=[
                            ReleaseAsset(
                                name="ripgrep-15.2.0-x86_64-pc-windows-msvc.zip",
                                browser_download_url=artifact_url,
                            )
                        ],
                    )
                },
                {artifact_url: b"new ripgrep archive"},
            )

            result = GitHubReleaseUpdater(client).update_manifest(manifest_path)

            updated = manifest_path.read_text(encoding="utf-8")
            expected_hash = hashlib.sha256(b"new ripgrep archive").hexdigest()
            self.assertEqual(result.status, Status.UPDATED)
            self.assertIn('version = "15.2.0"', updated)
            self.assertIn(f'url = "{artifact_url}"', updated)
            self.assertIn(f'hash = "sha256:{expected_hash}"', updated)
            self.assertIn(
                'extract_dir = "ripgrep-15.2.0-x86_64-pc-windows-msvc"',
                updated,
            )

    def test_preserves_url_fragment_when_asset_is_updated(self):
        with tempfile.TemporaryDirectory() as tmp:
            manifest_path = Path(tmp) / "desktop" / "motrix.toml"
            manifest_path.parent.mkdir()
            manifest_path.write_text(
                RIPGREP_MANIFEST.replace(
                    "ripgrep-15.1.0-x86_64-pc-windows-msvc.zip",
                    "Motrix-Setup-15.1.0.exe",
                ).replace(
                    "https://github.com/BurntSushi/ripgrep/releases/download/15.1.0/Motrix-Setup-15.1.0.exe",
                    "https://github.com/BurntSushi/ripgrep/releases/download/15.1.0/Motrix-Setup-15.1.0.exe#/dl.7z",
                ),
                encoding="utf-8",
            )
            artifact_url = "https://github.com/BurntSushi/ripgrep/releases/download/15.2.0/Motrix-Setup-15.2.0.exe"
            client = FakeGitHubClient(
                {
                    "BurntSushi/ripgrep": Release(
                        tag_name="15.2.0",
                        assets=[
                            ReleaseAsset(
                                name="Motrix-Setup-15.2.0.exe",
                                browser_download_url=artifact_url,
                            )
                        ],
                    )
                },
                {artifact_url: b"installer"},
            )

            result = GitHubReleaseUpdater(client).update_manifest(manifest_path)

            self.assertEqual(result.status, Status.UPDATED)
            self.assertIn(
                f'url = "{artifact_url}#/dl.7z"',
                manifest_path.read_text(encoding="utf-8"),
            )

    def test_skips_up_to_date_manifest_without_downloading(self):
        with tempfile.TemporaryDirectory() as tmp:
            manifest_path = Path(tmp) / "terminal" / "ripgrep.toml"
            manifest_path.parent.mkdir()
            manifest_path.write_text(RIPGREP_MANIFEST, encoding="utf-8")
            client = FakeGitHubClient(
                {
                    "BurntSushi/ripgrep": Release(
                        tag_name="15.1.0",
                        assets=[
                            ReleaseAsset(
                                name="ripgrep-15.1.0-x86_64-pc-windows-msvc.zip",
                                browser_download_url="https://example.invalid/rg.zip",
                            )
                        ],
                    )
                },
                {},
            )

            result = GitHubReleaseUpdater(client).update_manifest(manifest_path)

            self.assertEqual(result.status, Status.SKIPPED)
            self.assertEqual(client.downloaded_urls, [])

    def test_skips_when_release_tag_matches_current_url_tag_even_with_bucket_revision(self):
        with tempfile.TemporaryDirectory() as tmp:
            manifest_path = Path(tmp) / "network" / "aria2.toml"
            manifest_path.parent.mkdir()
            manifest_path.write_text(
                RIPGREP_MANIFEST.replace('version = "15.1.0"', 'version = "1.37.0-1"')
                .replace(
                    "https://github.com/BurntSushi/ripgrep/releases/download/15.1.0/ripgrep-15.1.0-x86_64-pc-windows-msvc.zip",
                    "https://github.com/aria2/aria2/releases/download/release-1.37.0/aria2-1.37.0-win-64bit-build1.zip",
                )
                .replace(
                    'extract_dir = "ripgrep-15.1.0-x86_64-pc-windows-msvc"',
                    'extract_dir = "aria2-1.37.0-win-64bit-build1"',
                ),
                encoding="utf-8",
            )
            client = FakeGitHubClient(
                {
                    "aria2/aria2": Release(
                        tag_name="release-1.37.0",
                        assets=[
                            ReleaseAsset(
                                name="aria2-1.37.0-win-64bit-build1.zip",
                                browser_download_url="https://example.invalid/aria2.zip",
                            )
                        ],
                    )
                },
                {},
            )

            result = GitHubReleaseUpdater(client).update_manifest(manifest_path)

            self.assertEqual(result.status, Status.SKIPPED)
            self.assertEqual(client.downloaded_urls, [])
            self.assertIn('version = "1.37.0-1"', manifest_path.read_text(encoding="utf-8"))

    def test_updates_bucket_revision_manifest_using_version_from_current_url_tag(self):
        with tempfile.TemporaryDirectory() as tmp:
            manifest_path = Path(tmp) / "network" / "aria2.toml"
            manifest_path.parent.mkdir()
            manifest_path.write_text(
                RIPGREP_MANIFEST.replace('version = "15.1.0"', 'version = "1.37.0-1"')
                .replace(
                    "https://github.com/BurntSushi/ripgrep/releases/download/15.1.0/ripgrep-15.1.0-x86_64-pc-windows-msvc.zip",
                    "https://github.com/aria2/aria2/releases/download/release-1.37.0/aria2-1.37.0-win-64bit-build1.zip",
                )
                .replace(
                    'extract_dir = "ripgrep-15.1.0-x86_64-pc-windows-msvc"',
                    'extract_dir = "aria2-1.37.0-win-64bit-build1"',
                ),
                encoding="utf-8",
            )
            artifact_url = "https://github.com/aria2/aria2/releases/download/release-1.38.0/aria2-1.38.0-win-64bit-build1.zip"
            client = FakeGitHubClient(
                {
                    "aria2/aria2": Release(
                        tag_name="release-1.38.0",
                        assets=[
                            ReleaseAsset(
                                name="aria2-1.38.0-win-64bit-build1.zip",
                                browser_download_url=artifact_url,
                            )
                        ],
                    )
                },
                {artifact_url: b"new aria2 archive"},
            )

            result = GitHubReleaseUpdater(client).update_manifest(manifest_path)

            updated = manifest_path.read_text(encoding="utf-8")
            self.assertEqual(result.status, Status.UPDATED)
            self.assertIn('version = "1.38.0"', updated)
            self.assertIn(f'url = "{artifact_url}"', updated)
            self.assertIn('extract_dir = "aria2-1.38.0-win-64bit-build1"', updated)

    def test_ignores_non_github_release_urls(self):
        with tempfile.TemporaryDirectory() as tmp:
            manifest_path = Path(tmp) / "desktop" / "app.toml"
            manifest_path.parent.mkdir()
            manifest_path.write_text(
                RIPGREP_MANIFEST.replace(
                    "https://github.com/BurntSushi/ripgrep/releases/download/15.1.0/ripgrep-15.1.0-x86_64-pc-windows-msvc.zip",
                    "https://example.invalid/app.zip",
                ),
                encoding="utf-8",
            )
            client = FakeGitHubClient({}, {})

            result = GitHubReleaseUpdater(client).update_manifest(manifest_path)

            self.assertEqual(result.status, Status.IGNORED)

    def test_reports_missing_matching_asset_without_changing_manifest(self):
        with tempfile.TemporaryDirectory() as tmp:
            manifest_path = Path(tmp) / "terminal" / "ripgrep.toml"
            manifest_path.parent.mkdir()
            manifest_path.write_text(RIPGREP_MANIFEST, encoding="utf-8")
            original = manifest_path.read_text(encoding="utf-8")
            client = FakeGitHubClient(
                {
                    "BurntSushi/ripgrep": Release(
                        tag_name="15.2.0",
                        assets=[
                            ReleaseAsset(
                                name="ripgrep-15.2.0-aarch64-apple-darwin.tar.gz",
                                browser_download_url="https://example.invalid/rg.tar.gz",
                            )
                        ],
                    )
                },
                {},
            )

            result = GitHubReleaseUpdater(client).update_manifest(manifest_path)

            self.assertEqual(result.status, Status.NEEDS_ATTENTION)
            self.assertIn("matching asset", result.reason)
            self.assertEqual(manifest_path.read_text(encoding="utf-8"), original)

    def test_reports_github_errors_without_changing_manifest(self):
        with tempfile.TemporaryDirectory() as tmp:
            manifest_path = Path(tmp) / "terminal" / "ripgrep.toml"
            manifest_path.parent.mkdir()
            manifest_path.write_text(RIPGREP_MANIFEST, encoding="utf-8")
            original = manifest_path.read_text(encoding="utf-8")

            result = GitHubReleaseUpdater(FailingGitHubClient()).update_manifest(manifest_path)

            self.assertEqual(result.status, Status.NEEDS_ATTENTION)
            self.assertIn("api failed for BurntSushi/ripgrep", result.reason)
            self.assertEqual(manifest_path.read_text(encoding="utf-8"), original)

    def test_writes_github_step_summary_when_configured(self):
        with tempfile.TemporaryDirectory() as tmp:
            summary_path = Path(tmp) / "summary.md"
            original = os.environ.get("GITHUB_STEP_SUMMARY")
            os.environ["GITHUB_STEP_SUMMARY"] = str(summary_path)
            try:
                _write_github_summary(
                    [
                        UpdateResult(
                            path=Path("terminal/ripgrep.toml"),
                            status=Status.UPDATED,
                            reason="15.1.0 -> 15.2.0",
                        ),
                        UpdateResult(
                            path=Path("desktop/app.toml"),
                            status=Status.NEEDS_ATTENTION,
                            reason="no matching asset in latest release",
                        ),
                    ]
                )
            finally:
                if original is None:
                    os.environ.pop("GITHUB_STEP_SUMMARY", None)
                else:
                    os.environ["GITHUB_STEP_SUMMARY"] = original

            summary = summary_path.read_text(encoding="utf-8")
            self.assertIn("# pv-bucket auto update", summary)
            self.assertIn("## updated: 1", summary)
            self.assertIn("`terminal/ripgrep.toml`: 15.1.0 -> 15.2.0", summary)
            self.assertIn("## needs_attention: 1", summary)
            self.assertIn("`desktop/app.toml`: no matching asset in latest release", summary)


if __name__ == "__main__":
    unittest.main()
