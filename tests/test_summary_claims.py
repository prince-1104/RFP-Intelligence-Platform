"""Phase E: a summary's claims, and the links it draws between facts, must be stated by the
cited passages. Synthetic passages only; this is not evidence of live-model accuracy."""

import uuid

from rfp.agents.extractors import SUMMARY_RELATION_INSTRUCTION, extraction_request
from rfp.agents.schemas import DraftField, EvidenceHit, ExtractionConfig, SourceRef
from rfp.agents.validator import summary_claim_problems, validate_field
from rfp.schemas.parsed import DocumentType

BID = uuid.UUID("11111111-2222-5333-8444-555555555555")

REQUIREMENTS = (
    "General Requirements:\n\n"
    "5. The Vendor must submit a Lead Disclosure Form with the proposal.\n\n"
    "6. Delivery within 30 days of award.\n\n"
    "Billing Instructions:\n\n"
    "1. Invoices shall be submitted within 15 days of delivery and shall include at a "
    "minimum the following:\n"
    "• Purchase order number.\n"
    "• Signed delivery receipt.\n\n"
    "2. Email invoices to the accounts office."
)
WARRANTY = (
    "Warranty Requirements\n\n"
    "1. Manufacturer warranty extended for all units - 4 years\n\n"
    "Warranty certificate to be presented upon award"
)
SCOPE = (
    "The Agency requests proposals for 25 Acme Notebook 14 laptops. Proposals are due 03/02/2031."
)


def _hit(evidence_id, text, page):
    return EvidenceHit(
        evidence_id=evidence_id,
        bid_id=BID,
        text=text,
        rank=1,
        mode="hybrid_rerank",
        final_score=0.5,
        sources=[
            SourceRef(
                file="rfp.pdf",
                relative_path="rfp.pdf",
                document_type=DocumentType.rfp,
                page=page,
                bid_id=BID,
            )
        ],
    )


def _summary(value, *texts):
    hits = [_hit(f"s{index}", text, index + 1) for index, text in enumerate(texts)]
    draft = DraftField(name="Bid Summary", value=value, evidence_ids=[h.evidence_id for h in hits])
    return validate_field(draft, hits)


def test_two_separate_requirements_are_not_merged_into_one_relationship():
    value = (
        "Delivery is due within 30 days of award, and invoices must be submitted within 15 "
        "days of delivery, including the Lead Disclosure Form."
    )
    problems = summary_claim_problems(value, [REQUIREMENTS])
    assert any("'the Lead Disclosure Form'" in p and "states them together" in p for p in problems)
    assert not _summary(value, REQUIREMENTS, SCOPE).passed


def test_explicit_invoice_contents_are_accepted():
    value = (
        "Invoices must be submitted within 15 days of delivery, including the purchase order "
        "number and a signed delivery receipt."
    )
    assert summary_claim_problems(value, [REQUIREMENTS]) == []


def test_document_required_independently_of_invoicing_is_accepted():
    value = (
        "The Vendor must submit a Lead Disclosure Form with the proposal. A warranty "
        "certificate is to be presented upon award, and invoices are due within 15 days of "
        "delivery."
    )
    assert summary_claim_problems(value, [REQUIREMENTS, WARRANTY]) == []


def test_summary_with_an_unsupported_relationship_is_rejected():
    value = (
        "The Agency requests proposals for 25 Acme Notebook 14 laptops. Invoices are due "
        "within 15 days of delivery, including a warranty certificate."
    )
    result = _summary(value, SCOPE, REQUIREMENTS, WARRANTY)
    assert not result.passed
    assert any(
        "'a warranty certificate'" in reason and "Invoices are due within 15 days" in reason
        for reason in result.reasons
    )


def test_valid_summary_covering_several_independent_requirements_is_accepted():
    value = (
        "The Agency requests proposals for 25 Acme Notebook 14 laptops, due 03/02/2031. "
        "The Vendor must submit a Lead Disclosure Form with the proposal, and delivery is "
        "required within 30 days of award. Units carry a manufacturer warranty extended to 4 "
        "years, with a warranty certificate presented upon award. Invoices are due within 15 "
        "days of delivery, including the purchase order number."
    )
    result = _summary(value, SCOPE, REQUIREMENTS, WARRANTY)
    assert result.passed, result.reasons


def test_each_material_claim_needs_its_own_cited_support():
    value = (
        "The Agency requests proposals for 25 Acme Notebook 14 laptops, due 03/02/2031. The "
        "Vendor must submit a Lead Disclosure Form with the proposal, and delivery is "
        "required within 30 days of award. Invoices are due within 15 days of delivery, "
        "including the purchase order number and a signed delivery receipt. A warranty "
        "certificate is presented upon award."
    )
    uncited = _summary(value, SCOPE, REQUIREMENTS)
    assert not uncited.passed
    assert uncited.reasons == [
        "Summary claim 'A warranty certificate is presented upon award.' is not supported by "
        "the cited evidence"
    ]
    assert _summary(value, SCOPE, REQUIREMENTS, WARRANTY).passed
    payment = (
        "The Agency requests proposals for 25 Acme Notebook 14 laptops. Payment follows "
        "within net terms agreed by the treasury."
    )
    problems = summary_claim_problems(payment, [SCOPE])
    assert problems == [
        "Summary claim 'Payment follows within net terms agreed by the treasury.' is not "
        "supported by the cited evidence"
    ]


def test_descriptive_lists_and_thousands_separators_are_not_relationships():
    scope = (
        "The Agency requests proposals for computing devices.\n\n"
        "Line 1: Acme Notebook 14, 25,000 Each. Line 2: Acme Dock 3, 1,200 Each.\n\n"
        "Required forms: Lead Disclosure Form and W9 Form.\n\n"
        "Repair certifications must accompany each proposal."
    )
    value = (
        "The Agency requests proposals for computing devices, including 25,000 Acme Notebook "
        "14 units and 1,200 Acme Dock 3 units. Required forms include the Lead Disclosure "
        "Form, and a W9 Form, along with repair certifications."
    )
    assert summary_claim_problems(value, [scope]) == []
    attached = "Repair certifications must accompany each proposal, including a W9 Form."
    assert summary_claim_problems(attached, [scope]) == [
        "Summary links 'a W9 Form' to 'Repair certifications must accompany each proposal', "
        "but no cited passage states them together"
    ]


def test_summary_prompt_keeps_validated_facts_separate():
    hits = {"Bid Summary": [_hit("sum-1", SCOPE, 1)]}
    facts = [("Payment Terms", "Invoices within 15 days", "fact-0")]
    request = extraction_request(
        "summary", hits, ["Bid Summary"], 0, {}, ExtractionConfig(), facts=facts
    )
    assert SUMMARY_RELATION_INSTRUCTION in request.user
    plain = extraction_request("summary", hits, ["Bid Summary"], 0, {}, ExtractionConfig())
    assert SUMMARY_RELATION_INSTRUCTION not in plain.user
