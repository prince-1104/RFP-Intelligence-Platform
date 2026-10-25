import { useEffect, useState } from "react";
import { api } from "../api/client";
import type { QAResponse } from "../api/types";
import { Empty, ErrorState } from "../components/ui";
import { AnswerPanel } from "../components/views";
import { useSelection } from "../providers";
import { readHistory, remember, type HistoryItem } from "../session";

type SearchHit = {
  rank: number;
  text: string;
  citation?: { original_file_name?: string; page_start?: number | null; content_hash?: string };
};

export function Ask() {
  const { collection, bid, notify } = useSelection();
  const [question, setQuestion] = useState("");
  const [mode, setMode] = useState("hybrid_rerank");
  const [answer, setAnswer] = useState<(QAResponse & { trace_id_path?: string }) | null>(null);
  const [hits, setHits] = useState<SearchHit[]>([]);
  const [error, setError] = useState("");
  const [history, setHistory] = useState<HistoryItem[]>([]);
  const [busy, setBusy] = useState(false);

  useEffect(() => setHistory(readHistory()), []);

  async function onAsk() {
    setBusy(true);
    setError("");
    try {
      const body = await api.ask({ bid_id: bid, question, collection, mode, top_k: 8 });
      setAnswer(body);
      setHistory(remember({
        question, status: body.status, answer: body.answer, trace_id: body.trace_id, at: new Date().toISOString(),
      }));
    } catch (exc) {
      setAnswer(null);
      setError(exc instanceof Error ? exc.message : "Ask failed.");
    } finally {
      setBusy(false);
    }
  }

  async function onSearch() {
    setError("");
    try {
      const body = await api.search({ query: question, collection, mode, bid_ids: bid ? [bid] : [], top_k: 8 });
      const results = Array.isArray(body.results) ? body.results as SearchHit[] : [];
      setHits(results);
      if (results.length === 0) notify("Search returned no rows.");
    } catch (exc) {
      setHits([]);
      setError(exc instanceof Error ? exc.message : "Search failed.");
    }
  }

  return (
    <>
      <h1>Ask & Search</h1>
      <p className="lede">One bid at a time. Status is answered, not found, or conflict. A model or search failure is an error, not an empty answer.</p>
      <div className="row">
        <label>Question<textarea value={question} onChange={(event) => setQuestion(event.target.value)} /></label>
      </div>
      <div className="row">
        <label>Mode
          <select value={mode} onChange={(event) => setMode(event.target.value)}>
            {["dense", "sparse", "hybrid", "hybrid_rerank"].map((item) => <option key={item}>{item}</option>)}
          </select>
        </label>
        <button className="btn" type="button" disabled={busy || !bid || !collection} onClick={() => void onAsk()}>Ask</button>
        <button className="btn ghost" type="button" disabled={!collection} onClick={() => void onSearch()}>Search</button>
      </div>
      <p className="note">Selected bid {bid || "none"}. Collection {collection || "none"}.</p>
      {error ? <ErrorState message={error} /> : null}
      {answer ? <AnswerPanel response={answer} /> : <Empty>No answer yet.</Empty>}
      <section>
        <h2>Search rows</h2>
        {hits.length === 0 ? <Empty>No search rows yet.</Empty> : (
          <table>
            <thead><tr><th>Rank</th><th>File</th><th>Page</th><th>Excerpt</th></tr></thead>
            <tbody>
              {hits.map((hit) => (
                <tr key={hit.rank}>
                  <td>{hit.rank}</td>
                  <td>{hit.citation?.original_file_name ?? "Not recorded"}</td>
                  <td>{hit.citation?.page_start ?? "Not recorded"}</td>
                  <td className="excerpt">{hit.text.slice(0, 280)}</td>
                </tr>
              ))}
            </tbody>
          </table>
        )}
      </section>
      <section className="panel">
        <h2>This browser session</h2>
        <p className="note">These questions stay in this browser. They are not a saved evaluation.</p>
        {history.length === 0 ? <Empty>No questions in this session.</Empty> : (
          <ul>
            {history.map((item) => <li key={item.at}>{item.status}: {item.question}</li>)}
          </ul>
        )}
      </section>
    </>
  );
}
