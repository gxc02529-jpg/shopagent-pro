from dataclasses import dataclass

from shopagent.adapters.mock_commerce import DEFAULT_ORDERS, MockAfterSalesAdapter, MockOrderAdapter
from shopagent.adapters.mock_knowledge import DEFAULT_KNOWLEDGE, MockKnowledgeAdapter
from shopagent.adapters.mock_pim import MockPIMAdapter
from shopagent.adapters.mysql_commerce import MySQLCommerceAdapter
from shopagent.adapters.mysql_knowledge import MySQLKnowledgeAdapter
from shopagent.adapters.remote_a2a_agent import RemoteA2AAgent
from shopagent.adapters.remote_mcp_tools import RemoteMCPToolClient
from shopagent.agents.after_sales import AfterSalesAgent
from shopagent.agents.base import AgentRegistry
from shopagent.agents.intent import IntentAgent
from shopagent.agents.order import OrderAgent
from shopagent.agents.product import ProductAgent
from shopagent.agents.recommendation import RecommendationAgent
from shopagent.domain.models import Intent
from shopagent.memory.in_memory import InMemoryTTLStore
from shopagent.memory.redis_store import RedisMemoryStore
from shopagent.operations.feedback import FeedbackLoopService
from shopagent.operations.mysql_store import MySQLOperationsStore
from shopagent.operations.store import InMemoryOperationsStore
from shopagent.orchestration.service import ShopAgentOrchestrator
from shopagent.ports.knowledge import KnowledgeRepository
from shopagent.ports.llm import (
    FallbackLLMProvider,
    HttpLLMProvider,
    LLMProvider,
    MockLLMProvider,
    RuleIntentProvider,
)
from shopagent.ports.memory import MemoryStore
from shopagent.ports.operations import OperationsStore
from shopagent.ports.retrieval import CharacterRecallBackend, RetrievalBackend, VectorRecallBackend
from shopagent.rag.service import KnowledgeService
from shopagent.settings import Settings
from shopagent.tools.knowledge_tools import register_knowledge_tools
from shopagent.tools.order_tools import register_order_tools
from shopagent.tools.product_tools import register_product_tools
from shopagent.tools.registry import ToolRegistry


@dataclass(slots=True)
class Container:
    orchestrator: ShopAgentOrchestrator
    memory: MemoryStore
    tools: ToolRegistry
    agents: AgentRegistry
    operations: OperationsStore
    feedback: FeedbackLoopService
    knowledge_repository: KnowledgeRepository
    commerce: object


def build_container(settings: Settings) -> Container:
    settings.validate_for_startup()
    products = MockPIMAdapter()
    if settings.operations_backend == "mysql":
        commerce = MySQLCommerceAdapter(settings.database_url, seed_orders=DEFAULT_ORDERS)
        orders = commerce
        after_sales = commerce
        operations: OperationsStore = MySQLOperationsStore(settings.database_url)
        knowledge_repository: KnowledgeRepository = MySQLKnowledgeAdapter(
            settings.database_url, seed_documents=DEFAULT_KNOWLEDGE
        )
    else:
        orders = MockOrderAdapter()
        after_sales = MockAfterSalesAdapter()
        commerce = after_sales
        operations = InMemoryOperationsStore()
        knowledge_repository = MockKnowledgeAdapter()
    if settings.rag_backend == "vector":
        retrieval_backend: RetrievalBackend = VectorRecallBackend(knowledge_repository)
    else:
        retrieval_backend = CharacterRecallBackend(knowledge_repository)
    knowledge = KnowledgeService(knowledge_repository, backend=retrieval_backend)
    tools = ToolRegistry()
    register_product_tools(tools, products)
    register_order_tools(tools, orders, after_sales)
    register_knowledge_tools(tools, knowledge)
    tool_client = (
        RemoteMCPToolClient(
            settings.mcp_url,
            service_secret=settings.service_token,
        )
        if settings.tool_transport == "mcp"
        else tools
    )
    local_agents = AgentRegistry()
    local_agents.register(
        ProductAgent(tool_client),
        description="商品搜索、详情和库存查询",
        intents=(Intent.PRODUCT_SEARCH, Intent.PRODUCT_DETAIL, Intent.STOCK_QUERY),
    )
    local_agents.register(
        RecommendationAgent(tool_client),
        description="按使用场景和预算推荐商品",
        intents=(Intent.RECOMMENDATION,),
    )
    local_agents.register(
        OrderAgent(tool_client),
        description="订单、物流和退款状态查询",
        intents=(Intent.ORDER_QUERY,),
    )
    local_agents.register(
        AfterSalesAgent(tool_client),
        description="退换货、投诉和售后工单",
        intents=(Intent.AFTER_SALES,),
    )
    if settings.agent_transport == "a2a":
        routing_agents = AgentRegistry()
        for descriptor in local_agents.describe():
            name = str(descriptor["name"])
            intents = tuple(Intent(value) for value in descriptor["intents"])
            routing_agents.register(
                RemoteA2AAgent(
                    name,
                    f"{settings.a2a_base_url.rstrip('/')}/{name}",
                    service_secret=settings.service_token,
                ),
                description=str(descriptor["description"]),
                intents=intents,
            )
    else:
        routing_agents = local_agents

    intent_agent = IntentAgent()
    rule_provider = RuleIntentProvider(intent_agent)
    if settings.llm_backend == "mock":
        llm: LLMProvider | None = FallbackLLMProvider(MockLLMProvider(), rule_provider)
    elif settings.llm_backend == "http":
        llm = FallbackLLMProvider(
            HttpLLMProvider(settings.llm_api_url, settings.llm_api_key, settings.llm_model),
            rule_provider,
        )
    else:
        llm = None

    if settings.memory_backend == "redis":
        memory: MemoryStore = RedisMemoryStore(
            settings.redis_url,
            ttl_seconds=settings.session_ttl_seconds,
            key_prefix=settings.redis_key_prefix,
        )
    else:
        memory = InMemoryTTLStore(settings.session_ttl_seconds)
    feedback = FeedbackLoopService(operations, knowledge_repository)
    orchestrator = ShopAgentOrchestrator(
        intent_agent=intent_agent,
        agents=routing_agents,
        memory_store=memory,
        operations=operations,
        low_confidence_threshold=settings.low_confidence_threshold,
        llm=llm,
        guardrails_enabled=settings.guardrails_enabled,
        redact_generated=settings.redact_generated_answers,
    )
    return Container(
        orchestrator=orchestrator,
        memory=memory,
        tools=tools,
        agents=local_agents,
        operations=operations,
        feedback=feedback,
        knowledge_repository=knowledge_repository,
        commerce=commerce,
    )
