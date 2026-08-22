# ruff: noqa: F401,F403,I001
"""Stable public entrypoint for the CaseFile Chat outcome evaluation suite."""

from casefile.benchmark.chat_outcome_suite import *  # noqa: F403
from casefile.benchmark.chat_outcome_suite import (
    _AUDIT_PRESET as _AUDIT_PRESET,
    _FREE_TEXT as _FREE_TEXT,
    _INSPECT_PRESET as _INSPECT_PRESET,
    _candidate as _candidate,
    _focus as _focus,
    _request_for_task as _request_for_task,
    _suggestion as _suggestion,
    main,
)


if __name__ == "__main__":
    main()
