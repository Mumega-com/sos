#!/usr/bin/env python3
"""load-test-dispatcher — hammer the dispatcher, measure latency + fail rate.

Works against both impls:
  python3 scripts/load-test-dispatcher.py http://localhost:6071 <token>
  python3 scripts/load-test-dispatcher.py https://mcp.mumega.com <token>

Outputs p50/p95/p99 latency, RPS, fail rate, 429 count (rate-limit working?).

Does NOT hammer production without --confirm-prod. Respects Hadi's budget.
"""
from __future__ import annotations

import argparse
import asyncio
import statistics
import sys
import time
from dataclasses import dataclass
from typing import Optional

import httpx


@dataclass
class Result:
    status: int
    latency_ms: float
    error: Optional[str] = None


async def one_request(client: httpx.AsyncClient, url: str) -> Result:
    start = time.monotonic()
    try:
        resp = await client.get(url, timeout=10.0)
        return Result(status=resp.status_code, latency_ms=(time.monotonic() - start) * 1000)
    except httpx.TimeoutException:
        return Result(status=0, latency_ms=(time.monotonic() - start) * 1000, error="timeout")
    except Exception as exc:
        return Result(status=0, latency_ms=(time.monotonic() - start) * 1000, error=str(exc)[:80])


async def load_test(
    base_url: str,
    token: str,
    rps: int,
    duration_s: int,
    endpoint: str,
) -> list[Result]:
    """Fire rps requests per second for duration_s seconds, concurrently."""
    url = f"{base_url.rstrip('/')}{endpoint}".replace("<token>", token)

    async with httpx.AsyncClient() as client:
        results: list[Result] = []
        interval = 1.0 / max(rps, 1)
        end_time = time.monotonic() + duration_s
        tasks: list[asyncio.Task] = []

        while time.monotonic() < end_time:
            task = asyncio.create_task(one_request(client, url))
            tasks.append(task)
            await asyncio.sleep(interval)

        results = await asyncio.gather(*tasks)
        return results


def summarize(results: list[Result], duration_s: int) -> None:
    n = len(results)
    by_status: dict[int, int] = {}
    for r in results:
        by_status[r.status] = by_status.get(r.status, 0) + 1

    latencies = sorted(r.latency_ms for r in results)

    def pct(p: float) -> float:
        idx = int(n * p / 100)
        return latencies[min(idx, n - 1)] if n else 0

    print(f"\n{'='*60}")
    print(f"Total requests: {n}")
    print(f"Duration:       {duration_s}s")
    print(f"Effective RPS:  {n / duration_s:.1f}")
    print(f"\nLatency (ms):")
    print(f"  min    {latencies[0]:.1f}" if latencies else "  (no samples)")
    print(f"  p50    {pct(50):.1f}")
    print(f"  p95    {pct(95):.1f}")
    print(f"  p99    {pct(99):.1f}")
    print(f"  max    {latencies[-1]:.1f}" if latencies else "")
    print(f"\nStatus distribution:")
    for status, count in sorted(by_status.items()):
        emoji = "✓" if status == 200 else "✗"
        print(f"  {emoji} {status}: {count} ({count*100/n:.1f}%)")

    error_count = sum(1 for r in results if r.error)
    if error_count:
        print(f"\nErrors: {error_count}")
        by_err: dict[str, int] = {}
        for r in results:
            if r.error:
                by_err[r.error] = by_err.get(r.error, 0) + 1
        for err, count in sorted(by_err.items(), key=lambda kv: -kv[1])[:5]:
            print(f"  {count}x {err}")

    rate_limit_hits = by_status.get(429, 0)
    if rate_limit_hits:
        print(f"\n429 responses: {rate_limit_hits} — rate limiter IS firing (expected if plan threshold exceeded)")


async def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("base_url", help="Dispatcher URL, e.g. http://localhost:6071")
    ap.add_argument("token", help="Test bearer token (raw, not hash)")
    ap.add_argument("--rps", type=int, default=5, help="Target requests per second (default 5)")
    ap.add_argument("--duration", type=int, default=30, help="Test duration in seconds (default 30)")
    ap.add_argument("--endpoint", default="/health", help="Endpoint to hit (/health, /sse/<token>, /mcp/<token>)")
    ap.add_argument("--confirm-prod", action="store_true",
                    help="Required if base_url contains mumega.com (avoid accidentally hammering production)")
    args = ap.parse_args()

    is_prod = any(host in args.base_url for host in ("mumega.com", "mumega.ai"))
    if is_prod and not args.confirm_prod:
        sys.exit("Refusing to load-test a production hostname without --confirm-prod.")

    total = args.rps * args.duration
    if total > 10_000 and not args.confirm_prod:
        sys.exit(f"Total request budget {total} > 10,000. Add --confirm-prod if intentional.")

    print(f"Target: {args.base_url}{args.endpoint}")
    print(f"Plan:   {args.rps} rps × {args.duration}s = {total} total requests")
    print(f"Start:  {time.strftime('%Y-%m-%d %H:%M:%S')}")

    results = await load_test(args.base_url, args.token, args.rps, args.duration, args.endpoint)
    summarize(results, args.duration)


if __name__ == "__main__":
    asyncio.run(main())
