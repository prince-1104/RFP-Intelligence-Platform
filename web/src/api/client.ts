import type {
  AuditResult,
  BidExtraction,
  BidRow,
  Capabilities,
  CollectionRow,
  ComparisonResponse,
  EvaluationSnapshot,
  Health,
  JobRecord,
  Overview,
  QAResponse,
  TracePayload,
} from "./types";

export class ApiError extends Error {
  status: number;
  code: string;
  requestId: string;

  constructor(status: number, code: string, message: string, requestId = "") {
    super(message);
    this.status = status;
    this.code = code;
    this.requestId = requestId;
  }
}

export class WorkbenchClient {
  constructor(readonly base = "http://127.0.0.1:8000") {}

  async health(): Promise<Health> {
    return this.get("/api/v1/workbench/health");
  }

  async capabilities(): Promise<Capabilities> {
    return this.get("/api/v1/workbench/capabilities");
  }

  async collections(): Promise<CollectionRow[]> {
    const body = await this.get<{ collections: CollectionRow[] }>("/api/v1/workbench/collections");
    return body.collections;
  }

  async bids(collection: string): Promise<{ collection: string; physical: string; bids: BidRow[] }> {
    const query = new URLSearchParams({ collection });
    return this.get(`/api/v1/workbench/bids?${query}`);
  }

  async search(body: {
    query: string;
    collection: string;
    mode: string;
    bid_ids: string[];
    top_k: number;
  }): Promise<Record<string, unknown>> {
    return this.post("/api/v1/workbench/search", body);
  }

  async ask(body: {
    bid_id: string;
    question: string;
    collection: string;
    mode: string;
    top_k: number;
  }): Promise<QAResponse & { trace_id_path?: string }> {
    return this.post("/api/v1/workbench/ask", body);
  }

  async compare(body: {
    bids: string[];
    question: string;
    collection: string;
  }): Promise<ComparisonResponse & { trace_id_path?: string }> {
    return this.post("/api/v1/workbench/compare", body);
  }

  async ingest(body: { confirm: true; collection: string; bid_id: string; path: string }): Promise<JobRecord> {
    return this.post("/api/v1/workbench/ingest", body);
  }

  async upload(filename: string, bytes: Blob): Promise<{ path: string; bytes: number }> {
    const query = new URLSearchParams({ filename });
    const response = await fetch(`${this.base}/api/v1/workbench/uploads?${query}`, {
      method: "POST",
      body: bytes,
      headers: { "Content-Type": "application/octet-stream" },
    });
    return this.read(response);
  }

  async extract(body: { confirm: true; collection: string; bid_id: string }): Promise<JobRecord> {
    return this.post("/api/v1/workbench/extract", body);
  }

  async extractions(): Promise<{ run: string; bid_id: string; source: string }[]> {
    const body = await this.get<{ extractions: { run: string; bid_id: string; source: string }[] }>(
      "/api/v1/workbench/extractions",
    );
    return body.extractions;
  }

  async extractionFile(source: string): Promise<BidExtraction> {
    const query = new URLSearchParams({ source });
    return this.get(`/api/v1/workbench/extractions/file?${query}`);
  }

  async trace(id: string): Promise<TracePayload> {
    return this.get(`/api/v1/workbench/traces/${encodeURIComponent(id)}`);
  }

  async evaluation(): Promise<EvaluationSnapshot[]> {
    const body = await this.get<{ snapshots: EvaluationSnapshot[] }>("/api/v1/workbench/evaluation");
    return body.snapshots;
  }

  async runEvaluation(collection: string): Promise<JobRecord> {
    return this.post("/api/v1/workbench/evaluation", { confirm: true, collection });
  }

  async audit(): Promise<AuditResult> {
    return this.get("/api/v1/workbench/audit");
  }

  async overview(): Promise<Overview> {
    return this.get("/api/v1/workbench/overview");
  }

  async job(id: string): Promise<JobRecord> {
    return this.get(`/api/v1/workbench/jobs/${id}`);
  }

  private get<T>(path: string): Promise<T> {
    return this.send(path);
  }

  private post<T>(path: string, body: unknown): Promise<T> {
    return this.send(path, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(body),
    });
  }

  private async send<T>(path: string, init?: RequestInit): Promise<T> {
    const response = await fetch(`${this.base}${path}`, init);
    return this.read(response);
  }

  private async read<T>(response: Response): Promise<T> {
    const payload = (await response.json()) as T & { error?: { code: string; message: string; request_id?: string } };
    if (!response.ok) {
      const error = payload.error;
      throw new ApiError(
        response.status,
        error?.code ?? "request_failed",
        error?.message ?? "The workbench request failed.",
        error?.request_id ?? "",
      );
    }
    return payload;
  }
}

export const api = new WorkbenchClient(import.meta.env.VITE_WORKBENCH_URL || "http://127.0.0.1:8000");
