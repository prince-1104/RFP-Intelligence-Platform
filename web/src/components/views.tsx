import type {
  AddendumChange,
  AuditResult,
  BidExtraction,
  ComparisonResponse,
  EvaluationSnapshot,
  FieldOutput,
  QAResponse,
} from "../api/types";
import { Badge, CitationList, Empty } from "./ui";

const METRICS = [
  { key: "recall@5" as const, label: "Recall@5", cls: "recall" },
  { key: "mrr@10" as const, label: "MRR@10", cls: "mrr" },
  { key: "ndcg@10" as const, label: "nDCG@10", cls: "ndcg" },
];

export function AnswerPanel({ response }: { response: QAResponse }) {
  return (
    <section className="panel">
      <div className="row">
        <Badge value={response.status} />
        <span className="note">Trace {response.trace_id}</span>
      </div>
      <p>{response.answer}</p>
      {response.status === "conflict" ? (
        <p className="note">Conflict means the returned citations disagree. It is not a partial answer.</p>
      ) : null}
      {response.completeness ? (
        <p className="note">
          Completeness is separate from status. Single-bid answers have no partial status.
          Covered {response.completeness.covered.length}, unresolved {response.completeness.unresolved.length}.
        </p>
      ) : null}
      <CitationList citations={response.citations} />
    </section>
  );
}

export function ComparisonPanel({ response }: { response: ComparisonResponse }) {
  return (
    <section className="panel">
      <div className="row">
        <Badge value={response.status} />
        <span className="note">Trace {response.trace_id}</span>
      </div>
      <p>{response.answer}</p>
      <p className="note">Relation labels are lexical. They are not proof that two passages are equivalent.</p>
      {response.aspects.length === 0 ? <Empty>No aspects were returned.</Empty> : (
        <table>
          <thead><tr><th>Aspect</th><th>Relation</th></tr></thead>
          <tbody>
            {response.aspects.map((aspect) => (
              <tr key={aspect.aspect}>
                <td>{aspect.aspect}</td>
                <td><Badge value={aspect.relation} /></td>
              </tr>
            ))}
          </tbody>
        </table>
      )}
      {response.findings.map((finding) => (
        <article key={finding.bid_id}>
          <h2>{finding.bid_id}</h2>
          <Badge value={finding.status} />
          <p>{finding.answer}</p>
          <CitationList citations={finding.citations} />
        </article>
      ))}
    </section>
  );
}

export function ExtractionView({
  extraction,
  fieldOrder,
}: {
  extraction: BidExtraction;
  fieldOrder: string[];
}) {
  const names = fieldOrder.length ? fieldOrder : Object.keys(extraction.fields);
  const counts = extraction.validation;
  return (
    <section>
      <p className="note">
        Confidence is a 0–1 heuristic ({extraction.kind === "saved_extraction" ? "saved snapshot" : "live result"}).
        The backend does not grade a field correct or incorrect.
      </p>
      <div className="row">
        <Badge value={`${counts.passed} passed`} />
        <Badge value={`${counts.failed} failed`} />
        <Badge value={`${counts.unresolved} unresolved`} />
        <Badge value={`${counts.not_found} not_found`} />
      </div>
      <table>
        <thead><tr><th>Field</th><th>Value</th><th>Confidence</th><th>Notes</th></tr></thead>
        <tbody>
          {names.map((name) => {
            const field: FieldOutput | undefined = extraction.fields[name];
            return (
              <tr key={name}>
                <td>{name}</td>
                <td>{field?.value ?? "Not found"}</td>
                <td>{field ? field.confidence.toFixed(2) : "Not recorded."}</td>
                <td>{field?.notes ?? "Not recorded."}</td>
              </tr>
            );
          })}
        </tbody>
      </table>
      <h2>Addenda</h2>
      {extraction.addendum_changes.length === 0 ? <Empty>No addendum changes were returned.</Empty> : (
        <Addenda changes={extraction.addendum_changes} />
      )}
    </section>
  );
}

function Addenda({ changes }: { changes: AddendumChange[] }) {
  return (
    <table>
      <thead><tr><th>Field</th><th>Status</th><th>Revised</th></tr></thead>
      <tbody>
        {changes.map((change, index) => (
          <tr key={`${change.field}-${index}`}>
            <td>{change.field}</td>
            <td><Badge value={change.status ?? "not_recorded"} /></td>
            <td>{change.revised_value ?? "Not recorded."}</td>
          </tr>
        ))}
      </tbody>
    </table>
  );
}

export function MetricChart({ snapshot }: { snapshot: EvaluationSnapshot }) {
  const modes = Object.entries(snapshot.modes);
  return (
    <section className="panel" aria-label="Retrieval metrics">
      <div className="kicker">{snapshot.kind}</div>
      <h2>{snapshot.source}</h2>
      <p className="note">
        Collection {snapshot.collection ?? "Not recorded."}. Dataset {snapshot.dataset ?? "Not recorded."}.
        A different file is a separate snapshot. These numbers are not compared as an improvement.
      </p>
      {modes.length === 0 ? <Empty>Not available</Empty> : (
        <div className="bars">
          {modes.map(([mode, metrics]) => (
            <div key={mode}>
              <div className="bar-group" aria-label={mode}>
                {METRICS.map((metric) => {
                  const value = metrics[metric.key];
                  const height = value == null ? 2 : Math.max(2, value * 160);
                  return <div key={metric.key} className={`bar ${metric.cls}`} style={{ height }} title={`${metric.label} ${value ?? "Not available"}`} />;
                })}
              </div>
              <div className="note">{mode}</div>
            </div>
          ))}
        </div>
      )}
      <table>
        <thead><tr><th>Mode</th><th>Recall@5</th><th>MRR@10</th><th>nDCG@10</th></tr></thead>
        <tbody>
          {modes.map(([mode, metrics]) => (
            <tr key={mode}>
              <td>{mode}</td>
              <td>{metrics["recall@5"] ?? "Not available"}</td>
              <td>{metrics["mrr@10"] ?? "Not available"}</td>
              <td>{metrics["ndcg@10"] ?? "Not available"}</td>
            </tr>
          ))}
        </tbody>
      </table>
    </section>
  );
}

export function AuditView({ audit }: { audit: AuditResult }) {
  return (
    <section>
      {audit.available ? (
        <p>Recorded problems: {audit.problem_count ?? "Not available"}</p>
      ) : (
        <Empty>{audit.detail ?? "Not available"}</Empty>
      )}
      <h2>Requirement matrix</h2>
      <p className="note">{audit.matrix?.label ?? "Saved audit document. The workbench does not re-score these rows."}</p>
      <p className="note">{audit.matrix?.source ?? "docs/FINAL_REQUIREMENTS_AUDIT.md"}</p>
      {audit.matrix?.text ? <pre className="matrix">{audit.matrix.text}</pre> : <Empty>The saved matrix is not on disk.</Empty>}
    </section>
  );
}
