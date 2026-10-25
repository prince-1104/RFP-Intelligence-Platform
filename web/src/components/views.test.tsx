import { fireEvent, render, screen } from "@testing-library/react";
import { AuditView, ComparisonPanel, ExtractionView, MetricChart, AnswerPanel } from "./views";
import { TraceList } from "./ui";
import type { AuditResult, BidExtraction, ComparisonResponse, EvaluationSnapshot, QAResponse, TraceEvent } from "../api/types";

const citation = {
  citation_id: "c1",
  evidence_id: "ev-22",
  document: "devices.pdf",
  page: 4,
  document_type: "rfp",
  excerpt: "The bid bond is five percent.",
};

test("a citation opens the returned excerpt and evidence id", () => {
  const response: QAResponse = {
    bid_id: "Bid1",
    question: "What is the bond?",
    answer: "Five percent.",
    status: "answered",
    claims: [{ text: "Five percent.", citation_ids: ["c1"] }],
    citations: [citation],
    completeness: null,
    limitations: [],
    confidence: null,
    validation: { passed: true, attempts: 1, reasons: [] },
    trace_id: "qa-1",
    evidence_count: 1,
  };
  render(<AnswerPanel response={response} />);
  fireEvent.click(screen.getByRole("button", { name: "c1" }));
  expect(screen.getByRole("complementary", { name: "Cited excerpt" })).toHaveTextContent("ev-22");
  expect(screen.getByRole("complementary", { name: "Cited excerpt" })).toHaveTextContent("The bid bond is five percent.");
  expect(screen.getByText(/excerpt returned with the citation/i)).toBeInTheDocument();
});

test("conflict is shown as disagreement, not a partial answer", () => {
  const response: QAResponse = {
    bid_id: "Bid1",
    question: "What is the deadline?",
    answer: "The dates disagree.",
    status: "conflict",
    claims: [{ text: "The dates disagree.", citation_ids: ["c1"] }],
    citations: [citation, { ...citation, citation_id: "c2", evidence_id: "ev-23", page: 9 }],
    completeness: null,
    limitations: [],
    confidence: null,
    validation: { passed: true, attempts: 1, reasons: [] },
    trace_id: "qa-2",
    evidence_count: 2,
  };
  render(<AnswerPanel response={response} />);
  expect(screen.getByText("conflict")).toBeInTheDocument();
  expect(screen.getByText(/not a partial answer/i)).toBeInTheDocument();
});

test("comparison shows partial status and the lexical relation", () => {
  const response: ComparisonResponse = {
    mode: "comparison",
    question: "Compare the bond.",
    bid_ids: ["Bid1", "Bid2"],
    status: "partial",
    answer: "Only one bid states a bond.",
    findings: [
      {
        bid_id: "Bid1",
        status: "answered",
        answer: "Five percent.",
        claims: [],
        citations: [citation],
        completeness: null,
        limitations: [],
        evidence_count: 1,
      },
      {
        bid_id: "Bid2",
        status: "not_found",
        answer: "Not found in the supplied documents.",
        claims: [],
        citations: [],
        completeness: null,
        limitations: [],
        evidence_count: 0,
      },
    ],
    aspects: [{ aspect: "bond", relation: "one_sided", claims: {} }],
    missing_evidence: ["Bid2"],
    limitations: [],
    trace_id: "cmp-1",
    evidence_count: { Bid1: 1, Bid2: 0 },
  };
  render(<ComparisonPanel response={response} />);
  expect(screen.getByText("partial")).toBeInTheDocument();
  expect(screen.getByText("one_sided")).toBeInTheDocument();
  expect(screen.getByText(/not proof that two passages are equivalent/i)).toBeInTheDocument();
});

test("extraction keeps field order, heuristic confidence and addendum status", () => {
  const extraction: BidExtraction = {
    bid_id: "Bid1",
    fields: {
      bid_bond: { value: "5%", sources: [{ file: "a.pdf", page: 2 }], confidence: 0.4, notes: "table cell" },
      deadline: { value: null, sources: [], confidence: 0, notes: "no support" },
    },
    addendum_changes: [{
      field: "deadline",
      original_value: "Monday",
      revised_value: null,
      addendum_number: 2,
      explanation: "dates disagree",
      status: "conflict",
      change_type: "replace",
    }],
    validation: { passed: 1, failed: 0, unresolved: 0, not_found: 1 },
    kind: "saved_extraction",
  };
  render(<ExtractionView extraction={extraction} fieldOrder={["bid_bond", "deadline"]} />);
  const cells = screen.getAllByRole("cell").map((cell) => cell.textContent);
  expect(cells.indexOf("bid_bond")).toBeLessThan(cells.indexOf("deadline"));
  expect(screen.getByText("0.40")).toBeInTheDocument();
  expect(screen.getByText(/0–1 heuristic/)).toBeInTheDocument();
  expect(screen.getByText(/does not grade a field correct or incorrect/i)).toBeInTheDocument();
  expect(screen.getByText("conflict")).toBeInTheDocument();
});

test("metrics stay attached to their file and are not called an improvement", () => {
  const snapshot: EvaluationSnapshot = {
    source: "artifacts/eval/retrieval_phase7.json",
    dataset: "retrieval_v1",
    collection: "phasef_cells",
    modes: { hybrid_rerank: { "recall@5": 0.8, "mrr@10": 0.7, "ndcg@10": 0.75 } },
    case_count: 12,
    kind: "saved_evaluation_snapshot",
  };
  render(<MetricChart snapshot={snapshot} />);
  expect(screen.getByText("artifacts/eval/retrieval_phase7.json")).toBeInTheDocument();
  expect(screen.getByText("0.8")).toBeInTheDocument();
  expect(screen.getByText("0.7")).toBeInTheDocument();
  expect(screen.getByText("0.75")).toBeInTheDocument();
  expect(screen.getByText(/not compared as an improvement/i)).toBeInTheDocument();
  expect(screen.getByText(/phasef_cells/)).toBeInTheDocument();
});

test("a trace shows recorded fields and marks the rest as not recorded", () => {
  const events: TraceEvent[] = [{ sequence: 1, agent: "retriever", task: "search", decision: "kept" }];
  render(<TraceList events={events} />);
  expect(screen.getByText("kept")).toBeInTheDocument();
  expect(screen.getAllByText("Not recorded.").length).toBeGreaterThan(0);
});

test("audit shows the script count and the saved matrix label", () => {
  const audit: AuditResult = {
    available: true,
    problem_count: 0,
    matrix: {
      source: "docs/FINAL_REQUIREMENTS_AUDIT.md",
      label: "Saved audit document. The workbench does not re-score these rows.",
      text: "# Matrix\nRow stays as written.",
    },
  };
  render(<AuditView audit={audit} />);
  expect(screen.getByText(/Recorded problems: 0/)).toBeInTheDocument();
  expect(screen.getByText(/does not re-score/)).toBeInTheDocument();
  expect(screen.getByText(/Row stays as written/)).toBeInTheDocument();
});
