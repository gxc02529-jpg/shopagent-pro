from __future__ import annotations

from typing import Protocol

from shopagent.agents.intent import IntentAgent
from shopagent.domain.models import Intent, IntentResult


class LLMProvider(Protocol):
    """Pluggable language-model boundary for intent classification and answer generation."""

    async def classify(self, content: str) -> IntentResult | None:
        """Return an intent classification, or None to defer to the rule engine."""
        ...

    async def generate(self, *, question: str, context: str = "", history: str = "") -> str | None:
        """Return a generated answer, or None to use the default/template response."""
        ...


class RuleIntentProvider:
    """Adapter exposing the existing rule-based IntentAgent behind the LLMProvider port."""

    def __init__(self, agent: IntentAgent | None = None) -> None:
        self._agent = agent or IntentAgent()

    async def classify(self, content: str) -> IntentResult | None:
        return await self._agent.classify(content)

    async def generate(self, *, question: str, context: str = "", history: str = "") -> str | None:
        return None


class MockLLMProvider:
    """Deterministic offline provider for tests, demos and smoke runs (no network)."""

    async def classify(self, content: str) -> IntentResult | None:
        lowered = content.lower()
        if any(k in lowered for k in ("退款", "退货", "换货", "售后", "投诉")):
            return IntentResult(intent=Intent.AFTER_SALES, confidence=0.9, reason="mock_rule")
        if any(k in lowered for k in ("推荐", "适合", "通勤", "选哪", "哪个好")):
            return IntentResult(intent=Intent.RECOMMENDATION, confidence=0.9, reason="mock_rule")
        if any(k in lowered for k in ("库存", "有货", "现货", "补货")):
            return IntentResult(intent=Intent.STOCK_QUERY, confidence=0.9, reason="mock_rule")
        if any(k in lowered for k in ("订单", "物流", "快递", "发货", "签收")):
            return IntentResult(intent=Intent.ORDER_QUERY, confidence=0.9, reason="mock_rule")
        if any(k in lowered for k in ("你好", "您好", "hi", "hello")):
            return IntentResult(intent=Intent.GREETING, confidence=0.9, reason="mock_rule")
        return IntentResult(intent=Intent.PRODUCT_SEARCH, confidence=0.7, reason="mock_default")

    async def generate(self, *, question: str, context: str = "", history: str = "") -> str | None:
        if not question:
            return None
        snippet = context[:80].strip()
        return f"[mock] 关于「{question}」：{snippet or '暂无上下文'}"


class HttpLLMProvider:
    """OpenAI-compatible chat-completions provider (httpx imported lazily)."""

    def __init__(self, api_url: str, api_key: str, model: str, *, timeout: float = 20.0) -> None:
        self._api_url = api_url.rstrip("/") + "/chat/completions"
        self._api_key = api_key
        self._model = model
        self._timeout = timeout

    async def classify(self, content: str) -> IntentResult | None:
        try:
            import json

            import httpx
        except ImportError:
            return None
        system = (
            "你是电商客服意图分类器。仅输出 JSON："
            '{"intent":"product_search|product_detail|stock_query|recommendation|'
            'order_query|after_sales|greeting|unknown","confidence":0.0}'
        )
        try:
            async with httpx.AsyncClient(timeout=self._timeout) as client:
                resp = await client.post(
                    self._api_url,
                    headers={"Authorization": f"Bearer {self._api_key}"},
                    json={
                        "model": self._model,
                        "messages": [
                            {"role": "system", "content": system},
                            {"role": "user", "content": content},
                        ],
                        "response_format": {"type": "json_object"},
                    },
                )
                resp.raise_for_status()
                data = json.loads(resp.json()["choices"][0]["message"]["content"])
                return IntentResult(
                    intent=Intent(data.get("intent", "unknown")),
                    confidence=float(data.get("confidence", 0.8)),
                    reason="llm_http",
                )
        except Exception:  # noqa: BLE001 - degrade gracefully, never break the flow
            return None

    async def generate(self, *, question: str, context: str = "", history: str = "") -> str | None:
        try:
            import httpx
        except ImportError:
            return None
        try:
            async with httpx.AsyncClient(timeout=self._timeout) as client:
                resp = await client.post(
                    self._api_url,
                    headers={"Authorization": f"Bearer {self._api_key}"},
                    json={
                        "model": self._model,
                        "messages": [
                            {
                                "role": "system",
                                "content": "你是电商客服助手，根据知识库回答用户问题。",
                            },
                            {
                                "role": "user",
                                "content": f"上下文：{context}\n历史：{history}\n问题：{question}",
                            },
                        ],
                    },
                )
                resp.raise_for_status()
                content = resp.json()["choices"][0]["message"]["content"].strip()
                return content or None
        except Exception:  # noqa: BLE001 - degrade gracefully, never break the flow
            return None


class FallbackLLMProvider:
    """Prefers the primary provider; on None/error defers to the rule provider."""

    def __init__(self, primary: LLMProvider, fallback: RuleIntentProvider) -> None:
        self._primary = primary
        self._fallback = fallback

    async def classify(self, content: str) -> IntentResult:
        try:
            result = await self._primary.classify(content)
        except Exception:  # noqa: BLE001 - degrade gracefully, never break the flow
            result = None
        if result is None:
            return await self._fallback.classify(content)
        return result

    async def generate(self, *, question: str, context: str = "", history: str = "") -> str | None:
        try:
            result = await self._primary.generate(
                question=question, context=context, history=history
            )
        except Exception:  # noqa: BLE001 - degrade gracefully, never break the flow
            result = None
        if result is None:
            return await self._fallback.generate(
                question=question, context=context, history=history
            )
        return result
