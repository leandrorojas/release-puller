# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Project Purpose

`release-puller` is a one-shot CLI that checks GitHub repos for a new latest release and, if found, clones/fetches the repo locally and checks out that tag. Scheduling (cron, systemd, etc.) is **intentionally out of scope** — don't add a loop, daemon, or scheduler.

## Commands

```bash
# Install deps. pyfangs is a private git dep over SSH — load your key first,
# or `uv sync` hangs silently waiting for auth (use `uv sync -v` to see the prompt).
eval "$(ssh-agent -s)" && ssh-add ~/.ssh/id_ed25519
uv sync

# Run (uv run uses the project venv, which is needed for pyfangs)
uv run src/rp.py                          # default config: src/config.toml (next to rp.py)
uv run src/rp.py --config path/to/config.toml

# Tests (pytest; pythonpath=src is set in pyproject.toml)
uv run pytest
uv run pytest tests/test_rp.py::test_git_error_exits_nonzero_with_git_stderr
```

There is no linter or formatter configured. Python >= 3.12 (uses `tomllib`, `X | None` syntax).

## Architecture

Everything lives in `src/rp.py` (stdlib only, plus `pyfangs`). Flow per invocation (`run()`):

1. Load TOML config (`--config`, default is `config.toml` beside `rp.py`, i.e. `src/config.toml`, not the repo root).
2. For each `[[repos]]` entry: `GET /repos/{owner}/{repo}/releases/latest` via `urllib.request`. A 404 is ambiguous, so it is followed by `GET /repos/{owner}/{repo}`: if the repo exists, it has no releases (skip, success); if not, it is a failure (a typo in the slug, or no token access).
3. Compare to the local checkout's tag from `git describe --tags --exact-match`. Match → skip.
4. Otherwise `git clone` (if `local_path` is missing) or `git fetch --tags`, then `git checkout <tag>` (detached HEAD). Clone URL is built from `protocol` (`https` default, or `ssh`).
5. If both `telegram_bot_token` and `telegram_chat_id` are set, send a notification through `pyfangs.telegram.TelegramNotifier`. It is async and wrapped in `asyncio.run` for each message.
6. `run()` returns the list of failed slugs. `main()` exits 1 if any failed, and pings healthchecks.io (`/start`, then success or `/fail`) when `healthchecks_uuid` is set. `main()` tees stdout/stderr into a buffer (`_Tee`), and that buffer becomes the ping body.

Design invariants:
- **No state file**: the git checkout on disk is the only source of truth for "current version".
- **Per-repo failure isolation, but failures are counted**: API, git, and Telegram errors are printed to stderr, recorded, and the loop `continue`s. The exit code is the job's only outcome signal for cron and monitoring. "Up to date" and "no releases" must stay successes, because they are the normal case on almost every run.
- **Monitoring never affects the outcome**: `ping_healthchecks` swallows every exception, so a healthchecks outage cannot crash the run or change the exit code.
- Git operations shell out to `git` via `subprocess`. There is no git library.

## Configuration

See `config.example.toml`. Top-level keys: `github_token` (falls back to the `GITHUB_TOKEN` env var; avoids the 60 req/hr unauthenticated limit), `telegram_bot_token`, `telegram_chat_id`, `healthchecks_uuid` (falls back to `HEALTHCHECKS_UUID`; the env var is the only source when the config itself fails to load). Each `[[repos]]` entry has `github = "owner/repo"`, `local_path` (`~` expanded), and optional `protocol`.

A real `config.toml` holds secrets and is gitignored.

## Notes

- `pyfangs` is pinned by git tag in `pyproject.toml` (`[tool.uv.sources]`). To bump it, change `rev` and re-run `uv sync` so `uv.lock` updates.
- release-puller pulls its own releases, and in production it also deploys other repos (for example `calendar-merge`). Tagging a release here ships on the next cron run.
- To verify under cron conditions, run it with an empty environment. An interactive shell's PATH can hide problems. Example: `env -i HOME=$HOME PATH=/usr/bin:/bin /bin/sh -c 'cd <repo> && <abs path to uv> run python3 src/rp.py'`.
- Keep `README.md` in sync when changing config keys or CLI flags. The README documents both.
