import { useEffect, useState } from "react";
import type { ReactNode } from "react";
import type { Citation, TraceEvent } from "../api/types";

export function Badge({ value }: { value: string }) {
  return <span className={`badge ${value.replace(/[^a-z_]/g, "")}`}>{value}</span>;
}

export function Empty({ children }: { children: ReactNode }) {
  return <div className="empty">{children}</div>;
}

export function ErrorState({ message }: { message: string }) {
  return <div className="error" role="alert">{message}</div>;
}

export function Dialog({
  title,
  body,
  confirmLabel,
  onConfirm,
  onClose,
}: {
  title: string;
  body: string;
  confirmLabel: string;
  onConfirm: () => void;
  onClose: () => void;
}) {
  return (
    <div className="dialog-back" role="presentation">
      <div className="dialog" role="dialog" aria-modal="true" aria-label={title}>
        <h2>{title}</h2>
        <p>{body}</p>
        <div className="row">
          <button className="btn" type="button" onClick={onConfirm}>{confirmLabel}</button>
          <button className="btn ghost" type="button" onClick={onClose}>Cancel</button>
        </div>
      </div>
    </div>
  );
}

export function Drawer({ title, onClose, children }: { title: string; onClose: () => void; children: ReactNode }) {
  return (
    <>
      <div className="drawer-back" onClick={onClose} />
      <aside className="drawer" aria-label={title}>
        <div className="row" style={{ justifyContent: "space-between" }}>
          <h2>{title}</h2>
          <button className="btn ghost" type="button" onClick={onClose}>Close</button>
        </div>
        {children}
      </aside>
    </>
  );
}

export function Toast({ text, onClose }: { text: string; onClose: () => void }) {
  useEffect(() => {
    const timer = window.setTimeout(onClose, 5000);
    return () => window.clearTimeout(timer);
  }, [onClose]);
  return <div className="toast" role="status">{text}</div>;
}

export function CitationList({ citations }: { citations: Citation[] }) {
  const [open, setOpen] = useState<Citation | null>(null);
  if (citations.length === 0) return <Empty>No citations were returned.</Empty>;
  return (
    <>
      <table>
        <thead>
          <tr><th>Id</th><th>Document</th><th>Page</th><th>Evidence</th></tr>
        </thead>
        <tbody>
          {citations.map((item) => (
            <tr key={item.citation_id}>
              <td>
                <button className="btn ghost" type="button" onClick={() => setOpen(item)}>
                  {item.citation_id}
                </button>
              </td>
              <td>{item.document}</td>
              <td>{item.page ?? "Not recorded"}</td>
              <td>{item.evidence_id}</td>
            </tr>
          ))}
        </tbody>
      </table>
      {open ? (
        <Drawer title="Cited excerpt" onClose={() => setOpen(null)}>
          <p className="note">This is the excerpt returned with the citation. The page itself is not rendered.</p>
          <p><b>Evidence</b> {open.evidence_id}</p>
          <p><b>Page</b> {open.page ?? "Not recorded"}</p>
          <p className="excerpt">{open.excerpt}</p>
        </Drawer>
      ) : null}
    </>
  );
}

const TRACE_FIELDS = ["query", "mode", "top_k", "result_count", "decision", "error", "provider", "model"] as const;

export function TraceList({ events }: { events: TraceEvent[] }) {
  if (events.length === 0) return <Empty>This trace has no events.</Empty>;
  return (
    <div className="grid">
      {events.map((event) => (
        <article className="card" key={event.sequence}>
          <div className="kicker">Event {event.sequence}</div>
          <h2>{event.agent} · {event.task}</h2>
          <dl>
            {TRACE_FIELDS.map((key) => {
              const value = event[key];
              const recorded = value !== undefined && value !== null && value !== "";
              return (
                <div key={key}>
                  <dt>{key}</dt>
                  <dd>{recorded ? String(value) : "Not recorded."}</dd>
                </div>
              );
            })}
          </dl>
        </article>
      ))}
    </div>
  );
}
