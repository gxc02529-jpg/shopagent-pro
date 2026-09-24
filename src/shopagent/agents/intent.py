from __future__ import annotations

import re

from shopagent.domain.models import Intent, IntentResult


class IntentAgent:
    _sku_pattern = re.compile(r"SKU-\d+", re.IGNORECASE)
    _order_pattern = re.compile(r"ORD-\d+", re.IGNORECASE)
    _ticket_pattern = re.compile(r"AS-[A-Z0-9]+", re.IGNORECASE)

    async def classify(self, content: str) -> IntentResult:
        text = content.strip()
        entities: dict[str, str] = {}
        if match := self._sku_pattern.search(text):
            entities["product_id"] = match.group(0).upper()
        if match := self._order_pattern.search(text):
            entities["order_id"] = match.group(0).upper()
        if self._ticket_pattern.search(text):
            return IntentResult(
                intent=Intent.AFTER_SALES,
                confidence=0.98,
                entities=entities,
                reason="ticket_id_match",
            )

        if any(phrase in text for phrase in ("退款状态", "退款进度", "退款到哪")):
            return IntentResult(
                intent=Intent.ORDER_QUERY,
                confidence=0.96,
                entities=entities,
                reason="refund_status_rule",
            )

        rules = [
            (Intent.STOCK_QUERY, ("库存", "有货", "现货", "补货")),
            (
                Intent.AFTER_SALES,
                ("退货", "换货", "退款", "发票", "投诉", "运费险", "理赔", "售后政策"),
            ),
            (Intent.ORDER_QUERY, ("订单", "物流", "快递", "发货", "签收", "到哪")),
            (Intent.RECOMMENDATION, ("推荐", "适合", "选哪", "哪个好", "通勤")),
            (Intent.PRODUCT_DETAIL, ("参数", "规格", "详情", "颜色", "续航", "材质")),
            (Intent.PRODUCT_SEARCH, ("商品", "价格", "多少钱", "耳机", "背包", "杯")),
            (Intent.GREETING, ("你好", "您好", "hi", "hello")),
        ]
        for intent, keywords in rules:
            if any(keyword.lower() in text.lower() for keyword in keywords):
                confidence = 0.96 if entities else 0.86
                return IntentResult(
                    intent=intent, confidence=confidence, entities=entities, reason="rule_match"
                )
        if entities:
            return IntentResult(
                intent=Intent.PRODUCT_DETAIL, confidence=0.9, entities=entities, reason="sku_match"
            )
        return IntentResult(intent=Intent.UNKNOWN, confidence=0.3, reason="no_rule_matched")
