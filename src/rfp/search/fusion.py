"""Weighted Reciprocal Rank Fusion over ranks, never over incompatible raw scores."""

from collections.abc import Hashable, Mapping, Sequence


def reciprocal_rank_fusion(
    rankings: Mapping[str, Sequence[Hashable]],
    k: int = 60,
    weights: Mapping[str, float] | None = None,
) -> list[tuple[Hashable, float]]:
    """Return ``(item, score)`` sorted by fused score.

    ``score = sum(weight_b / (k + rank_b))`` with one-based ranks. Ties keep the first branch
    order in which an item appeared, which makes the output deterministic.
    """
    if k < 1:
        raise ValueError("RRF k must be positive")
    scores: dict[Hashable, float] = {}
    first_seen: dict[Hashable, tuple[int, int]] = {}
    for branch_index, (branch, items) in enumerate(rankings.items()):
        weight = 1.0 if weights is None else weights.get(branch, 1.0)
        seen_in_branch: set[Hashable] = set()
        for rank, item in enumerate(items, start=1):
            if item in seen_in_branch:
                continue
            seen_in_branch.add(item)
            first_seen.setdefault(item, (rank, branch_index))
            if weight:
                scores[item] = scores.get(item, 0.0) + weight / (k + rank)
            else:
                scores.setdefault(item, 0.0)
    return sorted(scores.items(), key=lambda pair: (-pair[1], first_seen[pair[0]]))
