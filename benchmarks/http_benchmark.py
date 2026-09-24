from __future__ import annotations

import argparse
import asyncio
import json
import math
import platform
import statistics
import sys
import time
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import httpx

SCENARIOS = (
    ("product_detail", "SKU-1001 的规格参数"),
    ("stock", "SKU-1001 有库存吗"),
    ("order", "查询订单 ORD-20260001"),
    ("refund", "退款进度 ORD-20260002"),
    ("after_sales_policy", "退货规则是什么"),
    ("recommendation", "推荐适合通勤的降噪耳机"),
    ("greeting", "你好"),
)


async def run_stage(
    client: httpx.AsyncClient,
    *,
    base_url: str,
    concurrency: int,
    requests: int,
    stage_name: str,
    bearer_token: str,
) -> dict[str, Any]:
    queue: asyncio.Queue[int] = asyncio.Queue()
    for request_number in range(requests):
        queue.put_nowait(request_number)

    latencies: list[float] = []
    status_counts: dict[int, int] = {}
    failures: list[str] = []
    http_successes = 0
    task_successes = 0
    headers = {"Authorization": f"Bearer {bearer_token}"} if bearer_token else {}

    async def worker(worker_number: int) -> None:
        nonlocal http_successes, task_successes
        while True:
            try:
                request_number = queue.get_nowait()
            except asyncio.QueueEmpty:
                return
            scenario, content = SCENARIOS[request_number % len(SCENARIOS)]
            started = time.perf_counter()
            try:
                response = await client.post(
                    f"{base_url.rstrip('/')}/api/v1/chat",
                    headers={
                        **headers,
                        "Idempotency-Key": f"bench-{stage_name}-{request_number}",
                    },
                    json={
                        "channel": "web",
                        "user_id": f"bench-user-{worker_number}-{request_number}",
                        "session_id": f"bench-session-{stage_name}-{request_number}",
                        "content": content,
                        "context": {"benchmark_scenario": scenario},
                    },
                )
                latency_ms = (time.perf_counter() - started) * 1000
                latencies.append(latency_ms)
                status_counts[response.status_code] = status_counts.get(response.status_code, 0) + 1
                if response.status_code != 200:
                    failures.append(f"HTTP {response.status_code}: {response.text[:160]}")
                else:
                    http_successes += 1
                    payload = response.json()
                    if not payload.get("answer") or not payload.get("trace_id"):
                        failures.append("HTTP 200 without answer or trace_id")
                    elif payload.get("need_human") or (payload.get("data") or {}).get("error"):
                        failures.append(
                            "HTTP 200 but business task degraded: "
                            f"{payload.get('routing_decision', 'unknown')}"
                        )
                    else:
                        task_successes += 1
            except Exception as exc:  # noqa: BLE001 - benchmark must count transport failures
                latencies.append((time.perf_counter() - started) * 1000)
                failures.append(f"{type(exc).__name__}: {exc}")
            finally:
                queue.task_done()

    started = time.perf_counter()
    await asyncio.gather(*(worker(index) for index in range(concurrency)))
    elapsed = time.perf_counter() - started
    return {
        "concurrency": concurrency,
        "requests": requests,
        "http_successful_requests": http_successes,
        "http_success_rate": round(http_successes / requests, 4),
        "task_successful_requests": task_successes,
        "task_success_rate": round(task_successes / requests, 4),
        "failed_or_degraded_requests": len(failures),
        "elapsed_seconds": round(elapsed, 3),
        "throughput_rps": round(requests / elapsed, 2),
        "latency_ms": {
            "mean": round(statistics.fmean(latencies), 2),
            "p50": round(_percentile(latencies, 0.50), 2),
            "p95": round(_percentile(latencies, 0.95), 2),
            "p99": round(_percentile(latencies, 0.99), 2),
            "max": round(max(latencies), 2),
        },
        "status_counts": {str(key): value for key, value in sorted(status_counts.items())},
        "failure_samples": failures[:10],
    }


async def benchmark(args: argparse.Namespace) -> dict[str, Any]:
    concurrency_levels = tuple(int(value) for value in args.concurrency.split(","))
    limits = httpx.Limits(
        max_connections=max(concurrency_levels),
        max_keepalive_connections=max(concurrency_levels),
    )
    timeout = httpx.Timeout(args.timeout)
    async with httpx.AsyncClient(limits=limits, timeout=timeout, trust_env=False) as client:
        for index in range(args.warmup):
            await client.get(f"{args.base_url.rstrip('/')}/health")
        stages = []
        for concurrency in concurrency_levels:
            stages.append(
                await run_stage(
                    client,
                    base_url=args.base_url,
                    concurrency=concurrency,
                    requests=args.requests,
                    stage_name=f"c{concurrency}",
                    bearer_token=args.bearer_token,
                )
            )
    return {
        "generated_at": datetime.now(UTC).isoformat(),
        "profile": args.profile,
        "base_url": args.base_url,
        "environment": {
            "platform": platform.platform(),
            "processor": platform.processor() or "unknown",
            "python": sys.version.split()[0],
        },
        "method": {
            "endpoint": "POST /api/v1/chat",
            "request_mix": [name for name, _ in SCENARIOS],
            "warmup_requests": args.warmup,
            "requests_per_stage": args.requests,
            "timeout_seconds": args.timeout,
        },
        "stages": stages,
        "limitations": [
            "This client-side baseline measures one running deployment and is not a capacity promise.",
            "Production acceptance must use production-equivalent A2A/MCP, Redis, MySQL and model endpoints.",
            "Run a separate soak test and dependency fault-injection test before release.",
        ],
    }


def _percentile(values: list[float], quantile: float) -> float:
    ordered = sorted(values)
    rank = max(0, math.ceil(len(ordered) * quantile) - 1)
    return ordered[rank]


def main() -> None:
    parser = argparse.ArgumentParser(description="ShopAgent HTTP concurrency benchmark")
    parser.add_argument("--base-url", default="http://127.0.0.1:8080")
    parser.add_argument("--concurrency", default="20,50,100")
    parser.add_argument("--requests", type=int, default=500, help="requests per stage")
    parser.add_argument("--warmup", type=int, default=20)
    parser.add_argument("--timeout", type=float, default=10.0)
    parser.add_argument("--bearer-token", default="")
    parser.add_argument("--profile", default="unspecified")
    parser.add_argument("--output", default="")
    args = parser.parse_args()
    if args.requests < 1 or args.warmup < 0:
        parser.error("requests must be positive and warmup cannot be negative")
    levels = [value.strip() for value in args.concurrency.split(",") if value.strip()]
    if not levels or any(not value.isdigit() or int(value) < 1 for value in levels):
        parser.error("concurrency must be a comma-separated list of positive integers")
    report = asyncio.run(benchmark(args))
    output = json.dumps(report, ensure_ascii=False, indent=2)
    if args.output:
        destination = Path(args.output)
        destination.parent.mkdir(parents=True, exist_ok=True)
        destination.write_text(output + "\n", encoding="utf-8")
    print(output)


if __name__ == "__main__":
    main()
