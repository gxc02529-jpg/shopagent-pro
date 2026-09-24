import asyncio

from shopagent.agents.intent import IntentAgent
from shopagent.container import build_container
from shopagent.domain.models import Intent
from shopagent.ports.llm import (
    FallbackLLMProvider,
    HttpLLMProvider,
    MockLLMProvider,
    RuleIntentProvider,
)
from shopagent.settings import Settings


class _NullProvider:
    async def classify(self, content: str):
        return None

    async def generate(self, *, question: str, context: str = "", history: str = ""):
        return None


class _BoomProvider:
    async def classify(self, content: str):
        raise RuntimeError("boom")

    async def generate(self, *, question: str, context: str = "", history: str = ""):
        raise RuntimeError("boom")


def test_rule_provider_returns_rule_classification():
    provider = RuleIntentProvider(IntentAgent())
    result = asyncio.run(provider.classify("推荐一款适合通勤的耳机"))
    assert result.intent == Intent.RECOMMENDATION
    assert result.confidence > 0


def test_mock_provider_classifies_intents():
    provider = MockLLMProvider()
    assert asyncio.run(provider.classify("我要退货")).intent == Intent.AFTER_SALES
    assert asyncio.run(provider.classify("查一下库存")).intent == Intent.STOCK_QUERY


def test_fallback_defers_to_rule_when_primary_returns_none():
    provider = FallbackLLMProvider(_NullProvider(), RuleIntentProvider(IntentAgent()))
    result = asyncio.run(provider.classify("查询订单物流"))
    assert result.intent == Intent.ORDER_QUERY


def test_fallback_defers_to_rule_when_primary_raises():
    provider = FallbackLLMProvider(_BoomProvider(), RuleIntentProvider(IntentAgent()))
    result = asyncio.run(provider.classify("查库存"))
    assert result.intent == Intent.STOCK_QUERY


def test_http_provider_returns_none_on_unreachable_endpoint():
    provider = HttpLLMProvider("http://127.0.0.1:1/v1/chat/completions", "k", "m", timeout=2.0)
    assert asyncio.run(provider.classify("anything")) is None


def test_container_wires_llm_by_backend():
    assert build_container(Settings(llm_backend="rule")).orchestrator._llm is None
    assert build_container(Settings(llm_backend="mock")).orchestrator._llm is not None


def test_orchestrator_uses_llm_when_present():
    container = build_container(Settings(llm_backend="mock"))
    result = asyncio.run(container.orchestrator.classify("我要申请退款"))
    assert result.intent == Intent.AFTER_SALES


def test_mock_provider_classifies_all_intents():
    provider = MockLLMProvider()
    assert asyncio.run(provider.classify("推荐适合通勤的")).intent == Intent.RECOMMENDATION
    assert asyncio.run(provider.classify("还有库存吗")).intent == Intent.STOCK_QUERY
    assert asyncio.run(provider.classify("我的订单到哪了")).intent == Intent.ORDER_QUERY
    assert asyncio.run(provider.classify("你好")).intent == Intent.GREETING
    assert asyncio.run(provider.classify("随便问个东西")).intent == Intent.PRODUCT_SEARCH


def test_mock_provider_generate_handles_empty_and_normal():
    provider = MockLLMProvider()
    assert asyncio.run(provider.generate(question="")) is None
    answer = asyncio.run(provider.generate(question="q", context="这是上下文资料"))
    assert answer.startswith("[mock]") and "上下文资料" in answer


def test_rule_provider_generate_always_defers():
    provider = RuleIntentProvider(IntentAgent())
    assert asyncio.run(provider.generate(question="q", context="c")) is None


def test_fallback_generate_defers_to_rule_when_primary_none():
    class _GenNone:
        async def classify(self, content):
            return None

        async def generate(self, *, question, context="", history=""):
            return None

    provider = FallbackLLMProvider(_GenNone(), RuleIntentProvider(IntentAgent()))
    assert asyncio.run(provider.generate(question="q", context="c")) is None


class _FakeResp:
    def __init__(self, payload):
        self._payload = payload

    def raise_for_status(self):
        return None

    def json(self):
        return {"choices": [{"message": {"content": self._payload}}]}


class _FakeClient:
    def __init__(self, payload):
        self._payload = payload

    async def __aenter__(self):
        return self

    async def __aexit__(self, *exc):
        return False

    async def post(self, *args, **kwargs):
        return _FakeResp(self._payload)


def test_http_provider_classify_parses_response(monkeypatch):
    import httpx

    monkeypatch.setattr(
        httpx,
        "AsyncClient",
        lambda *a, **k: _FakeClient('{"intent":"stock_query","confidence":0.8}'),
    )
    provider = HttpLLMProvider("http://fake/v1", "k", "m")
    result = asyncio.run(provider.classify("查库存"))
    assert result is not None
    assert result.intent == Intent.STOCK_QUERY
    assert result.confidence == 0.8


def test_http_provider_generate_parses_response(monkeypatch):
    import httpx

    monkeypatch.setattr(httpx, "AsyncClient", lambda *a, **k: _FakeClient("  生成答案  "))
    provider = HttpLLMProvider("http://fake/v1", "k", "m")
    answer = asyncio.run(provider.generate(question="q", context="c", history="h"))
    assert answer == "生成答案"


def test_http_provider_returns_none_when_httpx_missing(monkeypatch):
    real_import = __import__

    def _fake_import(name, *args, **kwargs):
        if name == "httpx":
            raise ImportError("no httpx")
        return real_import(name, *args, **kwargs)

    monkeypatch.setattr("builtins.__import__", _fake_import)
    provider = HttpLLMProvider("http://fake/v1", "k", "m")
    assert asyncio.run(provider.classify("anything")) is None
    assert asyncio.run(provider.generate(question="q", context="c")) is None
