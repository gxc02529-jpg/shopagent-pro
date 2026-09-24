from __future__ import annotations

from shopagent.domain.models import KnowledgeDocument

DEFAULT_KNOWLEDGE = [
    KnowledgeDocument(
        id="KB-AFTER-001",
        domain="after_sales",
        title="七天无理由退货规则",
        content="商品签收次日起七天内，商品完好且不影响二次销售，可申请七天无理由退货。定制、生鲜、已拆封数字商品等依法不适用的品类除外。",
        keywords=["七天无理由", "退货", "商品完好", "签收"],
        version="2026.02",
        source="售后政策中心",
    ),
    KnowledgeDocument(
        id="KB-AFTER-002",
        domain="after_sales",
        title="换货与质量问题处理",
        content="签收后发现破损、错发或质量问题，请保留商品、包装和清晰照片并提交售后申请。审核通过后可换货或退款，质量问题产生的合理运费由商家承担。",
        keywords=["换货", "破损", "质量问题", "错发", "运费"],
        version="2026.02",
        source="售后政策中心",
    ),
    KnowledgeDocument(
        id="KB-AFTER-003",
        domain="after_sales",
        title="运费险说明",
        content="订单含运费险且满足理赔条件时，退货物流签收后由保险服务自动发起理赔。实际赔付金额以保险服务核定为准，可能与实际运费不同。",
        keywords=["运费险", "理赔", "退货运费", "赔付"],
        version="2026.02",
        source="保险服务说明",
    ),
    KnowledgeDocument(
        id="KB-AFTER-004",
        domain="after_sales",
        title="电子发票申请规则",
        content="订单支付完成后可申请电子发票。已开票订单如需修改抬头，应先申请红冲，完成后重新开具；处理时间通常为一至三个工作日。",
        keywords=["发票", "电子发票", "抬头", "红冲"],
        version="2026.02",
        source="财税服务说明",
    ),
]


class MockKnowledgeAdapter:
    def __init__(self, documents: list[KnowledgeDocument] | None = None) -> None:
        self._documents = list(documents or DEFAULT_KNOWLEDGE)

    async def list_documents(self, domain: str | None = None) -> list[KnowledgeDocument]:
        if not domain:
            return list(self._documents)
        return [item for item in self._documents if item.domain == domain]

    async def upsert(self, document: KnowledgeDocument) -> None:
        self._documents = [item for item in self._documents if item.id != document.id]
        self._documents.append(document)

    async def health(self) -> bool:
        return True

    async def close(self) -> None:
        return None
