import { useEffect, useState } from "react";
import { api } from "../api/client";
import type { BidExtraction, JobRecord } from "../api/types";
import { Dialog, Empty, ErrorState } from "../components/ui";
import { ExtractionView } from "../components/views";
import { useSelection } from "../providers";

type Run = { run: string; bid_id: string; source: string };

export function Extraction() {
  const { collection, bid, notify } = useSelection();
  const [runs, setRuns] = useState<Run[]>([]);
  const [fields, setFields] = useState<string[]>([]);
  const [current, setCurrent] = useState<BidExtraction | null>(null);
  const [error, setError] = useState("");
  const [confirm, setConfirm] = useState(false);
  const [job, setJob] = useState<JobRecord | null>(null);

  useEffect(() => {
    api.extractions().then(setRuns).catch((exc: Error) => setError(exc.message));
    api.capabilities().then((body) => setFields(body.fields)).catch(() => undefined);
  }, []);

  useEffect(() => {
    if (!job || job.status === "succeeded" || job.status === "failed") return;
    const timer = window.setInterval(() => {
      api.job(job.id).then((record) => {
        setJob(record);
        if (record.status === "succeeded") {
          api.extractions().then(setRuns).catch(() => undefined);
        }
      }).catch(() => undefined);
    }, 2000);
    return () => window.clearInterval(timer);
  }, [job]);

  function download() {
    if (!current) return;
    const saved = { ...current, source: undefined, kind: undefined };
    const blob = new Blob([JSON.stringify(saved, null, 2)], { type: "application/json" });
    const url = URL.createObjectURL(blob);
    const link = document.createElement("a");
    link.href = url;
    link.download = `${current.bid_id}.json`;
    link.click();
    URL.revokeObjectURL(url);
  }

  return (
    <>
      <h1>Extraction & Addenda</h1>
      <p className="lede">The twenty fields stay in contract order. Download returns the JSON the backend saved, with the source path added by the workbench.</p>
      {error ? <ErrorState message={error} /> : null}
      {runs.length === 0 ? <Empty>No saved extractions were found.</Empty> : (
        <table>
          <thead><tr><th>Run</th><th>Bid</th><th>File</th></tr></thead>
          <tbody>
            {runs.map((run) => (
              <tr key={run.source}>
                <td>{run.run}</td>
                <td>{run.bid_id}</td>
                <td>
                  <button className="btn ghost" type="button" onClick={() => {
                    api.extractionFile(run.source).then(setCurrent).catch((exc: Error) => setError(exc.message));
                  }}>{run.source}</button>
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      )}
      {current ? (
        <>
          <p className="note">Source {current.source ?? "Not recorded."}</p>
          <button className="btn ghost" type="button" onClick={download}>Download JSON</button>
          <ExtractionView extraction={current} fieldOrder={fields} />
        </>
      ) : null}
      <section className="panel">
        <h2>Run extraction</h2>
        <p className="note">This reads the selected collection and writes a new file under artifacts/workbench. It does not change the index.</p>
        <button className="btn" type="button" disabled={!collection || !bid} onClick={() => setConfirm(true)}>Extract {bid || "bid"}</button>
        {job ? <p>Job {job.id} is {job.status}.{job.error ? ` ${job.error.message}` : ""}</p> : null}
      </section>
      {confirm ? (
        <Dialog
          title="Start extraction"
          body="Extraction calls the existing runner for the selected bid. It can take several minutes and does not write to Qdrant."
          confirmLabel="Start"
          onClose={() => setConfirm(false)}
          onConfirm={() => {
            setConfirm(false);
            api.extract({ confirm: true, collection, bid_id: bid }).then((record) => {
              setJob(record);
              notify("Extraction queued.");
            }).catch((exc: Error) => notify(exc.message));
          }}
        />
      ) : null}
    </>
  );
}
