from __future__ import annotations

import os
import re
import shutil
import subprocess
import tomllib
from pathlib import Path
from types import TracebackType

import pytest

import notification_gateway.cli as cli
from notification_gateway.cli import main


def test_cli_refuses_unsafe_non_loopback_and_missing_provider(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    monkeypatch.delenv("WECOM_WEBHOOK_URL", raising=False)
    monkeypatch.delenv("NOTIFICATION_GATEWAY_AUTH_TOKEN", raising=False)
    db = str(tmp_path / "cli.sqlite3")
    assert main(["--db", db, "serve", "--host", "0.0.0.0"]) == 2
    assert "refusing" in capsys.readouterr().err
    assert not Path(db).exists()
    assert main(["--db", db, "serve", "--host", "0.0.0.0", "--allow-non-loopback"]) == 2
    assert "requires" in capsys.readouterr().err
    assert not Path(db).exists()
    monkeypatch.setenv("NOTIFICATION_GATEWAY_AUTH_TOKEN", "short")
    assert main(["--db", db, "serve", "--host", "0.0.0.0", "--allow-non-loopback"]) == 2
    assert "32-character" in capsys.readouterr().err
    assert not Path(db).exists()
    monkeypatch.delenv("NOTIFICATION_GATEWAY_AUTH_TOKEN")
    assert main(["--db", db, "work-once"]) == 2
    assert "WECOM_WEBHOOK_URL" in capsys.readouterr().err
    assert not Path(db).exists()
    assert main(["--db", db, "purge"]) == 0
    assert capsys.readouterr().out.strip() == "0"


def test_purge_does_not_initialize_unrelated_provider(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    monkeypatch.setenv(
        "WECOM_WEBHOOK_URL",
        "https://qyapi.weixin.qq.com/cgi-bin/webhook/send?key=",
    )
    assert main(["--db", str(tmp_path / "cli.sqlite3"), "purge"]) == 0
    assert capsys.readouterr().out.strip() == "0"


def test_cli_serves_loopback_and_runs_empty_worker(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
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
    assert Path("AGENTS.zh-CN.md").is_file()
    assert Path("SECURITY.md").is_file()
    assert Path("SECURITY.zh-CN.md").is_file()
    assert Path("README.zh-CN.md").is_file()
    assert Path("docs/privacy-and-mainland-china.md").is_file()
    assert Path("docs/privacy-and-mainland-china.zh-CN.md").is_file()


def test_public_documentation_has_reciprocal_english_and_chinese_versions() -> None:
    git = shutil.which("git")
    assert git is not None
    document_paths = subprocess.run(
        [git, "ls-files", "--cached", "--others", "--exclude-standard", "--", "*.md"],
        check=True,
        text=True,
        capture_output=True,
    ).stdout.splitlines()
    documents = {Path(path) for path in document_paths}
    english_documents = {path for path in documents if not path.name.endswith(".zh-CN.md")}
    expected_chinese = {
        path.with_name(f"{path.name.removesuffix('.md')}.zh-CN.md") for path in english_documents
    }
    actual_chinese = {path for path in documents if path.name.endswith(".zh-CN.md")}
    assert actual_chinese == expected_chinese

    for english_path in sorted(english_documents):
        chinese_path = english_path.with_name(f"{english_path.name.removesuffix('.md')}.zh-CN.md")
        english = english_path.read_text(encoding="utf-8")
        chinese = chinese_path.read_text(encoding="utf-8")
        assert f"]({chinese_path.name})" in english
        assert f"]({english_path.name})" in chinese
        assert re.search(r"[\u4e00-\u9fff]", chinese)
        assert english.count("\n## ") == chinese.count("\n## ")
        assert len(re.findall(r"(?m)^- ", english)) == len(re.findall(r"(?m)^- ", chinese))
        assert len(re.findall(r"(?m)^\d+\. ", english)) == len(re.findall(r"(?m)^\d+\. ", chinese))
        assert re.findall(r"```[^\n]*\n(.*?)```", english, re.DOTALL) == re.findall(
            r"```[^\n]*\n(.*?)```", chinese, re.DOTALL
        )
        external_pattern = r"\]\((https?://[^)]+)\)"
        assert set(re.findall(external_pattern, english)) == set(
            re.findall(external_pattern, chinese)
        )

        for document_path, content in ((english_path, english), (chinese_path, chinese)):
            for target in re.findall(r"\]\(([^)]+)\)", content):
                if target.startswith(("http://", "https://", "#")):
                    continue
                local_target = target.split("#", 1)[0]
                assert (document_path.parent / local_target).is_file(), (
                    f"broken local link {target!r} in {document_path}"
                )

    english_readme = Path("README.md").read_text(encoding="utf-8")
    chinese_readme = Path("README.zh-CN.md").read_text(encoding="utf-8")
    for phrase in (
        "## What this module is for",
        "Reuse this module when:",
        "Do not use this module when:",
        "a direct Webhook call is sufficient",
        "At-least-once, not exactly-once",
        "`serve` only accepts requests and returns status",
        "does not encrypt notification content at the application layer",
        "WeCom only",
        "cannot atomically join the producer's business-database transaction",
    ):
        assert phrase in english_readme
    for phrase in (
        "## 这个模块有什么用（What this module is for）",
        "以下情况适合复用本模块：",
        "以下情况不需要使用本模块：",
        "直接调用 Webhook 已经足够",
        "At-least-once，不是 exactly-once",
        "`serve` 只负责接收 request 和返回 status",
        "不会在 application layer 加密 notification content",
        "目前只有 WeCom",
        "无法与 Producer 的 business-database transaction 进行 atomic commit",
    ):
        assert phrase in chinese_readme
    for term in (
        "NotificationRequest",
        "DeliveryResult",
        "SQLite",
        "WAL",
        "at-least-once",
        "retry",
        "lease",
        "JSON Schema",
    ):
        assert term in chinese_readme

    chinese_invariants = Path("AGENTS.zh-CN.md").read_text(encoding="utf-8")
    assert len(re.findall(r"(?m)^\d+\. ", chinese_invariants)) == 16
    for term in ("at-least-once", "exactly-once", "SQLite", "WAL", "CI"):
        assert term in chinese_invariants

    build_config = tomllib.loads(Path("pyproject.toml").read_text(encoding="utf-8"))
    sdist_includes = build_config["tool"]["hatch"]["build"]["targets"]["sdist"]["include"]
    assert "/*.md" in sdist_includes
    assert "/**/*.md" in sdist_includes
    assert "/requirements" in sdist_includes


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
