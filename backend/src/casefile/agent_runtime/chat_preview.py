"""Bounded, fail-closed extraction of public answer previews (never reasoning)."""

from __future__ import annotations

import json
import time
from collections.abc import Callable
from typing import Any

from casefile.agent_runtime.public_language import public_language_rule_ids

FeedbackSink = Callable[[str, dict[str, Any]], None]


def answer_prefix(raw: str) -> str:
    """Parse only a top-level answer string; incomplete tokens remain buffered."""
    decoder = json.JSONDecoder()
    index = 0
    seen: set[str] = set()
    answer = ""

    def whitespace(offset: int) -> int:
        while offset < len(raw) and raw[offset].isspace():
            offset += 1
        return offset

    index = whitespace(index)
    if index == len(raw):
        return answer
    if raw[index] != "{":
        raise ValueError("Not a JSON object")
    index += 1
    while True:
        index = whitespace(index)
        if index == len(raw) or raw[index] == "}":
            return answer
        try:
            key, end = decoder.raw_decode(raw, index)
        except json.JSONDecodeError:
            return answer
        if not isinstance(key, str) or key in seen:
            raise ValueError("Invalid or repeated key")
        seen.add(key)
        index = whitespace(end)
        if index == len(raw):
            return answer
        if raw[index] != ":":
            raise ValueError("Missing colon")
        index = whitespace(index + 1)
        if index == len(raw):
            return answer
        if key == "answer":
            if raw[index] != '"':
                raise ValueError("Answer must be a string")
            answer, end, closed = _string_prefix(raw, index)
            if not closed:
                return answer
        else:
            try:
                _, end = decoder.raw_decode(raw, index)
            except json.JSONDecodeError:
                return answer
        index = whitespace(end)
        if index == len(raw) or raw[index] == "}":
            return answer
        if raw[index] != ",":
            raise ValueError("Missing separator")
        index += 1


def _string_prefix(raw: str, start: int) -> tuple[str, int, bool]:
    index = start + 1
    end = index
    while index < len(raw):
        char = raw[index]
        if char == '"':
            return json.loads(raw[start : index + 1]), index + 1, True
        if char == "\\":
            if index + 1 >= len(raw):
                break
            size = 6 if raw[index + 1] == "u" else 2
            if index + size > len(raw):
                break
            index += size
        else:
            index += 1
        end = index
    value = json.loads(raw[start:end] + '"')
    # A high surrogate may be waiting for its low surrogate in the next chunk.
    if value and 0xD800 <= ord(value[-1]) <= 0xDBFF:
        value = value[:-1]
    if any(0xD800 <= ord(char) <= 0xDFFF for char in value):
        raise ValueError("Unpaired surrogate")
    return value, end, False


class AnswerPreview:
    """A single model attempt; the sink owns TaskRun-wide budgets and fencing."""

    def __init__(self, sink: FeedbackSink, *, sensitive_values: tuple[str, ...] = ()):
        self.sink = sink
        self.sensitive_values = tuple(value for value in sensitive_values if len(value) >= 4)
        self.raw = ""
        self.published = ""
        self.disabled = False
        self.last_flush = 0.0
        self.sink("message.preview_started", {})

    def invalidate(self, *, discard: bool = True) -> None:
        if not self.disabled:
            self.sink("message.preview_invalidated", {"discard": discard})
        self.disabled = True

    def feed(self, delta: str) -> None:
        if self.disabled:
            return
        self.raw += delta
        if len(self.raw) > 1_048_576:
            self.invalidate()
            return
        self._publish(final=False)

    def finish(self) -> None:
        if self.disabled:
            return
        try:
            # Full syntax is required before releasing the retained tail.
            json.loads(self.raw)
        except (ValueError, RecursionError):
            self.invalidate()
            return
        self._publish(final=True)

    def _publish(self, *, final: bool) -> None:
        try:
            answer = answer_prefix(self.raw)
            if public_language_rule_ids(answer, sensitive_values=self.sensitive_values):
                self.invalidate()
                return
            if not answer.startswith(self.published):
                self.invalidate()
                return
            if final:
                safe = answer
            else:
                # Preserve enough lookahead for protected tokens and exact secrets.
                hold = max(512, max(map(len, self.sensitive_values), default=0))
                limit = max(0, len(answer) - hold)
                boundary = max((answer.rfind(c, 0, limit) for c in "。！？"), default=-1)
                safe = answer[: boundary + 1]
            now = time.monotonic()
            if len(safe) <= len(self.published) or (not final and now - self.last_flush < 0.5):
                return
            delta = safe[len(self.published) :]
            if len(safe.encode("utf-8")) > 65536:
                self.disabled = True
                return
            # Offset uses Unicode code points on both sides of the protocol.
            self.sink(
                "message.preview_delta",
                {"offset": len(self.published), "text": delta, "final": final},
            )
            self.published = safe
            self.last_flush = now
        except (ValueError, RecursionError, UnicodeError):
            self.invalidate()
