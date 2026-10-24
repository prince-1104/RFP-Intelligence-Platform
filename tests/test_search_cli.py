import json

import pytest
from qdrant_client import QdrantClient
from search_fakes import BID_A, sample_bid, services

from rfp import cli
from rfp.search.errors import IndexUnavailable


@pytest.fixture
def artifacts(tmp_path):
    folder = tmp_path / "chunks"
    folder.mkdir()
    for result in sample_bid():
        (folder / f"{result.document_id}.json").write_text(result.model_dump_json())
    return folder


@pytest.fixture
def wired(monkeypatch):
    client = QdrantClient(":memory:")
    built = {}

    def indexing(settings, cache_dir=None):
        built["indexer"], built["search"], _ = services(settings, client=client)
        return built["indexer"]

    def searching(settings):
        return services(settings, client=client)[1]

    monkeypatch.setattr("rfp.search.wiring.local_indexing_service", indexing)
    monkeypatch.setattr("rfp.search.wiring.search_service", searching)
    return built


def test_index_folder_then_cited_search(artifacts, wired, capsys):
    assert cli.main(["index-folder", "--path", str(artifacts)]) == 0
    report = json.loads(capsys.readouterr().out)
    assert report["completed"] == 5 and report["failed"] == 0 and report["points"] == 9
    assert cli.main(["index-folder", "--path", str(artifacts)]) == 0
    assert json.loads(capsys.readouterr().out)["unchanged"] == 5

    args = ["search", "--local", "--query", "PX-420", "--bid-id", str(BID_A), "--top-k", "2"]
    assert cli.main([*args, "--json"]) == 0
    body = json.loads(capsys.readouterr().out)
    top = body["results"][0]
    assert top["citation"]["relative_path"] == "specs/pumps.pdf"
    assert top["citation"]["page_start"] == 1
    assert cli.main(args) == 0
    text = capsys.readouterr().out
    assert "[1] pumps.pdf (specs/pumps.pdf) page 1" in text
    assert "exact identifiers: PX-420" in text and "Timings (ms)" in text


def test_text_output_for_html_and_duplicates(artifacts, wired, capsys):
    cli.main(["index-folder", "--path", str(artifacts)])
    capsys.readouterr()
    assert cli.main(["search", "--local", "--query", "pre-bid meeting", "--top-k", "1"]) == 0
    assert "notice.html (notice.html) HTML" in capsys.readouterr().out
    assert (
        cli.main(
            [
                "search",
                "--local",
                "--query",
                "proposals valid ninety days",
                "--mode",
                "sparse",
                "--top-k",
                "1",
            ]
        )
        == 0
    )
    assert "also in:" in capsys.readouterr().out


def test_search_errors_and_exit_codes(wired, monkeypatch, capsys):
    assert cli.main(["search", "--local", "--query", "?!"]) == 2
    assert json.loads(capsys.readouterr().err)["error"] == "invalid_query"
    assert cli.main(["search", "--local", "--query", "x", "--top-k", "0"]) == 2
    capsys.readouterr()

    def missing(settings):
        class Service:
            def search(self, request):
                raise IndexUnavailable()

        return Service()

    monkeypatch.setattr("rfp.search.wiring.search_service", missing)
    assert cli.main(["search", "--local", "--query", "pumps"]) == 1
    error = json.loads(capsys.readouterr().err)
    assert error["error"] == "index_not_found" and "index" in error["message"]


def test_index_folder_without_artifacts(tmp_path, wired, capsys):
    assert cli.main(["index-folder", "--path", str(tmp_path)]) == 1
    assert json.loads(capsys.readouterr().out)["error"] == "no_indexable_chunk_artifacts"
    assert cli.main(["index-folder", "--path", str(tmp_path / "missing")]) == 1
    assert json.loads(capsys.readouterr().err)["error"] == "input_directory_not_found"
