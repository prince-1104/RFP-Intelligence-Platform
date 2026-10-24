import csv
import io
import json
import subprocess
import sys
import uuid
from pathlib import Path

import pytest
from test_retrieval_evaluation import response, result

from rfp.evaluation.retrieval import EvalDataset, load_dataset
from rfp.evaluation.review import (
    CASE_COLUMNS,
    REVIEWER_COLUMNS,
    KnownMiss,
    ReviewError,
    collect_evidence,
    merge,
    read_reviews,
    sha256_file,
    validate_reviews,
    write_packet,
)

ROOT = Path(__file__).resolve().parents[1]
DATASET = ROOT / "evaluation" / "assessment_retrieval.json"
SCRIPT = ROOT / "scripts" / "build_retrieval_review_packet.py"
MODES = {"hybrid_rerank": 3, "hybrid": 2}
MISSES = (KnownMiss("example miss", ("b1-award-timing",)),)


def fake_search(dataset: EvalDataset):
    """First result reproduces the first judgment's evidence; second is an HTML chunk whose
    locator has no detail. Unanswerable cases return nothing."""

    def search(case, mode, top_k):
        if not case.judgments:
            return response([], ["identifier_evidence_not_found"])
        j = case.judgments[0]
        bid = dataset.bids[case.bid]
        first = result(1, " ".join(j.text), j.document, j.page, j.html_element_id, bid=bid)
        other = result(2, "unrelated listing text", "listing.html", element=None, bid=bid)
        other.citation.html_locators[0].element_id = None
        for item in (first, other):
            stable = uuid.uuid5(uuid.NAMESPACE_URL, f"{case.id}/{mode}/{item.rank}")
            item.occurrence_id = item.citation.occurrence_id = stable
            item.content_id = item.citation.content_id = stable
            item.citation.document_id = item.citation.chunk_generation_id = stable
        return response([first, other])

    return search


def documents(dataset: EvalDataset) -> dict[str, list[str]]:
    docs: dict[str, set[str]] = {}
    for case in dataset.cases:
        docs.setdefault(case.bid, set()).update(j.document for j in case.judgments)
    return {bid: sorted(paths) for bid, paths in docs.items()}


def build(tmp_path: Path, dataset: EvalDataset | None = None) -> Path:
    dataset = dataset or load_dataset(DATASET)
    evidence = collect_evidence(dataset, fake_search(dataset), MODES)
    manifest = {"dataset_sha256": sha256_file(DATASET), "retrieval_evidence": "fake"}
    write_packet(tmp_path, dataset, evidence, manifest, documents(dataset), "hybrid_rerank", MISSES)
    return tmp_path


def csv_rows(path: Path) -> list[dict[str, str]]:
    return list(csv.DictReader(io.StringIO(path.read_bytes().decode("utf-8-sig"))))


def write_reviews(path: Path, rows: list[dict[str, str]]) -> Path:
    buffer = io.StringIO()
    writer = csv.DictWriter(buffer, fieldnames=CASE_COLUMNS, lineterminator="\n")
    writer.writeheader()
    writer.writerows(rows)
    path.write_bytes(buffer.getvalue().encode("utf-8-sig"))
    return path


def test_every_case_exported_exactly_once_with_blank_reviewer_fields(tmp_path):
    dataset = load_dataset(DATASET)
    rows = csv_rows(build(tmp_path) / "cases.csv")
    assert [r["case_id"] for r in rows] == [c.id for c in dataset.cases]
    assert all(r[column] == "" for r in rows for column in REVIEWER_COLUMNS)
    packet = json.loads((tmp_path / "packet.json").read_text(encoding="utf-8"))
    assert [c["case_id"] for c in packet["cases"]] == [c.id for c in dataset.cases]
    assert set(packet["evidence"]) == {c.id for c in dataset.cases}
    evidence_ids = {r["case_id"] for r in csv_rows(tmp_path / "evidence.csv")}
    assert evidence_ids <= {c.id for c in dataset.cases}


def test_case_ids_survive_csv_round_trip(tmp_path):
    rows = read_reviews(build(tmp_path) / "cases.csv")
    dataset = load_dataset(DATASET)
    assert [r["case_id"] for r in rows] == [c.id for c in dataset.cases]
    decisions, problems = validate_reviews(rows, dataset, documents(dataset))
    assert problems == [] and decisions == {}


def test_missing_answers_and_evidence_are_reported_not_invented(tmp_path):
    dataset = load_dataset(DATASET)
    rows = {r["case_id"]: r for r in csv_rows(build(tmp_path) / "cases.csv")}
    assert all(r["current_expected_answer"] == "" for r in rows.values())
    for case in dataset.cases:
        if not case.answerable:
            assert rows[case.id]["current_expected_evidence"] == ""
    summary = (tmp_path / "SUMMARY.md").read_text(encoding="utf-8")
    assert "has no expected-answer field" in summary
    assert "not a per-case flag" in summary


def test_judgment_without_locator_is_marked_and_needs_labeling_links_only_named_ids(tmp_path):
    data = json.loads(DATASET.read_text(encoding="utf-8"))
    html_case = next(
        c for c in data["cases"] if any("html_element_id" in j for j in c["judgments"])
    )
    for judgment in html_case["judgments"]:
        judgment.pop("html_element_id", None)
    dataset = EvalDataset.model_validate(data)
    rows = {r["case_id"]: r for r in csv_rows(build(tmp_path, dataset) / "cases.csv")}
    assert "(no locator)" in rows[html_case["id"]]["current_expected_evidence"]
    summary = (tmp_path / "SUMMARY.md").read_text(encoding="utf-8")
    assert f"{html_case['id']} J" in summary
    linked = {cid for cid, r in rows.items() if r["listed_in_needs_human_labeling"]}
    named = {c.id for c in dataset.cases if any(c.id in i for i in dataset.needs_human_labeling)}
    assert linked == named


def test_candidate_citations_are_copied_from_search_output(tmp_path):
    dataset = load_dataset(DATASET)
    candidates = csv_rows(build(tmp_path) / "evidence.csv")
    by_case = {c.id: c for c in dataset.cases}
    for row in candidates:
        judgment = by_case[row["case_id"]].judgments[0]
        if row["rank"] == "1":
            if judgment.page is not None:
                assert row["page_start"] == row["page_end"] == str(judgment.page)
                assert row["html_element_ids"] == ""
            else:
                assert row["page_start"] == row["page_end"] == ""
                assert row["html_element_ids"] == judgment.html_element_id
            assert row["matches_current_judgments"].startswith("J1")
        else:
            assert row["page_start"] == row["page_end"] == ""
            assert row["html_element_ids"] == "" and row["locator_detail_missing"] == "yes"
            assert row["matches_current_judgments"] == "no"


def test_generation_is_deterministic_and_leaves_dataset_unchanged(tmp_path):
    before = sha256_file(DATASET)
    first = build(tmp_path / "a")
    second = build(tmp_path / "b")
    for name in ("cases.csv", "evidence.csv", "packet.json", "SUMMARY.md"):
        assert (first / name).read_bytes() == (second / name).read_bytes()
    assert sha256_file(DATASET) == before


def approve(case_id_row: dict[str, str], dataset: EvalDataset) -> dict[str, str]:
    case = next(c for c in dataset.cases if c.id == case_id_row["case_id"])
    j = case.judgments[0]
    locator = f"page={j.page}" if j.page else f"html={j.html_element_id}"
    return {
        "review_status": "approved",
        "reviewer_id": "rv1",
        "reviewer_answerability": "answerable",
        "reviewer_expected_answer": "an answer",
        "reviewer_evidence": f"{j.document} | {locator}",
    }


def test_invalid_duplicate_unknown_and_missing_ids_are_rejected(tmp_path):
    dataset = load_dataset(DATASET)
    rows = csv_rows(build(tmp_path) / "cases.csv")
    rows.append(dict(rows[0]))
    rows.append({**rows[1], "case_id": "no-such-case"})
    del rows[2]
    _, problems = validate_reviews(rows, dataset, documents(dataset))
    text = "\n".join(problems)
    assert "duplicate case_id" in text and "unknown case_id 'no-such-case'" in text
    assert f"missing case_id '{dataset.cases[2].id}'" in text


def test_partially_reviewed_or_inconsistent_cases_are_never_approved(tmp_path):
    dataset = load_dataset(DATASET)
    rows = csv_rows(build(tmp_path) / "cases.csv")
    rows[0].update(reviewer_expected_answer="filled", reviewer_id="rv1")  # status blank
    rows[1].update(approve(rows[1], dataset), reviewer_answerability="unanswerable")
    rows[2].update(approve(rows[2], dataset), reviewer_id="")
    rows[3].update(approve(rows[3], dataset), review_status="APPROVED")
    rows[4].update(approve(rows[4], dataset), reviewer_evidence="nowhere.pdf | page=0")
    decisions, problems = validate_reviews(rows, dataset, documents(dataset))
    assert decisions == {}
    text = "\n".join(problems)
    assert "review_status is blank" in text
    assert "use 'corrected'" in text and "reviewer_id is required" in text
    assert "not in ('approved'" in text and "one-based" in text


def test_merge_without_reviews_changes_nothing_and_require_complete_refuses(tmp_path):
    dataset = load_dataset(DATASET)
    proposed, labels, changes = merge(dataset, {})
    assert changes == [] and labels["counts"]["unreviewed"] == len(dataset.cases)
    assert [c.model_dump() for c in proposed.cases] == [c.model_dump() for c in dataset.cases]
    with pytest.raises(ReviewError):
        merge(dataset, {}, require_complete=True)


def test_corrections_produce_separate_proposed_dataset_and_audit_record(tmp_path):
    dataset = load_dataset(DATASET)
    rows = csv_rows(build(tmp_path) / "cases.csv")
    target = next(r for r in rows if r["case_id"] == "b1-award-timing")
    case = next(c for c in dataset.cases if c.id == "b1-award-timing")
    target.update(
        review_status="corrected",
        reviewer_id="rv1",
        reviewer_answerability="unanswerable",
        reviewer_notes="no award date in the documents",
    )
    rows[0].update(approve(rows[0], dataset))
    discuss = next(r for r in rows if r["case_id"] == "b2-memory")
    discuss.update(review_status="needs_discussion", reviewer_id="rv2", reviewer_notes="unclear")
    decisions, problems = validate_reviews(rows, dataset, documents(dataset))
    assert problems == []
    proposed, labels, changes = merge(dataset, decisions)
    updated = next(c for c in proposed.cases if c.id == case.id)
    assert updated.kind == "unanswerable" and not updated.answerable and not updated.judgments
    assert labels["counts"] == {
        "unreviewed": len(dataset.cases) - 3,
        "approved": 1,
        "corrected": 1,
        "rejected": 0,
        "needs_discussion": 1,
    }
    assert "b2-memory" in labels["not_approved"]
    record = next(item for item in labels["labels"] if item["case_id"] == case.id)
    assert record["original"]["kind"] == case.kind
    assert any("answerability answerable -> unanswerable" in c for c in changes)
    assert load_dataset(DATASET).model_dump() == dataset.model_dump()


def run_script(*args: str) -> subprocess.CompletedProcess:
    return subprocess.run(
        [sys.executable, str(SCRIPT), *args], capture_output=True, text=True, cwd=ROOT, check=False
    )


def test_merge_cli_refuses_canonical_output_and_leaves_dataset_untouched(tmp_path):
    packet_dir = build(tmp_path / "packet")
    reviews = packet_dir / "cases.csv"
    before = sha256_file(DATASET)
    common = ["--reviews", str(reviews), "--packet", str(packet_dir / "packet.json")]
    refused = run_script(
        "merge", *common, "--out-dataset", str(DATASET), "--out-labels", str(tmp_path / "l.json")
    )
    assert refused.returncode == 2 and "canonical" in refused.stdout
    ok = run_script(
        "merge",
        *common,
        "--out-dataset",
        str(tmp_path / "proposed.json"),
        "--out-labels",
        str(tmp_path / "labels.json"),
    )
    assert ok.returncode == 0, ok.stdout + ok.stderr
    assert "changes vs canonical dataset: none" in ok.stdout
    assert sha256_file(DATASET) == before
    incomplete = run_script(
        "merge",
        *common,
        "--require-complete",
        "--out-dataset",
        str(tmp_path / "p2.json"),
        "--out-labels",
        str(tmp_path / "l2.json"),
    )
    assert incomplete.returncode == 1 and "merge refused" in incomplete.stdout
    assert not (tmp_path / "p2.json").exists()


def test_read_only_guard_blocks_writes_before_any_request():
    sys.path.insert(0, str(ROOT / "scripts"))
    try:
        from local_guard import ReadOnlyQdrant
    finally:
        sys.path.remove(str(ROOT / "scripts"))

    class Client:
        def upsert(self, *args, **kwargs):
            raise AssertionError("write reached the client")

        def scroll(self, *args, **kwargs):
            return "read"

    guarded = ReadOnlyQdrant(Client())
    assert guarded.scroll() == "read"
    for name in ("upsert", "delete", "set_payload", "create_collection", "delete_collection"):
        with pytest.raises(PermissionError):
            getattr(guarded, name)


def test_packet_bound_to_dataset_version(tmp_path):
    packet_dir = build(tmp_path)
    packet = json.loads((packet_dir / "packet.json").read_text(encoding="utf-8"))
    packet["manifest"]["dataset_sha256"] = uuid.uuid4().hex
    (packet_dir / "packet.json").write_text(json.dumps(packet), encoding="utf-8")
    out = run_script(
        "validate",
        "--reviews",
        str(packet_dir / "cases.csv"),
        "--packet",
        str(packet_dir / "packet.json"),
    )
    assert out.returncode == 1 and "dataset changed" in out.stdout
