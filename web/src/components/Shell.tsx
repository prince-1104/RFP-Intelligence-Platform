import { NavLink, Outlet } from "react-router-dom";
import { Toast } from "./ui";
import { useSelection } from "../providers";

const LINKS = [
  ["/", "Overview"],
  ["/documents", "Bid Documents"],
  ["/ask", "Ask & Search"],
  ["/compare", "Compare Bids"],
  ["/extraction", "Extraction & Addenda"],
  ["/evaluation", "Retrieval Evaluation"],
  ["/trace", "Agent Trace"],
  ["/audit", "Submission Audit"],
] as const;

export function Shell() {
  const { collection, bid, healthLabel, toast, clearToast } = useSelection();
  return (
    <div className="app">
      <aside className="sidebar">
        <div className="brand">
          <strong>RFP Intelligence</strong>
          <span>Procurement Intelligence & Bid Evaluation Workbench</span>
        </div>
        <nav className="nav">
          {LINKS.map(([path, label]) => (
            <NavLink key={path} to={path} end={path === "/"}>
              {label}
            </NavLink>
          ))}
        </nav>
      </aside>
      <div className="main">
        <header className="header">
          <div className="header-meta">
            <span>Collection <b>{collection || "None selected"}</b></span>
            <span>Bid <b>{bid || "None selected"}</b></span>
            <span>Health <b>{healthLabel}</b></span>
          </div>
        </header>
        <main className="content">
          <Outlet />
        </main>
      </div>
      {toast ? <Toast text={toast} onClose={clearToast} /> : null}
    </div>
  );
}
