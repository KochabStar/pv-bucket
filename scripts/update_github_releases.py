#!/usr/bin/env python3
from __future__ import annotations

import argparse
import dataclasses
import enum
import hashlib
import json
import os
import re
import sys
import urllib.request
from pathlib import Path
from typing import Iterable


GITHUB_RELEASE_RE = re.compile(
    r"^https://github\.com/(?P<owner>[^/]+)/(?P<repo>[^/]+)/releases/download/(?P<tag>[^/]+)/(?P<asset>[^#]+)(?P<fragment>#/.+)?$"
)


@dataclasses.dataclass(frozen=True)
class ReleaseAsset:
    name: str
    browser_download_url: str


@dataclasses.dataclass(frozen=True)
class Release:
    tag_name: str
    assets: list[ReleaseAsset]


class Status(enum.Enum):
    UPDATED = "updated"
    SKIPPED = "skipped"
    IGNORED = "ignored"
    NEEDS_ATTENTION = "needs_attention"


@dataclasses.dataclass(frozen=True)
class UpdateResult:
    path: Path
    status: Status
    reason: str


class GitHubClient:
    def __init__(self, token: str | None = None) -> None:
        self.token = token

    def latest_release(self, repository: str) -> Release:
        request = urllib.request.Request(
            f"https://api.github.com/repos/{repository}/releases/latest",
            headers=self._headers(),
        )
        with urllib.request.urlopen(request, timeout=60) as response:
            payload = json.load(response)
        return Release(
            tag_name=payload["tag_name"],
            assets=[
                ReleaseAsset(
                    name=asset["name"],
                    browser_download_url=asset["browser_download_url"],
                )
                for asset in payload.get("assets", [])
            ],
        )

    def download_sha256(self, url: str) -> str:
        request = urllib.request.Request(url, headers=self._headers())
        digest = hashlib.sha256()
        with urllib.request.urlopen(request, timeout=300) as response:
            while chunk := response.read(1024 * 1024):
                digest.update(chunk)
        return digest.hexdigest()

    def _headers(self) -> dict[str, str]:
        headers = {
            "Accept": "application/vnd.github+json",
            "User-Agent": "pv-bucket-updater",
            "X-GitHub-Api-Version": "2022-11-28",
        }
        if self.token:
            headers["Authorization"] = f"Bearer {self.token}"
        return headers


class GitHubReleaseUpdater:
    def __init__(self, client: GitHubClient) -> None:
        self.client = client

    def update_bucket(self, root: Path) -> list[UpdateResult]:
        results: list[UpdateResult] = []
        for manifest_path in sorted(root.glob("*/*.toml")):
            results.append(self.update_manifest(manifest_path))
        return results

    def update_manifest(self, path: Path) -> UpdateResult:
        text = path.read_text(encoding="utf-8")
        url = _read_string_field(text, "url")
        current_version = _read_string_field(text, "version")
        if not url or not current_version:
            return UpdateResult(path, Status.NEEDS_ATTENTION, "missing version or url field")

        parsed = GITHUB_RELEASE_RE.match(url)
        if not parsed:
            return UpdateResult(path, Status.IGNORED, "not a GitHub release URL")

        try:
            repository = f"{parsed.group('owner')}/{parsed.group('repo')}"
            release = self.client.latest_release(repository)
            current_release_version = _normalize_version(parsed.group("tag"))
            latest_version = _normalize_version(release.tag_name)
            if release.tag_name == parsed.group("tag") or latest_version == current_version:
                return UpdateResult(path, Status.SKIPPED, "already up to date")

            asset = _select_matching_asset(
                parsed.group("asset"),
                current_release_version,
                latest_version,
                release.assets,
            )
            if asset is None:
                return UpdateResult(path, Status.NEEDS_ATTENTION, "no matching asset in latest release")

            fragment = parsed.group("fragment") or ""
            new_url = f"{asset.browser_download_url}{fragment}"
            new_hash = f"sha256:{self.client.download_sha256(asset.browser_download_url)}"
        except Exception as error:
            return UpdateResult(path, Status.NEEDS_ATTENTION, str(error))

        updated = _replace_string_field(text, "version", latest_version)
        updated = _replace_string_field(updated, "url", new_url)
        updated = _replace_string_field(updated, "hash", new_hash)
        updated = _replace_extract_dir(updated, current_release_version, latest_version)
        path.write_text(updated, encoding="utf-8", newline="")

        return UpdateResult(path, Status.UPDATED, f"{current_version} -> {latest_version}")


def _read_string_field(text: str, key: str) -> str | None:
    match = re.search(rf'(?m)^{re.escape(key)}\s*=\s*"([^"]*)"', text)
    return match.group(1) if match else None


def _replace_string_field(text: str, key: str, value: str) -> str:
    return re.sub(
        rf'(?m)^({re.escape(key)}\s*=\s*)"[^"]*"',
        rf'\1"{value}"',
        text,
        count=1,
    )


def _replace_extract_dir(text: str, old_version: str, new_version: str) -> str:
    old_extract_dir = _read_string_field(text, "extract_dir")
    if not old_extract_dir or old_version not in old_extract_dir:
        return text
    return _replace_string_field(text, "extract_dir", old_extract_dir.replace(old_version, new_version))


def _normalize_version(tag_name: str) -> str:
    for prefix in ("release-", "rust-v", "bun-v", "llvmorg-", "v"):
        if tag_name.startswith(prefix):
            return tag_name[len(prefix) :]
    return tag_name


def _select_matching_asset(
    current_asset_name: str,
    current_version: str,
    latest_version: str,
    assets: Iterable[ReleaseAsset],
) -> ReleaseAsset | None:
    expected_name = current_asset_name.replace(current_version, latest_version)
    for asset in assets:
        if asset.name == expected_name:
            return asset

    current_shape = _asset_shape(current_asset_name, current_version)
    candidates = [asset for asset in assets if _asset_shape(asset.name, latest_version) == current_shape]
    return candidates[0] if len(candidates) == 1 else None


def _asset_shape(name: str, version: str) -> str:
    return name.replace(version, "{version}")


def _print_summary(results: list[UpdateResult]) -> None:
    for status in Status:
        selected = [result for result in results if result.status == status]
        if not selected:
            continue
        print(f"{status.value}: {len(selected)}")
        for result in selected:
            print(f"  - {result.path.as_posix()}: {result.reason}")


def _write_github_summary(results: list[UpdateResult]) -> None:
    summary_path = os.environ.get("GITHUB_STEP_SUMMARY")
    if not summary_path:
        return

    lines = ["# pv-bucket auto update", ""]
    for status in Status:
        selected = [result for result in results if result.status == status]
        lines.append(f"## {status.value}: {len(selected)}")
        if selected:
            lines.extend(f"- `{result.path.as_posix()}`: {result.reason}" for result in selected)
        lines.append("")

    Path(summary_path).write_text("\n".join(lines), encoding="utf-8")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Update pv-bucket manifests from GitHub Releases.")
    parser.add_argument("--root", type=Path, default=Path.cwd(), help="Bucket repository root.")
    args = parser.parse_args(argv)

    client = GitHubClient(os.environ.get("GITHUB_TOKEN"))
    results = GitHubReleaseUpdater(client).update_bucket(args.root)
    _print_summary(results)
    _write_github_summary(results)
    return 0


if __name__ == "__main__":
    sys.exit(main())
