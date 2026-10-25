import { useEffect, useState } from "react";
import { api } from "../api/client";
import type { AuditResult } from "../api/types";
import { ErrorState } from "../components/ui";
import { AuditView } from "../components/views";

export function Audit() {
  const [body, setBody] = useState<AuditResult | null>(null);
  const [error, setError] = useState("");

  useEffect(() => {
    api.audit().then(setBody).catch((exc: Error) => setError(exc.message));
  }, []);

  return (
    <>
      <h1>Submission Audit</h1>
      <p className="lede">The audit reads saved artifacts. It does not grade the requirement matrix. The matrix below is the saved document.</p>
      {error ? <ErrorState message={error} /> : null}
      {body ? <AuditView audit={body} /> : null}
    </>
  );
}
