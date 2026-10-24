from typing import Protocol


class TokenCounter(Protocol):
    identifier: str

    def count(self, text: str) -> int: ...


class EstimatedTokenCounter:
    """Deterministic UTF-8 bytes / 4 estimate, not any embedding-model tokenizer."""

    identifier = "utf8-bytes-div4-v1"

    def count(self, text: str) -> int:
        return (len(text.encode("utf-8")) + 3) // 4
