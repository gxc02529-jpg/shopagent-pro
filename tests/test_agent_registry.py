import pytest

from shopagent.agents.base import AgentRegistry
from shopagent.container import build_container
from shopagent.domain.models import Intent
from shopagent.settings import Settings


def test_all_business_intents_are_registered():
    registry = build_container(Settings()).agents
    expected = {
        Intent.PRODUCT_SEARCH: "product_agent",
        Intent.PRODUCT_DETAIL: "product_agent",
        Intent.STOCK_QUERY: "product_agent",
        Intent.RECOMMENDATION: "recommendation_agent",
        Intent.ORDER_QUERY: "order_agent",
        Intent.AFTER_SALES: "after_sales_agent",
    }
    assert {intent: registry.resolve(intent).name for intent in expected} == expected


def test_registry_rejects_duplicate_intent_route():
    registry = AgentRegistry()

    class FakeAgent:
        name = "first"

    class OtherAgent:
        name = "second"

    registry.register(FakeAgent(), description="first", intents=(Intent.PRODUCT_SEARCH,))
    with pytest.raises(ValueError, match="intents already registered"):
        registry.register(OtherAgent(), description="second", intents=(Intent.PRODUCT_SEARCH,))
