import { ApiError, WorkbenchClient } from "./client";

const client = new WorkbenchClient("http://workbench.test");

function jsonResponse(body: unknown, status = 200): Response {
  return new Response(JSON.stringify(body), {
    status,
    headers: { "Content-Type": "application/json" },
  });
}

afterEach(() => {
  vi.restoreAllMocks();
});

test("a not_found answer is a successful response", async () => {
  vi.stubGlobal("fetch", vi.fn(async () => jsonResponse({
    status: "not_found",
    answer: "Not found in the supplied documents.",
    citations: [],
    trace_id: "t1",
  })));
  const body = await client.ask({
    bid_id: "Bid1",
    question: "Where is the bond?",
    collection: "phasef_cells",
    mode: "hybrid_rerank",
    top_k: 8,
  });
  expect(body.status).toBe("not_found");
  expect(fetch).toHaveBeenCalledWith(
    "http://workbench.test/api/v1/workbench/ask",
    expect.objectContaining({ method: "POST" }),
  );
});

test("a model failure stays an error", async () => {
  vi.stubGlobal("fetch", vi.fn(async () => jsonResponse({
    error: { code: "model_failed", message: "The model call failed.", request_id: "req-9" },
  }, 502)));
  await expect(client.ask({
    bid_id: "Bid1",
    question: "Where is the bond?",
    collection: "phasef_cells",
    mode: "hybrid_rerank",
    top_k: 8,
  })).rejects.toMatchObject({ status: 502, code: "model_failed", requestId: "req-9" });
  await expect(client.ask({
    bid_id: "Bid1",
    question: "Where is the bond?",
    collection: "phasef_cells",
    mode: "hybrid_rerank",
    top_k: 8,
  })).rejects.toBeInstanceOf(ApiError);
});

test("upload sends the file bytes and the filename", async () => {
  vi.stubGlobal("fetch", vi.fn(async () => jsonResponse({ path: "artifacts/workbench/uploads/a/note.pdf", bytes: 2 })));
  const saved = await client.upload("note.pdf", new Blob(["ab"]));
  expect(saved.path).toContain("note.pdf");
  const [url, init] = vi.mocked(fetch).mock.calls[0];
  expect(String(url)).toContain("filename=note.pdf");
  expect(init?.method).toBe("POST");
});
