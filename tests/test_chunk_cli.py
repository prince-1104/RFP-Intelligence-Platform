import json
import subprocess
import sys

from test_chunk_engine import BID

from rfp.chunking.local import chunk_folder
from rfp.cli import main
from rfp.ingestion.options import ParserOptions
from rfp.ingestion.parser import artifact_bytes, parse_document


def test_local_chunks_no_credentials_and_deterministic_outputs(tmp_path, settings):
    parsed_dir = tmp_path / "parsed"
    parsed_dir.mkdir()
    for name in ("a.html", "b.html"):
        parsed = parse_document(
            b"<h1>Legal</h1><p>All rights shall be retained.</p>", name, BID, ParserOptions()
        )
        (parsed_dir / f"{name}.json").write_bytes(artifact_bytes(parsed))
    (parsed_dir / "summary.json").write_text("{}")
    (tmp_path / ".env").write_text("DATABASE_URL=invalid\nR2_ENDPOINT_URL=invalid\n")
    output = tmp_path / "chunks"
    completed = subprocess.run(
        [
            sys.executable,
            "-m",
            "rfp.cli",
            "chunk-folder",
            "--path",
            str(parsed_dir),
            "--output",
            str(output),
        ],
        cwd=tmp_path,
        capture_output=True,
        text=True,
        timeout=30,
    )
    assert completed.returncode == 0, completed.stderr
    summary = json.loads(completed.stdout)
    assert summary["source_documents"] == 2 and summary["reused_content_records"] > 0
    assert summary["source_duplicates"]
    artifacts = {
        path.name: path.read_bytes()
        for path in output.glob("*.json")
        if path.name != "summary.json"
    }
    second, code = chunk_folder(parsed_dir, output, settings)
    assert code == 0 and second["unchanged_outputs"] == 2
    assert artifacts == {name: (output / name).read_bytes() for name in artifacts}


def test_malformed_artifact_does_not_discard_valid_remainder(tmp_path, settings):
    parsed_dir = tmp_path / "parsed"
    parsed_dir.mkdir()
    parsed = parse_document(b"<p>Readable content.</p>", "a.html", BID, ParserOptions())
    (parsed_dir / "valid.json").write_bytes(artifact_bytes(parsed))
    (parsed_dir / "bad.json").write_text('{"secret":"private"}')
    summary, code = chunk_folder(parsed_dir, tmp_path / "output", settings)
    assert code == 1 and summary["chunks"] == 1 and len(summary["failures"]) == 1
    assert "private" not in str(summary)


def test_empty_and_invalid_input_exit_codes(tmp_path, settings):
    summary, code = chunk_folder(tmp_path, tmp_path.parent / "chunk_empty_output", settings)
    assert code == 1 and summary["source_documents"] == 0
    assert (
        main(
            ["chunk-folder", "--path", str(tmp_path / "missing"), "--output", str(tmp_path / "out")]
        )
        == 1
    )


def test_production_chunking_requires_credentials(settings, monkeypatch, capsys):
    monkeypatch.setattr("rfp.cli.get_settings", lambda: settings)
    assert main(["chunk-bid", "--bid-id", str(BID)]) == 1
    assert json.loads(capsys.readouterr().err)["error"] == "r2_not_configured"
