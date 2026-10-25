import { useEffect, useState } from "react";
import { Link } from "react-router-dom";
import { api } from "../api/client";
import type { Overview as OverviewBody } from "../api/types";
import { Empty, ErrorState } from "../components/ui";
import { useSelection } from "../providers";

export function Overview() {
  const { health } = useSelection();
  const [body, setBody] = useState<OverviewBody | null>(null);
  const [error, setError] = useState("");

  useEffect(() => {
    api.overview().then(setBody).catch((exc: Error) => setError(exc.message));
  }, []);

  return (
    <>
      <h1>Overview</h1>
      <p className="lede">
        Local workbench. Search, answers, comparison, extraction and evaluation come from the existing Python functions.
        Postgres, Redis and the production index route are not used.
      </p>
      {error ? <ErrorState message={error} /> : null}
      <div className="grid">
        <article className="card">
          <div className="kicker">Qdrant</div>
          <p>{health ? health.qdrant : "Unavailable"}</p>
          <p className="note">Postgres {health?.postgres ?? "not_used"}. Redis {health?.redis ?? "not_used"}.</p>
        </article>
        <article className="card">
          <div className="kicker">Collections</div>
          <p>{body?.collections == null ? "Unavailable" : body.collections.length}</p>
        </article>
        <article className="card">
          <div className="kicker">Last ingest</div>
          <p>{body?.last_ingest?.status ?? "Not available"}</p>
        </article>
      </div>
      <section className="panel">
        <h2>Saved snapshots</h2>
        {!body ? <Empty>Loading saved files.</Empty> : (
          <>
            <p className="note">
              Evaluation files on disk: {body.evaluation.length ? body.evaluation[0].source : "Not available"}.
              Extractions listed: {body.extractions.length}.
            </p>
            {body.qa ? (
              <p className="note">Q&A snapshot {body.qa.source}. {body.qa.examples.length} examples shown. This file is not a live answer.</p>
            ) : <p className="note">Q&A snapshot: Not available.</p>}
          </>
        )}
      </section>
      <section className="steps">
        <h2>Live checks</h2>
        <Link to="/documents">Open the documents in the selected collection.</Link>
        <Link to="/ask">Ask one bid. The answer status is answered, not_found or conflict.</Link>
        <Link to="/compare">Compare two to four bids from that collection.</Link>
        <Link to="/extraction">Read a saved extraction, including addendum records.</Link>
        <Link to="/evaluation">Read Recall@5, MRR@10 and nDCG@10 from a saved report.</Link>
        <Link to="/audit">Read the submission audit and the saved requirement matrix.</Link>
      </section>
    </>
  );
}
