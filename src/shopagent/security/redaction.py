from __future__ import annotations

import re

_PHONE = re.compile(r"(?<!\d)(1[3-9]\d)(\d{4})(\d{4})(?!\d)")
_EMAIL = re.compile(
    r"(?<![A-Za-z0-9._%+-])([A-Za-z0-9._%+-])[A-Za-z0-9._%+-]*@([A-Za-z0-9.-]+\.[A-Za-z]{2,})"
)
_ORDER = re.compile(r"(?<![A-Z0-9])(ORD-)([A-Z0-9]{4,})(?![A-Z0-9])", re.IGNORECASE)
_LONG_NUMBER = re.compile(r"(?<!\d)(\d{3})(\d{4,})(\d{4})(?!\d)")
_ID_CARD = re.compile(r"(?<!\d)(\d{17}[\dXx])(?!\d)")
_NAME = re.compile(r"([\u4e00-\u9fa5]{1,3})(先生|女士|小姐|师傅|老师|同志)")
_ADDRESS = re.compile(
    r"(?:[\u4e00-\u9fa5]{2,}(?:省|市|区|县|镇))[\u4e00-\u9fa50-9]*?(?:路|街|道|巷|弄)[\u4e00-\u9fa50-9]*?号?"
)


def redact_sensitive_text(text: str) -> str:
    """Mask common customer identifiers before logs and analytics persistence."""
    value = _PHONE.sub(r"\1****\3", text)
    value = _EMAIL.sub(r"\1***@\2", value)
    value = _ORDER.sub(lambda match: f"{match.group(1)}****{match.group(2)[-4:]}", value)
    value = _LONG_NUMBER.sub(r"\1****\3", value)
    value = _ID_CARD.sub(lambda match: f"{match.group(1)[:6]}********{match.group(1)[-4:]}", value)
    value = _NAME.sub(r"**\2", value)
    value = _ADDRESS.sub("***", value)
    return value
