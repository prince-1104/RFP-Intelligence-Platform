"""process-bid on a synthetic unseen bid: real parsing, chunking and indexing (in-memory Qdrant,
fake embeddings) and a deterministic evidence-bound fake model. Not evidence of live accuracy."""

import json
from pathlib import Path

import pytest
from search_fakes import BID_B, generation, sample_bid, services
from unseen_bid import (
    ADDENDUM_FILE,
    ANSWERS,
    LISTING_FILE,
    RFP_FILE,
    BrokenModel,
    EvidenceModel,
    write_bid,
)

from rfp.agents.errors import ExtractionError
from rfp.agents.schemas import REQUIRED_FIELDS, BidExtraction, ExtractionTrace
from rfp.agents.semantics import semantic_problems
from rfp.agents.validator import date_phrases
from rfp.cli import main
from rfp.pipeline import EXIT_FAILED, EXIT_OK, EXIT_PARTIAL, PipelineError, process_bid
from rfp.search.errors import QdrantUnavailable

LABEL = "harbor-2026"
FILES = {RFP_FILE, ADDENDUM_FILE, LISTING_FILE}


@pytest.fixture
def stack(settings):
    indexer, search, client = services(settings)
    return indexer, search, client


def run(settings, stack, folder, output, model=None, bid=LABEL, env_file=None):
    indexer, search, _ = stack
    return process_bid(
        folder,
        output,
        settings=settings,
        services=lambda _settings: (indexer, search.search),
        bid=bid,
        llm_mode="test",
        model=model or EvidenceModel(ANSWERS),
        env_file=env_file or Path("missing.env"),
    )


def stage(summary, name):
    return next(item for item in summary["stages"] if item["name"] == name)


def test_unseen_bid_runs_through_every_stage(settings, stack, tmp_path):
    folder = write_bid(tmp_path / "harbor")
    out = tmp_path / "out"
    env = tmp_path / "test.env"
    env.write_text("LLM_API_KEY=not-a-real-key-123456\n", encoding="utf-8")

    code, summary = run(settings, stack, folder, out, env_file=env)

    assert code == EXIT_OK
    assert summary["status"] == "succeeded"
    assert [item["status"] for item in summary["stages"]] == ["succeeded"] * 6
    for name in ("extraction", "trace", "verification", "index_report", "parsed", "chunks"):
        assert (out / summary["outputs"][name]).exists()
    assert json.loads((out / "run_summary.json").read_text(encoding="utf-8")) == summary
    assert {doc["file"] for doc in stage(summary, "parse")["documents"]} == FILES

    document = BidExtraction.model_validate_json((out / f"{LABEL}.json").read_bytes())
    assert tuple(document.fields) == REQUIRED_FIELDS
    for name, value in ANSWERS.items():
        assert document.fields[name].value == value, name
    for name in set(REQUIRED_FIELDS) - set(ANSWERS):
        assert document.fields[name].value is None
        assert document.fields[name].notes == "Not found in documents"
    cited = {source.file for field in document.fields.values() for source in field.sources}
    assert cited <= FILES
    for name, page in (("Term of Bid", 2), ("Model_no", 2), ("Product", 2), ("contact_info", 2)):
        assert [(s.file, s.page) for s in document.fields[name].sources] == [(RFP_FILE, page)]
    assert document.fields["Due Date"].sources[0].file == ADDENDUM_FILE

    (change,) = document.addendum_changes
    assert change.field == "Due Date"
    assert change.original_value == "March 12, 2026 at 3:00 PM"
    assert change.revised_value == "March 26, 2026 at 3:00 PM"
    assert change.addendum_number == 1
    assert [(s.file, s.page) for s in change.sources] == [(ADDENDUM_FILE, 1), (RFP_FILE, 1)]

    verification = json.loads((out / "verification.json").read_text(encoding="utf-8"))
    assert verification["problems"] == [] and verification["secret_leaks"] == []
    accepted = [row for row in verification["fields"] if row["status"] == "accepted"]
    assert len(accepted) == len(ANSWERS)
    assert all(row["support_on_cited_pages"] == "verbatim" for row in accepted)
    trace = ExtractionTrace.model_validate_json((out / summary["outputs"]["trace"]).read_bytes())
    assert sorted(trace.documents) == sorted(FILES)
    assert "not-a-real-key" not in (out / "run_summary.json").read_text(encoding="utf-8")


def test_an_invented_value_stays_unknown_and_the_run_is_partial(settings, stack, tmp_path):
    folder = write_bid(tmp_path / "harbor")
    model = EvidenceModel(ANSWERS, invented={"Bid Bond Requirement": "A 5% bid bond is required."})

    code, summary = run(settings, stack, folder, tmp_path / "out", model=model)

    document = BidExtraction.model_validate_json((tmp_path / "out" / f"{LABEL}.json").read_bytes())
    assert document.fields["Bid Bond Requirement"].value is None
    assert document.fields["Bid Bond Requirement"].sources == []
    assert code == EXIT_PARTIAL and summary["status"] == "partial"
    assert stage(summary, "extract")["status"] == "partial"
    assert stage(summary, "extract")["validation"]["unresolved"] == 1


def test_evidence_from_another_bid_in_the_collection_is_never_used(settings, stack, tmp_path):
    indexer, _search, _ = stack
    indexer.index([generation(result) for result in sample_bid(BID_B)])
    folder = write_bid(tmp_path / "harbor")
    other_bid_number = "RFP-2026-014"
    model = EvidenceModel({**ANSWERS, "Bid Number": other_bid_number})

    code, _summary = run(settings, stack, folder, tmp_path / "out", model=model)

    out = tmp_path / "out"
    document = BidExtraction.model_validate_json((out / f"{LABEL}.json").read_bytes())
    assert document.fields["Bid Number"].value is None
    trace = json.loads((out / "traces" / f"extraction_{LABEL}.json").read_text(encoding="utf-8"))
    shown = {
        item["file"]
        for event in trace["events"]
        for item in event["evidence"]
        if item.get("file") is not None
    }
    assert shown and shown <= FILES
    assert other_bid_number not in (out / f"{LABEL}.json").read_text(encoding="utf-8")
    assert code == EXIT_OK


def test_rerun_after_removing_a_document_refuses_stale_evidence(settings, stack, tmp_path):
    folder = write_bid(tmp_path / "harbor")
    assert run(settings, stack, folder, tmp_path / "first")[0] == EXIT_OK
    (folder / LISTING_FILE).unlink()

    code, summary = run(settings, stack, folder, tmp_path / "second")

    assert code == EXIT_FAILED
    index = stage(summary, "index")
    assert index["status"] == "failed"
    assert index["error"]["code"] == "bid_has_other_documents"
    assert index["other_active_points_for_bid"] > 0
    assert stage(summary, "extract")["status"] == "skipped"
    assert not (tmp_path / "second" / f"{LABEL}.json").exists()
    assert (tmp_path / "second" / "run_summary.json").is_file()


def test_existing_output_is_not_overwritten(settings, stack, tmp_path):
    folder = write_bid(tmp_path / "harbor")
    out = tmp_path / "out"
    out.mkdir()
    (out / "keep.txt").write_text("earlier result", encoding="utf-8")

    with pytest.raises(PipelineError) as error:
        run(settings, stack, folder, out)

    assert error.value.code == "output_exists"
    assert sorted(path.name for path in out.iterdir()) == ["keep.txt"]
    assert (out / "keep.txt").read_text(encoding="utf-8") == "earlier result"


def test_invalid_inputs_are_refused_before_anything_is_written(settings, stack, tmp_path):
    with pytest.raises(PipelineError) as missing:
        run(settings, stack, tmp_path / "absent", tmp_path / "out")
    assert missing.value.code == "input_directory_not_found"

    folder = tmp_path / "docs-only"
    folder.mkdir()
    (folder / "notes.docx").write_bytes(b"PK\x03\x04 not supported")
    with pytest.raises(PipelineError) as unsupported:
        run(settings, stack, folder, tmp_path / "out")
    assert unsupported.value.code == "no_supported_documents"

    with pytest.raises(PipelineError) as inside:
        run(settings, stack, write_bid(tmp_path / "harbor"), tmp_path / "harbor" / "out")
    assert inside.value.code == "output_inside_input"
    with pytest.raises(PipelineError) as uuid_label:
        run(settings, stack, tmp_path / "harbor", tmp_path / "out", bid=str(BID_B))
    assert uuid_label.value.code == "invalid_bid_id"
    assert not (tmp_path / "out").exists()


def test_remote_qdrant_and_protected_output_folders_are_refused(settings, stack, tmp_path):
    folder = write_bid(tmp_path / "harbor")
    remote = settings.model_copy(update={"qdrant_url": "https://cluster.cloud.qdrant.io:6333"})
    with pytest.raises(ExtractionError) as unsafe:
        run(remote, stack, folder, tmp_path / "out")
    assert unsafe.value.code == "unsafe_search_target"

    with pytest.raises(ExtractionError) as protected:
        run(settings, stack, folder, Path.cwd() / "evaluation" / "pipeline-out")
    assert protected.value.code == "unsafe_output_path"
    assert not (tmp_path / "out").exists()
    assert not (Path.cwd() / "evaluation" / "pipeline-out").exists()


def test_unsupported_and_malformed_files_are_reported_and_the_run_is_partial(
    settings, stack, tmp_path
):
    folder = write_bid(tmp_path / "harbor")
    (folder / "notes.docx").write_bytes(b"PK\x03\x04 not supported")
    (folder / "broken.pdf").write_bytes(b"%PDF-1.7 this is not a PDF body")
    (folder / "empty.html").write_text("<html><body></body></html>", encoding="utf-8")

    code, summary = run(settings, stack, folder, tmp_path / "out")

    assert code == EXIT_PARTIAL and summary["status"] == "partial"
    validate = stage(summary, "validate")
    assert validate["status"] == "partial"
    assert {"path": "notes.docx", "reason": "unsupported_type"} in validate["ignored"]
    parse = stage(summary, "parse")
    assert parse["status"] == "partial"
    statuses = {doc["file"]: doc["status"] for doc in parse["documents"]}
    assert statuses["broken.pdf"] == "failed"
    assert statuses["empty.html"] == "failed"
    assert all(statuses[name] == "succeeded" for name in FILES)
    assert stage(summary, "verify")["status"] == "succeeded"
    document = BidExtraction.model_validate_json((tmp_path / "out" / f"{LABEL}.json").read_bytes())
    assert document.fields["Bid Number"].value == ANSWERS["Bid Number"]


def test_a_folder_of_only_malformed_documents_fails_at_the_parse_stage(settings, stack, tmp_path):
    folder = tmp_path / "broken"
    folder.mkdir()
    (folder / "solicitation.pdf").write_bytes(b"%PDF-1.7 truncated")

    code, summary = run(settings, stack, folder, tmp_path / "out")

    assert code == EXIT_FAILED and summary["status"] == "failed"
    assert stage(summary, "parse")["error"]["code"] == "no_parsable_documents"
    assert [stage(summary, name)["status"] for name in ("chunk", "index", "extract", "verify")] == [
        "skipped"
    ] * 4
    assert (tmp_path / "out" / "run_summary.json").is_file()


def test_invalid_model_output_leaves_fields_unknown_and_is_visible(settings, stack, tmp_path):
    folder = write_bid(tmp_path / "harbor")

    code, summary = run(settings, stack, folder, tmp_path / "out", model=BrokenModel())

    assert code == EXIT_FAILED and summary["status"] == "failed"
    extract = stage(summary, "extract")
    assert extract["error"]["code"] == "no_values_extracted"
    assert extract["model_errors"] > 0
    assert extract["validation"]["passed"] == 0
    out = tmp_path / "out"
    trace = json.loads((out / "traces" / f"extraction_{LABEL}.json").read_text(encoding="utf-8"))
    assert "llm_malformed" in {event.get("error") for event in trace["events"]}
    document = BidExtraction.model_validate_json((out / f"{LABEL}.json").read_bytes())
    assert all(field.value is None for field in document.fields.values())
    assert stage(summary, "verify")["status"] == "skipped"


def test_an_index_stage_failure_stops_the_run_with_a_code(settings, tmp_path):
    folder = write_bid(tmp_path / "harbor")

    def unavailable(_settings):
        raise QdrantUnavailable()

    code, summary = process_bid(
        folder,
        tmp_path / "out",
        settings=settings,
        services=unavailable,
        bid=LABEL,
        llm_mode="test",
        model=EvidenceModel(ANSWERS),
    )

    assert code == EXIT_FAILED
    index = stage(summary, "index")
    assert index["error"]["code"] == "vector_store_unavailable"
    assert stage(summary, "extract")["status"] == "skipped"
    assert stage(summary, "chunk")["status"] == "succeeded"


def test_cli_rejects_invalid_requests_with_exit_code_2(tmp_path, capsys):
    code = main(
        ["process-bid", "--input", str(tmp_path / "absent"), "--output", str(tmp_path / "out")]
    )
    assert code == 2
    assert json.loads(capsys.readouterr().err)["error"] == "input_directory_not_found"
    with pytest.raises(SystemExit):
        main(
            [
                "process-bid",
                "--input",
                str(tmp_path),
                "--output",
                str(tmp_path / "out"),
                "--collection",
                "Bad Name!",
            ]
        )


def test_model_number_needs_a_code_not_a_numbered_tier():
    tier = "Tier 1 Small Student Chromebook Laptop (Touch)"
    assert any("model number needs a code" in p for p in semantic_problems("Model_no", tier, []))
    for value in ("Dell Latitude 5550", "UM-40", "WD22TB4", "ThinkPad X1 Carbon"):
        assert semantic_problems("Model_no", value, []) == [], value


def test_date_phrase_does_not_absorb_the_next_line():
    text = "Proposals due: March 12, 2026 at 3:00 PM Proposals must be submitted"
    assert date_phrases(text) == ["March 12, 2026 at 3:00 PM"]
    assert date_phrases("due July 9, 2024 at 2:00 PM CST.") == ["July 9, 2024 at 2:00 PM CST"]
    assert date_phrases("2024-06-10 14:00 EDT then") == ["2024-06-10 14:00 EDT"]
