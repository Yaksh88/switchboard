import type { Window } from "../api/types";

const compact = new Intl.NumberFormat("en", { notation: "compact", maximumFractionDigits: 1 });
const whole = new Intl.NumberFormat("en");

export const formatCount = (n: number): string => (n < 10_000 ? whole.format(n) : compact.format(n));

export function formatMs(ms: number): string {
  if (ms < 1) return "0 ms";
  if (ms < 1000) return `${Math.round(ms)} ms`;
  return `${(ms / 1000).toFixed(ms < 10_000 ? 2 : 1)} s`;
}

export function formatUsd(usd: number | string): string {
  const value = Number(usd);
  if (value === 0) return "$0";
  if (value < 0.01) return `$${value.toFixed(4)}`;
  if (value < 100) return `$${value.toFixed(2)}`;
  return `$${whole.format(Math.round(value))}`;
}

export function formatPercent(fraction: number): string {
  const pct = fraction * 100;
  if (pct === 0) return "0%";
  if (pct < 10) return `${pct.toFixed(1)}%`;
  return `${Math.round(pct)}%`;
}

const relative = new Intl.RelativeTimeFormat("en", { numeric: "auto", style: "narrow" });

export function formatAgo(iso: string, now = Date.now()): string {
  const seconds = Math.round((new Date(iso).getTime() - now) / 1000);
  const abs = Math.abs(seconds);
  if (abs < 45) return "just now";
  if (abs < 3600) return relative.format(Math.round(seconds / 60), "minute");
  if (abs < 86_400) return relative.format(Math.round(seconds / 3600), "hour");
  return relative.format(Math.round(seconds / 86_400), "day");
}

export const formatDateTime = (iso: string): string =>
  new Date(iso).toLocaleString("en", {
    month: "short",
    day: "numeric",
    hour: "2-digit",
    minute: "2-digit",
    second: "2-digit",
  });

export function formatTick(iso: string, window: Window): string {
  const date = new Date(iso);
  if (window === "7d") return date.toLocaleDateString("en", { weekday: "short", day: "numeric" });
  return date.toLocaleTimeString("en", { hour: "2-digit", minute: "2-digit", hour12: false });
}
