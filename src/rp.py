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
            text=True,
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
            text=True,
        )
    else:
        subprocess.run(
            ["git", "fetch", "--tags"],
            cwd=local_path,
            check=True,
            capture_output=True,
            text=True,
        )

    subprocess.run(
        ["git", "checkout", tag],
        cwd=local_path,
        check=True,
        capture_output=True,
        text=True,
    )


def notify(bot_token: str, chat_id: str, slug: str, tag: str) -> None:
    """Send a Telegram notification about a newly synced release."""
    message = f"[{slug}] new release synced: {tag}"

    async def _send() -> None:
        async with TelegramNotifier(token=bot_token, chat_id=chat_id) as notifier:
            await notifier.send(message)

    asyncio.run(_send())


def ping_healthchecks(uuid: str, suffix: str = "", body: str = "") -> None:
    """Ping healthchecks.io. Never raises: monitoring must not affect the run."""
    url = f"{HEALTHCHECKS_BASE_URL}/{uuid}{suffix}"
    data = body.encode("utf-8")[-HEALTHCHECKS_MAX_BODY:]
    try:
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

    def flush(self) -> None:
        self._stream.flush()


def run(config: dict) -> list[str]:
    """Sync every configured repo. Returns the slugs of repos that failed.

    "Up to date" and "no releases" are successes. API errors, git errors and
    failed Telegram notifications are failures.
    """
    token = config.get("github_token") or os.environ.get("GITHUB_TOKEN")
    bot_token = config.get("telegram_bot_token")
    chat_id = config.get("telegram_chat_id")

    repos = config.get("repos", [])
    if not repos:
        print("No repos configured.", file=sys.stderr)
        return ["<config>"]

    failed: list[str] = []
    for repo_cfg in repos:
        slug = repo_cfg.get("github", "<missing github>")
        try:
            local_path = Path(repo_cfg["local_path"]).expanduser()
            owner, repo = slug.split("/", 1)
        except (KeyError, ValueError) as e:
            print(f"[{slug}] invalid repo config: {e!r}", file=sys.stderr)
            failed.append(slug)
            continue

        print(f"[{slug}] checking for latest release...")

        try:
            tag = get_latest_release(owner, repo, token)
        except Exception as e:
            print(f"[{slug}] error fetching release: {e}", file=sys.stderr)
            failed.append(slug)
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
            detail = (e.stderr or "").strip()
            print(f"[{slug}] git error: {e}" + (f"\n{detail}" if detail else ""), file=sys.stderr)
            failed.append(slug)
            continue

        print(f"[{slug}] synced to {tag}")

        if bot_token and chat_id:
            try:
                notify(bot_token, chat_id, slug, tag)
            except Exception as e:
                print(f"[{slug}] telegram notification failed: {e}", file=sys.stderr)
                failed.append(slug)

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

    try:
        config = load_config(args.config)
    except (OSError, tomllib.TOMLDecodeError) as e:
        message = f"error: cannot load config {args.config}: {e}"
        print(message, file=sys.stderr)
        # The config is unreadable, so only the env var can say where to report.
        hc_uuid = os.environ.get("HEALTHCHECKS_UUID")
        if hc_uuid:
            ping_healthchecks(hc_uuid, "/fail", message)
        sys.exit(1)

    hc_uuid = config.get("healthchecks_uuid") or os.environ.get("HEALTHCHECKS_UUID")
    if hc_uuid:
        ping_healthchecks(hc_uuid, "/start")

    # Record the run's output so it can be sent as the healthchecks ping body.
    output: list[str] = []
    stdout, stderr = sys.stdout, sys.stderr
    sys.stdout, sys.stderr = _Tee(stdout, output), _Tee(stderr, output)
    try:
        failed = run(config)
        if failed:
            print(f"failed: {', '.join(failed)}", file=sys.stderr)
    except BaseException:
        if hc_uuid:
            ping_healthchecks(hc_uuid, "/fail", "".join(output) + traceback.format_exc())
        raise
    finally:
        sys.stdout, sys.stderr = stdout, stderr

    if hc_uuid:
        ping_healthchecks(hc_uuid, "/fail" if failed else "", "".join(output))
    sys.exit(1 if failed else 0)


if __name__ == "__main__":
    main()
