"""Read-only snapshots of saved evaluation, extraction and Q&A artifacts.

Each record names its file. Missing files stay absent rather than being filled in.
"""

import json
from pathlib import Path
from typing import Any

from rfp.workbench.guards import workspace_root

EVALUATION_GLOBS = (
    "artifacts/eval/retrieval_phase7.json",
    "artifacts/phaseE_reliability/retrieval_*.json",
    "artifacts/phaseF_extraction/retrieval_*.json",
    "artifacts/workbench/evaluation/*.json",
    "submission/evidence/retrieval_evaluation.json",
)
EXTRACTION_GLOBS = (
    "artifacts/extraction_v4/*.json",
    "artifacts/phaseE_reliability/extraction_r3/*.json",
    "artifacts/phaseF_extraction/extraction_r*/*.json",
    "artifacts/workbench/extractions/*/*.json",
)
QA_SNAPSHOT = "artifacts/qa_examples.json"
AUDIT_MATRIX = "docs/FINAL_REQUIREMENTS_AUDIT.md"
METRICS = ("recall@5", "mrr@10", "ndcg@10")


def _read_json(path: Path) -> dict[str, Any] | None:
    if not path.is_file() or path.is_symlink() or path.stat().st_size > 20 * 1024 * 1024:
        return None
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None
    return payload if isinstance(payload, dict) else None


def _relative(path: Path) -> str:
    try:
        return path.resolve().relative_to(workspace_root()).as_posix()
    except ValueError:
        return path.name


def evaluation_snapshots() -> list[dict[str, Any]]:
    root = workspace_root()
    seen: set[Path] = set()
    rows = []
    for pattern in EVALUATION_GLOBS:
        for path in sorted(root.glob(pattern)):
            resolved = path.resolve()
            if resolved in seen:
                continue
            seen.add(resolved)
            payload = _read_json(path)
            if not payload or "modes" not in payload:
                continue
            modes = {}
            for name, summary in payload["modes"].items():
                if not isinstance(summary, dict):
                    continue
                modes[name] = {
                    "recall@5": summary.get("recall@5"),
                    "mrr@10": summary.get("mrr@10"),
                    "ndcg@10": summary.get("ndcg@10"),
                    "cases": summary.get("cases"),
                    "scored_cases": summary.get("scored_cases"),
                    "cross_bid_leaks": summary.get("cross_bid_leaks"),
                    "citation_errors": summary.get("citation_errors"),
                }
            rows.append(
                {
                    "source": _relative(path),
                    "dataset": payload.get("dataset"),
                    "collection": payload.get("collection"),
                    "modes": modes,
                    "case_count": len(payload.get("cases") or {}),
                    "kind": "saved_evaluation_snapshot",
                }
            )
    return rows


def evaluation_detail(source: str) -> dict[str, Any] | None:
    path = (workspace_root() / source).resolve()
    allowed = {item["source"] for item in evaluation_snapshots()}
    if source not in allowed:
        return None
    payload = _read_json(path)
    if payload is None:
        return None
    cases = payload.get("cases") or {}
    compact = []
    for case_id, modes in cases.items():
        if not isinstance(modes, dict):
            continue
        compact.append(
            {
                "id": case_id,
                "modes": {
                    mode: {
                        "first_relevant_rank": body.get("first_relevant_rank")
                        if isinstance(body, dict)
                        else None,
                        "recall@5": body.get("recall@5") if isinstance(body, dict) else None,
                    }
                    for mode, body in modes.items()
                },
            }
        )
    return {
        "source": source,
        "dataset": payload.get("dataset"),
        "collection": payload.get("collection"),
        "modes": {
            name: {key: summary.get(key) for key in (*METRICS, "cases", "scored_cases")}
            for name, summary in (payload.get("modes") or {}).items()
            if isinstance(summary, dict)
        },
        "cases": compact,
        "kind": "saved_evaluation_snapshot",
    }


def extraction_runs() -> list[dict[str, Any]]:
    root = workspace_root()
    rows = []
    seen: set[Path] = set()
    for pattern in EXTRACTION_GLOBS:
        for path in sorted(root.glob(pattern)):
            resolved = path.resolve()
            if resolved in seen or path.name == "summary.json" or path.parent.name == "traces":
                continue
            seen.add(resolved)
            payload = _read_json(path)
            if not payload or "fields" not in payload or "validation" not in payload:
                continue
            rows.append(
                {
                    "run": path.parent.name,
                    "bid_id": payload.get("bid_id") or path.stem,
                    "source": _relative(path),
                    "validation": payload.get("validation"),
                    "addendum_changes": len(payload.get("addendum_changes") or []),
                    "kind": "saved_extraction",
                }
            )
    return rows


def read_extraction(source: str) -> dict[str, Any] | None:
    allowed = {item["source"] for item in extraction_runs()}
    if source not in allowed:
        return None
    payload = _read_json((workspace_root() / source).resolve())
    if payload is None:
        return None
    payload["source"] = source
    payload["kind"] = "saved_extraction"
    return payload


def qa_snapshot() -> dict[str, Any] | None:
    payload = _read_json(workspace_root() / QA_SNAPSHOT)
    if payload is None:
        return None
    examples = []
    for item in (payload.get("examples") or [])[:20]:
        if not isinstance(item, dict):
            continue
        examples.append(
            {
                "id": item.get("id"),
                "bid_id": item.get("bid_id"),
                "question": item.get("question"),
                "status": item.get("status"),
                "answer": item.get("answer"),
            }
        )
    return {
        "source": QA_SNAPSHOT,
        "kind": "saved_qa_snapshot",
        "summary": payload.get("summary"),
        "examples": examples,
    }


def audit_matrix() -> dict[str, str]:
    path = workspace_root() / AUDIT_MATRIX
    text = path.read_text(encoding="utf-8") if path.is_file() else ""
    return {
        "source": AUDIT_MATRIX,
        "label": "Saved audit document. The workbench does not re-score these rows.",
        "text": text,
    }
