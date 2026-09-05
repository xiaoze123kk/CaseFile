"""Conservative author-request signals shared by rule routing and Goal prefilter.

Signals nominate a route; they never authorize operations or replace the semantic
interpreter, binding, confidence, or mutation proof gates.
"""

from __future__ import annotations

import re
from dataclasses import dataclass

INTENT_ROUTER_VERSION = "casefile-chat-router-v3"

_QUOTED = re.compile(r'```[\s\S]*?```|“[^”]*”|「[^」]*」|『[^』]*』|"[^"\n]*"')
_CLAUSES = re.compile(r"[，,。；;！!？?\n]|(?:但是|而是|然后|再|但)")
_NEGATION = re.compile(
    r"(?:不要|不用|无需|无须|不必|不得|禁止|不做|不进行|不(?:修改|删除|新增|创建)"
    r"|\b(?:do not|don't|never|without)\b)"
)
_READ_ONLY = re.compile(
    r"(?:只|仅)(?:需要|需|要)?(?:解释|分析|讨论|检查|给出分析|给出解释)"
    r"|(?:不要|无需|不必|禁止)(?:提出|给出|生成|进行)?(?:任何|可审阅的)?(?:修改|修复|补丁|patch)"
    r"|\b(?:explain only|analysis only|read.only)\b"
)
_REPAIR = re.compile(
    r"(?:修复|修正|修改建议|字段修改|补丁|改写|调整|改成|改为|更新|修改)"
    r"|\b(?:fix|repair|patch|rewrite|update)\b"
)
_ANALYSIS = re.compile(r"分析|梳理|比较|解释|评估|归纳|\b(?:analy[sz]e|explain|compare)\b")
_AUDIT = re.compile(r"审计|核查|检查|复查|验证|\b(?:audit|check|verify)\b")
_MUTATION = re.compile(_REPAIR.pattern + r"|新增|新建|创建|删除|移除|\b(?:create|delete|remove)\b")
_INQUIRY = re.compile(r"为什么|为何|如何|怎么|是否|要不要|需不需要|有哪些|\b(?:why|how|whether)\b")
_CONDITIONAL = re.compile(r"如果|假如|若|没有.*(?:就|则)|未发现.*(?:就|则)|\b(?:if|unless)\b")


@dataclass(frozen=True, slots=True)
class ChatRequestSignals:
    repair_requested: bool
    read_only: bool
    action_groups: frozenset[str]


def request_signals(message: str) -> ChatRequestSignals:
    # Titles, quotations, and code are data, not a request to run their verbs.
    text = _QUOTED.sub(" ", message.casefold())
    affirmative = [clause for clause in _CLAUSES.split(text) if not _NEGATION.search(clause)]
    repair = any(_REPAIR.search(clause) and not _INQUIRY.search(clause) for clause in affirmative)
    groups = frozenset(
        name for name, pattern in (
            ("analysis", _ANALYSIS), ("audit", _AUDIT), ("mutation", _MUTATION)
        ) if any(
            pattern.search(clause) and (name != "mutation" or not _INQUIRY.search(clause))
            for clause in affirmative
        )
    )
    return ChatRequestSignals(
        repair_requested=repair,
        read_only=any(
            _READ_ONLY.search(clause) and not _CONDITIONAL.search(clause)
            for clause in _CLAUSES.split(text)
        ) and "mutation" not in groups,
        action_groups=groups,
    )


def affirmative_request_contains(message: str, markers: tuple[str, ...]) -> bool:
    text = _QUOTED.sub(" ", message.casefold())
    return any(
        not _NEGATION.search(clause[:match.start()])
        and not re.search(r"(?:不|别|勿)\s*$", clause[:match.start()])
        for clause in _CLAUSES.split(text)
        for marker in markers
        for match in re.finditer(
            rf"\b{re.escape(marker)}\b" if marker.isascii() else re.escape(marker), clause
        )
    )


__all__ = ["ChatRequestSignals", "affirmative_request_contains", "request_signals"]
