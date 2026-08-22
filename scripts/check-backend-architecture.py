"""Read-only architecture checks for the maintained CaseFile backend."""

from __future__ import annotations

import ast
import sys
from dataclasses import dataclass
from pathlib import Path

MAX_MAINTAINED_LINES = 2_000

STABLE_EXPORTS = {
    "backend/src/casefile/agent_runtime/chat_execution.py": {
        "ChatCompletionValidationError",
        "ChatExecutionResult",
        "ChatExecutionRunner",
        "bind_chat_context_input",
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
    "backend/src/casefile/agent_runtime/brief_to_draft_v8/validation.py",
    "backend/src/casefile/application/workflow/",
    "backend/src/casefile/application/workflow_common.py",
    "backend/src/casefile/domain/verification_engine.py",
    "backend/src/casefile/worker/queue.py",
    "backend/src/casefile/worker/finalization.py",
    "backend/src/casefile/worker/executors/",
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
        if not any(isinstance(target, ast.Name) and target.id == name for target in targets):
            continue
        try:
            value = ast.literal_eval(node.value)
        except (TypeError, ValueError):
            return None
        if isinstance(value, (list, tuple)) and all(isinstance(item, str) for item in value):
            return set(value)
        return None
    return None


def _is_pure_validation_or_context(relative: Path) -> bool:
    parts = relative.parts
    return "context" in parts or "validation" in relative.stem


def collect_violations(repo_root: Path) -> list[Violation]:
    source_root = repo_root / "backend" / "src" / "casefile"
    violations: list[Violation] = []
    for path in sorted(source_root.rglob("*.py")):
        relative = path.relative_to(source_root)
        display = path.relative_to(repo_root).as_posix()
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        for node in ast.walk(tree):
            for module in _module_names(node):
                if relative.parts[0] == "application" and module.startswith(
                    ("casefile.api", "casefile.worker")
                ):
                    violations.append(
                        Violation(display, node.lineno, f"application cannot import {module}")
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
                        Violation(display, node.lineno, f"agent_runtime cannot import {module}")
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
                        Violation(display, node.lineno, f"pure rules cannot import {module}")
                    )
            if (
                relative.parts[0] == "benchmark"
                and isinstance(node, ast.ImportFrom)
                and node.module
                and not node.module.startswith("casefile.benchmark")
            ):
                private = sorted(alias.name for alias in node.names if alias.name.startswith("_"))
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
                Violation("docs/backend-code-map.md", 0, f"missing module path: {documented_path}")
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
