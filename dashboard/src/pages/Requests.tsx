import { useCallback, useMemo, useState } from "react";

import { useModels, useRequests } from "../api/hooks";
import type { CacheStatus, RequestFilters, RequestLog } from "../api/types";
import { Drawer } from "../components/Drawer";
import { CacheBadge, Empty, ErrorNotice, Loading, StatusBadge } from "../components/parts";
import {
  formatAgo,
  formatCount,
  formatDateTime,
  formatMs,
  formatUsd,
} from "../lib/format";

const CACHE_OPTIONS: CacheStatus[] = ["miss", "exact", "semantic", "bypass"];

function RequestDetail({ request }: { request: RequestLog }) {
  const served = request.provider
    ? request.upstream_model
      ? `${request.provider} (${request.upstream_model})`
      : request.provider
    : "No upstream reached";
  return (
    <>
      <dl className="facts">
        <dt>Time</dt>
        <dd>{formatDateTime(request.created_at)}</dd>
        <dt>Request ID</dt>
        <dd className="code">{request.request_id}</dd>
        <dt>Model</dt>
        <dd>{request.model_alias}</dd>
        <dt>Served by</dt>
        <dd>{served}</dd>
        <dt>Status</dt>
        <dd>
          <StatusBadge code={request.status_code} />
        </dd>
        <dt>Attempts</dt>
        <dd>{request.attempts}</dd>
        <dt>Cache</dt>
        <dd>
          <CacheBadge status={request.cache} />
        </dd>
        <dt>Streaming</dt>
        <dd>{request.stream ? "Yes" : "No"}</dd>
        <dt>Latency</dt>
        <dd>{formatMs(request.latency_ms)}</dd>
        {request.ttft_ms !== null ? (
          <>
            <dt>First token</dt>
            <dd>{formatMs(request.ttft_ms)}</dd>
          </>
        ) : null}
        <dt>Tokens</dt>
        <dd>
          {formatCount(request.prompt_tokens)} in, {formatCount(request.completion_tokens)} out
        </dd>
        <dt>Cost</dt>
        <dd>{formatUsd(request.cost_usd)}</dd>
        <dt>API key</dt>
        <dd className={request.api_key_id ? "code" : "muted"}>{request.api_key_id ?? "None"}</dd>
      </dl>
      {request.error ? (
        <div>
          <h3>Error</h3>
          <pre className="block">{request.error}</pre>
        </div>
      ) : null}
    </>
  );
}

export function Requests() {
  const [filters, setFilters] = useState<RequestFilters>({});
  const [selected, setSelected] = useState<RequestLog | null>(null);
  const requests = useRequests(filters);
  const models = useModels("7d");
  const close = useCallback(() => setSelected(null), []);

  const rows = useMemo(() => requests.data?.pages.flatMap((page) => page.items) ?? [], [requests.data]);
  const filtered = Object.values(filters).some(Boolean);

  const update = <K extends keyof RequestFilters>(key: K, value: string) =>
    setFilters((current) => {
      const next = { ...current };
      if (value) next[key] = value as RequestFilters[K];
      else delete next[key];
      return next;
    });

  return (
    <div className="page">
      <header className="page__head">
        <div>
          <h1>Requests</h1>
          <p className="page__lede">Every request the gateway handled, newest first.</p>
        </div>
        <div className="filters">
          <label className="field">
            Model
            <select
              className="input"
              value={filters.model ?? ""}
              onChange={(e) => update("model", e.target.value)}
            >
              <option value="">All models</option>
              {models.data?.map((m) => (
                <option key={m.model} value={m.model}>
                  {m.model}
                </option>
              ))}
            </select>
          </label>
          <label className="field">
            Outcome
            <select
              className="input"
              value={filters.status ?? ""}
              onChange={(e) => update("status", e.target.value)}
            >
              <option value="">All outcomes</option>
              <option value="ok">Succeeded</option>
              <option value="error">Failed</option>
            </select>
          </label>
          <label className="field">
            Cache
            <select
              className="input"
              value={filters.cache ?? ""}
              onChange={(e) => update("cache", e.target.value)}
            >
              <option value="">Any</option>
              {CACHE_OPTIONS.map((option) => (
                <option key={option} value={option}>
                  {option}
                </option>
              ))}
            </select>
          </label>
        </div>
      </header>

      {requests.isError ? <ErrorNotice error={requests.error} /> : null}
      {requests.isPending ? <Loading what="requests" /> : null}

      {requests.data && rows.length === 0 ? (
        <Empty title={filtered ? "No requests match these filters" : "No requests yet"}>
          {filtered ? (
            <button type="button" className="button button--quiet" onClick={() => setFilters({})}>
              Clear filters
            </button>
          ) : (
            <p>Requests appear here as soon as clients call the gateway.</p>
          )}
        </Empty>
      ) : null}

      {rows.length > 0 ? (
        <section className="section" aria-label="Request list">
          <div className="table-wrap">
            <table className="table">
              <thead>
                <tr>
                  <th scope="col">When</th>
                  <th scope="col">Request</th>
                  <th scope="col">Model</th>
                  <th scope="col">Served by</th>
                  <th scope="col">Status</th>
                  <th scope="col" className="table__num">Latency</th>
                  <th scope="col" className="table__num">Tokens</th>
                  <th scope="col" className="table__num">Cost</th>
                  <th scope="col">Cache</th>
                </tr>
              </thead>
              <tbody>
                {rows.map((row) => (
                  <tr
                    key={row.id}
                    className={`table__row--click${selected?.id === row.id ? " table__row--selected" : ""}`}
                    onClick={() => setSelected(row)}
                  >
                    <td title={formatDateTime(row.created_at)}>{formatAgo(row.created_at)}</td>
                    <td>
                      <button
                        type="button"
                        className="code table__id"
                        style={{ background: "none", border: 0, padding: 0 }}
                        onClick={(e) => {
                          e.stopPropagation();
                          setSelected(row);
                        }}
                      >
                        {row.request_id}
                      </button>
                    </td>
                    <td>{row.model_alias}</td>
                    <td>{row.provider ?? <span className="muted">none</span>}</td>
                    <td>
                      <StatusBadge code={row.status_code} />
                    </td>
                    <td className="table__num">{formatMs(row.latency_ms)}</td>
                    <td className="table__num">
                      {formatCount(row.prompt_tokens + row.completion_tokens)}
                    </td>
                    <td className="table__num">{formatUsd(row.cost_usd)}</td>
                    <td>
                      <CacheBadge status={row.cache} />
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
          <div>
            {requests.hasNextPage ? (
              <button
                type="button"
                className="button button--quiet"
                onClick={() => void requests.fetchNextPage()}
                disabled={requests.isFetchingNextPage}
              >
                {requests.isFetchingNextPage ? "Loading more" : "Load more"}
              </button>
            ) : (
              <p className="muted">That's every request.</p>
            )}
          </div>
        </section>
      ) : null}

      {selected ? (
        <Drawer title="Request" subtitle={selected.request_id} onClose={close}>
          <RequestDetail request={selected} />
        </Drawer>
      ) : null}
    </div>
  );
}
