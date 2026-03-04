# release-puller

Polls GitHub releases and pulls the latest version when a new one is detected.

Zero external dependencies — stdlib only (Python 3.11+).

## Install

```bash
pip install -e .
```

## Usage

```bash
release-puller --config config.toml
```

Or via module:

```bash
python -m release_puller --config config.toml
```

## Configuration

Create a TOML config file (see `config.example.toml`):

```toml
# Optional: GitHub personal access token (avoids 60 req/hr rate limit)
# github_token = "ghp_..."

[[repos]]
github = "owner/repo"
local_path = "/home/user/projects/repo"
# protocol = "ssh"  # default: "https"
```

- `github_token` — also accepted via the `GITHUB_TOKEN` environment variable
- `protocol` — `"https"` (default) or `"ssh"` per repo. SSH uses your SSH key; HTTPS uses the token.

## How It Works

On each invocation:

1. Loads the TOML config
2. For each configured repo, queries the GitHub API for the latest release tag
3. If the repo is already cloned and checked out at that tag — skips (already up to date)
4. If it's a new tag — clones the repo (or fetches if it already exists) and checks out the tag

There is no built-in scheduler. Use cron, systemd timers, or similar to run periodically.
