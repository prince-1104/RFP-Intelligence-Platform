"""Specialist field groups and the prompt that may only cite retrieved evidence ids."""

import json
import re
from dataclasses import dataclass

from rfp.agents.llm import ModelRequest, parse_json_object
from rfp.agents.schemas import REQUIRED_FIELDS, DraftField, EvidenceHit, ExtractionConfig
from rfp.schemas.parsed import DocumentType
from rfp.schemas.search import RetrievalMode

GROUP_ORDER = ("identity", "logistics", "commercial", "product", "summary")

GROUP_INSTRUCTIONS = {
    "identity": (
        "Bid Number is the official solicitation number, Title the bid title, company_name the "
        "issuing organization and contact_info the procurement contact. "
        "Copy identifiers, names, emails and phone numbers verbatim from the evidence."
    ),
    "logistics": (
        "Due Date is the submission deadline, Bid Submission Type the submission method, Term of "
        "Bid the contract term, then Pre Bid Meeting, Installation and Delivery Date. When an "
        "addendum explicitly states a new due date, use that statement rather "
        "than the original date, and mention the original date in notes. Do not treat a later "
        "addendum as an override unless its text says what changed."
    ),
    "commercial": (
        "Bid Bond Requirement, Payment Terms, Any Additional Documentation Required (extra forms), "
        "MFG for Registration (manufacturer registration) and Contract or Cooperative to use. "
        "A generic insurance or bond clause is not an amount. Use null when no amount, "
        "percentage or explicit 'not required' statement is present."
    ),
    "product": (
        "Model_no, Part_no (part or SKU numbers), Product (product and quantity) and Product "
        "Specification (CPU, RAM, storage, display, battery and warranty). Copy them "
        "verbatim. Specifications may be in a separate specs document rather than the main RFP."
    ),
    "summary": (
        "Write a 3 to 6 sentence summary using only the evidence. Every number, date and "
        "identifier in the summary must appear in the cited evidence."
    ),
}


@dataclass(frozen=True)
class QueryStep:
    query: str
    mode: RetrievalMode = RetrievalMode.hybrid_rerank
    doc_types: tuple[DocumentType, ...] = ()
    addendum_numbers: tuple[int, ...] = ()
    hard: bool = False


@dataclass(frozen=True)
class FieldSpec:
    name: str
    group: str
    steps: tuple[QueryStep, ...]

    @property
    def hard(self) -> bool:
        return any(step.hard for step in self.steps)


def _steps(
    *queries: str, hard: bool = False, doc_types: tuple[DocumentType, ...] = ()
) -> tuple[QueryStep, ...]:
    mode = RetrievalMode.hybrid_rerank
    primary = tuple(QueryStep(query, mode, doc_types, hard=hard) for query in queries[:1])
    extra = tuple(
        QueryStep(query, RetrievalMode.sparse, doc_types, hard=True) for query in queries[1:]
    )
    return primary + extra


def _catalog() -> dict[str, FieldSpec]:
    specs = [
        FieldSpec("Bid Number", "identity", _steps("official solicitation or RFP number")),
        FieldSpec(
            "Title",
            "identity",
            _steps(
                "full title of the bid or request for proposal",
                "request for proposal solicitation title",
            ),
        ),
        FieldSpec(
            "Due Date",
            "logistics",
            (
                QueryStep("final proposal submission deadline after addendums", hard=True),
                QueryStep(
                    "new due date for this RFP",
                    RetrievalMode.sparse,
                    (DocumentType.addendum,),
                    hard=True,
                ),
                QueryStep(
                    "solicitation due date on the original RFP",
                    RetrievalMode.hybrid_rerank,
                    (DocumentType.rfp,),
                    hard=True,
                ),
            ),
        ),
        FieldSpec(
            "Bid Submission Type",
            "logistics",
            _steps("how the bid or proposal must be submitted, portal or sealed"),
        ),
        FieldSpec(
            "Term of Bid",
            "logistics",
            _steps("contract term duration and renewal options"),
        ),
        FieldSpec(
            "Pre Bid Meeting",
            "logistics",
            _steps(
                "pre-proposal or pre-bid meeting date time location and whether it is mandatory"
            ),
        ),
        FieldSpec(
            "Installation",
            "logistics",
            _steps("whether installation deployment imaging or white glove services are required"),
        ),
        FieldSpec(
            "Bid Bond Requirement",
            "commercial",
            _steps(
                "required bid bond or bid security amount or percentage, or that none is required"
            ),
        ),
        FieldSpec(
            "Delivery Date",
            "logistics",
            (
                QueryStep("required delivery date or delivery window after award", hard=True),
                QueryStep(
                    "When is the contract expected to be awarded?",
                    RetrievalMode.sparse,
                    hard=True,
                ),
                QueryStep(
                    "anticipated approval or award date", RetrievalMode.hybrid_rerank, hard=True
                ),
            ),
        ),
        FieldSpec(
            "Payment Terms", "commercial", _steps("payment terms net days and invoicing conditions")
        ),
        FieldSpec(
            "Any Additional Documentation Required",
            "commercial",
            _steps("affidavits forms certificates insurance and W-9 required with the response"),
        ),
        FieldSpec(
            "MFG for Registration",
            "commercial",
            _steps("manufacturer the bidder must be registered or authorized with"),
        ),
        FieldSpec(
            "Contract or Cooperative to use",
            "commercial",
            _steps("cooperative purchasing agreement or state contract vehicle"),
        ),
        FieldSpec(
            "Model_no", "product", _steps("model number of the requested product", hard=True)
        ),
        FieldSpec(
            "Part_no", "product", _steps("part number or SKU of the requested product", hard=True)
        ),
        FieldSpec("Product", "product", _steps("requested product name and quantity")),
        FieldSpec(
            "contact_info",
            "identity",
            (
                QueryStep("procurement contact name email and phone", hard=True),
                QueryStep("buyer name and email address", hard=True),
                QueryStep(
                    "Who is the buyer for this solicitation and how can they be contacted?",
                    RetrievalMode.sparse,
                    hard=True,
                ),
            ),
        ),
        FieldSpec("company_name", "identity", _steps("issuing organization or agency name")),
        FieldSpec(
            "Bid Summary",
            "summary",
            _steps(
                "scope of the procurement, deadlines, products and submission requirements",
                hard=True,
            ),
        ),
        FieldSpec(
            "Product Specification",
            "product",
            (
                QueryStep(
                    "technical specifications CPU RAM storage display battery and warranty",
                    hard=True,
                ),
                QueryStep(
                    "RAM memory display screen size and battery specifications",
                    RetrievalMode.sparse,
                    (DocumentType.specs,),
                    hard=True,
                ),
                QueryStep(
                    "laptop or device specifications for memory display and battery",
                    RetrievalMode.hybrid_rerank,
                    hard=True,
                ),
            ),
        ),
    ]
    catalog = {spec.name: spec for spec in specs}
    if tuple(catalog) != REQUIRED_FIELDS:
        raise RuntimeError("Field catalog drifted from the required 20 fields")
    return catalog


CATALOG = _catalog()


def fields_in_group(group: str) -> list[FieldSpec]:
    return [CATALOG[name] for name in REQUIRED_FIELDS if CATALOG[name].group == group]


def steps_for(spec: FieldSpec, attempt: int, reasons: list[str]) -> tuple[QueryStep, ...]:
    steps = list(spec.steps)
    text = " ".join(reasons).casefold()
    if attempt > 0 and ("addendum" in text or "override" in text or "conflict" in text):
        steps.insert(
            0,
            QueryStep(
                f"{spec.name} revised by addendum",
                RetrievalMode.sparse,
                (DocumentType.addendum,),
                hard=True,
            ),
        )
    if attempt > 1 and reasons:
        steps.append(
            QueryStep(
                spec.steps[0].query,
                RetrievalMode.dense,
                spec.steps[0].doc_types,
                hard=True,
            )
        )
    return tuple(steps)


def _excerpt(text: str, limit: int) -> str:
    collapsed = " ".join(text.split())
    return collapsed if len(collapsed) <= limit else collapsed[: limit - 3] + "..."


def extraction_request(
    group: str,
    evidence: dict[str, list[EvidenceHit]],
    names: list[str],
    attempt: int,
    reasons: dict[str, list[str]],
    config: ExtractionConfig,
) -> ModelRequest:
    blocks = []
    for name in names:
        hits = evidence.get(name) or []
        lines = [f"Field: {name}"]
        if reasons.get(name):
            lines.append("Previous rejection: " + "; ".join(reasons[name]))
        if not hits:
            lines.append("Evidence: none")
        for hit in hits[:8]:
            source = hit.sources[0]
            where = f"page {source.page}" if source.page else f"html #{source.html_element_id}"
            lines.append(
                f"[{hit.evidence_id}] {source.file} {where} type={source.document_type.value} "
                f"addendum={source.addendum_number} rank={hit.rank} mode={hit.mode}"
            )
            lines.append(_excerpt(hit.text, config.excerpt_chars))
        blocks.append("\n".join(lines))
    user = (
        f"Attempt: {attempt}\n"
        "Return a JSON object with a fields object whose keys are exactly "
        f"{json.dumps(names)}. For each field use value (string or null), "
        "evidence_ids (the ids listed under that field whose text states the value), and notes. "
        "Copy values verbatim from the evidence. "
        "Use null and notes 'Not found in documents' when the evidence does not state the value. "
        "Do not invent citations, pages, or facts.\n\n" + "\n\n".join(blocks)
    )
    return ModelRequest(
        purpose=f"extract:{group}",
        system=(
            "You extract RFP fields from supplied evidence only. "
            + GROUP_INSTRUCTIONS[group]
            + " Respond with JSON only."
        ),
        user=user,
    )


def critic_request(
    name: str, draft: DraftField, hits: list[EvidenceHit], config: ExtractionConfig
) -> ModelRequest:
    cited = [hit for hit in hits if hit.evidence_id in draft.evidence_ids]
    body = "\n".join(
        f"[{hit.evidence_id}] {_excerpt(hit.text, config.excerpt_chars)}" for hit in cited
    )
    return ModelRequest(
        purpose=f"critic:{name}",
        system=(
            "You check whether the evidence supports the value. "
            'Respond with JSON {"supported": true or false, "reason": "..."}. '
            "Rank and confidence are not support."
        ),
        user=f"Field: {name}\nValue: {draft.value}\nEvidence:\n{body}",
    )


def _key(name: str) -> str:
    return re.sub(r"[^a-z0-9]", "", name.casefold())


def drafts_from_model(names: list[str], payload: dict) -> dict[str, DraftField]:
    raw_fields = payload.get("fields")
    if not isinstance(raw_fields, dict):
        raise ValueError("fields")
    loose = {_key(key): value for key, value in raw_fields.items() if isinstance(key, str)}
    drafts: dict[str, DraftField] = {}
    for name in names:
        raw = raw_fields.get(name, loose.get(_key(name)))
        if not isinstance(raw, dict):
            drafts[name] = DraftField(name=name, value=None, notes="Model omitted this field")
            continue
        raw_value = raw.get("value")
        if not isinstance(raw_value, str) or raw_value.strip().casefold() in {"", "null"}:
            value = None
        else:
            value = " ".join(raw_value.split())
        ids = raw.get("evidence_ids")
        notes = raw.get("notes")
        drafts[name] = DraftField(
            name=name,
            value=value,
            evidence_ids=[item for item in ids if isinstance(item, str)]
            if isinstance(ids, list)
            else [],
            notes=notes.strip() if isinstance(notes, str) else "",
            model_confidence=raw.get("confidence")
            if isinstance(raw.get("confidence"), (int, float))
            else None,
        )
    return drafts


def parse_drafts(names: list[str], text: str) -> dict[str, DraftField]:
    return drafts_from_model(names, parse_json_object(text))
