import json
import os
import subprocess
import sys
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


def load_state(path: Path) -> dict:
    if not path.exists():
        return {}
    with open(path) as f:
        return json.load(f)


def save_state(path: Path, data: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "w") as f:
        json.dump(data, f, indent=2)
        f.write("\n")


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

    default_state = Path.home() / ".local" / "share" / "release-puller" / "state.json"
    state_path = Path(config.get("state_file", str(default_state)))
    state = load_state(state_path)

    repos = config.get("repos", [])
    if not repos:
        print("No repos configured.", file=sys.stderr)
        return

    changed = False
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

        current = state.get(slug)
        if tag == current:
            print(f"[{slug}] up to date ({tag})")
            continue

        print(f"[{slug}] new release: {tag} (was {current or 'untracked'})")
        github_url = f"https://github.com/{slug}.git"

        try:
            sync_repo(github_url, local_path, tag)
        except subprocess.CalledProcessError as e:
            print(f"[{slug}] git error: {e}", file=sys.stderr)
            continue

        state[slug] = tag
        changed = True
        print(f"[{slug}] synced to {tag}")

    if changed:
        save_state(state_path, state)
        print(f"State saved to {state_path}")
