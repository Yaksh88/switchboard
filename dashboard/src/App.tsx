import { lazy, Suspense } from "react";
import { Route, Routes } from "react-router-dom";

import { Loading } from "./components/parts";
import { Shell } from "./components/Shell";
import { Providers } from "./pages/Providers";
import { Requests } from "./pages/Requests";
import { Settings } from "./pages/Settings";

// The chart pages pull in the charting library, so they load on demand.
const Overview = lazy(() => import("./pages/Overview").then((m) => ({ default: m.Overview })));
const Evals = lazy(() => import("./pages/Evals").then((m) => ({ default: m.Evals })));

export function App() {
  return (
    <Suspense
      fallback={
        <div className="page">
          <Loading what="the page" />
        </div>
      }
    >
      <Routes>
        <Route element={<Shell />}>
          <Route index element={<Overview />} />
          <Route path="requests" element={<Requests />} />
          <Route path="evals" element={<Evals />} />
          <Route path="providers" element={<Providers />} />
          <Route path="settings" element={<Settings />} />
          <Route path="*" element={<Overview />} />
        </Route>
      </Routes>
    </Suspense>
  );
}
