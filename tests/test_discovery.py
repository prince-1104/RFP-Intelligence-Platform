import uuid

import pytest

from rfp.ingestion.discovery import DiscoveryError, discover, normalize_path, read_source


def test_nested_discovery_and_duplicate_content(tmp_path, settings):
    (tmp_path / "nested").mkdir()
    (tmp_path / "a.HTML").write_bytes(b"<p>same</p>")
    (tmp_path / "nested" / "b.htm").write_bytes(b"<p>same</p>")
    (tmp_path / "nested" / "c.pdf").write_bytes(b"pdf")
    for name in ("notes.txt", ".hidden.pdf", "~draft.pdf"):
        (tmp_path / name).write_bytes(b"ignored")
    result = discover(tmp_path, settings, "operator-bid-2026")
    assert [f.relative_path for f in result.files] == ["a.HTML", "nested/b.htm", "nested/c.pdf"]
    assert len(result.ignored) == 3
    assert result.files[0].sha256 == result.files[1].sha256
    assert result.files[0].document_id(result.bid_id) != result.files[1].document_id(result.bid_id)
    assert result.bid_id == discover(tmp_path, settings, "operator-bid-2026").bid_id
    assert read_source(result.files[0], 1024) == b"<p>same</p>"


def test_empty_and_invalid_directory(tmp_path, settings):
    assert discover(tmp_path, settings).warnings == ["no_supported_documents"]
    with pytest.raises(DiscoveryError, match="input_directory_not_found"):
        discover(tmp_path / "missing", settings)


@pytest.mark.parametrize(
    "path", ["../a.pdf", "/a.pdf", "C:/a.pdf", "x\\a.pdf", "x/./a.pdf", "x//a.pdf", "a\n.pdf", ""]
)
def test_reject_invalid_relative_paths(path):
    with pytest.raises(DiscoveryError):
        normalize_path(path)


@pytest.mark.parametrize(
    "field,value,code",
    [
        ("ingestion_max_file_bytes", 1024, "file_size_limit"),
        ("ingestion_max_total_bytes", 1024, "total_source_size_limit"),
        ("ingestion_max_files", 1, "file_count_limit"),
    ],
)
def test_discovery_limits(tmp_path, settings, field, value, code):
    for name in ("a.pdf", "b.html"):
        (tmp_path / name).write_bytes(b"x" * (1025 if code == "file_size_limit" else 600))
    settings = settings.model_copy(update={field: value})
    with pytest.raises(DiscoveryError, match=code):
        discover(tmp_path, settings)


def test_changed_source_is_rejected(tmp_path, settings):
    source = tmp_path / "a.html"
    source.write_bytes(b"one")
    discovered = discover(tmp_path, settings).files[0]
    source.write_bytes(b"two")
    with pytest.raises(DiscoveryError, match="source_changed"):
        read_source(discovered, 1024)


def test_symlink_is_not_followed(tmp_path, settings):
    outside = tmp_path.parent / f"{uuid.uuid4()}.html"
    outside.write_bytes(b"outside")
    link = tmp_path / "linked.html"
    try:
        try:
            link.symlink_to(outside)
        except OSError:
            pytest.skip("Operating system does not permit creation of test symlinks")
        result = discover(tmp_path, settings)
        assert not result.files
        assert result.ignored[0]["reason"] == "symbolic_link"
    finally:
        outside.unlink()
