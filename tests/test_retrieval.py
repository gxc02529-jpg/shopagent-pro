import asyncio

from shopagent.adapters.mock_knowledge import MockKnowledgeAdapter
from shopagent.container import build_container
from shopagent.domain.models import ChatMessage
from shopagent.ports.retrieval import CharacterRecallBackend, VectorRecallBackend
from shopagent.rag.service import KnowledgeService
from shopagent.settings import Settings


def test_character_recall_ranks_relevant_policy():
    backend = CharacterRecallBackend(MockKnowledgeAdapter())
    hits = asyncio.run(backend.search("退货的运费险怎么理赔", domain="after_sales"))
    assert hits[0].document_id == "KB-AFTER-003"
    assert hits[0].source == "保险服务说明"
    assert hits[0].score > 0.5


def test_vector_backend_falls_back_to_character_when_model_missing():
    backend = VectorRecallBackend(MockKnowledgeAdapter())
    hits = asyncio.run(backend.search("退货的运费险怎么理赔", domain="after_sales"))
    assert hits and hits[0].document_id == "KB-AFTER-003"


def test_knowledge_service_default_backend_preserves_behavior():
    service = KnowledgeService(MockKnowledgeAdapter())
    hits = asyncio.run(service.search("退货的运费险怎么理赔", domain="after_sales"))
    assert hits[0].document_id == "KB-AFTER-003"


def test_container_wires_character_backend_by_default():
    container = build_container(Settings(rag_backend="character"))
    assert container.knowledge_repository is not None
    # Default character backend is exercised through the orchestrator's RAG path.
    result = asyncio.run(
        container.orchestrator.handle(
            ChatMessage(
                user_id="demo-user",
                session_id="retrieval-default",
                content="七天无理由退货有什么条件？",
            )
        )
    )
    assert "KB-AFTER-001" in result.answer


def test_vector_backend_falls_back_on_encode_error(monkeypatch):
    import sys
    import types

    fake_st = types.ModuleType("sentence_transformers")

    class _BoomModel:
        def __init__(self, *args, **kwargs):
            pass

        def encode(self, texts):
            raise RuntimeError("model boom")

    fake_st.SentenceTransformer = _BoomModel
    monkeypatch.setitem(sys.modules, "sentence_transformers", fake_st)

    backend = VectorRecallBackend(MockKnowledgeAdapter())
    hits = asyncio.run(backend.search("退货的运费险怎么理赔", domain="after_sales"))
    assert hits and hits[0].document_id == "KB-AFTER-003"
