from __future__ import annotations

import argparse
import asyncio
import json
import math
import statistics
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from shopagent.container import build_container
from shopagent.domain.models import ChatMessage, Intent
from shopagent.settings import Settings


@dataclass(frozen=True, slots=True)
class AgentEvaluationCase:
    id: str
    content: str
    expected_intent: Intent
    expected_agent: str | None = None
    expected_handoff: bool = False
    required_tools: tuple[str, ...] = field(default_factory=tuple)
    user_id: str = "demo-user"

    @classmethod
    def from_dict(cls, value: dict[str, Any]) -> AgentEvaluationCase:
        return cls(
            id=str(value["id"]),
            content=str(value["content"]),
            expected_intent=Intent(value["expected_intent"]),
            expected_agent=value.get("expected_agent"),
            expected_handoff=bool(value.get("expected_handoff", False)),
            required_tools=tuple(value.get("required_tools", [])),
            user_id=str(value.get("user_id", "demo-user")),
        )


def load_cases(path: str | Path) -> list[AgentEvaluationCase]:
    cases: list[AgentEvaluationCase] = []
    with Path(path).open(encoding="utf-8") as source:
        for line_number, line in enumerate(source, 1):
            if not line.strip() or line.lstrip().startswith("#"):
                continue
            try:
                cases.append(AgentEvaluationCase.from_dict(json.loads(line)))
            except (KeyError, TypeError, ValueError, json.JSONDecodeError) as exc:
                raise ValueError(f"invalid evaluation case at line {line_number}") from exc
    if not cases:
        raise ValueError("evaluation dataset is empty")
    duplicate_ids = len(cases) - len({case.id for case in cases})
    if duplicate_ids:
        raise ValueError(f"evaluation dataset contains {duplicate_ids} duplicate ids")
    return cases


async def evaluate_cases(
    cases: list[AgentEvaluationCase], *, threshold: float = 0.80
) -> dict[str, Any]:
    container = build_container(Settings(low_confidence_threshold=threshold))
    intent_hits = 0
    routing_hits = 0
    routing_total = 0
    completed = 0
    automatable_total = 0
    observed_handoffs = 0
    expected_handoffs = 0
    true_handoffs = 0
    latencies: list[float] = []
    failures: list[dict[str, Any]] = []

    try:
        for index, case in enumerate(cases):
            response = await container.orchestrator.handle(
                ChatMessage(
                    user_id=case.user_id,
                    session_id=f"eval-{index}-{case.id}",
                    content=case.content,
                )
            )
            intent_ok = response.intent.intent == case.expected_intent
            intent_hits += int(intent_ok)
            latencies.append(response.latency_ms)

            if case.expected_agent is not None:
                routing_total += 1
                routing_hits += int(response.routed_agent == case.expected_agent)

            if case.expected_handoff:
                expected_handoffs += 1
            if response.need_human:
                observed_handoffs += 1
            if case.expected_handoff and response.need_human:
                true_handoffs += 1

            task_ok = False
            if not case.expected_handoff:
                automatable_total += 1
                task_ok = (
                    intent_ok
                    and response.routed_agent == case.expected_agent
                    and not response.need_human
                    and bool(response.answer.strip())
                    and not response.data.get("error")
                    and not response.data.get("missing_fields")
                    and set(case.required_tools).issubset(response.tools_used)
                )
                completed += int(task_ok)

            if (
                not intent_ok
                or (
                    case.expected_agent is not None and response.routed_agent != case.expected_agent
                )
                or response.need_human != case.expected_handoff
                or (not case.expected_handoff and not task_ok)
            ):
                failures.append(
                    {
                        "id": case.id,
                        "expected_intent": case.expected_intent.value,
                        "actual_intent": response.intent.intent.value,
                        "confidence": response.intent.confidence,
                        "expected_agent": case.expected_agent,
                        "actual_agent": response.routed_agent,
                        "expected_handoff": case.expected_handoff,
                        "actual_handoff": response.need_human,
                        "routing_decision": response.routing_decision,
                        "tools_used": response.tools_used,
                    }
                )
    finally:
        for component in (
            container.memory,
            container.operations,
            container.knowledge_repository,
            container.commerce,
        ):
            await component.close()

    tool_values = list(container.tools.metrics().values())
    tool_calls = sum(int(value["calls"]) for value in tool_values)
    tool_successes = sum(int(value["successes"]) for value in tool_values)
    return {
        "dataset_cases": len(cases),
        "threshold": threshold,
        "intent_accuracy": _ratio(intent_hits, len(cases)),
        "routing_accuracy": _ratio(routing_hits, routing_total),
        "automated_task_completion_rate": _ratio(completed, automatable_total),
        "observed_handoff_rate": _ratio(observed_handoffs, len(cases)),
        "handoff_precision": _ratio(true_handoffs, observed_handoffs),
        "handoff_recall": _ratio(true_handoffs, expected_handoffs),
        "tool_success_rate": _ratio(tool_successes, tool_calls),
        "latency_ms": {
            "p50": round(statistics.median(latencies), 3),
            "p95": round(_percentile(latencies, 0.95), 3),
            "max": round(max(latencies), 3),
        },
        "counts": {
            "intent_correct": intent_hits,
            "routing_correct": routing_hits,
            "routing_evaluated": routing_total,
            "automated_tasks_completed": completed,
            "automatable_tasks": automatable_total,
            "human_handoffs": observed_handoffs,
            "expected_handoffs": expected_handoffs,
            "tool_calls": tool_calls,
            "tool_successes": tool_successes,
        },
        "failed_cases": failures,
        "scope": "deterministic offline regression; not production traffic",
    }


async def calibrate_threshold(
    cases: list[AgentEvaluationCase],
    *,
    candidates: tuple[float, ...] = (0.50, 0.55, 0.60, 0.70, 0.75, 0.80, 0.85, 0.90, 0.95),
    false_accept_cost: int = 5,
    false_handoff_cost: int = 1,
    safety_margin: float = 0.05,
) -> dict[str, Any]:
    container = build_container(Settings())
    classified: list[tuple[AgentEvaluationCase, Intent, float]] = []
    try:
        for case in cases:
            result = await container.orchestrator.classify(case.content)
            classified.append((case, result.intent, result.confidence))
    finally:
        for component in (
            container.memory,
            container.operations,
            container.knowledge_repository,
            container.commerce,
        ):
            await component.close()

    rows: list[dict[str, Any]] = []
    for threshold in candidates:
        false_accepts = 0
        false_handoffs = 0
        correct_routes = 0
        for case, predicted_intent, confidence in classified:
            auto_route = predicted_intent != Intent.UNKNOWN and confidence >= threshold
            should_auto_route = not case.expected_handoff
            false_accepts += int(auto_route and not should_auto_route)
            false_handoffs += int(not auto_route and should_auto_route)
            correct_routes += int(auto_route == should_auto_route)
        rows.append(
            {
                "threshold": threshold,
                "routing_accuracy": _ratio(correct_routes, len(cases)),
                "false_accepts": false_accepts,
                "false_handoffs": false_handoffs,
                "weighted_cost": false_accepts * false_accept_cost
                + false_handoffs * false_handoff_cost,
            }
        )

    minimum_cost = min(row["weighted_cost"] for row in rows)
    best_rows = [row for row in rows if row["weighted_cost"] == minimum_cost]
    positive_confidences = [
        confidence
        for case, predicted_intent, confidence in classified
        if not case.expected_handoff and predicted_intent == case.expected_intent
    ]
    safety_ceiling = min(positive_confidences) - safety_margin if positive_confidences else 0.80
    eligible = [row for row in best_rows if row["threshold"] <= safety_ceiling]
    recommended = max(eligible or best_rows, key=lambda row: row["threshold"])
    return {
        "dataset_cases": len(cases),
        "cost_policy": {
            "false_accept_cost": false_accept_cost,
            "false_handoff_cost": false_handoff_cost,
            "safety_margin": safety_margin,
        },
        "recommended_threshold": recommended["threshold"],
        "selection_reason": (
            "minimum weighted routing cost, then highest threshold that preserves a safety "
            "margin below the weakest correctly classified automatable case"
        ),
        "candidates": rows,
        "scope": "recalibrate for every intent model, prompt, label set and traffic distribution",
    }


def _ratio(numerator: int, denominator: int) -> float | None:
    return round(numerator / denominator, 4) if denominator else None


def _percentile(values: list[float], quantile: float) -> float:
    ordered = sorted(values)
    rank = max(0, math.ceil(len(ordered) * quantile) - 1)
    return ordered[rank]


def run() -> None:
    parser = argparse.ArgumentParser(description="ShopAgent agent quality evaluation")
    parser.add_argument("mode", choices=("evaluate", "calibrate"))
    parser.add_argument(
        "--dataset", default="evaluation/agent_cases.jsonl", help="JSONL evaluation dataset"
    )
    parser.add_argument("--threshold", type=float, default=0.80)
    args = parser.parse_args()
    cases = load_cases(args.dataset)
    if args.mode == "evaluate":
        report = asyncio.run(evaluate_cases(cases, threshold=args.threshold))
    else:
        report = asyncio.run(calibrate_threshold(cases))
    print(json.dumps(report, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    run()
