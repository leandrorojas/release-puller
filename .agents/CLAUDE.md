# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Project Purpose

`release-puller` is a lightweight CLI tool that polls GitHub repositories for new releases and syncs them locally. It is generic and reusable across any project. When a new GitHub release tag is detected, the tool clones the repo (or fetches + checks out the tag if already cloned).

Scheduling (cron, systemd, daemons) is **intentionally out of scope** — users wire up their own invocation mechanism. The tool runs as a simple one-shot command: check for a new release, act if one exists, then exit.

## Architecture

On each invocation the tool:
1. Reads configuration from a TOML config file (`tomllib`)
2. For each configured repo, calls the GitHub Releases API to get the latest release tag
3. If the repo is already cloned, checks the current tag on disk via `git describe --tags --exact-match`
4. If the tag matches — skips. If it's new — clones or fetches + checks out the tag
5. If Telegram is configured, sends a notification via `pyfangs.telegram.TelegramNotifier`

Key design principles:
- **No state file** — the local git checkout is the source of truth
- **No scheduler built-in** — users call the tool however they want (cron, systemd timer, manual)

## Project Structure

```
src/rp.py               — single-file script: CLI, config loading, GitHub API, git sync, notifications
config.example.toml      — example TOML config
pyproject.toml           — dependency management (uv sync)
```

## Dependencies

Managed via `pyproject.toml` and installed with `uv sync`.

- `pyfangs` (from git, v0.7.2) — provides `TelegramNotifier` for optional Telegram notifications

## Configuration

Config is a TOML file with:
- `github_token` (optional) — GitHub PAT for API auth; also accepted via `GITHUB_TOKEN` env var
- `telegram_bot_token` (optional) — Telegram bot token from @BotFather
- `telegram_chat_id` (optional) — Telegram chat ID for notifications
- `[[repos]]` array, each with:
  - `github` — `"owner/repo"` format
  - `local_path` — where to clone/sync
  - `protocol` (optional) — `"https"` (default) or `"ssh"`

## Running

```bash
uv sync
python3 src/rp.py --config config.example.toml
```

## GitHub API

Uses `GET /repos/{owner}/{repo}/releases/latest` via `urllib.request`. Supports optional `GITHUB_TOKEN` env var or `github_token` config key to avoid the 60 req/hr unauthenticated rate limit.
