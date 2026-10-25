export type Health = {
  status: string;
  qdrant: string;
  postgres: string;
  redis: string;
  model_cache: string;
  qdrant_url: string;
};

export type Capabilities = {
  modes: string[];
  fields: string[];
  compare_min: number;
  compare_max: number;
  qa_statuses: string[];
  comparison_statuses: string[];
  relations: string[];
  page_furniture: string;
  table_prose: string;
  confidence: string;
};

export type CollectionRow = { base: string; physical: string };

export type BidDocument = {
  document_id: string;
  file: string;
  relative_path: string;
  doc_type: string | null;
  points: number;
};

export type BidRow = {
  bid_uuid: string;
  label: string;
  documents: BidDocument[];
  points: number;
};

export type Citation = {
  citation_id: string;
  evidence_id: string;
  document: string;
  page: number | null;
  page_end?: number | null;
  html_element_id?: string | null;
  document_type: string;
  excerpt: string;
};

export type Claim = { text: string; citation_ids: string[] };

export type Completeness = {
  list_question: boolean;
  candidates: string[];
  covered: string[];
  unresolved: { item: string; reason: string }[];
  omitted: string[];
  exhaustive: false;
};

export type QAResponse = {
  bid_id: string;
  question: string;
  answer: string;
  status: "answered" | "not_found" | "conflict";
  claims: Claim[];
  citations: Citation[];
  completeness: Completeness | null;
  limitations: string[];
  confidence: number | null;
  validation: { passed: boolean; attempts: number; reasons: string[] };
  trace_id: string;
  evidence_count: number;
};

export type BidFinding = {
  bid_id: string;
  status: "answered" | "not_found";
  answer: string;
  claims: Claim[];
  citations: Citation[];
  completeness: Completeness | null;
  limitations: string[];
  evidence_count: number;
};

export type ComparisonResponse = {
  mode: "comparison";
  question: string;
  bid_ids: string[];
  status: "answered" | "partial" | "not_found";
  answer: string;
  findings: BidFinding[];
  aspects: { aspect: string; relation: "identical" | "different" | "one_sided"; claims: Record<string, string[]> }[];
  missing_evidence: string[];
  limitations: string[];
  trace_id: string;
  evidence_count: Record<string, number>;
};

export type FieldOutput = {
  value: string | null;
  sources: { file: string; page?: number | null; html_element_id?: string | null }[];
  confidence: number;
  notes: string;
};

export type AddendumChange = {
  field: string;
  original_value: string | null;
  revised_value: string | null;
  addendum_number: number | null;
  explanation: string;
  status: "applied" | "superseded" | "conflict" | "not_applied" | null;
  change_type: string;
};

export type BidExtraction = {
  bid_id: string;
  fields: Record<string, FieldOutput>;
  addendum_changes: AddendumChange[];
  validation: { passed: number; failed: number; unresolved: number; not_found: number };
  source?: string;
  kind?: string;
};

export type TraceEvent = {
  sequence: number;
  agent: string;
  task: string;
  field?: string | null;
  started_at?: string;
  ended_at?: string;
  duration_ms?: number;
  query?: string | null;
  mode?: string | null;
  top_k?: number | null;
  result_count?: number | null;
  decision?: string | null;
  reasons?: string[];
  error?: string | null;
  provider?: string | null;
  model?: string | null;
};

export type TracePayload = {
  source?: string;
  trace_id?: string;
  events?: TraceEvent[];
  status?: string;
  outcome?: string;
};

export type ModeMetrics = {
  "recall@5": number | null;
  "mrr@10": number | null;
  "ndcg@10": number | null;
  cases?: number | null;
  scored_cases?: number | null;
};

export type EvaluationSnapshot = {
  source: string;
  dataset: string | null;
  collection: string | null;
  modes: Record<string, ModeMetrics>;
  case_count: number;
  kind: string;
};

export type JobRecord = {
  id: string;
  kind: string;
  status: "queued" | "running" | "succeeded" | "failed";
  created_at: string;
  ended_at: string | null;
  error: { code: string; message: string } | null;
  result: Record<string, unknown> | null;
};

export type AuditResult = {
  available: boolean;
  problem_count: number | null;
  detail?: string;
  extractions?: { run: string; bid: string; problems: string[] }[];
  qa?: { problems: string[] };
  retrieval?: { problems: string[] };
  matrix?: { source: string; label: string; text: string };
};

export type Overview = {
  collections: CollectionRow[] | null;
  last_ingest: JobRecord | null;
  evaluation: EvaluationSnapshot[];
  extractions: { run: string; bid_id: string; source: string; validation: BidExtraction["validation"]; addendum_changes: number; kind: string }[];
  qa: { source: string; kind: string; summary?: Record<string, unknown>; examples: { id?: string; bid_id?: string; question?: string; status?: string; answer?: string }[] } | null;
};

export type ApiFailure = { error: { code: string; message: string; request_id: string } };
