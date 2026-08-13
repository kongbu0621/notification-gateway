from __future__ import annotations

import os
import re
import shutil
import subprocess
from pathlib import Path
from types import TracebackType

import notification_gateway.cli as cli
from notification_gateway.cli import main


def test_cli_refuses_unsafe_non_loopback_and_missing_provider(
    tmp_path: Path, monkeypatch, capsys
) -> None:
    monkeypatch.delenv("WECOM_WEBHOOK_URL", raising=False)
    monkeypatch.delenv("NOTIFICATION_GATEWAY_AUTH_TOKEN", raising=False)
    db = str(tmp_path / "cli.sqlite3")
    assert main(["--db", db, "serve", "--host", "0.0.0.0"]) == 2
    assert "refusing" in capsys.readouterr().err
    assert main(["--db", db, "serve", "--host", "0.0.0.0", "--allow-non-loopback"]) == 2
    assert "requires" in capsys.readouterr().err
    assert main(["--db", db, "work-once"]) == 2
    assert "WECOM_WEBHOOK_URL" in capsys.readouterr().err
    assert main(["--db", db, "purge"]) == 0
    assert capsys.readouterr().out.strip() == "0"


def test_cli_serves_loopback_and_runs_empty_worker(tmp_path: Path, monkeypatch) -> None:
    class FakeServer:
        served = False

        def __enter__(self) -> FakeServer:
            return self

        def __exit__(
            self,
            exc_type: type[BaseException] | None,
            exc_value: BaseException | None,
            traceback: TracebackType | None,
        ) -> None:
            return None

        def serve_forever(self) -> None:
            self.served = True

    server = FakeServer()
    monkeypatch.setattr(cli, "make_server", lambda host, port, app: server)
    db = str(tmp_path / "cli.sqlite3")
    assert main(["--db", db, "serve", "--host", "localhost"]) == 0
    assert server.served

    monkeypatch.setenv(
        "WECOM_WEBHOOK_URL",
        "https://qyapi.weixin.qq.com/cgi-bin/webhook/send?key=test-only-dummy-key",
    )
    assert main(["--db", db, "work-once"]) == 3


def test_repository_ignores_runtime_and_contains_public_safety_docs() -> None:
    git = shutil.which("git")
    assert git is not None
    ignored = subprocess.run(
        [
            git,
            "check-ignore",
            "runtime-data/test.sqlite3",
            "runtime-data/test.sqlite3-wal",
            "logs/gateway.log",
            "backups/data.db",
            ".env",
        ],
        check=True,
        text=True,
        capture_output=True,
    ).stdout
    assert "sqlite3-wal" in ignored
    assert "gateway.log" in ignored
    assert Path("AGENTS.md").is_file()
    assert Path("SECURITY.md").is_file()
    assert Path("docs/privacy-and-mainland-china.md").is_file()


def test_tracked_files_do_not_contain_external_task_links_or_real_secrets() -> None:
    git = shutil.which("git")
    assert git is not None
    tracked = subprocess.run(
        [git, "ls-files", "--cached", "--others", "--exclude-standard", "-z"],
        check=True,
        capture_output=True,
    ).stdout.split(b"\0")
    forbidden = (
        b"chatgpt.com" + b"/s/",
        b"BEGIN OPENSSH" + b" PRIVATE KEY",
        b"gh" + b"p_",
    )
    personal_email = re.compile(rb"[a-z0-9._%+-]+@" + b"q" + rb"q\.com\b", re.I)
    for raw_path in tracked:
        if not raw_path:
            continue
        path = Path(os.fsdecode(raw_path))
        if not path.is_file():
            continue
        data = path.read_bytes()
        for marker in forbidden:
            assert marker not in data, f"forbidden public marker in {os.fsdecode(raw_path)}"
        assert personal_email.search(data) is None, (
            f"personal email found in {os.fsdecode(raw_path)}"
        )
