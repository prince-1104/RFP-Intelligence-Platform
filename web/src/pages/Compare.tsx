import { useEffect, useState } from "react";
import { api } from "../api/client";
import type { BidRow, ComparisonResponse } from "../api/types";
import { Empty, ErrorState } from "../components/ui";
import { ComparisonPanel } from "../components/views";
import { useSelection } from "../providers";

export function Compare() {
  const { collection } = useSelection();
  const [bids, setBids] = useState<BidRow[]>([]);
  const [chosen, setChosen] = useState<string[]>([]);
  const [question, setQuestion] = useState("");
  const [result, setResult] = useState<ComparisonResponse | null>(null);
  const [error, setError] = useState("");

  useEffect(() => {
    if (!collection) return;
    api.bids(collection).then((body) => setBids(body.bids)).catch((exc: Error) => setError(exc.message));
  }, [collection]);

  function toggle(label: string) {
    setChosen((current) => {
      if (current.includes(label)) return current.filter((item) => item !== label);
      if (current.length >= 4) return current;
      return [...current, label];
    });
  }

  async function onCompare() {
    setError("");
    try {
      setResult(await api.compare({ bids: chosen, question, collection }));
    } catch (exc) {
      setResult(null);
      setError(exc instanceof Error ? exc.message : "Comparison failed.");
    }
  }

  return (
    <>
      <h1>Compare Bids</h1>
      <p className="lede">Two to four bids. Comparison status is answered, partial, or not found. A partial comparison means at least one selected bid had no finding.</p>
      {bids.length === 0 ? <Empty>No bids were returned for this collection.</Empty> : (
        <div className="row">
          {bids.map((row) => (
            <label key={row.bid_uuid}>
              <span>{row.label}</span>
              <input type="checkbox" checked={chosen.includes(row.label)} onChange={() => toggle(row.label)} />
            </label>
          ))}
        </div>
      )}
      <label>Question<textarea value={question} onChange={(event) => setQuestion(event.target.value)} /></label>
      <button className="btn" type="button" disabled={chosen.length < 2} onClick={() => void onCompare()}>Compare</button>
      {error ? <ErrorState message={error} /> : null}
      {result ? <ComparisonPanel response={result} /> : <Empty>No comparison yet.</Empty>}
    </>
  );
}
