import { useMemo, useState } from "react";
import {
  Area,
  AreaChart,
  Bar,
  BarChart,
  CartesianGrid,
  Line,
  LineChart,
  ResponsiveContainer,
  Tooltip,
  XAxis,
  YAxis,
} from "recharts";

import { useModels, useOverview } from "../api/hooks";
import type { ModelRow, SeriesPoint, Window } from "../api/types";
import {
  ChartTooltip,
  Empty,
  ErrorNotice,
  Loading,
  Meter,
  Readout,
  WindowSwitcher,
} from "../components/parts";
import {
  formatCount,
  formatDateTime,
  formatMs,
  formatPercent,
  formatTick,
  formatUsd,
} from "../lib/format";

type Point = SeriesPoint & { ok: number };

const REDUCED_MOTION =
  typeof window !== "undefined" && window.matchMedia("(prefers-reduced-motion: reduce)").matches;

const xAxis = (range: Window) => ({
  dataKey: "ts",
  tickFormatter: (value: string) => formatTick(value, range),
  tickLine: false,
  axisLine: { stroke: "var(--rule)" },
  minTickGap: 56,
  tick: { fill: "var(--ink-2)", fontSize: 12 },
});

const yAxis = (format: (n: number) => string) => ({
  tickFormatter: format,
  tickLine: false,
  axisLine: false,
  width: 52,
  tick: { fill: "var(--ink-2)", fontSize: 12 },
});

const grid = <CartesianGrid vertical={false} stroke="var(--rule-soft)" strokeDasharray="2 4" />;

function Legend({ items }: { items: { label: string; color: string }[] }) {
  return (
    <div className="legend">
      {items.map((item) => (
        <span className="legend__item" key={item.label}>
          <span className="legend__swatch" style={{ background: item.color }} />
          {item.label}
        </span>
      ))}
    </div>
  );
}

/** Requests per time bucket, split into succeeded and failed. The page's one drawn-in moment. */
function TrafficRibbon({ data, range }: { data: Point[]; range: Window }) {
  return (
    <div className="chart chart--tall">
      <ResponsiveContainer width="100%" height="100%">
        <BarChart data={data} margin={{ top: 8, right: 4, bottom: 0, left: 0 }} barCategoryGap="18%">
          {grid}
          <XAxis {...xAxis(range)} />
          <YAxis {...yAxis(formatCount)} allowDecimals={false} />
          <Tooltip
            cursor={{ fill: "var(--hover)" }}
            content={({ active, payload }) => {
              const point = payload?.[0]?.payload as Point | undefined;
              if (!active || !point) return null;
              return (
                <ChartTooltip
                  title={formatDateTime(point.ts)}
                  rows={[
                    { name: "Succeeded", value: formatCount(point.ok), color: "var(--signal)" },
                    { name: "Failed", value: formatCount(point.errors), color: "var(--fault)" },
                  ]}
                />
              );
            }}
          />
          <Bar
            dataKey="ok"
            stackId="requests"
            fill="var(--signal)"
            isAnimationActive={!REDUCED_MOTION}
            animationDuration={900}
          />
          <Bar
            dataKey="errors"
            stackId="requests"
            fill="var(--fault)"
            isAnimationActive={!REDUCED_MOTION}
            animationDuration={900}
          />
        </BarChart>
      </ResponsiveContainer>
    </div>
  );
}

function LatencyChart({ data, range }: { data: Point[]; range: Window }) {
  return (
    <div className="chart">
      <ResponsiveContainer width="100%" height="100%">
        <LineChart data={data} margin={{ top: 8, right: 4, bottom: 0, left: 0 }}>
          {grid}
          <XAxis {...xAxis(range)} />
          <YAxis {...yAxis(formatMs)} />
          <Tooltip
            content={({ active, payload }) => {
              const point = payload?.[0]?.payload as Point | undefined;
              if (!active || !point) return null;
              return (
                <ChartTooltip
                  title={formatDateTime(point.ts)}
                  rows={[{ name: "p95 latency", value: formatMs(point.p95_ms), color: "var(--ink)" }]}
                />
              );
            }}
          />
          <Line
            dataKey="p95_ms"
            stroke="var(--ink)"
            strokeWidth={1.75}
            dot={false}
            type="monotone"
            isAnimationActive={false}
          />
        </LineChart>
      </ResponsiveContainer>
    </div>
  );
}

function SpendChart({ data, range }: { data: Point[]; range: Window }) {
  return (
    <div className="chart">
      <ResponsiveContainer width="100%" height="100%">
        <AreaChart data={data} margin={{ top: 8, right: 4, bottom: 0, left: 0 }}>
          {grid}
          <XAxis {...xAxis(range)} />
          <YAxis {...yAxis(formatUsd)} />
          <Tooltip
            content={({ active, payload }) => {
              const point = payload?.[0]?.payload as Point | undefined;
              if (!active || !point) return null;
              return (
                <ChartTooltip
                  title={formatDateTime(point.ts)}
                  rows={[{ name: "Spend", value: formatUsd(point.cost_usd), color: "var(--signal)" }]}
                />
              );
            }}
          />
          <Area
            dataKey="cost_usd"
            stroke="var(--signal)"
            strokeWidth={1.75}
            fill="var(--signal)"
            fillOpacity={0.14}
            type="monotone"
            isAnimationActive={false}
          />
        </AreaChart>
      </ResponsiveContainer>
    </div>
  );
}

function ModelsTable({ rows }: { rows: ModelRow[] }) {
  return (
    <div className="table-wrap">
      <table className="table">
        <thead>
          <tr>
            <th scope="col">Model</th>
            <th scope="col" className="table__num">Requests</th>
            <th scope="col" className="table__num">Failed</th>
            <th scope="col" className="table__num">Median</th>
            <th scope="col" className="table__num">p95</th>
            <th scope="col" className="table__num">Spend</th>
            <th scope="col" className="table__num">From cache</th>
          </tr>
        </thead>
        <tbody>
          {rows.map((row) => (
            <tr key={row.model}>
              <th scope="row" className="expanded">{row.model}</th>
              <td className="table__num">{formatCount(row.requests)}</td>
              <td className="table__num">
                <Meter fraction={row.error_rate} tone="fault" />
              </td>
              <td className="table__num">{formatMs(row.p50_ms)}</td>
              <td className="table__num">{formatMs(row.p95_ms)}</td>
              <td className="table__num">{formatUsd(row.cost_usd)}</td>
              <td className="table__num">{formatPercent(row.cache_hit_rate)}</td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}

const SAMPLE_REQUEST = `curl localhost:8080/v1/chat/completions \\
  -H 'content-type: application/json' \\
  -d '{"model": "mock", "messages": [{"role": "user", "content": "hello"}]}'`;

export function Overview() {
  const [range, setRange] = useState<Window>("24h");
  const overview = useOverview(range);
  const models = useModels(range);

  const data = useMemo<Point[]>(
    () => (overview.data?.series ?? []).map((p) => ({ ...p, ok: p.requests - p.errors })),
    [overview.data],
  );
  const totals = overview.data?.totals;
  const rangeLabel = { "1h": "the last hour", "24h": "the last 24 hours", "7d": "the last 7 days" }[range];

  return (
    <div className="page">
      <header className="page__head">
        <div>
          <h1>Traffic</h1>
          <p className="page__lede">What the gateway handled over {rangeLabel}.</p>
        </div>
        <WindowSwitcher value={range} onChange={setRange} />
      </header>

      {overview.isError ? <ErrorNotice error={overview.error} /> : null}
      {overview.isPending ? <Loading what="traffic" /> : null}

      {totals ? (
        <>
          <section className="readouts" aria-label="Summary">
            <Readout label="Requests" value={formatCount(totals.requests)} />
            <Readout
              label="Error rate"
              value={formatPercent(totals.error_rate)}
              detail={`${formatCount(totals.errors)} failed`}
              tone={totals.error_rate >= 0.05 ? "fault" : undefined}
            />
            <Readout
              label="p95 latency"
              value={formatMs(totals.p95_ms)}
              detail={`median ${formatMs(totals.p50_ms)}`}
            />
            <Readout label="Spend" value={formatUsd(totals.cost_usd)} detail="estimated from token prices" />
            <Readout
              label="Served from cache"
              value={formatPercent(totals.cache_hit_rate)}
              detail="of all requests"
            />
            <Readout
              label="Tokens"
              value={formatCount(totals.prompt_tokens + totals.completion_tokens)}
              detail={`${formatCount(totals.prompt_tokens)} in, ${formatCount(totals.completion_tokens)} out`}
            />
          </section>

          {totals.requests === 0 ? (
            <Empty title={`No requests in ${rangeLabel}`}>
              <p>Send one through the gateway and it will show up here within a few seconds.</p>
              <pre className="snippet">
                <code>{SAMPLE_REQUEST}</code>
              </pre>
            </Empty>
          ) : (
            <>
              <section className="section">
                <div className="section__head">
                  <h2>Requests over time</h2>
                  <Legend
                    items={[
                      { label: "Succeeded", color: "var(--signal)" },
                      { label: "Failed", color: "var(--fault)" },
                    ]}
                  />
                </div>
                <TrafficRibbon data={data} range={range} />
              </section>

              <div className="split">
                <section className="section">
                  <div className="section__head">
                    <h2>p95 latency</h2>
                    <span className="section__note">Slowest 5% of requests</span>
                  </div>
                  <LatencyChart data={data} range={range} />
                </section>
                <section className="section">
                  <div className="section__head">
                    <h2>Spend</h2>
                    <span className="section__note">Cache hits cost nothing</span>
                  </div>
                  <SpendChart data={data} range={range} />
                </section>
              </div>

              <section className="section">
                <h2>By model</h2>
                {models.isError ? <ErrorNotice error={models.error} /> : null}
                {models.data ? <ModelsTable rows={models.data} /> : <Loading what="models" />}
              </section>
            </>
          )}
        </>
      ) : null}
    </div>
  );
}
