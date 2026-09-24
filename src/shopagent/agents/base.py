from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol

from shopagent.domain.models import AgentResult, ChatMessage, Intent, IntentResult, SessionMemory


class Agent(Protocol):
    name: str

    async def execute(
        self, message: ChatMessage, intent: IntentResult, memory: SessionMemory
    ) -> AgentResult: ...


@dataclass(frozen=True, slots=True)
class AgentDescriptor:
    name: str
    description: str
    intents: tuple[Intent, ...]


class AgentRegistry:
    """Intent-to-agent registration boundary used by the orchestrator."""

    def __init__(self) -> None:
        self._agents: dict[str, Agent] = {}
        self._routes: dict[Intent, str] = {}
        self._descriptors: dict[str, AgentDescriptor] = {}

    def register(self, agent: Agent, *, description: str, intents: tuple[Intent, ...]) -> None:
        if agent.name in self._agents:
            raise ValueError(f"agent already registered: {agent.name}")
        conflicts = [intent for intent in intents if intent in self._routes]
        if conflicts:
            raise ValueError(f"intents already registered: {conflicts}")
        self._agents[agent.name] = agent
        self._descriptors[agent.name] = AgentDescriptor(agent.name, description, intents)
        self._routes.update({intent: agent.name for intent in intents})

    def resolve(self, intent: Intent) -> Agent | None:
        agent_name = self._routes.get(intent)
        return self._agents.get(agent_name) if agent_name else None

    def get(self, agent_name: str) -> Agent | None:
        return self._agents.get(agent_name)

    def descriptor(self, agent_name: str) -> AgentDescriptor | None:
        return self._descriptors.get(agent_name)

    def describe(self) -> list[dict[str, object]]:
        return [
            {
                "name": item.name,
                "description": item.description,
                "intents": [intent.value for intent in item.intents],
            }
            for item in self._descriptors.values()
        ]
