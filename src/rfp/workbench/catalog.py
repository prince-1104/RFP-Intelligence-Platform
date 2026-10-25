"""Read-only views of the loopback Qdrant collections. No API key is sent."""

import unicodedata
import uuid
from typing import Any

import httpx

from rfp.agents.errors import ExtractionError
from rfp.agents.safety import LOOPBACK, assert_local_qdrant
from rfp.ingestion.discovery import NAMESPACE
from rfp.workbench.errors import WorkbenchError
from rfp.workbench.guards import PHYSICAL_NAME, collection_base, workspace_root


def qdrant_json(url: str, path: str, payload: dict | None = None) -> Any:
    try:
        safe = assert_local_qdrant(url)
    except ExtractionError as exc:
        raise WorkbenchError(exc.code, exc.message, 403) from exc
    if httpx.URL(safe).host not in LOOPBACK:
        raise WorkbenchError(
            "unsafe_search_target", "The workbench uses loopback Qdrant only.", 403
        )
    try:
        with httpx.Client(timeout=5, trust_env=False) as client:
            response = (
                client.post(f"{safe}{path}", json=payload)
                if payload is not None
                else client.get(f"{safe}{path}")
            )
            response.raise_for_status()
            return response.json()
    except httpx.HTTPError as exc:
        raise WorkbenchError("qdrant_unavailable", "Local Qdrant is not reachable.", 503) from exc


def qdrant_ready(url: str) -> bool:
    """Qdrant answers /readyz with plain text, so only the status code is checked."""
    try:
        safe = assert_local_qdrant(url)
    except ExtractionError as exc:
        raise WorkbenchError(exc.code, exc.message, 403) from exc
    if httpx.URL(safe).host not in LOOPBACK:
        raise WorkbenchError(
            "unsafe_search_target", "The workbench uses loopback Qdrant only.", 403
        )
    try:
        with httpx.Client(timeout=5, trust_env=False) as client:
            return client.get(f"{safe}/readyz").status_code == 200
    except httpx.HTTPError:
        return False


def list_collections(url: str) -> list[dict[str, Any]]:
    body = qdrant_json(url, "/collections")
    found = []
    for item in body.get("result", {}).get("collections", []):
        physical = str(item.get("name", ""))
        match = PHYSICAL_NAME.fullmatch(physical)
        if not match:
            continue
        found.append({"base": match.group(1), "physical": physical})
    return sorted(found, key=lambda row: row["physical"])


def scroll_points(url: str, physical: str) -> list[dict[str, Any]]:
    collection_base(physical)
    points: list[dict[str, Any]] = []
    offset: Any = None
    while len(points) < 4000:
        payload: dict[str, Any] = {
            "limit": 256,
            "with_payload": [
                "bid_id",
                "document_id",
                "relative_path",
                "original_file_name",
                "doc_type",
                "page_start",
            ],
            "with_vector": False,
        }
        if offset is not None:
            payload["offset"] = offset
        body = qdrant_json(url, f"/collections/{physical}/points/scroll", payload)
        result = body.get("result", {})
        points.extend(result.get("points", []))
        offset = result.get("next_page_offset")
        if not offset:
            break
    return points


def label_index() -> dict[str, str]:
    """Folder names under data/assessment, keyed by the same UUID the extractors use."""
    root = workspace_root() / "data" / "assessment"
    labels: dict[str, str] = {}
    if not root.is_dir():
        return labels
    for folder in root.iterdir():
        if folder.is_dir() and not folder.is_symlink():
            normalized = unicodedata.normalize("NFC", folder.name)
            labels[str(uuid.uuid5(NAMESPACE, normalized))] = folder.name
    return labels


def bids_from_points(points: list[dict[str, Any]]) -> list[dict[str, Any]]:
    labels = label_index()
    grouped: dict[str, dict[str, Any]] = {}
    for point in points:
        payload = point.get("payload") or {}
        bid_uuid = str(payload.get("bid_id") or "")
        if not bid_uuid:
            continue
        bid = grouped.setdefault(
            bid_uuid,
            {
                "bid_uuid": bid_uuid,
                "label": labels.get(bid_uuid, bid_uuid),
                "documents": {},
                "points": 0,
            },
        )
        bid["points"] += 1
        document_id = str(payload.get("document_id") or "")
        if not document_id:
            continue
        document = bid["documents"].setdefault(
            document_id,
            {
                "document_id": document_id,
                "file": payload.get("original_file_name") or payload.get("relative_path") or "",
                "relative_path": payload.get("relative_path") or "",
                "doc_type": payload.get("doc_type"),
                "points": 0,
            },
        )
        document["points"] += 1
    rows = []
    for bid in grouped.values():
        bid["documents"] = sorted(bid["documents"].values(), key=lambda item: item["file"])
        rows.append(bid)
    return sorted(rows, key=lambda item: str(item["label"]))
