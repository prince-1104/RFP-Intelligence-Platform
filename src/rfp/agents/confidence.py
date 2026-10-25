"""Evidence-quality score for an extracted field.

The output schema requires a number in [0, 1]. This is a deterministic heuristic over
what the workflow verified, not a probability and not calibrated against labelled
outcomes. A higher score means more of the checks below held; it does not mean the
value is that likely to be right.

Accepted value, starting at 0.55:
  +0.25 the value is copied word for word from a cited passage,
  +0.15 instead, every distinctive token of the value is in the cited passages,
  +0.05 instead, it is only a faithful restatement (descriptive fields);
  +0.10 two or more cited sources each support the value on their own;
  -0.10 per retry the field needed (at most two);
  -0.25 the value was kept although validation found it incomplete (capped at 0.45).
  The result is clamped to [0.30, 0.95].
Not found: 0.50, or 0.40 when retries were needed. Absence cannot be cited, so it
  never scores high.
Unresolved: 0.15, or 0.10 when the addenda conflict.
"""

from dataclasses import dataclass
from typing import Literal

Support = Literal["verbatim", "tokens", "paraphrase", "none"]

ACCEPTED_BASE = 0.55
SUPPORT_BONUS: dict[str, float] = {"verbatim": 0.25, "tokens": 0.15, "paraphrase": 0.05}
AGREEMENT_BONUS = 0.10
RETRY_PENALTY = 0.10
INCOMPLETE_PENALTY = 0.25
INCOMPLETE_CAP = 0.45
FLOOR, CEILING = 0.30, 0.95
NOT_FOUND, NOT_FOUND_RETRIED = 0.50, 0.40
UNRESOLVED, CONFLICT = 0.15, 0.10


@dataclass(frozen=True)
class Signals:
    status: str
    support: Support = "none"
    agreeing_sources: int = 0
    retries: int = 0
    incomplete: bool = False
    conflict: bool = False

    def reasons(self) -> list[str]:
        return [
            f"status={self.status}",
            f"support={self.support}",
            f"agreeing_sources={self.agreeing_sources}",
            f"retries={self.retries}",
            f"incomplete={self.incomplete}",
            f"conflict={self.conflict}",
        ]


def score(signals: Signals) -> float:
    if signals.status == "accepted":
        value = ACCEPTED_BASE + SUPPORT_BONUS.get(signals.support, 0.0)
        if signals.agreeing_sources >= 2:
            value += AGREEMENT_BONUS
        value -= RETRY_PENALTY * min(signals.retries, 2)
        if signals.incomplete:
            value = min(value - INCOMPLETE_PENALTY, INCOMPLETE_CAP)
        return round(min(max(value, FLOOR), CEILING), 2)
    if signals.status == "not_found":
        return NOT_FOUND_RETRIED if signals.retries else NOT_FOUND
    return CONFLICT if signals.conflict else UNRESOLVED


def band(value: float) -> str:
    """Coarse reading of the score for traces and reports."""
    if value >= 0.8:
        return "high"
    if value >= 0.5:
        return "medium"
    return "low"
