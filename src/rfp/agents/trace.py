"""Ordered execution trace. Sequence numbers are assigned on the orchestrator thread."""

from datetime import UTC, datetime

from rfp.agents.schemas import ExtractionTrace, TokenUsage, TraceEvent


def clock() -> str:
    return datetime.now(UTC).isoformat(timespec="milliseconds").replace("+00:00", "Z")


class Tracer:
    def __init__(self) -> None:
        self._events: list[TraceEvent] = []

    def extend(self, events: list[TraceEvent]) -> None:
        for event in events:
            self._events.append(event.model_copy(update={"sequence": len(self._events)}))

    def finish(
        self,
        *,
        workflow_id: str,
        bid_id: str,
        bid_uuid: str,
        documents: list[str],
        final_statuses: dict[str, str],
        outcome: str,
        tokens: TokenUsage,
        provider: str,
        model: str,
        error: str | None,
    ) -> ExtractionTrace:
        return ExtractionTrace.model_validate(
            {
                "workflow_id": workflow_id,
                "bid_id": bid_id,
                "bid_uuid": bid_uuid,
                "documents": documents,
                "events": [event.model_dump() for event in self._events],
                "final_statuses": final_statuses,
                "outcome": outcome,
                "token_usage": tokens.model_dump(),
                "model": {"provider": provider or "none", "model": model or "none"},
                "error": error,
            }
        )
