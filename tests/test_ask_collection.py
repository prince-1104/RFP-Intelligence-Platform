"""`ask --collection`: Q&A over a bid indexed into a separate local collection. In-memory Qdrant,
fake embeddings and a fake model; not evidence of live answer quality."""

import json
import re
from pathlib import Path

import pytest
from search_fakes import BID_B, generation, sample_bid, services
from unseen_bid import ANSWERS, RFP_FILE, EvidenceModel, write_bid

from rfp.agents import qa_runner
from rfp.agents.errors import ExtractionError
from rfp.agents.llm import ModelRequest, ModelResponse
from rfp.agents.qa_runner import execute_ask
from rfp.cli import local_settings, main
from rfp.pipeline import EXIT_OK, process_bid
from rfp.search.errors import IndexUnavailable

LABEL = "harbor-2026"
LOCAL = "http://127.0.0.1:6333"
COLLECTION = "trial_run"


class QuoteModel:
    """Cites the passage that contains `quote`; answers not_found when no passage does."""

    provider = "fake"
    model = "quote"

    def __init__(self, answer: str, quote: str):
        self.answer, self.quote = answer, quote
        self.prompts: list[str] = []

    def complete(self, request: ModelRequest) -> ModelResponse:
        self.prompts.append(request.user)
        current = None
        for line in request.user.splitlines():
            match = re.match(r"\[(E\d+)\]", line)
            if match:
                current = match.group(1)
            elif current and self.quote in line:
                reply = {
                    "status": "answered",
                    "answer": self.answer,
                    "citations": [{"evidence_id": current, "quote": self.quote}],
                }
                return ModelResponse(json.dumps(reply), self.provider, self.model, 10, 5, 1.0)
        reply = {"status": "not_found", "answer": "Not found in documents", "citations": []}
        return ModelResponse(json.dumps(reply), self.provider, self.model, 10, 5, 1.0)


@pytest.fixture
def trial(settings, tmp_path):
    trial_settings = settings.model_copy(update={"qdrant_collection": COLLECTION})
    indexer, search, client = services(trial_settings)
    code, _ = process_bid(
        write_bid(tmp_path / "harbor"),
        tmp_path / "out",
        settings=trial_settings,
        services=lambda _settings: (indexer, search.search),
        bid=LABEL,
        llm_mode="test",
        model=EvidenceModel(ANSWERS),
        env_file=Path("missing.env"),
    )
    assert code == EXIT_OK
    return indexer, search, client


def ask(search, model, question="What is the RFP number?"):
    return execute_ask(
        bid=LABEL,
        question=question,
        qdrant_url=LOCAL,
        llm_mode="test",
        fixture=None,
        trace_dir=None,
        output=None,
        search=search,
        model=model,
    )


def test_a_bid_in_a_new_collection_can_be_queried(trial):
    _, search, _ = trial
    model = QuoteModel("The RFP number is HC-2026-118.", "HC-2026-118")

    response, _ = ask(search.search, model)

    assert search.settings.qdrant_collection == COLLECTION
    assert response.status == "answered"
    assert response.citations and {c.document for c in response.citations} <= {
        RFP_FILE,
        "Addendum_1_HC-2026-118.pdf",
        "listing.html",
    }
    assert any("HC-2026-118" in c.excerpt for c in response.citations)


def test_evidence_from_another_bid_in_the_collection_is_rejected(trial):
    indexer, search, _ = trial
    indexer.index([generation(result) for result in sample_bid(BID_B)])
    requests = []

    def recorded(request):
        requests.append(request)
        return search.search(request)

    model = QuoteModel("The RFP number is RFP-2026-014.", "RFP-2026-014")
    response, _ = ask(recorded, model)

    assert response.status == "not_found" and response.citations == []
    assert "RFP-2026-014" not in "".join(model.prompts)
    _, bid_uuid = qa_runner.resolve_bid(LABEL)
    assert requests and all(r.filters.bid_ids == [bid_uuid] for r in requests)


def test_a_missing_or_invalid_collection_fails_safely(settings):
    _, _, client = services(settings)
    missing = settings.model_copy(update={"qdrant_collection": "missing_run"})
    _, search, _ = services(missing, client=client)
    with pytest.raises(IndexUnavailable):
        ask(search.search, QuoteModel("x", "x"))

    with pytest.raises(SystemExit):
        main(["ask", "--bid-id", LABEL, "--question", "Bid number?", "--collection", "Bad Name!"])
    with pytest.raises(ExtractionError) as remote:
        execute_ask(
            bid=LABEL,
            question="Bid number?",
            qdrant_url="https://cluster.cloud.qdrant.io:6333",
            llm_mode="test",
            fixture=None,
            trace_dir=None,
            output=None,
            collection=COLLECTION,
        )
    assert remote.value.code == "unsafe_search_target"


def test_cli_passes_the_collection_and_defaults_to_the_configured_one(
    monkeypatch, tmp_path, capsys
):
    seen: list[str | None] = []

    def fake_local_search(url, collection=None):
        seen.append(collection)
        return lambda request: (_ for _ in ()).throw(IndexUnavailable())

    monkeypatch.setattr(qa_runner, "local_search", fake_local_search)
    base = ["ask", "--bid-id", LABEL, "--question", "Bid number?", "--llm-mode", "test"]
    base += ["--trace-dir", str(tmp_path / "traces")]

    assert main([*base, "--collection", COLLECTION]) == 1
    assert json.loads(capsys.readouterr().err)["error"] == "index_not_found"
    assert main(base) == 1
    assert seen == [COLLECTION, None]


def test_local_search_uses_the_named_collection_and_otherwise_the_default(monkeypatch):
    configured: list[str] = []

    class Stub:
        def search(self, request):
            raise IndexUnavailable()

    def fake_service(current):
        configured.append(current.qdrant_collection)
        return Stub()

    monkeypatch.setattr("rfp.search.wiring.search_service", fake_service)
    qa_runner.local_search(LOCAL, COLLECTION)
    qa_runner.local_search(LOCAL)

    assert configured == [COLLECTION, local_settings(LOCAL).qdrant_collection]
    assert configured[1] != COLLECTION
