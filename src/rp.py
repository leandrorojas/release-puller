"""release-puller — poll GitHub releases and pull the latest version."""

import argparse
import json
import os
import subprocess
import sys
import tomllib
import urllib.error
import urllib.request
from pathlib import Path


def get_latest_release(owner: str, repo: str, token: str | None = None) -> str | None:
    """Fetch the latest release tag from GitHub REST API.

    Returns the tag name string, or None if no releases exist.
    """
    url = f"https://api.github.com/repos/{owner}/{repo}/releases/latest"
    req = urllib.request.Request(url)
    req.add_header("Accept", "application/vnd.github+json")
    if token:
        req.add_header("Authorization", f"Bearer {token}")

    try:
        with urllib.request.urlopen(req) as resp:
            data = json.loads(resp.read())
    except urllib.error.HTTPError as e:
        if e.code == 404:
            return None
        raise

    return data["tag_name"]


def get_current_tag(local_path: Path) -> str | None:
    """Return the tag checked out in local_path, or None if not on an exact tag."""
    try:
        result = subprocess.run(
            ["git", "describe", "--tags", "--exact-match"],
            cwd=local_path,
            capture_output=True,
            text=True,
        )
        if result.returncode == 0:
            return result.stdout.strip()
    except Exception:
        pass
    return None


def sync_repo(github_url: str, local_path: Path, tag: str) -> None:
    """Clone or update a repo and check out the given tag."""
    if not local_path.exists():
        subprocess.run(
            ["git", "clone", github_url, str(local_path)],
            check=True,
        )
    else:
        subprocess.run(
            ["git", "fetch", "--tags"],
            cwd=local_path,
            check=True,
        )

    subprocess.run(
        ["git", "checkout", tag],
        cwd=local_path,
        check=True,
    )


def run(config: dict) -> None:
    token = config.get("github_token") or os.environ.get("GITHUB_TOKEN")

    repos = config.get("repos", [])
    if not repos:
        print("No repos configured.", file=sys.stderr)
        return

    for repo_cfg in repos:
        slug = repo_cfg["github"]
        local_path = Path(repo_cfg["local_path"]).expanduser()
        owner, repo = slug.split("/", 1)

        print(f"[{slug}] checking for latest release...")

        try:
            tag = get_latest_release(owner, repo, token)
        except Exception as e:
            print(f"[{slug}] error fetching release: {e}", file=sys.stderr)
            continue

        if tag is None:
            print(f"[{slug}] no releases found, skipping")
            continue

        current = get_current_tag(local_path) if local_path.exists() else None
        if tag == current:
            print(f"[{slug}] up to date ({tag})")
            continue

        print(f"[{slug}] new release: {tag} (was {current or 'untracked'})")
        protocol = repo_cfg.get("protocol", "https")
        if protocol == "ssh":
            github_url = f"git@github.com:{slug}.git"
        else:
            github_url = f"https://github.com/{slug}.git"

        try:
            sync_repo(github_url, local_path, tag)
        except subprocess.CalledProcessError as e:
            print(f"[{slug}] git error: {e}", file=sys.stderr)
            continue

        print(f"[{slug}] synced to {tag}")


def load_config(path: Path) -> dict:
    with open(path, "rb") as f:
        return tomllib.load(f)


def main() -> None:
    parser = argparse.ArgumentParser(
        prog="release-puller",
        description="Poll GitHub releases and pull the latest version.",
    )
    default_config = Path(__file__).resolve().parent / "config.toml"
    parser.add_argument(
        "--config",
        type=Path,
        default=default_config,
        help=f"Path to TOML config file (default: {default_config})",
    )
    args = parser.parse_args()

    if not args.config.exists():
        print(f"error: config file not found: {args.config}", file=sys.stderr)
        sys.exit(1)

    config = load_config(args.config)
    run(config)


if __name__ == "__main__":
    main()
