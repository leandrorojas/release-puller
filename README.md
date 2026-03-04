# release-puller

Polls GitHub releases and pulls the latest version when a new one is detected.

## Install

The `pyfangs` dependency is fetched from a private GitHub repo via SSH. Make sure your SSH key is loaded before running `uv sync`, otherwise the command will hang silently waiting for authentication:

```bash
eval "$(ssh-agent -s)"
ssh-add ~/.ssh/id_ed25519
uv sync
```

If `uv sync` appears stuck, run `uv sync -v` to see verbose output and get the SSH passphrase prompt.

## Usage

```bash
python3 src/rp.py
```

By default, it looks for `config.toml` in the same directory as `rp.py`. Override with `--config`:

```bash
python3 src/rp.py --config /path/to/config.toml
```

## Configuration

Create a TOML config file (see `config.example.toml`):

```toml
# Optional: GitHub personal access token (avoids 60 req/hr rate limit)
# github_token = "ghp_..."

# Optional: Telegram notifications on new releases
# telegram_bot_token = "123456:ABC..."
# telegram_chat_id = "987654321"

[[repos]]
github = "owner/repo"
local_path = "/home/user/projects/repo"
# protocol = "ssh"  # default: "https"
```

- `github_token` — also accepted via the `GITHUB_TOKEN` environment variable
- `protocol` — `"https"` (default) or `"ssh"` per repo. SSH uses your SSH key; HTTPS uses the token.
- `telegram_bot_token` / `telegram_chat_id` — when both are set, a Telegram message is sent after each new release is synced. Get a token from [@BotFather](https://t.me/BotFather).

## How It Works

On each invocation:

1. Loads the TOML config
2. For each configured repo, queries the GitHub API for the latest release tag
3. If the repo is already cloned and checked out at that tag — skips (already up to date)
4. If it's a new tag — clones the repo (or fetches if it already exists) and checks out the tag
5. If Telegram is configured, sends a notification: `[owner/repo] new release synced: v1.2.3`

There is no built-in scheduler. Use cron, systemd timers, or similar to run periodically.
