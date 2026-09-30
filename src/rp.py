"""release-puller — poll GitHub releases and pull the latest version."""

import argparse
import asyncio
import json
import os
import subprocess
import sys
import tomllib
import traceback
import urllib.error
import urllib.request
from pathlib import Path

from pyfangs.telegram import TelegramNotifier

HEALTHCHECKS_BASE_URL = "https://hc-ping.com"
# healthchecks.io keeps at most 100 kB of each ping body
HEALTHCHECKS_MAX_BODY = 100_000


def _github_get(path: str, token: str | None) -> dict:
    req = urllib.request.Request(f"https://api.github.com{path}")
    req.add_header("Accept", "application/vnd.github+json")
    if token:
        req.add_header("Authorization", f"Bearer {token}")
    with urllib.request.urlopen(req, timeout=30) as resp:
        return json.loads(resp.read())


def get_latest_release(owner: str, repo: str, token: str | None = None) -> str | None:
    """Fetch the latest release tag from GitHub REST API.

    Returns the tag name string, or None if the repo exists but has no releases.
    Raises if the repo itself is not found (typo in slug, or no access).
    """
    try:
        return _github_get(f"/repos/{owner}/{repo}/releases/latest", token)["tag_name"]
    except urllib.error.HTTPError as e:
        if e.code != 404:
            raise

    # A 404 above means either "no releases" or "no such repo"; only the first is OK.
    try:
        _github_get(f"/repos/{owner}/{repo}", token)
    except urllib.error.HTTPError as e:
        if e.code == 404:
            raise RuntimeError("repository not found (check the slug, or token access)") from e
        raise
    return None


def get_current_tag(local_path: Path) -> str | None:
    """Return the tag checked out in local_path, or None if not on an exact tag."""
    try:
        result = subprocess.run(
            ["git", "describe", "--tags", "--exact-match"],
            cwd=local_path,
            capture_output=True,
            encoding="utf-8",
            errors="replace",
        )
        if result.returncode == 0:
            return result.stdout.strip()
    except Exception:
        pass
    return None


def sync_repo(github_url: str, local_path: Path, tag: str) -> None:
    """Clone or update a repo and check out the given tag.

    git's stderr is captured so it can be reported via CalledProcessError.stderr.
    """
    if not local_path.exists():
        subprocess.run(
            ["git", "clone", github_url, str(local_path)],
            check=True,
            capture_output=True,
            encoding="utf-8",
            errors="replace",
        )
    else:
        subprocess.run(
            ["git", "fetch", "--tags"],
            cwd=local_path,
            check=True,
            capture_output=True,
            encoding="utf-8",
            errors="replace",
        )

    subprocess.run(
        ["git", "checkout", tag],
        cwd=local_path,
        check=True,
        capture_output=True,
        encoding="utf-8",
        errors="replace",
    )


def notify(bot_token: str, chat_id: str, slug: str, tag: str) -> None:
    """Send a Telegram notification about a newly synced release."""
    message = f"[{slug}] new release synced: {tag}"

    async def _send() -> None:
        async with TelegramNotifier(token=bot_token, chat_id=chat_id) as notifier:
            await notifier.send(message)

    asyncio.run(_send())


def ping_healthchecks(uuid: str | None, suffix: str = "", body: str = "") -> None:
    """Ping healthchecks.io, if a check is configured.

    Never raises: monitoring must not affect the run.
    """
    if not uuid:
        return
    try:
        # Keep the tail (where the failure reason is) and never split a UTF-8 character.
        tail = body.encode("utf-8", "replace")[-HEALTHCHECKS_MAX_BODY:]
        data = tail.decode("utf-8", "ignore").encode("utf-8")
        url = f"{HEALTHCHECKS_BASE_URL}/{uuid}{suffix}"
        with urllib.request.urlopen(urllib.request.Request(url, data=data), timeout=10):
            pass
    except Exception as e:
        print(f"healthchecks ping failed ({suffix or 'success'}): {e}", file=sys.stderr)


class _Tee:
    """Write-through stream wrapper that also records everything written."""

    def __init__(self, stream, buffer: list[str]):
        self._stream = stream
        self._buffer = buffer

    def write(self, s: str) -> int:
        self._buffer.append(s)
        return self._stream.write(s)

    def __getattr__(self, name):
        # isatty, encoding, fileno, flush, ... behave like the wrapped stream.
        return getattr(self._stream, name)


def sync_one(label: str, repo_cfg, token: str | None, bot_token: str | None, chat_id: str | None) -> bool:
    """Check one repo and sync it if there is a new release. Returns False on failure."""
    slug = repo_cfg.get("github") if isinstance(repo_cfg, dict) else None
    local = repo_cfg.get("local_path") if isinstance(repo_cfg, dict) else None
    if not isinstance(slug, str) or slug.count("/") != 1 or not isinstance(local, str):
        print(
            f'[{label}] invalid repo config: need github = "owner/repo" and local_path = "..."',
            file=sys.stderr,
        )
        return False
    owner, repo = slug.split("/")
    local_path = Path(local).expanduser()

    print(f"[{slug}] checking for latest release...")

    try:
        tag = get_latest_release(owner, repo, token)
    except Exception as e:
        print(f"[{slug}] error fetching release: {e}", file=sys.stderr)
        return False

    if tag is None:
        print(f"[{slug}] no releases found, skipping")
        return True

    current = get_current_tag(local_path) if local_path.exists() else None
    if tag == current:
        print(f"[{slug}] up to date ({tag})")
        return True

    print(f"[{slug}] new release: {tag} (was {current or 'untracked'})")
    if repo_cfg.get("protocol", "https") == "ssh":
        github_url = f"git@github.com:{slug}.git"
    else:
        github_url = f"https://github.com/{slug}.git"

    try:
        sync_repo(github_url, local_path, tag)
    except subprocess.CalledProcessError as e:
        detail = (e.stderr or "").strip()
        print(f"[{slug}] git error: {e}" + (f"\n{detail}" if detail else ""), file=sys.stderr)
        return False

    print(f"[{slug}] synced to {tag}")

    if bot_token and chat_id:
        try:
            notify(bot_token, chat_id, slug, tag)
        except Exception as e:
            print(f"[{slug}] telegram notification failed: {e}", file=sys.stderr)
            return False

    return True


def run(config: dict) -> list[str]:
    """Sync every configured repo. Returns the labels of repos that failed.

    "Up to date" and "no releases" are successes. Config, API, git and
    Telegram errors are failures. One repo failing never stops the others.
    """
    token = config.get("github_token") or os.environ.get("GITHUB_TOKEN")
    bot_token = config.get("telegram_bot_token")
    chat_id = config.get("telegram_chat_id")

    failed: list[str] = []
    for repo_cfg in config["repos"]:
        label = str(repo_cfg.get("github", "?") if isinstance(repo_cfg, dict) else repo_cfg)
        try:
            ok = sync_one(label, repo_cfg, token, bot_token, chat_id)
        except Exception as e:
            # e.g. git missing from PATH, permission errors
            print(f"[{label}] unexpected error: {type(e).__name__}: {e}", file=sys.stderr)
            ok = False
        if not ok:
            failed.append(label)
    return failed


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
    # Under cron stdout is a file and block-buffered; keep it in order with stderr.
    sys.stdout.reconfigure(line_buffering=True)

    # Until the config loads, only the env var can say where to report.
    hc_uuid = os.environ.get("HEALTHCHECKS_UUID")
    try:
        config = load_config(args.config)
        hc_uuid = config.get("healthchecks_uuid") or hc_uuid
        if not isinstance(config.get("repos"), list) or not config["repos"]:
            raise ValueError("no [[repos]] configured")
    except (OSError, ValueError) as e:  # ValueError covers TOML and UTF-8 decode errors
        message = f"error: cannot load config {args.config}: {e}"
        print(message, file=sys.stderr)
        ping_healthchecks(hc_uuid, "/fail", message)
        sys.exit(1)

    # Record the run's output so it can be sent as the healthchecks ping body.
    output: list[str] = []
    stdout, stderr = sys.stdout, sys.stderr
    sys.stdout, sys.stderr = _Tee(stdout, output), _Tee(stderr, output)
    ok = False
    crash = ""
    try:
        ping_healthchecks(hc_uuid, "/start")
        failed = run(config)
        if failed:
            print(f"failed: {', '.join(failed)}", file=sys.stderr)
        ok = not failed
    except BaseException:
        crash = traceback.format_exc()
        raise
    finally:
        sys.stdout, sys.stderr = stdout, stderr
        ping_healthchecks(hc_uuid, "" if ok else "/fail", "".join(output) + crash)
    sys.exit(0 if ok else 1)

if __name__ == "__main__":
    main()
