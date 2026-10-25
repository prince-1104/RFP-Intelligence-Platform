"""Deterministic addendum reconciliation report for indexed bids. No model calls.

    python scripts/report_addenda.py --bid-id Bid1 --bid-id Bid2
        --output artifacts/phaseB_addenda/reconciliation

For each reconciled extraction field it runs the field's first catalog query (all documents) and
the extraction workflow's addendum check query against local Qdrant, then writes the requirement
history from `rfp.agents.revisions`: baseline passages, every addendum statement with its change
type, status and reason, and verbatim quotes with file and page. Due Date uses its own rule.
Optional `--question` adds question-scoped histories like the ones the Q&A prompt receives.
Searches are read-only and limited to one bid; a remote Qdrant URL is refused, and existing
report files are not overwritten.
"""

import argparse
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from rfp.agents.extractors import CATALOG  # noqa: E402
from rfp.agents.orchestrator import ADDENDUM_CHECK_TOP_K  # noqa: E402
from rfp.agents.qa_runner import local_search  # noqa: E402
from rfp.agents.reconciliation import FIELD_TERMS, reconcile_due_date  # noqa: E402
from rfp.agents.retrieval import retrieve  # noqa: E402
from rfp.agents.revisions import question_terms, reconcile  # noqa: E402
from rfp.agents.runner import assert_output_dir, resolve_bid  # noqa: E402
from rfp.agents.schemas import DraftField, EvidenceHit  # noqa: E402
from rfp.cli import collection_name  # noqa: E402
from rfp.schemas.parsed import DocumentType  # noqa: E402
from rfp.schemas.search import RetrievalMode  # noqa: E402

TOP_K = 12


def _search(search, bid, query, doc_types, prefix, top_k=TOP_K) -> list[EvidenceHit]:
    hits, _ = retrieve(
        search,
        bid,
        query,
        mode=RetrievalMode.hybrid_rerank,
        top_k=top_k,
        doc_types=doc_types,
        addendum_numbers=[],
        evidence_prefix=prefix,
    )
    return hits


def report(search, label: str, questions: list[str]) -> dict:
    _, bid = resolve_bid(label)
    fields = {}
    for name, terms in FIELD_TERMS.items():
        base = _search(search, bid, CATALOG[name].steps[0].query, [], "base-")
        check = _search(
            search,
            bid,
            " ".join([name, *terms]),
            [DocumentType.addendum],
            "addendum-",
            ADDENDUM_CHECK_TOP_K,
        )
        fields[name] = reconcile(name, terms, base + check, bid).model_dump(mode="json")
    due: list[EvidenceHit] = []
    for index, step in enumerate(CATALOG["Due Date"].steps):
        due += _search(search, bid, step.query, list(step.doc_types), f"due{index}-")
    decision = reconcile_due_date(DraftField(name="Due Date", value=None), due)
    asked = {}
    for question in questions:
        hits = _search(search, bid, question, [], "q-") + _search(
            search, bid, question, [DocumentType.addendum], "qa-", 5
        )
        asked[question] = reconcile("question", question_terms(question), hits, bid).model_dump(
            mode="json"
        )
    return {
        "bid_id": label,
        "bid_uuid": str(bid),
        "fields": fields,
        "due_date": None if decision.change is None else decision.change.model_dump(mode="json"),
        "due_date_reject_reason": decision.reject_reason,
        "questions": asked,
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--bid-id", action="append", required=True)
    parser.add_argument("--question", action="append", default=[])
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--qdrant-url", default="http://127.0.0.1:6333")
    parser.add_argument("--collection", type=collection_name, default=None)
    args = parser.parse_args()
    out = assert_output_dir(args.output)
    targets = {label: out / f"reconciliation_{label}.json" for label in args.bid_id}
    existing = [str(path) for path in targets.values() if path.exists()]
    if existing:
        print(json.dumps({"error": "output_exists", "paths": existing}))
        return 2
    search = local_search(args.qdrant_url, args.collection)
    out.mkdir(parents=True, exist_ok=True)
    for label, path in targets.items():
        payload = report(search, label, args.question)
        path.write_text(json.dumps(payload, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
        summary = {name: item["status"] for name, item in payload["fields"].items()}
        print(json.dumps({"bid_id": label, "path": str(path), "fields": summary}))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
