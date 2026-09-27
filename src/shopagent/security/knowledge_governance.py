from __future__ import annotations

import hashlib

from shopagent.domain.models import KnowledgeDocument
from shopagent.security.guardrails import detect_prompt_injection
from shopagent.security.redaction import redact_sensitive_text


class KnowledgePolicyViolation(ValueError):
    """Raised when content cannot safely enter the published knowledge corpus."""


def scan_knowledge_document(
    document: KnowledgeDocument,
    existing: list[KnowledgeDocument] | None = None,
) -> list[str]:
    """Return auditable risk flags and block high-risk knowledge at ingestion.

    Retrieved documents are evidence, never executable instructions. We therefore
    reject instruction-override language and customer PII before indexing. Possible
    semantic conflicts are flagged for the already-required human review rather
    than silently choosing the newest text.
    """

    text = "\n".join([document.title, document.content, *document.keywords])
    blocking: list[str] = []
    if detect_prompt_injection(text):
        blocking.append("prompt_injection")
    if redact_sensitive_text(text) != text:
        blocking.append("sensitive_information")
    if not document.evidence_only:
        blocking.append("executable_knowledge_not_allowed")
    if blocking:
        raise KnowledgePolicyViolation(
            "knowledge rejected by ingestion policy: " + ", ".join(blocking)
        )

    risks: list[str] = []
    normalized_title = "".join(document.title.casefold().split())
    for item in existing or []:
        if item.id == document.id or item.tenant_id != document.tenant_id:
            continue
        same_title = "".join(item.title.casefold().split()) == normalized_title
        shared_keywords = set(item.keywords) & set(document.keywords)
        if (same_title or len(shared_keywords) >= 2) and item.content != document.content:
            risks.append(f"potential_conflict:{item.id}:{item.version}")
    return sorted(set(risks))


def seal_knowledge_document(document: KnowledgeDocument) -> KnowledgeDocument:
    """Attach a deterministic integrity digest to the reviewed publication."""

    sealed_content = (
        f"{document.id}\0{document.tenant_id}\0{document.domain}\0{document.title}\0"
        f"{document.content}\0{document.version}\0{document.source}"
    )
    digest = hashlib.sha256(sealed_content.encode()).hexdigest()
    return document.model_copy(update={"content_hash": digest, "evidence_only": True})
