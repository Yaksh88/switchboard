import { useCallback, useMemo, useState } from "react";
import { Line, LineChart, ResponsiveContainer, YAxis } from "recharts";

import { useEvalRun, useEvalRuns } from "../api/hooks";
import type { EvalCase, EvalRunSummary } from "../api/types";
import { Drawer } from "../components/Drawer";
import { Empty, ErrorNotice, Loading, Meter } from "../components/parts";
import { formatAgo, formatDateTime, formatMs, formatPercent, formatUsd } from "../lib/format";

interface Suite {
  name: string;
  runs: EvalRunSummary[]; // oldest first
}

function groupBySuite(runs: EvalRunSummary[]): Suite[] {
  const bySuite = new Map<string, EvalRunSummary[]>();
  for (const run of runs) bySuite.set(run.suite, [...(bySuite.get(run.suite) ?? []), run]);
  return [...bySuite.entries()]
    .map(([name, group]) => ({
      name,
      runs: group.sort((a, b) => a.created_at.localeCompare(b.created_at)),
    }))
    .sort((a, b) => a.name.localeCompare(b.name));
}

function SuiteTrend({ suite }: { suite: Suite }) {
  const latest = suite.runs[suite.runs.length - 1];
  const previous = suite.runs[suite.runs.length - 2];
  if (!latest) return null;
  const change = previous ? Math.round((latest.pass_rate - previous.pass_rate) * 100) : null;

  return (
    <div className="trend">
      <h3 className="expanded">{suite.name}</h3>
      <span className="trend__rate expanded">{formatPercent(latest.pass_rate)}</span>
      <span className="trend__meta">
        {latest.model}
        {change !== null && change !== 0
          ? `, ${change > 0 ? "up" : "down"} ${Math.abs(change)} ${Math.abs(change) === 1 ? "point" : "points"} on the last run`
          : ""}
      </span>
      <div className="trend__chart" aria-hidden="true">
        <ResponsiveContainer width="100%" height="100%">
          <LineChart data={suite.runs} margin={{ top: 4, right: 6, bottom: 4, left: 6 }}>
            <YAxis hide domain={[0, 1]} />
            <Line
              dataKey="pass_rate"
              stroke="var(--signal)"
              strokeWidth={2}
              type="monotone"
              dot={false}
              isAnimationActive={false}
            />
          </LineChart>
        </ResponsiveContainer>
      </div>
      <span className="trend__meta">
        {suite.runs.length} {suite.runs.length === 1 ? "run" : "runs"}
      </span>
    </div>
  );
}

function CaseRow({ result }: { result: EvalCase }) {
  const failing = result.scores.filter((s) => !s.passed);
  return (
    <li className="case" style={{ listStyle: "none" }}>
      <div className="case__head">
        <span className={`badge ${result.passed ? "badge--ok" : "badge--fault"}`}>
          {result.passed ? "Passed" : "Failed"}
        </span>
        <h3>{result.case_id}</h3>
        {result.critical ? <span className="badge badge--warn badge--plain">Critical</span> : null}
        <span className="muted">{formatMs(result.latency_ms)}</span>
        {result.cost_usd !== null ? <span className="muted">{formatUsd(result.cost_usd)}</span> : null}
      </div>
      {result.error ? <p className="case__reasons">{result.error}</p> : null}
      {failing.length > 0 ? (
        <ul className="case__reasons">
          {failing.map((score, index) => (
            <li key={`${score.type}-${index}`}>
              {score.type}: {score.reason}
            </li>
          ))}
        </ul>
      ) : null}
      {result.output ? (
        <details>
          <summary>Model output</summary>
          <pre className="block">{result.output}</pre>
        </details>
      ) : null}
    </li>
  );
}

function RunDetail({ id }: { id: string }) {
  const run = useEvalRun(id);
  if (run.isError) return <ErrorNotice error={run.error} />;
  if (!run.data) return <Loading what="the run" />;

  const cases = run.data.result.cases;
  const failed = cases.filter((c) => !c.passed);
  return (
    <>
      <dl className="facts">
        <dt>Pass rate</dt>
        <dd>
          {formatPercent(run.data.pass_rate)} ({run.data.passed_cases} of {run.data.total_cases})
        </dd>
        <dt>Model</dt>
        <dd>{run.data.model}</dd>
        <dt>Ran</dt>
        <dd>{formatDateTime(run.data.created_at)}</dd>
        <dt>Duration</dt>
        <dd>{run.data.duration_s.toFixed(1)} s</dd>
      </dl>
      <div>
        <h3>
          {failed.length === 0
            ? "Every case passed"
            : `${failed.length} ${failed.length === 1 ? "case" : "cases"} failed`}
        </h3>
        <ul style={{ margin: "8px 0 0", padding: 0 }}>
          {[...failed, ...cases.filter((c) => c.passed)].map((c) => (
            <CaseRow key={c.case_id} result={c} />
          ))}
        </ul>
      </div>
    </>
  );
}

export function Evals() {
  const runs = useEvalRuns();
  const [selected, setSelected] = useState<EvalRunSummary | null>(null);
  const close = useCallback(() => setSelected(null), []);

  const suites = useMemo(() => groupBySuite(runs.data ?? []), [runs.data]);
  const newestFirst = useMemo(
    () => [...(runs.data ?? [])].sort((a, b) => b.created_at.localeCompare(a.created_at)),
    [runs.data],
  );

  return (
    <div className="page">
      <header className="page__head">
        <div>
          <h1>Evals</h1>
          <p className="page__lede">
            Pass rates for your eval suites, so a prompt or model change that hurts quality shows up
            before users see it.
          </p>
        </div>
      </header>

      {runs.isError ? <ErrorNotice error={runs.error} /> : null}
      {runs.isPending ? <Loading what="eval runs" /> : null}

      {runs.data && runs.data.length === 0 ? (
        <Empty title="No eval runs yet">
          <p>Run a suite with --report and its results will be stored here.</p>
          <pre className="snippet">
            <code>switchboard eval run examples/evals/smoke.yaml --report</code>
          </pre>
        </Empty>
      ) : null}

      {suites.length > 0 ? (
        <>
          <section className="section">
            <h2>Suites</h2>
            <div className="trends">
              {suites.map((suite) => (
                <SuiteTrend key={suite.name} suite={suite} />
              ))}
            </div>
          </section>

          <section className="section">
            <h2>Runs</h2>
            <div className="table-wrap">
              <table className="table">
                <thead>
                  <tr>
                    <th scope="col">Suite</th>
                    <th scope="col">Model</th>
                    <th scope="col" className="table__num">Pass rate</th>
                    <th scope="col" className="table__num">Cases</th>
                    <th scope="col" className="table__num">Duration</th>
                    <th scope="col">When</th>
                  </tr>
                </thead>
                <tbody>
                  {newestFirst.map((run) => (
                    <tr
                      key={run.id}
                      className={`table__row--click${selected?.id === run.id ? " table__row--selected" : ""}`}
                      onClick={() => setSelected(run)}
                    >
                      <th scope="row">
                        <button
                          type="button"
                          className="expanded"
                          style={{ background: "none", border: 0, padding: 0, textAlign: "left" }}
                          onClick={(e) => {
                            e.stopPropagation();
                            setSelected(run);
                          }}
                        >
                          {run.suite}
                        </button>
                      </th>
                      <td>{run.model}</td>
                      <td className="table__num">
                        <Meter fraction={run.pass_rate} />
                      </td>
                      <td className="table__num">
                        {run.passed_cases}/{run.total_cases}
                      </td>
                      <td className="table__num">{run.duration_s.toFixed(1)} s</td>
                      <td title={formatDateTime(run.created_at)}>{formatAgo(run.created_at)}</td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          </section>
        </>
      ) : null}

      {selected ? (
        <Drawer title={selected.suite} subtitle={formatDateTime(selected.created_at)} onClose={close}>
          <RunDetail id={selected.id} />
        </Drawer>
      ) : null}
    </div>
  );
}
