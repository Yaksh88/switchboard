"""Turn ApacheBench output and the gateway's access log into one results table.

Client-side numbers include the load generator (running on the same machine), so they are an
upper bound. The in-gateway numbers are the gateway's own measured time per request.
"""

from __future__ import annotations

import json
import re
import sys
from pathlib import Path


def percentile(values: list[float], q: float) -> float:
    ordered = sorted(values)
    return ordered[min(len(ordered) - 1, int(q * len(ordered)))]


def ms(value: float) -> str:
    # The access log records latency to 0.1 ms, so anything below that is just "under".
    return "<0.1 ms" if value < 0.1 else f"{value:.1f} ms"


def parse_ab(path: str) -> dict[str, float]:
    text = Path(path).read_text()
    rps = re.search(r"Requests per second:\s+([\d.]+)", text)
    failed = re.search(r"Failed requests:\s+(\d+)", text)
    non2xx = re.search(r"Non-2xx responses:\s+(\d+)", text)
    cuts = {int(p): float(ms) for p, ms in re.findall(r"^\s+(\d+)%\s+(\d+)", text, re.M)}
    if not rps:
        raise SystemExit(f"could not read ApacheBench output from {path}:\n{text[-500:]}")
    return {
        "rps": float(rps.group(1)),
        "p50": cuts.get(50, float("nan")),
        "p95": cuts.get(95, float("nan")),
        "p99": cuts.get(99, float("nan")),
        "failed": float(failed.group(1)) + (float(non2xx.group(1)) if non2xx else 0),
    }


def gateway_latencies(path: str) -> dict[str, list[float]]:
    by_cache: dict[str, list[float]] = {}
    for line in Path(path).read_text().splitlines():
        try:
            row = json.loads(line)
        except ValueError:
            continue
        if row.get("logger") == "switchboard.access":
            by_cache.setdefault(row["cache"], []).append(float(row["latency_ms"]))
    return by_cache


def main(uncached_ab: str, cached_ab: str, log: str) -> None:
    gateway = gateway_latencies(log)
    rows = [
        ("uncached", parse_ab(uncached_ab), gateway.get("bypass", [])),
        ("cached", parse_ab(cached_ab), gateway.get("exact", [])),
    ]

    print(
        "| Scenario | Requests/s | Client p50 | p95 | p99 | In-gateway p50 | p95 | p99 | Failed |"
    )
    print("| --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: |")
    for name, ab, times in rows:
        inside = (
            f"{ms(percentile(times, 0.5))} | {ms(percentile(times, 0.95))} "
            f"| {ms(percentile(times, 0.99))}"
            if times
            else "- | - | -"
        )
        print(
            f"| {name} | {ab['rps']:,.0f} | {ab['p50']:.0f} ms | {ab['p95']:.0f} ms "
            f"| {ab['p99']:.0f} ms | {inside} | {ab['failed']:.0f} |"
        )


if __name__ == "__main__":
    main(*sys.argv[1:4])
