import { useEffect, useState } from "react";
import { api, ApiError } from "../api/client";
import type { BidRow, JobRecord } from "../api/types";
import { Dialog, Empty, ErrorState } from "../components/ui";
import { useSelection } from "../providers";

export function Documents() {
  const { collection, setCollection, collections, setBid, notify } = useSelection();
  const [bids, setBids] = useState<BidRow[]>([]);
  const [physical, setPhysical] = useState("");
  const [error, setError] = useState("");
  const [path, setPath] = useState("");
  const [name, setName] = useState("workbench_local");
  const [bidId, setBidId] = useState("");
  const [confirm, setConfirm] = useState(false);
  const [job, setJob] = useState<JobRecord | null>(null);

  useEffect(() => {
    if (!collection) return;
    api.bids(collection).then((body) => {
      setBids(body.bids);
      setPhysical(body.physical);
      setError("");
      setBid((current) => (
        body.bids.some((row) => row.label === current) ? current : (body.bids[0]?.label ?? "")
      ));
    }).catch((exc: Error) => setError(exc.message));
  }, [collection, setBid]);

  useEffect(() => {
    if (!job || job.status === "succeeded" || job.status === "failed") return;
    const timer = window.setInterval(() => {
      api.job(job.id).then(setJob).catch(() => undefined);
    }, 2000);
    return () => window.clearInterval(timer);
  }, [job]);

  async function onUpload(file: File | undefined) {
    if (!file) return;
    try {
      const saved = await api.upload(file.name, file);
      const folder = saved.path.slice(0, saved.path.lastIndexOf("/"));
      setPath(folder);
      notify(`Saved ${saved.bytes} bytes. Ingest uses the upload folder.`);
    } catch (exc) {
      notify(exc instanceof ApiError ? exc.message : "Upload failed.");
    }
  }

  return (
    <>
      <h1>Bid Documents</h1>
      <p className="lede">Bid names come from the collection payload and local assessment folders. They are not a fixed Bid1 and Bid2 list.</p>
      <div className="row">
        <label>Collection
          <select value={collection} onChange={(event) => setCollection(event.target.value)}>
            {collections.map((row) => <option key={row.physical} value={row.base}>{row.base}</option>)}
          </select>
        </label>
      </div>
      <p className="note">Physical collection {physical || "Not available"}.</p>
      {error ? <ErrorState message={error} /> : null}
      {bids.length === 0 ? <Empty>No bids were returned for this collection.</Empty> : (
        <table>
          <thead><tr><th>Label</th><th>Documents</th><th>Points</th></tr></thead>
          <tbody>
            {bids.map((row) => (
              <tr key={row.bid_uuid}>
                <td><button className="btn ghost" type="button" onClick={() => setBid(row.label)}>{row.label}</button></td>
                <td>{row.documents.map((doc) => doc.file).join(", ") || "Not recorded"}</td>
                <td>{row.points}</td>
              </tr>
            ))}
          </tbody>
        </table>
      )}
      <section className="panel">
        <h2>Ingest into a new collection</h2>
        <p className="note">Writes are allowed only for a new name that starts with workbench. Existing collections, including bid_documents, are refused. The folder must sit under data or artifacts/workbench/uploads.</p>
        <div className="row">
          <label>Folder<input value={path} onChange={(event) => setPath(event.target.value)} /></label>
          <label>Bid id<input value={bidId} onChange={(event) => setBidId(event.target.value)} /></label>
          <label>New collection<input value={name} onChange={(event) => setName(event.target.value)} /></label>
          <label>PDF or HTML
            <input type="file" accept=".pdf,.html,.htm" onChange={(event) => void onUpload(event.target.files?.[0])} />
          </label>
          <button className="btn" type="button" onClick={() => setConfirm(true)}>Ingest</button>
        </div>
        {job ? <p>Job {job.id} is {job.status}.{job.error ? ` ${job.error.message}` : ""}</p> : null}
      </section>
      {confirm ? (
        <Dialog
          title="Start ingestion"
          body="This indexes the folder into the new local collection. It does not call the production index route."
          confirmLabel="Start"
          onClose={() => setConfirm(false)}
          onConfirm={() => {
            setConfirm(false);
            api.ingest({ confirm: true, collection: name, bid_id: bidId, path }).then((record) => {
              setJob(record);
              notify("Ingest queued.");
            }).catch((exc: Error) => notify(exc.message));
          }}
        />
      ) : null}
    </>
  );
}
