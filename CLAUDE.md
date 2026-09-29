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
```

There is no test suite, linter, or formatter configured. Python >= 3.12 (uses `tomllib`, `X | None` syntax).

## Architecture

Everything lives in `src/rp.py` (stdlib only, plus `pyfangs`). Flow per invocation (`run()`):

1. Load TOML config (`--config`, default is `config.toml` beside `rp.py`, i.e. `src/config.toml`, not the repo root).
2. For each `[[repos]]` entry: `GET /repos/{owner}/{repo}/releases/latest` via `urllib.request` (404 → no releases, skip).
3. Compare to the local checkout's tag from `git describe --tags --exact-match`. Match → skip.
4. Otherwise `git clone` (if `local_path` is missing) or `git fetch --tags`, then `git checkout <tag>` (detached HEAD). Clone URL is built from `protocol` (`https` default, or `ssh`).
5. If both `telegram_bot_token` and `telegram_chat_id` are set, send a notification through `pyfangs.telegram.TelegramNotifier`. It is async and wrapped in `asyncio.run` for each message.

Design invariants:
- **No state file**: the git checkout on disk is the only source of truth for "current version".
- **Per-repo failure isolation**: API, git, and Telegram errors are printed to stderr and the loop `continue`s. The process exits non-zero only when the config file is missing.
- Git operations shell out to `git` via `subprocess`. There is no git library.

## Configuration

See `config.example.toml`. Top-level keys: `github_token` (falls back to the `GITHUB_TOKEN` env var; avoids the 60 req/hr unauthenticated limit), `telegram_bot_token`, `telegram_chat_id`. Each `[[repos]]` entry has `github = "owner/repo"`, `local_path` (`~` expanded), and optional `protocol`.

A real `config.toml` holds secrets and is gitignored.

## Notes

- `pyfangs` is pinned by git tag in `pyproject.toml` (`[tool.uv.sources]`). To bump it, change `rev` and re-run `uv sync` so `uv.lock` updates.
- Keep `README.md` in sync when changing config keys or CLI flags. The README documents both.
