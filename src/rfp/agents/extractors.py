"""Specialist field groups and the prompt that may only cite retrieved evidence ids."""

import json
import re
from dataclasses import dataclass

from rfp.agents.llm import ModelRequest, parse_json_object
from rfp.agents.schemas import REQUIRED_FIELDS, DraftField, EvidenceHit, ExtractionConfig
from rfp.agents.validator import PARAPHRASE_FIELDS
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
        "Write a 3 to 6 sentence summary using only the evidence. Cover the scope, the "
        "products and quantities, key specifications, the due date, the submission method "
        "and material requirements when the evidence or validated values state them. Every "
        "number, date and identifier in the summary must appear in the cited evidence."
    ),
}


FIELD_DEFINITIONS = {
    "Bid Number": (
        "The solicitation number printed on the issuing agency's own RFP or bid document. "
        "Prefer it over a listing site's internal reference number."
    ),
    "Title": (
        "The name of this solicitation, such as the cover or listing title. A form or "
        "document-type heading alone is not a title. On a bid listing page the title often "
        "follows the solicitation number."
    ),
    "Due Date": "Date and time responses must be received by.",
    "Bid Submission Type": (
        "How responses must be delivered: an online portal or system, email, or sealed hard "
        "copy. A procurement method or regulation name is not a submission type."
    ),
    "Term of Bid": "Length of the resulting contract and any renewal options.",
    "Pre Bid Meeting": (
        "Date, time, place and whether attendance is mandatory, if a meeting is held."
    ),
    "Installation": (
        "Whether the vendor must install, deploy, set up or image the goods. A statement "
        "that deliveries and deployments must include a service counts."
    ),
    "Bid Bond Requirement": (
        "Required bid bond or bid security, or a statement that none is needed."
    ),
    "Delivery Date": (
        "When the goods must be delivered: a date, or a schedule such as a number of days "
        "after award. A proposal due date is not a delivery date, even inside a product table."
    ),
    "Payment Terms": "When and how the buyer pays invoices.",
    "Any Additional Documentation Required": (
        "Affidavits, forms, certificates or attachments the response or award requires. "
        "An instruction to tick a box on a form is not a separate document."
    ),
    "MFG for Registration": (
        "The manufacturer the bidder must be authorized by, registered with or a reseller for. "
        "When no manufacturer is named, the manufacturer or authorized-reseller qualification "
        "the bidder must meet, such as factory authorization or manufacturer certifications "
        "a reseller must submit. A section heading alone is not a requirement. "
        "Business-entity checkboxes are not manufacturer requirements."
    ),
    "Contract or Cooperative to use": (
        "The master contract, cooperative or purchasing agreement this purchase uses."
    ),
    "Model_no": (
        "Manufacturer model name or number of the requested product, such as a series name "
        "and number."
    ),
    "Part_no": (
        "Part, SKU or item number that identifies the requested product, not the list of "
        "component codes inside one product's configuration."
    ),
    "Product": (
        "The goods or services being bought, with quantities when stated: each distinct item "
        "with its own quantity, unit and model. A list of device categories is acceptable "
        "when no single product is named. An example is not a requested item."
    ),
    "contact_info": "Name, email and phone of the procurement contact or buyer.",
    "company_name": "The organization issuing the solicitation.",
    "Bid Summary": "A short summary of scope, key dates and submission requirements.",
    "Product Specification": (
        "Required technical specifications such as processor, memory, storage, display, "
        "battery and warranty. When several device types are specified, list the stated "
        "minimums for each under the device or tier name, so each warranty stays with its "
        "device."
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
                QueryStep(
                    "closing date and time on the bid listing",
                    RetrievalMode.hybrid_rerank,
                    (DocumentType.bid_page,),
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
            _steps(
                "affidavits forms and certificates that must be submitted with the response",
                "required forms attachments and affidavits",
            ),
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
        FieldSpec(
            "Product",
            "product",
            _steps("requested product name and quantity", "item description quantity unit"),
        ),
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
        FieldSpec(
            "company_name",
            "identity",
            (
                QueryStep("issuing organization or agency name"),
                QueryStep("organization soliciting offers or proposals"),
            ),
        ),
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
                QueryStep(
                    "minimum requirements for each device type or tier",
                    RetrievalMode.hybrid_rerank,
                    hard=True,
                ),
            ),
        ),
    ]
    catalog = {spec.name: spec for spec in specs}
    if tuple(catalog) != REQUIRED_FIELDS or set(FIELD_DEFINITIONS) != set(REQUIRED_FIELDS):
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
    if attempt > 0 and "heading" in text:
        steps.insert(
            0,
            QueryStep(
                spec.steps[0].query,
                RetrievalMode.hybrid_rerank,
                (DocumentType.bid_page,),
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


STOPWORDS = frozenset(
    {"the", "and", "for", "with", "that", "this", "from", "what", "when", "whether", "must", "or"}
)


def focus_terms(spec: FieldSpec) -> list[str]:
    words = re.findall(r"[a-z][a-z-]{3,}", " ".join(step.query for step in spec.steps).casefold())
    return list(dict.fromkeys(word[:6] for word in words if word not in STOPWORDS))


LIST_FIELDS = frozenset(
    {"Product", "Product Specification", "Any Additional Documentation Required", "Bid Summary"}
)
"""Fields whose answer is several distinct requirements, often spread over passages."""
SUMMARY_RELATION_INSTRUCTION = (
    "Each validated value is a separate fact. Keep every requirement, condition, document "
    "and deadline attached to what its own passage attaches it to: do not make one field's "
    "items part of another (for example, documents required with the proposal are not "
    "invoice contents unless a passage says so). Write each requirement as its own clause."
)
LIST_INSTRUCTION = (
    "This field can hold several distinct requirements spread over passages. List each one "
    "with the device, item or condition it applies to; keep different quantities, units, "
    "model or part numbers and warranty terms separate; and cite every passage you use. "
    "Return the value as one string, items separated by semicolons."
)
DOCUMENT_INSTRUCTION = (
    "Include only documents the text explicitly requires (must, shall, required, to be "
    "submitted or presented). A form named in a vendor question, an example, or the "
    "wording of a blank form is not a requirement; keep the condition of a conditional one."
)


def _table_row(line: str) -> str:
    """A serialized table row as its non-empty cells separated by ' | '."""
    try:
        cells = json.loads(line)
    except ValueError:
        return line
    if not isinstance(cells, list):
        return line
    kept = [" ".join(str(cell).split()) for cell in cells if cell not in (None, "")]
    return " | ".join(cell for cell in kept if cell)


def compact(text: str) -> str:
    """Prompt text for a passage: table rows lose empty cells and JSON escapes.

    Table chunks store each row as a JSON list padded with nulls, which can push the
    item and quantity cells outside the excerpt window. Validation uses the stored text.
    """
    lines = [
        _table_row(line.strip()) if line.strip().startswith("[") else line
        for line in text.split("\n")
    ]
    return "\n".join(line for line in lines if line.strip())


def _excerpt(text: str, limit: int, terms: list[str] | None = None) -> str:
    """The passage, or the window of `limit` characters that mentions the most field terms.

    Long chunks often hold the answer after the opening lines, so a head-only cut can
    hide it from the model. The validator still checks the full passage.
    """
    collapsed = " ".join(compact(text).split())
    if len(collapsed) <= limit:
        return collapsed
    lowered = collapsed.casefold()
    best, best_score = 0, -1
    if terms:
        hits = sorted(
            match.start() for term in terms for match in re.finditer(re.escape(term), lowered)
        )
        for start in range(0, len(collapsed) - limit + 100, 100):
            score = sum(1 for position in hits if start <= position < start + limit - 6)
            if score > best_score:
                best, best_score = start, score
    start = min(best, max(0, len(collapsed) - (limit - 6)))
    body = collapsed[start : start + limit - 6]
    return ("..." if start else "") + body + ("..." if start + len(body) < len(collapsed) else "")


def extraction_request(
    group: str,
    evidence: dict[str, list[EvidenceHit]],
    names: list[str],
    attempt: int,
    reasons: dict[str, list[str]],
    config: ExtractionConfig,
    rejected: dict[str, str] | None = None,
    facts: list[tuple[str, str, str]] | None = None,
) -> ModelRequest:
    blocks = []
    for name in names:
        hits = evidence.get(name) or []
        lines = [f"Field: {name}", f"Definition: {FIELD_DEFINITIONS[name]}"]
        if name in LIST_FIELDS and name != "Bid Summary":
            lines.append(LIST_INSTRUCTION)
        if name == "Any Additional Documentation Required":
            lines.append(DOCUMENT_INSTRUCTION)
        if name == "Bid Summary" and facts:
            lines.append(
                "Validated field values (cite the id in brackets when the summary uses one):"
            )
            lines.extend(f"[{evidence_id}] {field}: {value}" for field, value, evidence_id in facts)
            lines.append(SUMMARY_RELATION_INSTRUCTION)
        if reasons.get(name):
            lines.append("Previous rejection: " + "; ".join(reasons[name]))
            if rejected and rejected.get(name):
                lines.append(
                    f"Rejected value: {rejected[name][:300]!r}. Do not return it again unchanged."
                )
            if any("does not support" in reason for reason in reasons[name]):
                lines.append(
                    "State the complete value from one passage below, keeping its numbers and "
                    "key words; do not add facts."
                    if name in PARAPHRASE_FIELDS
                    else "Copy the value word for word from one passage below; do not "
                    "paraphrase, summarize or add words."
                )
        if not hits:
            lines.append("Evidence: none")
        for hit in hits[:8]:
            source = hit.sources[0]
            where = f"page {source.page}" if source.page else f"html #{source.html_element_id}"
            lines.append(
                f"[{hit.evidence_id}] {source.file} {where} type={source.document_type.value} "
                f"addendum={source.addendum_number} rank={hit.rank} mode={hit.mode}"
            )
            limit = config.list_excerpt_chars if name in LIST_FIELDS else config.excerpt_chars
            lines.append(_excerpt(hit.text, limit, focus_terms(CATALOG[name])))
        blocks.append("\n".join(lines))
    user = (
        f"Attempt: {attempt}\n"
        "Return a JSON object with a fields object whose keys are exactly "
        f"{json.dumps(names)}. For each field use value (string or null), "
        "evidence_ids (a list of the ids, from any field in this request, whose text states "
        "the value), and notes. "
        + (
            "Write the summary in your own sentences; do not copy a passage. "
            if group == "summary"
            else "Copy values verbatim from the evidence; a list may join items copied from "
            "several passages. "
        )
        + "Use null and notes 'Not found in documents' when the evidence does not state the value. "
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
        json_object=True,
    )


def critic_request(
    name: str, draft: DraftField, hits: list[EvidenceHit], config: ExtractionConfig
) -> ModelRequest:
    cited = [hit for hit in hits if hit.evidence_id in draft.evidence_ids]
    words = re.findall(r"[a-z0-9][a-z0-9@.-]{3,}", (draft.value or "").casefold())
    terms = list(dict.fromkeys(word[:8] for word in words)) or focus_terms(CATALOG[name])
    body = "\n".join(
        f"[{hit.evidence_id}] {_excerpt(hit.text, config.excerpt_chars, terms)}" for hit in cited
    )
    return ModelRequest(
        purpose=f"critic:{name}",
        system=(
            "You check whether the evidence supports the value. "
            'Respond with JSON {"supported": true or false, "reason": "..."}. '
            "Rank and confidence are not support."
        ),
        user=f"Field: {name}\nValue: {draft.value}\nEvidence:\n{body}",
        json_object=True,
    )


def _key(name: str) -> str:
    return re.sub(r"[^a-z0-9]", "", name.casefold())


def evidence_prefix(name: str) -> str:
    return re.sub(r"[^a-z0-9]+", "-", name.casefold()).strip("-")


def _evidence_ids(name: str, ids: object) -> list[str]:
    """String ids as given; a bare number N means the field's own evidence id N.

    Models sometimes return the list as one string such as "[due-date-1, due-date-3]".
    """
    if isinstance(ids, str):
        ids = re.split(r"[\s,;]+", ids)
    if not isinstance(ids, list):
        return []
    result: list[str] = []
    for item in ids:
        if isinstance(item, int) and not isinstance(item, bool):
            item = str(item)
        if not isinstance(item, str):
            continue
        item = item.strip().strip("[]\"'")
        if item:
            result.append(f"{evidence_prefix(name)}-{item}" if item.isdigit() else item)
    return result


PLACEHOLDERS = frozenset(
    {"not found in documents", "not found", "not stated", "n/a", "none", "null"}
)


def _scalars(raw: dict) -> list[str]:
    return [str(item) for item in raw.values() if isinstance(item, str | int | float)]


def _value(raw: object) -> str | None:
    """A string value; an object or list reply is joined from its non-empty, stated parts.

    A list of objects (one per item) becomes one part per object, its scalar values
    joined by spaces, so the text stays checkable against the evidence.
    """
    if isinstance(raw, dict):
        raw = _scalars(raw)
    if isinstance(raw, list):
        parts = [
            " ".join(_scalars(item)).strip() if isinstance(item, dict) else str(item).strip()
            for item in raw
        ]
        raw = ", ".join(
            part for part in parts if part and part.casefold().rstrip(".") not in PLACEHOLDERS
        )
    if not isinstance(raw, str) or raw.strip().casefold() in {"", "null", "none"}:
        return None
    return " ".join(raw.split())


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
        value = _value(raw.get("value"))
        ids = raw.get("evidence_ids")
        notes = raw.get("notes")
        drafts[name] = DraftField(
            name=name,
            value=value,
            evidence_ids=_evidence_ids(name, ids),
            notes=notes.strip() if isinstance(notes, str) else "",
            model_confidence=raw.get("confidence")
            if isinstance(raw.get("confidence"), (int, float))
            else None,
        )
    return drafts


def parse_drafts(names: list[str], text: str) -> dict[str, DraftField]:
    return drafts_from_model(names, parse_json_object(text))
