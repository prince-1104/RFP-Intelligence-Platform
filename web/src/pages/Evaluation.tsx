import { useEffect, useState } from "react";
import { api } from "../api/client";
import type { EvaluationSnapshot, JobRecord } from "../api/types";
import { Dialog, Empty, ErrorState } from "../components/ui";
import { MetricChart } from "../components/views";
import { useSelection } from "../providers";

export function Evaluation() {
  const { collection, notify } = useSelection();
  const [rows, setRows] = useState<EvaluationSnapshot[]>([]);
  const [selected, setSelected] = useState<EvaluationSnapshot | null>(null);
  const [error, setError] = useState("");
  const [confirm, setConfirm] = useState(false);
  const [job, setJob] = useState<JobRecord | null>(null);

  useEffect(() => {
    api.evaluation().then((items) => {
      setRows(items);
      setSelected(items[0] ?? null);
    }).catch((exc: Error) => setError(exc.message));
  }, []);

  useEffect(() => {
    if (!job || job.status === "succeeded" || job.status === "failed") return;
    const timer = window.setInterval(() => {
      api.job(job.id).then((record) => {
        setJob(record);
        if (record.status === "succeeded") {
          api.evaluation().then(setRows).catch(() => undefined);
        }
      }).catch(() => undefined);
    }, 2000);
    return () => window.clearInterval(timer);
  }, [job]);

  return (
    <>
      <h1>Retrieval Evaluation</h1>
      <p className="lede">Each chart is one saved report. The file name, collection and dataset stay on the chart. A later file is not labeled an improvement.</p>
      {error ? <ErrorState message={error} /> : null}
      {rows.length === 0 ? <Empty>Not available</Empty> : (
        <div className="row">
          {rows.map((row) => (
            <button className="btn ghost" type="button" key={row.source} onClick={() => setSelected(row)}>
              {row.source}
            </button>
          ))}
        </div>
      )}
      {selected ? <MetricChart snapshot={selected} /> : null}
      <section className="panel">
        <h2>New local run</h2>
        <p className="note">Runs the existing evaluation script against {collection || "the selected collection"} and writes a new file. An existing output for that name is left untouched.</p>
        <button className="btn" type="button" disabled={!collection} onClick={() => setConfirm(true)}>Run evaluation</button>
        {job ? <p>Job {job.id} is {job.status}.{job.error ? ` ${job.error.message}` : ""}</p> : null}
      </section>
      {confirm ? (
        <Dialog
          title="Start evaluation"
          body="This scores the saved retrieval dataset against the selected local collection. It does not edit labels or gold answers."
          confirmLabel="Start"
          onClose={() => setConfirm(false)}
          onConfirm={() => {
            setConfirm(false);
            api.runEvaluation(collection).then((record) => {
              setJob(record);
              notify("Evaluation queued.");
            }).catch((exc: Error) => notify(exc.message));
          }}
        />
      ) : null}
    </>
  );
}
