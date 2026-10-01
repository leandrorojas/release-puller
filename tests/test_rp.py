import subprocess
import sys
import urllib.error

import pytest

import rp

REAL_PING = rp.ping_healthchecks
REAL_SYNC_REPO = rp.sync_repo


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
        if uuid:
            state["pings"].append((uuid, suffix, body))

    monkeypatch.setattr(rp, "get_latest_release", get_latest_release)
    monkeypatch.setattr(rp, "get_current_tag", get_current_tag)
    monkeypatch.setattr(rp, "sync_repo", sync_repo)
    monkeypatch.setattr(rp, "notify", notify)
    monkeypatch.setattr(rp, "ping_healthchecks", ping_healthchecks)
    monkeypatch.delenv("HEALTHCHECKS_UUID", raising=False)
    monkeypatch.delenv("GITHUB_TOKEN", raising=False)

    def repo_toml(slug):
        name = slug.split("/")[-1]
        (tmp_path / name).mkdir(exist_ok=True)
        return f'[[repos]]\ngithub = "{slug}"\nlocal_path = "{tmp_path / name}"\n'

    def run_raw(content: str | bytes):
        config = tmp_path / "config.toml"
        if isinstance(content, bytes):
            config.write_bytes(content)
        else:
            config.write_text(content)
        monkeypatch.setattr(sys, "argv", ["rp.py", "--config", str(config)])
        with pytest.raises(SystemExit) as exc:
            rp.main()
        return exc.value.code

    def run_main(slugs, extra=""):
        return run_raw("\n".join([extra] + [repo_toml(slug) for slug in slugs]))

    state["run"] = run_main
    state["run_raw"] = run_raw
    state["repo_toml"] = repo_toml
    state["tmp_path"] = tmp_path
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


def test_telegram_failure_is_a_warning_not_a_failure(world, capsys):
    world["latest"] = {"o/a": "v2"}
    world["notify_error"] = True
    extra = 'telegram_bot_token = "t"\ntelegram_chat_id = "c"\nhealthchecks_uuid = "abc"\n'
    assert world["run"](["o/a"], extra) == 0
    assert "[o/a] warning: telegram notification failed: telegram down" in capsys.readouterr().err
    uuid, suffix, body = world["pings"][-1]
    assert suffix == ""  # success ping, not /fail
    assert "telegram notification failed" in body


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


# --- robustness: config and per-repo errors must fail cleanly, never crash ---


def test_non_utf8_config_exits_nonzero_and_pings_fail(world, monkeypatch, capsys):
    monkeypatch.setenv("HEALTHCHECKS_UUID", "env-uuid")
    assert world["run_raw"](b'github_token = "\xff"\n') == 1
    assert "cannot load config" in capsys.readouterr().err
    assert world["pings"] == [("env-uuid", "/fail", world["pings"][0][2])]
    assert "cannot load config" in world["pings"][0][2]


def test_no_repos_pings_fail_using_config_uuid(world):
    assert world["run"]([], 'healthchecks_uuid = "abc"\n') == 1
    assert [(u, s) for u, s, _ in world["pings"]] == [("abc", "/fail")]
    assert "no [[repos]] configured" in world["pings"][0][2]


@pytest.mark.parametrize("bad_entry", [
    '[[repos]]\ngithub = 123\nlocal_path = "/tmp/x"\n',
    '[[repos]]\ngithub = "o/x"\nlocal_path = 5\n',
    '[[repos]]\nlocal_path = "/tmp/x"\n',
    '[[repos]]\ngithub = "a/b/c"\nlocal_path = "/tmp/x"\n',
])
def test_bad_repo_entry_fails_only_that_repo(world, capsys, bad_entry):
    world["latest"] = {"o/a": "v1"}
    world["current"] = {"a": "v1"}
    assert world["run_raw"](bad_entry + "\n" + world["repo_toml"]("o/a")) == 1
    captured = capsys.readouterr()
    assert "invalid repo config" in captured.err
    assert "[o/a] up to date (v1)" in captured.out


def test_repos_as_strings_fails_cleanly(world, capsys):
    assert world["run_raw"]('repos = ["o/a"]\n') == 1
    assert "[o/a] invalid repo config" in capsys.readouterr().err


def test_unexpected_git_error_fails_only_that_repo(world, monkeypatch, capsys):
    world["latest"] = {"o/a": "v2", "o/b": "v1"}
    world["current"] = {"b": "v1"}

    def sync_repo(github_url, local_path, tag):
        raise FileNotFoundError("git")

    monkeypatch.setattr(rp, "sync_repo", sync_repo)
    assert world["run"](["o/a", "o/b"]) == 1
    captured = capsys.readouterr()
    assert "[o/a] unexpected error: FileNotFoundError" in captured.err
    assert "[o/b] up to date (v1)" in captured.out


def test_real_sync_repo_survives_non_utf8_git_output(world, monkeypatch, capsys):
    bin_dir = world["tmp_path"] / "bin"
    bin_dir.mkdir()
    fake_git = bin_dir / "git"
    fake_git.write_text("#!/bin/sh\nprintf 'fatal: bad path \\377\\n' >&2\nexit 128\n")
    fake_git.chmod(0o755)
    monkeypatch.setenv("PATH", str(bin_dir))
    monkeypatch.setattr(rp, "sync_repo", REAL_SYNC_REPO)
    world["latest"] = {"o/a": "v2"}
    assert world["run"](["o/a"]) == 1
    err = capsys.readouterr().err
    assert "[o/a] git error" in err
    assert "fatal: bad path \ufffd" in err


# --- healthchecks ping details ---


def test_start_ping_failure_is_in_final_body(world, monkeypatch):
    def ping(uuid, suffix="", body=""):
        if suffix == "/start":
            print("healthchecks ping failed (/start): timed out", file=sys.stderr)
        world["pings"].append((uuid, suffix, body))

    monkeypatch.setattr(rp, "ping_healthchecks", ping)
    world["latest"] = {"o/a": "v1"}
    world["current"] = {"a": "v1"}
    assert world["run"](["o/a"], 'healthchecks_uuid = "abc"\n') == 0
    assert "ping failed (/start)" in world["pings"][-1][2]


def test_crash_sends_fail_ping_with_traceback(world, monkeypatch):
    def boom(config):
        raise RuntimeError("kaboom")

    monkeypatch.setattr(rp, "run", boom)
    with pytest.raises(RuntimeError):
        world["run"](["o/a"], 'healthchecks_uuid = "abc"\n')
    uuid, suffix, body = world["pings"][-1]
    assert suffix == "/fail"
    assert "RuntimeError: kaboom" in body


def _capture_urlopen(monkeypatch):
    sent = []

    class Resp:
        def __enter__(self):
            return self

        def __exit__(self, *a):
            return False

    def urlopen(req, timeout=None):
        sent.append(req)
        return Resp()

    monkeypatch.setattr(rp.urllib.request, "urlopen", urlopen)
    return sent


def test_ping_truncates_to_limit_on_character_boundary(monkeypatch):
    sent = _capture_urlopen(monkeypatch)
    REAL_PING("abc", "/fail", "é" * rp.HEALTHCHECKS_MAX_BODY + "reason")
    data = sent[0].data
    assert len(data) <= rp.HEALTHCHECKS_MAX_BODY
    assert data.decode("utf-8").endswith("reason")  # strict decode: no split character


def test_ping_never_raises_on_unencodable_body(monkeypatch):
    sent = _capture_urlopen(monkeypatch)
    REAL_PING("abc", "/fail", "bad path \udcff")
    assert sent[0].full_url.endswith("/abc/fail")


def test_ping_without_uuid_does_nothing(monkeypatch):
    sent = _capture_urlopen(monkeypatch)
    REAL_PING(None, "/fail", "x")
    assert sent == []


def test_tee_forwards_stream_attributes(world, monkeypatch):
    def notify(bot_token, chat_id, slug, tag):
        sys.stderr.isatty()
        sys.stdout.encoding

    monkeypatch.setattr(rp, "notify", notify)
    world["latest"] = {"o/a": "v2"}
    extra = 'telegram_bot_token = "t"\ntelegram_chat_id = "c"\n'
    assert world["run"](["o/a"], extra) == 0
