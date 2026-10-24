"""A fictional bid for end-to-end pipeline tests, unrelated to the assessment bids.

The documents are generated with PyMuPDF so the real parsers, chunker and indexer run on them.
``EvidenceModel`` is a deterministic stand-in for the LLM: it returns a scripted value only when
that value appears in a passage shown for the field, and cites that passage's id.
"""

import json
import re
from pathlib import Path

import pymupdf

from rfp.agents.llm import ModelRequest, ModelResponse

RFP_FILE = "Harborview_RFP_HC-2026-118.pdf"
ADDENDUM_FILE = "Addendum_1_HC-2026-118.pdf"
LISTING_FILE = "listing.html"

RFP_PAGES = [
    [
        "Request for Proposals",
        "RFP No. HC-2026-118",
        "Harborview County Water Authority",
        "Ultrasonic Water Meter Replacement",
        "Proposals due: March 12, 2026 at 3:00 PM",
        "Proposals must be submitted electronically through the HarborBuy portal.",
    ],
    [
        "Scope of Work",
        "The Authority requires 250 ultrasonic water meters, model UM-40.",
        "Contract term: two (2) years with one (1) optional one-year renewal.",
        "Questions: Dana Whitfield, procurement@harborview-water.example, (555) 010-4477",
    ],
]
ADDENDUM_PAGES = [
    [
        "Addendum No. 1",
        "RFP No. HC-2026-118",
        "The new due date for this RFP will be March 26, 2026 at 3:00 PM.",
        "All other terms remain unchanged.",
    ]
]
LISTING_HTML = """<html><body>
<h1 id="solicitation">Solicitation HC-2026-118</h1>
<p id="title">Ultrasonic Water Meter Replacement</p>
<p id="agency">Issuing agency: Harborview County Water Authority</p>
</body></html>"""

ANSWERS = {
    "Bid Number": "HC-2026-118",
    "Title": "Ultrasonic Water Meter Replacement",
    "Due Date": "March 26, 2026 at 3:00 PM",
    "Bid Submission Type": "submitted electronically through the HarborBuy portal",
    "Term of Bid": "two (2) years with one (1) optional one-year renewal",
    "Model_no": "UM-40",
    "Product": "250 ultrasonic water meters",
    "contact_info": "Dana Whitfield, procurement@harborview-water.example, (555) 010-4477",
    "company_name": "Harborview County Water Authority",
}


def pdf_bytes(pages: list[list[str]]) -> bytes:
    with pymupdf.open() as document:
        for lines in pages:
            page = document.new_page()
            for number, line in enumerate(lines):
                page.insert_text((50, 72 + 24 * number), line, fontsize=10)
        return document.tobytes()


def write_bid(folder: Path) -> Path:
    folder.mkdir(parents=True, exist_ok=True)
    (folder / RFP_FILE).write_bytes(pdf_bytes(RFP_PAGES))
    (folder / ADDENDUM_FILE).write_bytes(pdf_bytes(ADDENDUM_PAGES))
    (folder / LISTING_FILE).write_text(LISTING_HTML, encoding="utf-8")
    return folder


def _flat(text: str) -> str:
    return " ".join(text.split()).casefold()


class EvidenceModel:
    """Scripted answers, each returned only with the id of a shown passage that contains it.

    ``invented`` values are returned regardless of the evidence, citing the first passage, to
    check that the validator rejects them.
    """

    provider = "fake"
    model = "evidence"

    def __init__(self, answers: dict[str, str], invented: dict[str, str] | None = None):
        self.answers = answers
        self.invented = invented or {}
        self.requests: list[ModelRequest] = []

    def complete(self, request: ModelRequest) -> ModelResponse:
        self.requests.append(request)
        if request.purpose.startswith("critic"):
            return self._reply({"supported": True, "reason": "fake critic"})
        fields: dict[str, dict] = {}
        current, evidence_id, first = None, None, None
        for line in request.user.splitlines():
            if line.startswith("Field: "):
                current, evidence_id = line.removeprefix("Field: "), None
                first = None
            elif current and re.match(r"\[[^\]]+\] ", line):
                evidence_id = line[1 : line.index("]")]
                first = first or evidence_id
                if current in self.invented and current not in fields:
                    fields[current] = {
                        "value": self.invented[current],
                        "evidence_ids": [first],
                        "notes": "Scripted invented value.",
                    }
            elif current and evidence_id and current not in fields:
                value = self.answers.get(current)
                if value and _flat(value) in _flat(line):
                    fields[current] = {
                        "value": value,
                        "evidence_ids": [evidence_id],
                        "notes": "Copied from the cited evidence.",
                    }
        for name in re.findall(r"(?m)^Field: (.+)$", request.user):
            fields.setdefault(
                name, {"value": None, "evidence_ids": [], "notes": "Not found in documents"}
            )
        return self._reply({"fields": fields})

    def _reply(self, payload: dict) -> ModelResponse:
        return ModelResponse(json.dumps(payload), self.provider, self.model, 5, 5, 0.0)


class BrokenModel:
    provider = "fake"
    model = "broken"

    def complete(self, request: ModelRequest) -> ModelResponse:
        return ModelResponse("this is not JSON", self.provider, self.model, 1, 1, 0.0)
