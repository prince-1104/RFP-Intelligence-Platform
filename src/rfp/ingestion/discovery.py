import hashlib
import os
import stat
import unicodedata
import uuid
from dataclasses import dataclass, field
from pathlib import Path, PurePosixPath, PureWindowsPath

from rfp.core.config import Settings

CONTENT_TYPES = {".pdf": "application/pdf", ".html": "text/html", ".htm": "text/html"}
NAMESPACE = uuid.UUID("287706ad-5dc4-432b-8b7f-5b11a614ad94")


class DiscoveryError(ValueError):
    """Safe discovery failure code, without absolute source paths."""


def normalize_path(value: str) -> str:
    value = unicodedata.normalize("NFC", value)
    path = PurePosixPath(value)
    if (
        not value
        or "\\" in value
        or path.is_absolute()
        or PureWindowsPath(value).drive
        or any(ord(c) < 32 for c in value)
        or any(part in {"", ".", ".."} for part in value.split("/"))
    ):
        raise DiscoveryError("invalid_relative_path")
    if len(value) > 1000 or len(path.name) > 500:
        raise DiscoveryError("source_path_too_long")
    return value


@dataclass(frozen=True)
class SourceFile:
    path: Path
    relative_path: str
    sha256: str
    size: int
    content_type: str

    def document_id(self, bid_id: uuid.UUID) -> uuid.UUID:
        return uuid.uuid5(bid_id, f"{self.relative_path}\n{self.sha256}")


@dataclass
class DiscoveryResult:
    root: Path
    bid_id: uuid.UUID
    bid_identifier: str
    files: list[SourceFile] = field(default_factory=list)
    ignored: list[dict[str, str]] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)


def read_source(source: SourceFile, max_bytes: int) -> bytes:
    """Recheck symlinks, size and hash to reject changes between discovery and use."""
    if source.path.is_symlink() or not source.path.is_file():
        raise DiscoveryError("source_changed")
    # Resolve ancestors too: a directory may have been replaced since discovery.
    if source.path.resolve() != source.path:
        raise DiscoveryError("source_changed")
    with source.path.open("rb") as stream:
        data = stream.read(max_bytes + 1)
    if len(data) > max_bytes:
        raise DiscoveryError("file_size_limit")
    if len(data) != source.size or hashlib.sha256(data).hexdigest() != source.sha256:
        raise DiscoveryError("source_changed")
    return data


def discover(
    folder: Path, settings: Settings, bid_identifier: str | None = None
) -> DiscoveryResult:
    root = folder.expanduser().resolve()
    if not root.is_dir():
        raise DiscoveryError("input_directory_not_found")
    identifier = unicodedata.normalize("NFC", bid_identifier or root.name).strip()
    if not identifier or len(identifier) > 500 or any(ord(c) < 32 for c in identifier):
        raise DiscoveryError("invalid_bid_identifier")
    result = DiscoveryResult(root, uuid.uuid5(NAMESPACE, identifier), identifier)
    total = 0
    seen: set[str] = set()
    visited = 0

    def walk_error(error: OSError) -> None:
        raise DiscoveryError("directory_unreadable") from None

    for base, directories, filenames in os.walk(root, followlinks=False, onerror=walk_error):
        directories.sort()
        filenames.sort()
        visited += len(directories)
        if visited > settings.ingestion_max_files * 20:
            raise DiscoveryError("directory_entry_limit")
        for name in list(directories):
            path = Path(base) / name
            if name.startswith(".") or path.is_symlink() or path.is_junction():
                directories.remove(name)
                result.ignored.append(
                    {
                        "path": path.relative_to(root).as_posix(),
                        "reason": "hidden_or_link_directory",
                    }
                )
        for name in filenames:
            visited += 1
            if visited > settings.ingestion_max_files * 20:
                raise DiscoveryError("directory_entry_limit")
            path = Path(base) / name
            relative = normalize_path(path.relative_to(root).as_posix())
            reason = None
            if name.startswith((".", "~", "~$")) or name.endswith(("~", ".tmp", ".part")):
                reason = "temporary_or_hidden"
            elif path.is_symlink() or path.is_junction():
                reason = "symbolic_link"
            elif path.suffix.lower() not in CONTENT_TYPES:
                reason = "unsupported_type"
            if reason:
                result.ignored.append({"path": relative, "reason": reason})
                continue
            info = path.stat()
            if not stat.S_ISREG(info.st_mode):
                result.ignored.append({"path": relative, "reason": "not_regular_file"})
                continue
            if relative in seen:
                raise DiscoveryError("normalized_path_collision")
            seen.add(relative)
            if info.st_size > settings.ingestion_max_file_bytes:
                raise DiscoveryError("file_size_limit")
            total += info.st_size
            if total > settings.ingestion_max_total_bytes:
                raise DiscoveryError("total_source_size_limit")
            if len(result.files) >= settings.ingestion_max_files:
                raise DiscoveryError("file_count_limit")
            digest = hashlib.sha256()
            actual_size = 0
            with path.open("rb") as stream:
                while chunk := stream.read(1024 * 1024):
                    actual_size += len(chunk)
                    if actual_size > settings.ingestion_max_file_bytes:
                        raise DiscoveryError("file_size_limit")
                    digest.update(chunk)
            if actual_size != info.st_size:
                raise DiscoveryError("source_changed")
            result.files.append(
                SourceFile(
                    path,
                    relative,
                    digest.hexdigest(),
                    info.st_size,
                    CONTENT_TYPES[path.suffix.lower()],
                )
            )
    result.files.sort(key=lambda source: source.relative_path)
    if not result.files:
        result.warnings.append("no_supported_documents")
    return result
