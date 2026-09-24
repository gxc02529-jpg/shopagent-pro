from __future__ import annotations

import re

# Deny-list of injection cues (intentional overlaps across languages). Kept heuristic
# and conservative: it flags attempts to override system behavior, not legitimate questions.
_INJECTION_PATTERNS = [
    r"ignore\s+(the\s+)?previous\s+instructions",
    r"disregard\s+(the\s+)?(above|previous|prior)",
    r"forget\s+(everything|your\s+instructions|the\s+above)",
    r"system\s+prompt",
    r"you\s+are\s+now",
    r"override\s+(your\s+)?(rules|guidelines|instructions)",
    r"jailbreak",
    r"\bdan\b",
    r"忽略\s*(之前|以上|前面|上面)\s*(的)?\s*(指令|要求|提示|设定)",
    r"无视\s*(之前|以上|前面)\s*(的)?\s*(指令|要求|提示)",
    r"忘记\s*(前面|以上|之前)\s*(的)?\s*(内容|指令|设定)",
    r"系统\s*提示",
    r"把你\s*(自己\s*)?当成",
    r"角色\s*扮演\s*(成|为)",
    r"越狱",
    r"输出\s*(你\s*)?的\s*(提示词|系统提示|prompt)",
]

_COMPILED = [re.compile(pattern, re.IGNORECASE) for pattern in _INJECTION_PATTERNS]


def detect_prompt_injection(text: str) -> bool:
    """Return True if the text looks like a prompt-injection / jailbreak attempt."""
    if not text:
        return False
    return any(pattern.search(text) for pattern in _COMPILED)


# Deflection shown when an injection is detected and guardrails are enabled.
SAFE_DEFLECTION = (
    "我无法执行改写系统指令或绕过安全规则的请求。如果你有商品、订单、物流或售后相关问题，"
    "请直接描述需求，我会按既定流程为你处理。"
)
