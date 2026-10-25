import { useState } from "react";
import { api } from "../api/client";
import type { TracePayload } from "../api/types";
import { Empty, ErrorState, TraceList } from "../components/ui";

export function Trace() {
  const [id, setId] = useState("");
  const [payload, setPayload] = useState<TracePayload | null>(null);
  const [error, setError] = useState("");

  async function load() {
    setError("");
    try {
      setPayload(await api.trace(id.trim()));
    } catch (exc) {
      setPayload(null);
      setError(exc instanceof Error ? exc.message : "Trace lookup failed.");
    }
  }

  return (
    <>
      <h1>Agent Trace</h1>
      <p className="lede">Only fields stored on the trace event are filled in. Anything the file omitted stays marked as not recorded.</p>
      <div className="row">
        <label>Trace id<input value={id} onChange={(event) => setId(event.target.value)} /></label>
        <button className="btn" type="button" disabled={!id.trim()} onClick={() => void load()}>Open</button>
      </div>
      {error ? <ErrorState message={error} /> : null}
      {payload ? (
        <>
          <p className="note">Source {payload.source ?? "Not recorded."} Status {payload.status ?? payload.outcome ?? "Not recorded."}</p>
          <TraceList events={payload.events ?? []} />
        </>
      ) : <Empty>No trace is open.</Empty>}
    </>
  );
}
