"""Path and collection limits for the local workbench. Reads may use any local collection.
Writes may use only a caller-chosen name that starts with ``workbench``."""

import re
from pathlib import Path

from rfp.workbench.errors import WorkbenchError

COLLECTION_NAME = re.compile(r"[a-z][a-z0-9_]{2,40}")
PHYSICAL_NAME = re.compile(r"([a-z][a-z0-9_]{2,40})__[0-9a-f]{12}")
PROTECTED_COLLECTIONS = frozenset(
    {
        "bid_documents",
        "phasec_clean",
        "pipeline_check",
        "rfp_smoke",
        "phasee_labels",
        "phasef_tables",
        "phasef_rows",
        "phasef_order",
        "phasef_cells",
    }
)
UPLOAD_SUFFIXES = frozenset({".pdf", ".html", ".htm"})
MAX_UPLOAD_BYTES = 50 * 1024 * 1024


def collection_base(value: str) -> str:
    name = value.strip()
    match = PHYSICAL_NAME.fullmatch(name)
    if match:
        return match.group(1)
    if not COLLECTION_NAME.fullmatch(name):
        raise WorkbenchError(
            "invalid_collection",
            "Collection names use 3-41 lowercase letters, digits or underscores.",
        )
    return name


def assert_readable_collection(value: str) -> str:
    return collection_base(value)


def assert_writable_collection(value: str) -> str:
    name = collection_base(value)
    if name in PROTECTED_COLLECTIONS or not name.startswith("workbench"):
        raise WorkbenchError(
            "collection_protected",
            "Indexing from the workbench can write only to a new collection whose name "
            "starts with workbench. Existing collections stay read-only.",
            409,
        )
    return name


def workspace_root() -> Path:
    return Path.cwd().resolve()


def _contained(path: Path, root: Path) -> bool:
    return path == root or root in path.parents


def resolve_source(relative: str, *, roots: tuple[Path, ...]) -> Path:
    """A workspace-relative directory or file. Symlinks and paths outside the roots are refused."""
    raw = relative.strip().replace("\\", "/")
    if not raw or raw.startswith("/") or ".." in Path(raw).parts:
        raise WorkbenchError("invalid_path", "The path must stay inside the workspace.")
    candidate = (workspace_root() / raw).resolve()
    if not any(_contained(candidate, root.resolve()) for root in roots):
        raise WorkbenchError("invalid_path", "That path is outside the allowed workspace folders.")
    if candidate.is_symlink():
        raise WorkbenchError("invalid_path", "Symlinks are not accepted.")
    if not candidate.exists():
        raise WorkbenchError("invalid_path", "That path does not exist.", 404)
    return candidate


def allowed_input_roots() -> tuple[Path, ...]:
    root = workspace_root()
    return (root / "data", root / "artifacts" / "workbench" / "uploads")


def safe_upload_name(filename: str) -> str:
    name = Path(filename).name
    suffix = Path(name).suffix.lower()
    if suffix not in UPLOAD_SUFFIXES or name != filename or ".." in name:
        raise WorkbenchError("unsupported_file", "Only PDF and HTML files can be uploaded.", 415)
    if not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_. -]{0,120}", name):
        raise WorkbenchError("unsupported_file", "The file name contains unsupported characters.")
    return name
