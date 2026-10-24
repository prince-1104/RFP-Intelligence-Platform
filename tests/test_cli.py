import json
import subprocess
import sys
from unittest.mock import Mock

import pytest

from rfp.cli import main, parse_folder
from rfp.ingestion.options import ParserOptions
from rfp.ingestion.parser import parse_isolated


def test_cli_without_credentials(tmp_path):
    source = tmp_path / "source"
    output = tmp_path / "parsed"
    source.mkdir()
    (source / "a.html").write_text("<h1>Synthetic bid</h1><p>Item AB-123</p>")
    # Private .env is deliberately invalid. Offline command must not load it.
    (tmp_path / ".env").write_text("DATABASE_URL=invalid\nR2_ENDPOINT_URL=not-a-url\n")
    completed = subprocess.run(
        [
            sys.executable,
            "-m",
            "rfp.cli",
            "parse-folder",
            "--path",
            str(source),
            "--output",
            str(output),
        ],
        cwd=tmp_path,
        capture_output=True,
        text=True,
        timeout=30,
    )
    assert completed.returncode == 0, completed.stderr
    assert json.loads(completed.stdout)["parsed"] == 1
    summary = json.loads((output / "summary.json").read_text())
    assert summary["failed"] == 0
    artifacts = list(output.glob("*.json"))
    assert len(artifacts) == 2


def test_cli_exit_codes_and_validation(tmp_path, capsys):
    source = tmp_path / "source"
    source.mkdir()
    assert main(["parse-folder", "--path", str(source), "--output", str(tmp_path / "output")]) == 1
    (source / "bad.pdf").write_bytes(b"bad")
    assert main(["parse-folder", "--path", str(source), "--output", str(tmp_path / "output")]) == 1
    assert main(["parse-folder", "--path", str(source), "--output", str(source)]) == 1
    assert "output_must_be_outside_source_folder" in capsys.readouterr().err
    with pytest.raises(SystemExit) as raised:
        main(["parse-folder"])
    assert raised.value.code == 2


def test_production_cli_requires_r2(settings, monkeypatch, tmp_path, capsys):
    monkeypatch.setattr("rfp.cli.get_settings", lambda: settings)
    assert main(["ingest-folder", "--path", str(tmp_path)]) == 1
    error = json.loads(capsys.readouterr().err)
    assert error["error"] == "r2_not_configured"


def test_parser_timeout_terminates_child(monkeypatch):
    import uuid

    process = Mock()
    process.wait.side_effect = [subprocess.TimeoutExpired("parser", 1), None]
    process.poll.return_value = None
    monkeypatch.setattr("rfp.ingestion.parser.subprocess.Popen", Mock(return_value=process))
    result = parse_isolated(
        b"<p>text</p>", "a.html", uuid.uuid4(), ParserOptions(timeout_seconds=1)
    )
    assert result.processing_status == "failed" and result.warnings == ["parser_timeout"]
    process.terminate.assert_called_once()


def test_local_artifact_symlink_rejected(settings, tmp_path):
    source = tmp_path / "source"
    source.mkdir()
    (source / "a.html").write_text("<h1>Notice</h1>")
    output = tmp_path / "output"
    output.mkdir()
    outside = tmp_path / "private"
    outside.write_text("preserve")
    try:
        (output / "summary.json").symlink_to(outside)
    except OSError:
        pytest.skip("Operating system does not permit creation of test symlinks")
    with pytest.raises(ValueError, match="output_symlink_rejected"):
        parse_folder(source, output, settings)
    assert outside.read_text() == "preserve"
