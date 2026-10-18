import io
import json
import threading
from unittest.mock import Mock

import httpx
import psycopg
import pytest
from test_platform import queued

import main as launcher
import manage
from majlis import bot, cli, tunnel
from majlis.domain import Problem


def test_bot_commands_and_private_chat_only(db):
    queued(db)
    api = Mock()
    message = {
        "from": {"id": 3001, "first_name": "Buyer"},
        "chat": {"id": 3001, "type": "private"},
        "text": "/status",
    }
    bot.respond(db, api, message, "https://example.com")
    assert "MJ-TEST-1" in api.send.call_args.args[1]
    message["text"] = "/id"
    bot.respond(db, api, message, "https://example.com")
    assert api.send.call_args.args[1] == "Ваш Telegram ID: 3001"
    message["text"] = "/start"
    bot.respond(db, api, message, "https://example.com")
    assert api.send.call_args.args[2] == "https://example.com"
    count = api.send.call_count
    message["chat"]["type"] = "group"
    bot.respond(db, api, message, "https://example.com")
    assert api.send.call_count == count


def test_telegram_error_does_not_expose_token(cfg, monkeypatch):
    api = bot.Telegram()
    api.client.close()
    api.client = httpx.Client(
        transport=httpx.MockTransport(
            lambda request: httpx.Response(
                429,
                json={
                    "ok": False,
                    "error_code": 429,
                    "description": "private secret",
                    "parameters": {"retry_after": 60},
                },
            )
        )
    )
    try:
        with pytest.raises(bot.TelegramFailure) as exc:
            api.send(3001, "Test")
        assert exc.value.retry_after == 60
        assert cfg.bot_token.get_secret_value() not in str(exc.value)
        assert "private secret" not in str(exc.value)
    finally:
        api.close()


def test_bot_stops_before_polling_after_lost_leader(db, cfg, monkeypatch):
    original = psycopg.connect
    leader = []
    stop = threading.Event()

    def connect(*args, **kwargs):
        connection = original(*args, **kwargs)
        if kwargs.get("autocommit"):
            leader.append(connection)
        return connection

    class API:
        calls = 0

        def configure(self, url):
            leader[0].close()
            return {"username": "fictional"}

        def call(self, *args):
            self.calls += 1
            stop.set()
            return []

        def close(self):
            pass

    api = API()
    cfg.mini_app_url = "https://example.com"
    monkeypatch.setattr(bot.psycopg, "connect", connect)
    monkeypatch.setattr(bot, "Telegram", lambda: api)
    with pytest.raises(psycopg.OperationalError):
        bot.run(stop)
    assert api.calls == 0


@pytest.mark.parametrize(
    "line",
    [
        "bad",
        "[]",
        "null",
        '{"type":"v1.tcpip_forward.register.accepted","data":{}}',
        json.dumps(
            {
                "type": "v1.tcpip_forward.register.accepted",
                "data": "https://x.lhr.life.attacker.invalid",
            }
        ),
    ],
)
def test_tunnel_ignores_invalid_or_suffix_host(line):
    assert tunnel.accepted_url(line) is None


def test_tunnel_accepts_and_closes_controlled_process(monkeypatch, tmp_path):
    process = Mock()
    process.poll.return_value = None
    process.stdout = io.StringIO(
        json.dumps(
            {"type": "v1.tcpip_forward.register.accepted", "data": "https://fictional.lhr.life"}
        )
        + "\n"
    )
    monkeypatch.setattr(tunnel, "ROOT", tmp_path)
    monkeypatch.setattr(tunnel.shutil, "which", lambda name: "/usr/bin/ssh")
    popen = Mock(return_value=process)
    monkeypatch.setattr(tunnel.subprocess, "Popen", popen)
    current = tunnel.LocalhostTunnel(8876)
    assert current.start(timeout=1) == "https://fictional.lhr.life"
    argv = popen.call_args.args[0]
    assert "IdentityFile=none" in argv and "nokey@localhost.run" in argv and "-R" in argv
    current.close()
    process.terminate.assert_called_once()
    assert process.stdout.closed


def test_tunnel_timeout_cleans_child(monkeypatch, tmp_path):
    process = Mock()
    process.poll.return_value = None
    process.stdout = io.StringIO("")
    monkeypatch.setattr(tunnel, "ROOT", tmp_path)
    monkeypatch.setattr(tunnel.shutil, "which", lambda name: "/usr/bin/ssh")
    monkeypatch.setattr(tunnel.subprocess, "Popen", lambda *args, **kwargs: process)
    with pytest.raises(tunnel.TunnelError):
        tunnel.LocalhostTunnel(8876).start(timeout=0.01)
    process.terminate.assert_called_once()


def test_tunnel_missing_binary_and_bad_ports(monkeypatch):
    monkeypatch.setattr(tunnel.shutil, "which", lambda name: None)
    with pytest.raises(tunnel.TunnelError):
        tunnel.LocalhostTunnel(8876).start()
    for port in [0, 65536, True, "8876"]:
        with pytest.raises(ValueError):
            tunnel.LocalhostTunnel(port)


def test_cli_migration_and_failure_sanitization(monkeypatch, capsys):
    fake = Mock()
    monkeypatch.setattr(cli, "Database", lambda: fake)
    monkeypatch.setattr("sys.argv", ["cli", "migrate"])
    assert cli.main() == 0
    fake.migrate.assert_called_once()
    fake.close.assert_called_once()
    fake.migrate.side_effect = RuntimeError("postgresql://SECRET")
    assert cli.main() == 1
    assert "SECRET" not in capsys.readouterr().err


def test_cli_notify_dispatch(monkeypatch):
    monkeypatch.setattr("sys.argv", ["cli", "notify"])
    run = Mock()
    monkeypatch.setattr(bot, "notification_worker", run)
    monkeypatch.setattr(cli.signal, "signal", lambda *args: None)
    assert cli.main() == 0
    run.assert_called_once()
    assert isinstance(run.call_args.args[0], threading.Event)


def test_launcher_health_verifies_instance_and_rejects_non_https(monkeypatch):
    class Client:
        def __enter__(self):
            return self

        def __exit__(self, *args):
            pass

        def get(self, *args, **kwargs):
            return httpx.Response(
                200,
                json={"instance_id": "expected"},
                request=httpx.Request("GET", "https://example.com/health"),
            )

    monkeypatch.setattr(httpx, "Client", lambda **kwargs: Client())
    launcher.wait_http("https://example.com", "expected", timeout=1)
    with pytest.raises(RuntimeError):
        launcher.wait_http("https://example.com", "other", timeout=1)
    with pytest.raises(Problem):
        launcher.wait_http("http://example.com", "expected", timeout=1)
    launcher.wait_http("http://127.0.0.1:8876", "expected", timeout=1)


def test_launcher_stops_child_and_check_does_not_start_services(monkeypatch, capsys):
    process = Mock()
    process.poll.return_value = None
    process.wait.side_effect = [launcher.subprocess.TimeoutExpired("python", 8), None]
    launcher.stop_process(process)
    process.kill.assert_called_once()
    monkeypatch.setattr("sys.argv", ["main.py", "--check"])
    popen = Mock()
    monkeypatch.setattr(launcher.subprocess, "Popen", popen)
    launcher.run()
    popen.assert_not_called()
    assert "Ключ скрыт" in capsys.readouterr().out


def test_restore_rejects_source_target_before_any_command(cfg, monkeypatch, tmp_path):
    archive = tmp_path / "fictional.dump"
    archive.write_bytes(b"fake")
    run = Mock()
    monkeypatch.setattr(manage, "settings", lambda: cfg)
    monkeypatch.setattr(manage, "run_db", run)
    from urllib.parse import urlsplit

    source = urlsplit(cfg.database_url.get_secret_value()).path.lstrip("/")
    for target in ["majlis", source, "bad;DROP_test"]:
        with pytest.raises(ValueError):
            manage.main(
                [
                    "restore-test",
                    "--container",
                    "majlis-local-db-1",
                    "--file",
                    str(archive),
                    "--target",
                    target,
                ]
            )
    run.assert_not_called()


def test_test_fixture_rejects_production_or_mismatched_migrator():
    from conftest import validate_test_urls

    for a, b in [
        ("", ""),
        ("postgresql://app@localhost/demo_test", "postgresql://owner@localhost/majlis"),
        ("postgresql://app@localhost/majlis", "postgresql://owner@localhost/majlis"),
        ("postgresql://app@localhost/demo_test", "postgresql://app@localhost/demo_test"),
    ]:
        with pytest.raises(RuntimeError):
            validate_test_urls(a, b)
    assert validate_test_urls(
        "postgresql://app@localhost/demo_test", "postgresql://owner@localhost/demo_test"
    )
