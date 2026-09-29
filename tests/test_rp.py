import subprocess
import sys
import urllib.error

import pytest

import rp

REAL_PING = rp.ping_healthchecks


@pytest.fixture
def world(monkeypatch, tmp_path):
    """Fake GitHub/git/Telegram/healthchecks. Tests set `latest` and `current` per slug."""
    state = {"latest": {}, "current": {}, "api_errors": set(), "git_errors": set(),
             "notify_error": False, "pings": []}

    def get_latest_release(owner, repo, token=None):
        slug = f"{owner}/{repo}"
        if slug in state["api_errors"]:
            raise urllib.error.HTTPError("url", 403, "rate limit exceeded", {}, None)
        return state["latest"].get(slug)

    def get_current_tag(local_path):
        return state["current"].get(local_path.name)

    def sync_repo(github_url, local_path, tag):
        if any(slug in github_url for slug in state["git_errors"]):
            raise subprocess.CalledProcessError(128, ["git", "checkout", tag], stderr="fatal: bad tag")

    def notify(bot_token, chat_id, slug, tag):
        if state["notify_error"]:
            raise RuntimeError("telegram down")

    def ping_healthchecks(uuid, suffix="", body=""):
        state["pings"].append((uuid, suffix, body))

    monkeypatch.setattr(rp, "get_latest_release", get_latest_release)
    monkeypatch.setattr(rp, "get_current_tag", get_current_tag)
    monkeypatch.setattr(rp, "sync_repo", sync_repo)
    monkeypatch.setattr(rp, "notify", notify)
    monkeypatch.setattr(rp, "ping_healthchecks", ping_healthchecks)
    monkeypatch.delenv("HEALTHCHECKS_UUID", raising=False)
    monkeypatch.delenv("GITHUB_TOKEN", raising=False)

    def run_main(slugs, extra=""):
        lines = [extra]
        for slug in slugs:
            name = slug.split("/")[-1]
            (tmp_path / name).mkdir(exist_ok=True)
            lines.append(f'[[repos]]\ngithub = "{slug}"\nlocal_path = "{tmp_path / name}"\n')
        config = tmp_path / "config.toml"
        config.write_text("\n".join(lines))
        monkeypatch.setattr(sys, "argv", ["rp.py", "--config", str(config)])
        with pytest.raises(SystemExit) as exc:
            rp.main()
        return exc.value.code

    state["run"] = run_main
    return state


def test_all_synced_exits_zero(world):
    world["latest"] = {"o/a": "v2", "o/b": "v3"}
    world["current"] = {"a": "v1", "b": None}
    assert world["run"](["o/a", "o/b"]) == 0


def test_all_up_to_date_exits_zero(world):
    world["latest"] = {"o/a": "v1", "o/b": "v1"}
    world["current"] = {"a": "v1", "b": "v1"}
    assert world["run"](["o/a", "o/b"]) == 0


def test_no_releases_exits_zero(world):
    assert world["run"](["o/a"]) == 0


def test_api_error_on_one_repo_exits_nonzero_and_keeps_going(world, capsys):
    world["latest"] = {"o/a": "v1", "o/b": "v2"}
    world["current"] = {"b": "v2"}
    world["api_errors"] = {"o/a"}
    assert world["run"](["o/a", "o/b"]) == 1
    captured = capsys.readouterr()
    assert "[o/a] error fetching release" in captured.err
    assert "[o/b] up to date (v2)" in captured.out


def test_git_error_exits_nonzero_with_git_stderr(world, capsys):
    world["latest"] = {"o/a": "v2"}
    world["git_errors"] = {"o/a"}
    assert world["run"](["o/a"]) == 1
    assert "fatal: bad tag" in capsys.readouterr().err


def test_telegram_failure_exits_nonzero(world):
    world["latest"] = {"o/a": "v2"}
    world["notify_error"] = True
    extra = 'telegram_bot_token = "t"\ntelegram_chat_id = "c"\n'
    assert world["run"](["o/a"], extra) == 1


def test_no_repos_configured_exits_nonzero(world):
    assert world["run"]([]) == 1


def test_invalid_slug_exits_nonzero(world):
    assert world["run"](["not-a-slug"]) == 1


def test_missing_config_exits_nonzero(monkeypatch, tmp_path):
    monkeypatch.delenv("HEALTHCHECKS_UUID", raising=False)
    monkeypatch.setattr(sys, "argv", ["rp.py", "--config", str(tmp_path / "nope.toml")])
    with pytest.raises(SystemExit) as exc:
        rp.main()
    assert exc.value.code == 1


def test_healthchecks_success_pings_start_then_success_with_output(world):
    world["latest"] = {"o/a": "v1"}
    world["current"] = {"a": "v1"}
    assert world["run"](["o/a"], 'healthchecks_uuid = "abc"\n') == 0
    assert [(u, s) for u, s, _ in world["pings"]] == [("abc", "/start"), ("abc", "")]
    assert "[o/a] up to date (v1)" in world["pings"][-1][2]


def test_healthchecks_failure_pings_fail_with_reason(world):
    world["api_errors"] = {"o/a"}
    assert world["run"](["o/a"], 'healthchecks_uuid = "abc"\n') == 1
    uuid, suffix, body = world["pings"][-1]
    assert suffix == "/fail"
    assert "[o/a] error fetching release" in body


def test_healthchecks_uuid_from_env(world, monkeypatch):
    monkeypatch.setenv("HEALTHCHECKS_UUID", "env-uuid")
    assert world["run"](["o/a"]) == 0
    assert {u for u, _, _ in world["pings"]} == {"env-uuid"}


def test_healthchecks_unreachable_never_raises(monkeypatch, capsys):
    monkeypatch.setattr(rp, "HEALTHCHECKS_BASE_URL", "http://127.0.0.1:9")  # nothing listens
    REAL_PING("abc", "/fail", "body")
    assert "healthchecks ping failed (/fail)" in capsys.readouterr().err


def test_healthchecks_unreachable_keeps_exit_code(world, monkeypatch):
    monkeypatch.setattr(rp, "HEALTHCHECKS_BASE_URL", "http://127.0.0.1:9")
    monkeypatch.setattr(rp, "ping_healthchecks", REAL_PING)
    world["latest"] = {"o/a": "v1"}
    world["current"] = {"a": "v1"}
    assert world["run"](["o/a"], 'healthchecks_uuid = "abc"\n') == 0
    world["api_errors"] = {"o/a"}
    assert world["run"](["o/a"], 'healthchecks_uuid = "abc"\n') == 1


def _http_404():
    return urllib.error.HTTPError("url", 404, "Not Found", {}, None)


def test_latest_release_missing_repo_raises(monkeypatch):
    def fake_get(path, token):
        raise _http_404()

    monkeypatch.setattr(rp, "_github_get", fake_get)
    with pytest.raises(RuntimeError, match="repository not found"):
        rp.get_latest_release("o", "typo")


def test_latest_release_existing_repo_without_releases_returns_none(monkeypatch):
    def fake_get(path, token):
        if path.endswith("/releases/latest"):
            raise _http_404()
        return {"full_name": "o/a"}

    monkeypatch.setattr(rp, "_github_get", fake_get)
    assert rp.get_latest_release("o", "a") is None
