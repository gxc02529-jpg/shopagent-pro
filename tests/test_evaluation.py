import asyncio
from pathlib import Path

from shopagent.evaluation import calibrate_threshold, evaluate_cases, load_cases

DATASET = Path(__file__).parents[1] / "evaluation" / "agent_cases.jsonl"


def test_agent_evaluation_dataset_and_metrics_are_reproducible():
    cases = load_cases(DATASET)
    report = asyncio.run(evaluate_cases(cases, threshold=0.80))

    assert report["dataset_cases"] == 56
    assert report["intent_accuracy"] >= 0.95
    assert report["routing_accuracy"] >= 0.95
    assert report["automated_task_completion_rate"] >= 0.90
    assert report["handoff_recall"] == 1.0
    assert report["tool_success_rate"] == 1.0


def test_threshold_calibration_is_cost_based_and_recommends_checked_in_default():
    cases = load_cases(DATASET)
    report = asyncio.run(calibrate_threshold(cases))

    assert report["recommended_threshold"] == 0.80
    selected = next(
        row for row in report["candidates"] if row["threshold"] == report["recommended_threshold"]
    )
    assert selected["false_accepts"] == 0
    assert selected["false_handoffs"] == 0
