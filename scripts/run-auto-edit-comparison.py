"""Frozen comparison using the production scene executor and PostgreSQL journal.

Test helpers create isolated task/lease scaffolding with a fake planner. Paid prose
uses the unmodified production _scene orchestration, providers, budgets and store.
No live credential is written to the scaffold's provider setting.
"""

from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
from dataclasses import replace
from hashlib import sha256
from pathlib import Path
from time import perf_counter
from typing import Any
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "backend/tests/integration"))
sys.path.insert(0, str(ROOT / "backend/src"))

from casefile.agent_runtime.credentials import generate_master_key
from casefile.agent_runtime.prose_runtime import prose_runtime_binding
from casefile.agent_runtime.prose_writer import FakeProseWriterProvider
from casefile.benchmark import prose_auto_edit_comparison as comparison
from casefile.benchmark.prose_writer_eval import (
    load_prose_writer_dev_suite,
)
from casefile.data_postgres.models import (
    AgentModelCall,
    AgentStepRun,
    CompileArtifact,
)
from casefile.domain.narrative_compiler import canonical_json_sha256
from casefile.worker.executors.prose_providers import ProseProviders
from casefile.worker.executors.prose_shadow import ProseShadowExecutor
from sqlalchemy import create_engine, select, text
from test_prose_auto_edit_runtime import EditorialProvider
from test_prose_shadow_runtime import _prepare, _providers, _run


def save(path: Path, value: Any) -> None:
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2), encoding="utf-8")


class ProductionArm:
    def __init__(self, database: Any, output: Path, live: bool) -> None:
        self.database, self.output, self.live = database, output, live
        self.sequence = 0

    def __call__(self, task: dict[str, Any], mode: str, api_key: str) -> dict[str, Any]:
        self.sequence += 1
        folder = self.output / f"arm-{self.sequence:02d}-{mode}"
        folder.mkdir()
        save(folder / "input.json", task)
        factory, _, run, _ = _prepare(self.database, prose_mode=mode)
        save(folder / "identity.json", {**run, "status": "prepared"})
        captured: dict[str, Any] = {}

        def execute(executor: ProseShadowExecutor, manifest: dict[str, Any]) -> Any:
            store = executor.store
            executor.ordered = sorted(
                task["scene_plan"]["scenes"], key=lambda s: s["discourse_order"]
            )
            executor.previous_auto_edit_issues = task.get("previous_edit_issues", [])
            executor.source = {
                "scene_plan_hash": canonical_json_sha256(task["scene_plan"]),
                "narrative_ir_hash": canonical_json_sha256(task["narrative_ir"]),
                "profile_hash": canonical_json_sha256(task["asset"]["profile"]),
            }
            store.scene_id = task["checklist"]["scene_id"]
            started = perf_counter()
            state, accepted, reason = executor._scene(
                task["scene_plan"],
                task["narrative_ir"],
                task["asset"]["profile"],
                task["asset"]["previous_scene_render"],
                api_key,
            )
            scene = next(s for s in executor.ordered if s["scene_id"] == store.scene_id)
            projected = executor._scene_manifest(scene, state, reason)
            executor.scenes.append(projected)
            # This is a scene benchmark, not a full-plan completion assertion.
            executor.ordered = [scene]
            captured.update(
                **projected,
                mode=mode,
                completed=accepted is not None,
                status=state,
                error_code=reason,
                selection_reason=accepted.get("selection_reason") if accepted else None,
                character_count=accepted["character_count"] if accepted else 0,
                unresolved_issues=list(executor.previous_auto_edit_issues),
                wall_latency_ms=round((perf_counter() - started) * 1000),
                _render=accepted,
                _text=comparison._text(accepted) if accepted else "",
            )
            return executor._manifest("succeeded" if accepted else state, reason)

        try:
            providers = (
                ProseProviders.deepseek()
                if self.live
                else replace(
                    _providers(),
                    continuity=FakeProseWriterProvider(
                        candidates=({"verdict": "pass", "issues": []},)
                    ),
                )
            )
            if not self.live and mode == "auto_edit":
                providers = replace(providers, rewriter=EditorialProvider("retain"))
            with patch.object(ProseShadowExecutor, "execute", execute):
                _run(factory, run, providers, self.database[2])
        finally:
            # The DB journal is committed after every request, including failures.
            # Export all responses and immutable artifacts even on an interrupted arm.
            with factory() as session:
                calls = list(
                    session.scalars(
                        select(AgentModelCall)
                        .join(
                            AgentStepRun,
                            AgentStepRun.id == AgentModelCall.agent_step_run_id,
                        )
                        .where(
                            AgentModelCall.task_run_id == run["task_run_id"],
                            AgentStepRun.diagnostic_jsonb["scene_id"].astext
                            == task["checklist"]["scene_id"],
                        )
                        .order_by(AgentModelCall.id)
                    )
                )
                fields = (
                    "id",
                    "request_fingerprint",
                    "status",
                    "model_id",
                    "prompt_version",
                    "prompt_component_id",
                    "prompt_sha256",
                    "input_hash",
                    "output_hash",
                    "raw_output_text",
                    "response_jsonb",
                    "usage_jsonb",
                    "error_code",
                    "latency_ms",
                    "parse_status",
                    "issues_jsonb",
                )
                save(
                    folder / "calls.json",
                    [{k: getattr(c, k) for k in fields} for c in calls],
                )
                artifacts = list(
                    session.scalars(
                        select(CompileArtifact).where(
                            CompileArtifact.compile_run_id == run["compile_run_id"]
                        )
                    )
                )
                save(
                    folder / "artifacts.json",
                    [
                        {
                            "key": a.artifact_key,
                            "hash": a.content_hash,
                            "content": a.content_jsonb,
                        }
                        for a in artifacts
                    ],
                )
            if not captured:
                captured.update(
                    mode=mode,
                    status="infrastructure_failed",
                    completed=False,
                    error_code="worker_did_not_deliver",
                    character_count=0,
                    unresolved_issues=[],
                    _render=None,
                    _text="",
                )
            captured["physical_request_count"] = len(calls)
            captured["unknown_usage_count"] = sum(
                not c.usage_jsonb.get("usage_known", False) for c in calls
            )
            captured["usage"] = {
                k: sum(int(c.usage_jsonb.get(k, 0)) for c in calls)
                for k in ("input_tokens", "output_tokens", "total_tokens")
            }
            captured["evidence_directory"] = str(folder)
            save(folder / "result.json", captured)
        print(
            json.dumps(
                {
                    "arm": self.sequence,
                    "mode": mode,
                    "status": captured["status"],
                    "calls": len(calls),
                    "tokens": captured["usage"]["total_tokens"],
                }
            ),
            flush=True,
        )
        return captured


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--live", action="store_true")
    args = parser.parse_args()
    url = os.environ["AUTO_EDIT_TEST_DATABASE_URL"]
    if not url.rsplit("/", 1)[-1].startswith("casefile_auto_edit_"):
        raise ValueError("dedicated_auto_edit_database_required")
    args.output.mkdir(parents=True, exist_ok=False)
    engine = create_engine(url)
    with engine.begin() as connection:
        actor = connection.execute(
            text(
                "INSERT INTO users(display_name) VALUES ('Frozen auto edit comparison') RETURNING id"
            )
        ).scalar_one()
    database = (engine, actor, generate_master_key())
    suite = load_prose_writer_dev_suite()
    files = {
        str(p.relative_to(ROOT)): sha256(p.read_bytes()).hexdigest()
        for base in ("backend/src/casefile", "packages", "prompts", "scripts")
        for p in (ROOT / base).rglob("*")
        if p.is_file()
        and p.suffix in {".py", ".json", ".md"}
        and "node_modules" not in p.parts
        and "__pycache__" not in p.parts
    }
    frozen = {
        "head": subprocess.check_output(
            ["git", "rev-parse", "HEAD"], cwd=ROOT, text=True
        ).strip(),
        "source_hashes": files,
        "suite_hash": canonical_json_sha256(suite),
        "runtimes": {
            m: prose_runtime_binding(prose_mode=m) for m in ("auto_edit", "full_polish")
        },
        "execution": "production_scene_executor_and_durable_postgres_store",
        "scaffold": "fake_planner_only_no_planning_cost_included",
        "live": args.live,
    }
    save(args.output / "frozen.json", frozen)
    save(args.output / "suite.json", suite)
    arm = ProductionArm(database, args.output, args.live)
    key = (
        (
            os.environ.get("CASEFILE_DEEPSEEK_API_KEY")
            or os.environ.get("DEEPSEEK_API_KEY")
            or ""
        )
        if args.live
        else "sk-fake-offline"
    )
    if args.live:
        if not key:
            raise ValueError("missing_credential")
        comparison.run(args.output / "report.json", key, arm=arm, frozen=frozen)
    else:
        task = suite["tasks"][0]
        for mode in ("auto_edit", "full_polish"):
            result = arm(task, mode, key)
            assert result["completed"], result
    engine.dispose()


if __name__ == "__main__":
    main()
