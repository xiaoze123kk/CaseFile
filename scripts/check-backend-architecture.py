"""Read-only architecture checks for the maintained CaseFile backend."""

from __future__ import annotations

import ast
import sys
from dataclasses import dataclass
from pathlib import Path

MAX_MAINTAINED_LINES = 3_000

GOAL_SESSION_SPEC_PATH = "docs/m3.8-goal-session-runtime.md"
GOAL_SESSION_REFERENCE_PATHS = frozenset(
    {
        "backend/migrations/README.md",
        "docs/architecture-boundaries.md",
        "docs/backend-code-map.md",
        "docs/contracts-code-map.md",
        "docs/data-consistency.md",
    }
)
GOAL_SESSION_REQUIRED_MARKERS = frozenset(
    {
        "GoalSession > TaskRun",
        "succeeded + checkpointed",
        "waiting_clarification",
        "waiting_patch_review",
        "delivery_mode?: new_goal | steer | follow_up | replace",
        "max_goal_revisions = 8",
        "max_task_run_slices = 12",
        "max_consumed_steer_or_replace = 6",
        "agent_goal_sessions",
        "agent_goal_transitions",
        "CASEFILE_CHAT_GOAL_SESSION_ROLLOUT=off | shadow | active",
        "agent_goal_delivery_mode_required",
        "PublicGoalSession",
        "PublicGoalDelivery",
        "不增加 `target_state=\"applied\"`",
        "no_auto_apply=true",
    }
)

STABLE_EXPORTS = {
    "backend/src/casefile/agent_runtime/chat_execution.py": {
        "ChatCompletionValidationError",
        "ChatExecutionResult",
        "ChatExecutionRunner",
        "bind_chat_context_input",
        "coordinate_chat_candidate_validation",
        "prepare_chat_request_artifacts",
        "validate_chat_candidate",
    },
    "backend/src/casefile/agent_runtime/providers.py": {
        "AgentProvider",
        "DeepSeekAgentsProvider",
        "FakeProvider",
        "GenerationProvider",
        "OpenAIAgentsProvider",
        "ProviderProtocolError",
    },
    "backend/src/casefile/application/verification_engine.py": {
        "BatchSimulation",
        "FindingRef",
        "FindingSeverity",
        "ImpactPlanner",
        "ImpactSummary",
        "MutationSimulation",
        "OperationDelta",
        "PatchOperation",
        "VerificationEngine",
        "VerificationFinding",
        "VerificationResult",
    },
    "backend/src/casefile/application/workflow_service.py": {
        "DEFAULT_BUDGET",
        "DEFAULT_MODEL",
        "DEFAULT_PROVIDER",
        "SUPPORTED_PROVIDERS",
        "WorkflowService",
        "append_task_event",
        "event_view",
        "source_view",
        "task_failure_view",
        "task_view",
    },
    "backend/src/casefile/worker/runtime.py": {
        "Worker",
        "WorkerConfig",
        "provider_for_task",
    },
}

CODE_MAP_PATHS = {
    "backend/src/casefile/agent_runtime/chat_preparation.py",
    "backend/src/casefile/agent_runtime/chat_reference_normalization.py",
    "backend/src/casefile/agent_runtime/chat_validation_contracts.py",
    "backend/src/casefile/agent_runtime/provider_adapters/",
    "backend/src/casefile/agent_runtime/prose_writer.py",
    "backend/src/casefile/agent_runtime/prose_rewriter.py",
    "backend/src/casefile/agent_runtime/prose_rewrite_supervisor.py",
    "backend/src/casefile/agent_runtime/brief_to_draft_v8/validation.py",
    "backend/src/casefile/application/workflow/",
    "backend/src/casefile/application/chat_public_contracts.py",
    "backend/src/casefile/application/chat_public_events.py",
    "backend/src/casefile/application/goal_session_repository.py",
    "backend/src/casefile/application/goal_session_state.py",
    "backend/src/casefile/application/workflow_common.py",
    "backend/src/casefile/domain/verification_engine.py",
    "backend/src/casefile/worker/queue.py",
    "backend/src/casefile/worker/finalization.py",
    "backend/src/casefile/worker/executors/",
    "backend/src/casefile/worker/dispatch.py",
    "backend/src/casefile/worker/execution.py",
    "backend/src/casefile/worker/failures.py",
    "backend/src/casefile/worker/generation_reuse.py",
    "backend/src/casefile/worker/handlers/",
    "backend/src/casefile/worker/input_contracts.py",
    "backend/src/casefile/benchmark/prose_writer_eval.py",
    "backend/src/casefile/worker/observability.py",
    "backend/src/casefile/worker/provider_resolution.py",
}


@dataclass(frozen=True, slots=True)
class Violation:
    path: str
    line: int
    message: str

    def render(self) -> str:
        location = f"{self.path}:{self.line}" if self.line else self.path
        return f"{location}: {self.message}"


def _module_names(node: ast.AST) -> tuple[str, ...]:
    if isinstance(node, ast.Import):
        return tuple(alias.name for alias in node.names)
    if isinstance(node, ast.ImportFrom) and node.module:
        return (node.module,)
    return ()


def _literal_string_set(tree: ast.Module, name: str) -> set[str] | None:
    for node in tree.body:
        if not isinstance(node, (ast.Assign, ast.AnnAssign)):
            continue
        targets = node.targets if isinstance(node, ast.Assign) else [node.target]
        if not any(
            isinstance(target, ast.Name) and target.id == name for target in targets
        ):
            continue
        value_node = node.value
        if (
            isinstance(value_node, ast.Call)
            and isinstance(value_node.func, ast.Name)
            and value_node.func.id == "frozenset"
            and len(value_node.args) == 1
            and not value_node.keywords
        ):
            value_node = value_node.args[0]
        try:
            value = ast.literal_eval(value_node)
        except (TypeError, ValueError):
            return None
        if isinstance(value, (list, tuple, set, frozenset)) and all(
            isinstance(item, str) for item in value
        ):
            return set(value)
        return None
    return None


def _is_pure_validation_or_context(relative: Path) -> bool:
    parts = relative.parts
    return "context" in parts or "validation" in relative.stem


def _http_route_path(function: ast.FunctionDef | ast.AsyncFunctionDef) -> str | None:
    for decorator in function.decorator_list:
        if not isinstance(decorator, ast.Call) or not decorator.args:
            continue
        target = decorator.func
        if not isinstance(target, ast.Attribute) or target.attr not in {
            "get",
            "post",
            "put",
            "patch",
            "delete",
        }:
            continue
        route = decorator.args[0]
        if isinstance(route, ast.Constant) and isinstance(route.value, str):
            return route.value
    return None


def _agent_route_internal_names(
    function: ast.FunctionDef | ast.AsyncFunctionDef,
) -> set[str]:
    forbidden = {
        "TaskEvent",
        "TaskRun",
        "event_view",
        "payload_jsonb",
        "result_jsonb",
        "task_view",
    }
    names: set[str] = set()
    for node in ast.walk(function):
        if isinstance(node, ast.Name) and node.id in forbidden:
            names.add(node.id)
        if isinstance(node, ast.Attribute) and node.attr in forbidden:
            names.add(node.attr)
    return names


def _goal_session_spec_violations(repo_root: Path) -> list[Violation]:
    path = repo_root / GOAL_SESSION_SPEC_PATH
    if not path.is_file():
        return [
            Violation(
                GOAL_SESSION_SPEC_PATH,
                0,
                "M3.8 GoalSession architecture decision is missing",
            )
        ]
    content = path.read_text(encoding="utf-8")
    violations = [
        Violation(
            GOAL_SESSION_SPEC_PATH,
            0,
            f"missing frozen M3.8 marker: {marker}",
        )
        for marker in sorted(GOAL_SESSION_REQUIRED_MARKERS)
        if marker not in content
    ]
    for relative in sorted(GOAL_SESSION_REFERENCE_PATHS):
        reference_path = repo_root / relative
        has_reference = reference_path.is_file() and GOAL_SESSION_SPEC_PATH in (
            reference_path.read_text(encoding="utf-8")
        )
        if not has_reference:
            violations.append(
                Violation(
                    relative,
                    0,
                    f"missing reference to frozen M3.8 spec: {GOAL_SESSION_SPEC_PATH}",
                )
            )
    return violations


def collect_violations(repo_root: Path) -> list[Violation]:
    source_root = repo_root / "backend" / "src" / "casefile"
    violations: list[Violation] = _goal_session_spec_violations(repo_root)
    dispatch_relative = Path("backend/src/casefile/worker/dispatch.py")
    dispatch_path = repo_root / dispatch_relative
    dispatch_tree = ast.parse(
        dispatch_path.read_text(encoding="utf-8"),
        filename=str(dispatch_path),
    )
    worker_task_types = _literal_string_set(dispatch_tree, "SUPPORTED_TASK_TYPES")
    if worker_task_types is None:
        violations.append(
            Violation(
                dispatch_relative.as_posix(),
                0,
                "SUPPORTED_TASK_TYPES must be a literal string collection",
            )
        )
        worker_task_types = set()
    for path in sorted(source_root.rglob("*.py")):
        relative = path.relative_to(source_root)
        display = path.relative_to(repo_root).as_posix()
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        if display == "backend/src/casefile/worker/runtime.py":
            task_literals = {
                node.value
                for node in ast.walk(tree)
                if isinstance(node, ast.Constant)
                and isinstance(node.value, str)
                and node.value in worker_task_types
            }
            if task_literals:
                violations.append(
                    Violation(
                        display,
                        0,
                        (
                            "composition root contains task-specific branches: "
                            f"{sorted(task_literals)}"
                        ),
                    )
                )
            worker_class = next(
                (
                    node
                    for node in tree.body
                    if isinstance(node, ast.ClassDef) and node.name == "Worker"
                ),
                None,
            )
            if worker_class is None or worker_class.bases:
                violations.append(
                    Violation(
                        display,
                        0,
                        "Worker must use component composition without mixin bases",
                    )
                )
        if display.startswith("backend/src/casefile/api/"):
            for function in (
                node
                for node in ast.walk(tree)
                if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))
            ):
                route = _http_route_path(function)
                if route is None or "/agent" not in route:
                    continue
                internal_names = _agent_route_internal_names(function)
                if internal_names:
                    violations.append(
                        Violation(
                            display,
                            function.lineno,
                            (
                                "public /agent route references internal workflow views: "
                                f"{sorted(internal_names)}"
                            ),
                        )
                    )
        for node in ast.walk(tree):
            for module in _module_names(node):
                if (
                    display == "backend/src/casefile/worker/runtime.py"
                    and module.startswith(
                        (
                            "casefile.agent_runtime",
                            "casefile.contracts",
                            "casefile.domain",
                        )
                    )
                ):
                    violations.append(
                        Violation(
                            display,
                            node.lineno,
                            f"Worker composition root cannot import task rules from {module}",
                        )
                    )
                if relative.parts[0] == "application" and module.startswith(
                    ("casefile.api", "casefile.worker")
                ):
                    violations.append(
                        Violation(
                            display, node.lineno, f"application cannot import {module}"
                        )
                    )
                if relative.parts[0] == "agent_runtime" and module.startswith(
                    (
                        "casefile.application",
                        "casefile.data_postgres",
                        "sqlalchemy",
                        "fastapi",
                    )
                ):
                    violations.append(
                        Violation(
                            display,
                            node.lineno,
                            f"agent_runtime cannot import {module}",
                        )
                    )
                if _is_pure_validation_or_context(relative) and module.startswith(
                    (
                        "casefile.agent_runtime.providers",
                        "casefile.agent_runtime.provider_adapters",
                        "casefile.data_postgres",
                        "sqlalchemy",
                        "fastapi",
                    )
                ):
                    violations.append(
                        Violation(
                            display, node.lineno, f"pure rules cannot import {module}"
                        )
                    )
            if (
                relative.parts[0] == "benchmark"
                and isinstance(node, ast.ImportFrom)
                and node.module
                and not node.module.startswith("casefile.benchmark")
            ):
                private = sorted(
                    alias.name for alias in node.names if alias.name.startswith("_")
                )
                if private:
                    violations.append(
                        Violation(
                            display,
                            node.lineno,
                            f"benchmark cannot import private symbols: {', '.join(private)}",
                        )
                    )

    for root in (repo_root / "backend" / "src", repo_root / "backend" / "tests"):
        for path in sorted(root.rglob("*.py")):
            if "casefile_contracts" in path.parts:
                continue
            line_count = len(path.read_text(encoding="utf-8").splitlines())
            if line_count > MAX_MAINTAINED_LINES:
                violations.append(
                    Violation(
                        path.relative_to(repo_root).as_posix(),
                        0,
                        (
                            f"maintained Python file has {line_count} lines; "
                            f"limit is {MAX_MAINTAINED_LINES}"
                        ),
                    )
                )

    code_map = (repo_root / "docs" / "backend-code-map.md").read_text(encoding="utf-8")
    for documented_path in sorted(CODE_MAP_PATHS):
        if documented_path not in code_map:
            violations.append(
                Violation(
                    "docs/backend-code-map.md",
                    0,
                    f"missing module path: {documented_path}",
                )
            )

    for relative, expected in STABLE_EXPORTS.items():
        path = repo_root / relative
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        actual = _literal_string_set(tree, "__all__")
        if actual != expected:
            violations.append(
                Violation(
                    relative,
                    0,
                    (
                        f"stable __all__ drift: expected {sorted(expected)}, "
                        f"got {sorted(actual or ())}"
                    ),
                )
            )
    return sorted(violations, key=lambda item: (item.path, item.line, item.message))


def main() -> int:
    repo_root = Path(__file__).resolve().parent.parent
    violations = collect_violations(repo_root)
    if violations:
        print("Backend architecture check failed:", file=sys.stderr)
        for violation in violations:
            print(f"- {violation.render()}", file=sys.stderr)
        return 1
    print("Backend architecture check passed.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
