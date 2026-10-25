import { Route, Routes } from "react-router-dom";
import { Shell } from "./components/Shell";
import { Providers } from "./providers";
import { Ask } from "./pages/Ask";
import { Audit } from "./pages/Audit";
import { Compare } from "./pages/Compare";
import { Documents } from "./pages/Documents";
import { Evaluation } from "./pages/Evaluation";
import { Extraction } from "./pages/Extraction";
import { Overview } from "./pages/Overview";
import { Trace } from "./pages/Trace";

export function AppRoutes() {
  return (
    <Providers>
      <Routes>
        <Route element={<Shell />}>
          <Route index element={<Overview />} />
          <Route path="documents" element={<Documents />} />
          <Route path="ask" element={<Ask />} />
          <Route path="compare" element={<Compare />} />
          <Route path="extraction" element={<Extraction />} />
          <Route path="evaluation" element={<Evaluation />} />
          <Route path="trace" element={<Trace />} />
          <Route path="audit" element={<Audit />} />
        </Route>
      </Routes>
    </Providers>
  );
}
