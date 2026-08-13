"""Context-window budgeting helpers for prompt assembly.

The graph passes rich reports and tool outputs between agents. Local
OpenAI-compatible servers often default to 8K context, so every prompt boundary
needs a conservative cap instead of relying on the model/server to reject
oversized requests.
"""

from __future__ import annotations

from collections.abc import Iterable
from typing import Any

DEFAULT_CONTEXT_TOKENS = 8192


def context_budget_chars(max_context_tokens: int | None) -> int:
    """Return an approximate prompt character budget after reserving headroom."""
    tokens = int(max_context_tokens or DEFAULT_CONTEXT_TOKENS)
    if tokens <= 8192:
        return 18000
    if tokens <= 16384:
        return 40000
    if tokens <= 32768:
        return 85000
    return max(18000, int(tokens * 2.6))


def report_section_budget(max_context_tokens: int | None) -> int:
    tokens = int(max_context_tokens or DEFAULT_CONTEXT_TOKENS)
    if tokens <= 8192:
        return 2200
    if tokens <= 16384:
        return 5000
    return 9000


def history_section_budget(max_context_tokens: int | None) -> int:
    tokens = int(max_context_tokens or DEFAULT_CONTEXT_TOKENS)
    if tokens <= 8192:
        return 3000
    if tokens <= 16384:
        return 7000
    return 12000


def short_section_budget(max_context_tokens: int | None) -> int:
    tokens = int(max_context_tokens or DEFAULT_CONTEXT_TOKENS)
    if tokens <= 8192:
        return 1800
    if tokens <= 16384:
        return 3500
    return 6000


def max_context_tokens_from_config(config: dict[str, Any] | None = None) -> int:
    if config and config.get("max_context_tokens"):
        return int(config["max_context_tokens"])
    try:
        from tradingagents.dataflows.config import get_config

        return int(get_config().get("max_context_tokens") or DEFAULT_CONTEXT_TOKENS)
    except Exception:
        return DEFAULT_CONTEXT_TOKENS


def truncate_text(text: Any, max_chars: int, label: str = "text") -> str:
    """Preserve the beginning and end of long text with an explicit notice."""
    value = "" if text is None else str(text)
    if max_chars <= 0 or len(value) <= max_chars:
        return value

    notice = f"\n\n[{label} truncated from {len(value)} to {max_chars} chars]\n\n"
    if len(notice) >= max_chars:
        return notice[:max_chars]

    remaining = max_chars - len(notice)
    head_len = max(1, remaining // 2)
    tail_len = max(1, remaining - head_len)
    return value[:head_len].rstrip() + notice + value[-tail_len:].lstrip()


def _copy_message_with_content(message: Any, content: str) -> Any:
    if hasattr(message, "model_copy"):
        return message.model_copy(update={"content": content})
    if hasattr(message, "copy"):
        return message.copy(update={"content": content})
    return message


def compact_messages_for_context(
    messages: Iterable[Any],
    *,
    max_chars: int,
    max_tool_chars: int | None = None,
) -> list[Any]:
    """Cap tool outputs and total chat history before sending to an LLM.

    Message instances are copied so LangGraph state is not mutated. The latest
    messages keep their tails, which matters for tool-call results that often
    put the most recent rows at the end.
    """
    items = list(messages)
    if not items:
        return []

    tool_cap = max_tool_chars or max(500, max_chars // 12)
    compacted = []
    for msg in items:
        content = getattr(msg, "content", None)
        if content is None:
            compacted.append(msg)
            continue
        label = "tool output" if getattr(msg, "type", None) == "tool" else "message"
        cap = tool_cap if label == "tool output" else max_chars
        compacted.append(_copy_message_with_content(msg, truncate_text(content, cap, label)))

    joined_len = sum(len(str(getattr(msg, "content", ""))) for msg in compacted)
    if joined_len <= max_chars:
        return compacted

    separator_budget = max(len(compacted) - 1, 0)
    per_message = max(200, (max_chars - separator_budget) // len(compacted))
    return [
        _copy_message_with_content(
            msg,
            truncate_text(
                getattr(msg, "content", ""),
                min(per_message, tool_cap if getattr(msg, "type", None) == "tool" else per_message),
                "tool output" if getattr(msg, "type", None) == "tool" else "message",
            ),
        )
        for msg in compacted
    ]
