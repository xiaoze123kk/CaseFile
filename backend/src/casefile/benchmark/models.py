"""Benchmark domain models.

These are pure domain types -- no ORM, no framework dependencies.
"""

from dataclasses import dataclass, field
from datetime import datetime
from enum import StrEnum
from typing import Any
from uuid import UUID, uuid4


class BenchmarkStatus(StrEnum):
    PENDING = "pending"
    RUNNING = "running"
    COMPLETED = "completed"
    FAILED = "failed"
    CANCELLED = "cancelled"


class BenchmarkDimension(StrEnum):
    AI_MODEL = "ai_model"
    CONTENT_QUALITY = "content_quality"
    SYSTEM_PERFORMANCE = "system_performance"


class MetricSeverity(StrEnum):
    GATE = "gate"  # blocks release if unmet
    TARGET = "target"  # desired, but not blocking
    OBSERVATION = "observation"  # informational only


@dataclass
class BenchmarkScenario:
    """A single benchmark scenario -- what to test and how to measure it."""

    id: UUID = field(default_factory=uuid4)
    name: str = ""
    description: str = ""
    dimension: BenchmarkDimension = BenchmarkDimension.AI_MODEL
    casefile_id: UUID | None = None  # target CaseFile for the test
    task_type: str = ""  # e.g. brief_to_draft, issue_to_patch, full_validation
    config: dict[str, Any] = field(default_factory=dict)
    expected_metrics: list[str] = field(default_factory=list)


@dataclass
class BenchmarkSuite:
    """A collection of scenarios that together form a regression suite."""

    id: UUID = field(default_factory=uuid4)
    name: str = ""
    description: str = ""
    version: str = "0.1.0"
    scenarios: list[BenchmarkScenario] = field(default_factory=list)
    created_at: datetime = field(default_factory=datetime.utcnow)


@dataclass
class BenchmarkMetric:
    """A single measurement from a benchmark run."""

    id: UUID = field(default_factory=uuid4)
    name: str = ""  # e.g. structure_validity_rate, latency_p50_ms
    value: float = 0.0
    unit: str = ""  # e.g. percent, ms, count
    target: float | None = None  # threshold to compare against
    severity: MetricSeverity = MetricSeverity.OBSERVATION
    metadata: dict[str, Any] = field(default_factory=dict)


@dataclass
class BenchmarkRun:
    """One execution of a benchmark suite or scenario.

    Frozen context fields are recorded at run time so results are reproducible
    and comparable across model/prompt/schema versions.
    """

    id: UUID = field(default_factory=uuid4)
    status: BenchmarkStatus = BenchmarkStatus.PENDING
    dimension: BenchmarkDimension = BenchmarkDimension.AI_MODEL

    # input identity
    fixture_id: str = ""
    mode: str = "fake"

    # frozen context -- recorded so results are reproducible
    model_name: str = ""
    prompt_version: str = ""
    agent_version: str = ""
    toolset_version: str = ""
    schema_version: str = ""

    # timing and cost
    started_at: datetime | None = None
    completed_at: datetime | None = None
    duration_ms: int = 0
    total_tokens: int = 0
    repeats: int = 1

    # results
    metrics: list[BenchmarkMetric] = field(default_factory=list)
    content_hashes: list[str] = field(default_factory=list)
    error_message: str | None = None
