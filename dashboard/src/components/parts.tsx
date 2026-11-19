import type { ReactNode } from "react";
import { Link } from "react-router-dom";

import { ApiError } from "../api/client";
import type { CacheStatus, Window } from "../api/types";
import { formatPercent } from "../lib/format";

export function Readout({
  label,
  value,
  detail,
  tone,
}: {
  label: string;
  value: string;
  detail?: string;
  tone?: "fault";
}) {
  return (
    <div className="readout">
      <span className="readout__label">{label}</span>
      <span className={`readout__value expanded${tone ? ` readout__value--${tone}` : ""}`}>{value}</span>
      {detail ? <span className="readout__detail">{detail}</span> : null}
    </div>
  );
}

const WINDOWS: { value: Window; label: string }[] = [
  { value: "1h", label: "1 hour" },
  { value: "24h", label: "24 hours" },
  { value: "7d", label: "7 days" },
];

export function WindowSwitcher({ value, onChange }: { value: Window; onChange: (w: Window) => void }) {
  return (
    <div className="segmented" role="group" aria-label="Time window">
      {WINDOWS.map((option) => (
        <button
          key={option.value}
          type="button"
          className="segmented__option"
          aria-pressed={value === option.value}
          onClick={() => onChange(option.value)}
        >
          {option.label}
        </button>
      ))}
    </div>
  );
}

export function StatusBadge({ code }: { code: number }) {
  const tone = code < 400 ? "ok" : code === 429 || code === 499 || code < 500 ? "warn" : "fault";
  const hint: Record<number, string> = {
    429: "Rate limited or over budget",
    499: "Client disconnected",
    503: "No upstream available",
  };
  return (
    <span className={`badge badge--${tone}`} title={hint[code]}>
      {code}
    </span>
  );
}

export function CacheBadge({ status }: { status: CacheStatus }) {
  const hit = status === "exact" || status === "semantic";
  return <span className={`badge badge--plain${hit ? " badge--ok" : ""}`}>{status}</span>;
}

export function Meter({ fraction, tone }: { fraction: number; tone?: "fault" }) {
  const width = `${Math.min(100, Math.max(0, fraction * 100))}%`;
  return (
    <span className="meter">
      <span>{formatPercent(fraction)}</span>
      <span className="meter__track" aria-hidden="true">
        <span className={`meter__fill${tone ? ` meter__fill--${tone}` : ""}`} style={{ width, display: "block" }} />
      </span>
    </span>
  );
}

export function Empty({ title, children }: { title: string; children?: ReactNode }) {
  return (
    <div className="empty">
      <p className="empty__title expanded">{title}</p>
      {children ? <div className="empty__body">{children}</div> : null}
    </div>
  );
}

export function ErrorNotice({ error }: { error: unknown }) {
  if (error instanceof ApiError && error.status === 503) {
    return (
      <div className="notice notice--amber" role="alert">
        <p className="notice__title">The admin API is turned off</p>
        <p className="notice__body">{error.message}</p>
      </div>
    );
  }
  if (error instanceof ApiError && error.status === 403) {
    return (
      <div className="notice notice--fault" role="alert">
        <p className="notice__title">This token can't open the dashboard</p>
        <p className="notice__body">
          Check the admin token in <Link to="/settings">Settings</Link>.
        </p>
      </div>
    );
  }
  const message = error instanceof Error ? error.message : "Something went wrong.";
  return (
    <div className="notice notice--fault" role="alert">
      <p className="notice__title">Couldn't load this data</p>
      <p className="notice__body">{message}</p>
    </div>
  );
}

export function Loading({ what }: { what: string }) {
  return (
    <p className="muted" role="status">
      Loading {what}
    </p>
  );
}

interface TooltipRow {
  name: string;
  value: string;
  color: string;
}

export function ChartTooltip({ title, rows }: { title: string; rows: TooltipRow[] }) {
  return (
    <div className="tooltip">
      <p className="tooltip__title">{title}</p>
      {rows.map((row) => (
        <div className="tooltip__row" key={row.name}>
          <span>
            <span
              className="legend__swatch"
              style={{ background: row.color, display: "inline-block", marginRight: 6 }}
            />
            {row.name}
          </span>
          <span>{row.value}</span>
        </div>
      ))}
    </div>
  );
}
