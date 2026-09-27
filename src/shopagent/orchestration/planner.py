from __future__ import annotations

import asyncio
import re
from collections.abc import Awaitable, Callable
from dataclasses import dataclass

from shopagent.domain.models import IntentResult

IntentClassifier = Callable[[str], Awaitable[IntentResult]]


@dataclass(frozen=True, slots=True)
class PlannedTask:
    id: str
    content: str
    intent: IntentResult
    position: int
    depends_on: tuple[str, ...] = ()
    condition: str | None = None


@dataclass(frozen=True, slots=True)
class ExecutionPlan:
    tasks: tuple[PlannedTask, ...]

    @property
    def is_multi(self) -> bool:
        return len(self.tasks) > 1

    @property
    def is_parallel(self) -> bool:
        return self.is_multi and all(not task.depends_on for task in self.tasks)


@dataclass(frozen=True, slots=True)
class _Segment:
    content: str
    depends_on_position: int | None = None
    condition: str | None = None


class QueryPlanner:
    """Deterministic query splitter for auditable multi-domain orchestration.

    It deliberately handles only explicit Chinese coordination/condition markers.
    Ambiguous sentences stay as one task and follow the normal confidence/handoff
    path instead of being aggressively split into unsafe actions.
    """

    _conditional = re.compile(r"^(?P<head>.+?)[，,]\s*如果(?P<condition>.+?)就(?P<tail>.+)$")
    _independent_separator = re.compile(r"(?:[，,]\s*)?(?:另外|同时|再(?:帮我)?|并且|还有|然后)\s*")
    _sentence_separator = re.compile(r"[；;。]+")

    def __init__(self, max_tasks: int = 5) -> None:
        self._max_tasks = max_tasks

    async def build(self, content: str, classifier: IntentClassifier) -> ExecutionPlan:
        segments = self._split(content)
        intents = await asyncio.gather(*(classifier(item.content) for item in segments))

        inherited_entities: dict[str, str] = {}
        tasks: list[PlannedTask] = []
        for position, (segment, intent) in enumerate(zip(segments, intents, strict=True)):
            merged_entities = {**inherited_entities, **intent.entities}
            inherited_entities.update(intent.entities)
            normalized_intent = intent.model_copy(update={"entities": merged_entities})
            dependency = (
                (f"task-{segment.depends_on_position + 1}",)
                if segment.depends_on_position is not None
                else ()
            )
            tasks.append(
                PlannedTask(
                    id=f"task-{position + 1}",
                    content=segment.content,
                    intent=normalized_intent,
                    position=position,
                    depends_on=dependency,
                    condition=segment.condition,
                )
            )
        return ExecutionPlan(tasks=tuple(tasks))

    def _split(self, content: str) -> list[_Segment]:
        text = content.strip()
        conditional = self._conditional.match(text)
        if conditional:
            head = conditional.group("head").strip()
            tail = conditional.group("tail").strip()
            condition = conditional.group("condition").strip()
            if head and tail:
                return [
                    _Segment(head),
                    _Segment(tail, depends_on_position=0, condition=condition),
                ][: self._max_tasks]

        raw_sentences = self._sentence_separator.split(text)
        parts: list[str] = []
        for sentence in raw_sentences:
            parts.extend(self._independent_separator.split(sentence))
        normalized = [part.strip(" ，,。；;") for part in parts if part.strip(" ，,。；;")]
        if len(normalized) <= 1:
            return [_Segment(text)]
        return [_Segment(part) for part in normalized[: self._max_tasks]]
