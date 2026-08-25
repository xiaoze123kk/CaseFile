"""Stable Worker entrypoint.

Owns the claim -> dispatch -> execute -> finalize loop and public Worker
configuration. Does not own routing, context, validation, repair, or provider
protocol rules. Public API: ``Worker``, ``WorkerConfig``, ``provider_for_task``.
"""

from __future__ import annotations

import os
import socket
import time
from collections.abc import Callable
from copy import deepcopy
from dataclasses import dataclass, replace
from typing import Any, Literal, cast

from sqlalchemy import select
from sqlalchemy.orm import Session, sessionmaker

from casefile.agent_runtime import (
    CANDIDATE_STRATEGY_VERSION,
    AgentProvider,
    BriefAnchorExtractRequest,
    BriefIntakeQuestionsRequest,
    BriefIntakeSynthesizeRequest,
    BriefPolishRequest,
    BriefStrategyOptionsRequest,
    CandidateStrategy,
    DeepSeekAgentsProvider,
    GenerationRequest,
    GenerationResult,
    OpenAIAgentsProvider,
    PolishMode,
    ReverseParseRequest,
)
from casefile.agent_runtime.chat_execution import (
    ChatExecutionRunner,
    prepare_chat_request_artifacts,
)
from casefile.agent_runtime.chat_intent import (
    route_public_payload,
)
from casefile.agent_runtime.credentials import decrypt_api_key
from casefile.application.agent_mutation import (
    append_repair_companions,
    general_mutation_impact_hash,
)
from casefile.application.closure_repair import ClosureRepairMode
from casefile.contracts import (
    ContractValidationError,
    public_validation_issues,
    validate_casefile,
)
from casefile.data_postgres.models import (
    Brief,
    BriefVersion,
    TaskRun,
    UserProviderSetting,
)
from casefile.data_postgres.repositories import ProjectRepository
from casefile.domain.verification_engine import VerificationEngine
from casefile.worker.closure_repair import (
    execute_chat_closure_repair,
    execute_mutation_closure_repair,
)
from casefile.worker.executors.chat import (
    ChatTaskExecutorMixin,
    resolve_chat_route,
)
from casefile.worker.executors.completion import CompletionExecutorMixin
from casefile.worker.finalization import TaskFinalizationMixin
from casefile.worker.queue import QueueMixin
from casefile.worker.support import (
    TaskCancellationRequested,
    _json_hash,
    _merge_numeric_usage,
    _network_retries,
    _optional_string,
    _previous_attempt_repair_feedback,
    _required_integer,
    _required_object,
    _required_string,
    _reusable_component_steps,
    _text_hash,
)

ProviderFactory = Callable[[TaskRun], AgentProvider]
GeneralMutationMode = Literal["off", "shadow", "suggest"]


def provider_for_task(task: TaskRun) -> AgentProvider:
    if task.provider == "openai":
        return OpenAIAgentsProvider()
    if task.provider == "deepseek":
        return DeepSeekAgentsProvider()
    raise RuntimeError(f"Unsupported provider frozen on TaskRun: {task.provider}")


DEFAULT_CONTEXT_HARD_INPUT_TOKENS = 128_000


@dataclass(frozen=True, slots=True)
class WorkerConfig:
    worker_id: str
    poll_seconds: float = 1.0
    lease_seconds: int = 600
    closure_repair_mode: ClosureRepairMode = "shadow"
    general_mutation_mode: GeneralMutationMode = "off"
    general_mutation_create_enabled: bool = False
    general_mutation_delete_enabled: bool = False

    def __post_init__(self) -> None:
        if self.closure_repair_mode not in {"off", "shadow", "suggest"}:
            raise ValueError("CLOSURE_REPAIR_MODE must be one of: off, shadow, suggest")
        if self.general_mutation_mode not in {"off", "shadow", "suggest"}:
            raise ValueError(
                "CASEFILE_CHAT_GENERAL_MUTATION_MODE must be one of: off, shadow, suggest"
            )

    @classmethod
    def from_environment(cls) -> WorkerConfig:
        default_id = f"{socket.gethostname()}-{os.getpid()}"
        return cls(
            worker_id=os.environ.get("CASEFILE_WORKER_ID", default_id),
            poll_seconds=float(os.environ.get("CASEFILE_WORKER_POLL_SECONDS", "1")),
            lease_seconds=int(os.environ.get("CASEFILE_WORKER_LEASE_SECONDS", "600")),
            closure_repair_mode=cast(
                ClosureRepairMode,
                os.environ.get("CLOSURE_REPAIR_MODE", "shadow").strip().lower(),
            ),
            general_mutation_mode=cast(
                GeneralMutationMode,
                os.environ.get("CASEFILE_CHAT_GENERAL_MUTATION_MODE", "off")
                .strip()
                .lower(),
            ),
            general_mutation_create_enabled=(
                os.environ.get("CASEFILE_CHAT_GENERAL_MUTATION_CREATE_ENABLED", "false")
                .strip()
                .lower()
                in {"1", "true", "yes", "on"}
            ),
            general_mutation_delete_enabled=(
                os.environ.get("CASEFILE_CHAT_GENERAL_MUTATION_DELETE_ENABLED", "false")
                .strip()
                .lower()
                in {"1", "true", "yes", "on"}
            ),
        )


class Worker(TaskFinalizationMixin, QueueMixin, ChatTaskExecutorMixin, CompletionExecutorMixin):
    """Consume TaskRuns with `FOR UPDATE SKIP LOCKED`; one instance executes serially."""

    def __init__(
        self,
        session_factory: sessionmaker[Session],
        *,
        config: WorkerConfig,
        provider_factory: ProviderFactory | None = None,
    ) -> None:
        self.session_factory = session_factory
        self.config = config
        self.provider_factory = provider_factory or provider_for_task

    def run_forever(self) -> None:
        while True:
            if not self.run_once():
                time.sleep(self.config.poll_seconds)

    def run_once(self) -> bool:
        claimed = self._claim_next()
        if claimed is None:
            return False
        if claimed == "cancelled":
            return True
        task_run_id, attempt_id = claimed
        self._execute(task_run_id, attempt_id)
        return True

    def _execute(self, task_run_id: int, attempt_id: int) -> None:
        candidate: dict[str, Any] | None = None
        usage: dict[str, Any] = {}
        validation_errors: list[dict[str, Any]] = []
        sensitive_values: tuple[str, ...] = ()
        try:
            task_snapshot, api_key = self._load_task_context(task_run_id)
            sensitive_values = (api_key,)
            provider = self.provider_factory(task_snapshot)
            if task_snapshot.task_type == "brief_polish":
                source_text = _required_string(
                    task_snapshot.input_jsonb,
                    "source_text",
                )
                if _text_hash(source_text) != task_snapshot.input_hash:
                    raise RuntimeError("Frozen SourceRecord payload does not match its input hash")
                polish_mode = _required_string(
                    task_snapshot.input_jsonb,
                    "polish_mode",
                )
                if polish_mode not in {"proofread", "rewrite", "narrative_enhance"}:
                    raise RuntimeError("Frozen polish mode is invalid")
                polish_request = BriefPolishRequest(
                    task_run_id=task_snapshot.id,
                    prompt_version=task_snapshot.prompt_version,
                    source_text=source_text,
                    polish_mode=cast(PolishMode, polish_mode),
                    input_hash=task_snapshot.input_hash,
                    model_id=task_snapshot.model_id,
                    api_key=api_key,
                    max_turns=int(task_snapshot.budget_jsonb.get("max_turns", 12)),
                    emit=lambda event_type, stage, payload: self._emit(
                        task_run_id, event_type, stage, payload
                    ),
                    network_retries=_network_retries(task_snapshot),
                )
                polish_result = provider.polish(polish_request)
                candidate = polish_result.candidate.model_dump(mode="json")
                usage = polish_result.usage
                self._complete_polish(
                    task_run_id,
                    attempt_id,
                    polish_result,
                )
                return
            if task_snapshot.task_type == "brief_anchor_extract":
                frozen_brief = _required_object(task_snapshot.input_jsonb, "brief")
                if _json_hash(frozen_brief) != task_snapshot.input_hash:
                    raise RuntimeError("Frozen Brief payload does not match its input hash")
                mode = task_snapshot.input_jsonb.get("mode", "extract")
                if mode not in {"extract", "suggest_author_answer"}:
                    raise RuntimeError("Frozen Brief anchor extraction mode is invalid")
                extract_request = BriefAnchorExtractRequest(
                    task_run_id=task_snapshot.id,
                    prompt_version=task_snapshot.prompt_version,
                    brief=frozen_brief,
                    input_hash=task_snapshot.input_hash,
                    model_id=task_snapshot.model_id,
                    api_key=api_key,
                    max_turns=int(task_snapshot.budget_jsonb.get("max_turns", 12)),
                    emit=lambda event_type, stage, payload: self._emit(
                        task_run_id, event_type, stage, payload
                    ),
                    network_retries=_network_retries(task_snapshot),
                    mode=cast(Literal["extract", "suggest_author_answer"], mode),
                )
                extract_result = provider.extract_anchors(extract_request)
                candidate = extract_result.candidate.model_dump(
                    mode="json",
                    exclude_none=True,
                )
                usage = extract_result.usage
                self._complete_anchor_extract(
                    task_run_id,
                    attempt_id,
                    extract_result,
                )
                return
            if task_snapshot.task_type == "brief_strategy_options":
                strategy_request = self._load_strategy_options_request(
                    task_snapshot,
                    api_key,
                )
                strategy_result = provider.strategy_options(strategy_request)
                candidate = strategy_result.candidate.model_dump(mode="json")
                usage = strategy_result.usage
                self._complete_strategy_options(
                    task_run_id,
                    attempt_id,
                    strategy_result,
                )
                return
            if task_snapshot.task_type == "brief_intake_questions":
                if _json_hash(task_snapshot.input_jsonb) != task_snapshot.input_hash:
                    raise RuntimeError(
                        "Frozen Brief Intake question payload does not match its input hash"
                    )
                frozen_source = _required_object(task_snapshot.input_jsonb, "source")
                mode = task_snapshot.input_jsonb.get("mode", "initial")
                if mode not in ("initial", "additional"):
                    raise RuntimeError("Frozen Brief Intake question mode is invalid")
                existing_questions = task_snapshot.input_jsonb.get("existing_questions", [])
                if not isinstance(existing_questions, list):
                    raise RuntimeError("Frozen Brief Intake existing questions must be an array")
                questions_request = BriefIntakeQuestionsRequest(
                    task_run_id=task_snapshot.id,
                    prompt_version=task_snapshot.prompt_version,
                    source_text=_required_string(frozen_source, "content_text"),
                    existing_questions=deepcopy(existing_questions),
                    mode=mode,
                    input_hash=task_snapshot.input_hash,
                    model_id=task_snapshot.model_id,
                    api_key=api_key,
                    max_turns=int(task_snapshot.budget_jsonb.get("max_turns", 12)),
                    emit=lambda event_type, stage, payload: self._emit(
                        task_run_id, event_type, stage, payload
                    ),
                    network_retries=_network_retries(task_snapshot),
                )
                questions_result = provider.intake_questions(questions_request)
                candidate = questions_result.candidate.model_dump(mode="json")
                usage = questions_result.usage
                self._complete_intake_questions(
                    task_run_id,
                    attempt_id,
                    questions_result,
                )
                return
            if task_snapshot.task_type == "brief_intake_synthesize":
                if _json_hash(task_snapshot.input_jsonb) != task_snapshot.input_hash:
                    raise RuntimeError(
                        "Frozen Brief Intake synthesis payload does not match its input hash"
                    )
                synthesize_request = BriefIntakeSynthesizeRequest(
                    task_run_id=task_snapshot.id,
                    prompt_version=task_snapshot.prompt_version,
                    input_data=task_snapshot.input_jsonb,
                    input_hash=task_snapshot.input_hash,
                    model_id=task_snapshot.model_id,
                    api_key=api_key,
                    max_turns=int(task_snapshot.budget_jsonb.get("max_turns", 12)),
                    emit=lambda event_type, stage, payload: self._emit(
                        task_run_id, event_type, stage, payload
                    ),
                    network_retries=_network_retries(task_snapshot),
                )
                synthesize_result = provider.synthesize_intake(synthesize_request)
                candidate = synthesize_result.candidate.model_dump(mode="json")
                usage = synthesize_result.usage
                self._complete_intake_synthesize(
                    task_run_id,
                    attempt_id,
                    synthesize_result,
                )
                return
            if task_snapshot.task_type == "reverse_parse":
                if _json_hash(task_snapshot.input_jsonb) != task_snapshot.input_hash:
                    raise RuntimeError("Frozen reverse parse payload does not match its input hash")
                blocks = task_snapshot.input_jsonb.get("blocks", [])
                if not isinstance(blocks, list):
                    raise RuntimeError("Frozen reverse parse blocks must be an array")
                parse_request = ReverseParseRequest(
                    task_run_id=task_snapshot.id,
                    prompt_version=task_snapshot.prompt_version,
                    blocks=deepcopy(blocks),
                    input_hash=task_snapshot.input_hash,
                    model_id=task_snapshot.model_id,
                    api_key=api_key,
                    max_turns=int(task_snapshot.budget_jsonb.get("max_turns", 12)),
                    emit=lambda event_type, stage, payload: self._emit(
                        task_run_id, event_type, stage, payload
                    ),
                    network_retries=_network_retries(task_snapshot),
                )
                parse_result = provider.reverse_parse(parse_request)
                candidate = parse_result.candidate.model_dump(mode="json")
                usage = parse_result.usage
                self._complete_reverse_parse(task_run_id, attempt_id, parse_result)
                return
            if task_snapshot.task_type == "casefile_chat":
                chat_request = self._load_chat_request(task_snapshot, api_key)
                previous_routing = self._load_previous_chat_routing(task_snapshot.id)
                chat_request = resolve_chat_route(
                    chat_request,
                    budget=task_snapshot.budget_jsonb,
                    provider=provider,
                    previous=previous_routing,
                    allow_general_mutation_create=(
                        self.config.general_mutation_mode != "off"
                        and self.config.general_mutation_create_enabled
                    ),
                    allow_general_mutation_delete=(
                        self.config.general_mutation_mode != "off"
                        and self.config.general_mutation_delete_enabled
                    ),
                    allow_general_mutation_update=(
                        self.config.general_mutation_mode != "off"
                    ),
                )
                chat_request = prepare_chat_request_artifacts(chat_request)
                if chat_request.route is not None:
                    if previous_routing is None:
                        self._emit_chat_routing_events(task_run_id, chat_request)
                        if chat_request.route.route_source == "fallback":
                            self._emit(
                                task_run_id,
                                "router.fallback",
                                "routing",
                                route_public_payload(chat_request.route),
                            )
                    if (
                        chat_request.task_understanding is not None
                        and chat_request.task_understanding.primary_intent == "logic_audit"
                    ):
                        verification_trigger = str(
                            task_snapshot.input_jsonb.get("verification_trigger", "chat")
                        )
                        self._emit(
                            task_run_id,
                            "verification.started",
                            "verification",
                            {
                                "trigger": verification_trigger,
                                "profile": "balanced",
                                "draft_revision": task_snapshot.input_draft_revision,
                                "input_hash": task_snapshot.input_hash,
                            },
                        )
                chat_request = self._emit_chat_context_events(
                    task_run_id, task_snapshot, chat_request
                )

                def complete_chat(result: Any) -> None:
                    general_mutation_envelope, general_mutation_usage = (
                        self._execute_general_mutation(
                            task_snapshot,
                            chat_request,
                            provider,
                            api_key,
                        )
                    )
                    if general_mutation_envelope is None:
                        repair_envelope, repair_usage = execute_chat_closure_repair(
                            task_snapshot,
                            result,
                            provider=provider,
                            api_key=api_key,
                            mode=self.config.closure_repair_mode,
                            emit=lambda event_type, stage, payload: self._emit(
                                task_run_id, event_type, stage, payload
                            ),
                        )
                    elif "bound" in general_mutation_envelope:
                        repair_envelope, repair_usage, repair_result = (
                            execute_mutation_closure_repair(
                                task_snapshot,
                                general_mutation_envelope["bound"].mutation_set,
                                provider=provider,
                                api_key=api_key,
                                mode=self.config.closure_repair_mode,
                                emit=lambda event_type, stage, payload: self._emit(
                                    task_run_id, event_type, stage, payload
                                ),
                            )
                        )
                        if (
                            self.config.closure_repair_mode == "suggest"
                            and repair_result is not None
                            and repair_result.repaired
                        ):
                            repaired_bound = append_repair_companions(
                                general_mutation_envelope["bound"],
                                task_snapshot.input_jsonb["casefile"],
                                [
                                    item.as_dict()
                                    for item in repair_result.companion_operations
                                ],
                            )
                            repaired_simulation = VerificationEngine(
                                profile="fast",
                                closure_policy_version=(
                                    repaired_bound.mutation_set.closure_policy_version
                                ),
                            ).simulate_mutation_set(
                                task_snapshot.input_jsonb["casefile"],
                                repaired_bound.mutation_set,
                            )
                            if not repaired_simulation.can_apply:
                                raise RuntimeError("Repaired General Mutation proof diverged")
                            general_mutation_envelope = {
                                **general_mutation_envelope,
                                "primary_bound": general_mutation_envelope["bound"],
                                "bound": repaired_bound,
                                "simulation": repaired_simulation,
                                "impact_hash": general_mutation_impact_hash(
                                    repaired_simulation
                                ),
                            }
                    else:
                        repair_envelope, repair_usage = None, {}
                    self._complete_chat(
                        task_run_id,
                        attempt_id,
                        result,
                        route=chat_request.route,
                        repair_envelope=repair_envelope,
                        repair_usage=_merge_numeric_usage(
                            repair_usage, general_mutation_usage
                        ),
                        general_mutation_envelope=general_mutation_envelope,
                    )

                execution = ChatExecutionRunner(provider).run(chat_request, complete=complete_chat)
                chat_result = execution.result
                candidate = chat_result.candidate.model_dump(mode="json")
                usage = execution.usage
                self._maybe_compact_chat_thread(
                    task_snapshot,
                    provider,
                    api_key,
                    model_requested_compaction=(
                        int(
                            getattr(
                                chat_result.tools,
                                "requested_thread_compaction",
                                0,
                            )
                        )
                        > 0
                    ),
                )
                return
            if task_snapshot.task_type != "brief_to_draft":
                raise RuntimeError(f"Unsupported TaskRun type: {task_snapshot.task_type}")
            generation_request = self._load_generation_request(task_snapshot, api_key)
            result: GenerationResult | None = None
            repair_limit = (
                0
                if task_snapshot.prompt_version == "brief-to-draft-v7"
                else int(task_snapshot.budget_jsonb.get("structural_repair_attempts", 5))
            )
            feedback = generation_request.repair_feedback
            feedback_history: list[dict[str, Any]] = list(feedback)
            for repair_no in range(repair_limit + 1):
                if repair_no:
                    self._emit(
                        task_run_id,
                        "model.repair_started",
                        "repairing",
                        {"repair_no": repair_no, "max_repairs": repair_limit},
                    )
                try:
                    result = provider.generate(
                        replace(generation_request, repair_feedback=feedback)
                    )
                    candidate = result.candidate
                    validate_casefile(candidate)
                    break
                except ContractValidationError as error:
                    public_issues = public_validation_issues(error.errors)
                    validation_errors.append({"repair_no": repair_no, "issues": public_issues})
                    feedback_history.append({"repair_no": repair_no, "issues": error.errors})
                    feedback = tuple(feedback_history[-3:])
                    self._emit(
                        task_run_id,
                        "validation.failed",
                        "validating",
                        {
                            "repair_no": repair_no,
                            "issue_count": len(error.errors),
                            "issues": public_issues,
                        },
                    )
                    if repair_no >= repair_limit:
                        raise
            if result is None or candidate is None:
                raise RuntimeError("Provider returned no candidate")
            usage = result.usage
            self._complete_generation_candidate(
                task_run_id,
                attempt_id,
                candidate,
                result,
                validation_errors,
            )
        except TaskCancellationRequested:
            self._cancel(
                task_run_id,
                attempt_id,
                usage=usage,
                validation_errors=validation_errors,
            )
        except Exception as error:
            provider_usage = getattr(error, "usage", None)
            if isinstance(provider_usage, dict):
                usage = _merge_numeric_usage(usage, provider_usage)
            provider_tools = getattr(error, "tools", None)
            if provider_tools is not None and hasattr(provider_tools, "as_dict"):
                usage["tool_metrics"] = provider_tools.as_dict()
            if self._cancel(
                task_run_id,
                attempt_id,
                usage=usage,
                validation_errors=validation_errors,
            ):
                return
            self._fail(
                task_run_id,
                attempt_id,
                error,
                candidate=candidate,
                usage=usage,
                validation_errors=validation_errors,
                sensitive_values=sensitive_values,
            )

    def _load_task_context(self, task_run_id: int) -> tuple[TaskRun, str]:
        with self.session_factory() as session, session.begin():
            task = session.scalar(select(TaskRun).where(TaskRun.id == task_run_id))
            if task is None or task.status != "running" or task.leased_by != self.config.worker_id:
                raise RuntimeError("TaskRun lease is no longer owned by this worker")
            setting = session.get(UserProviderSetting, task.provider_setting_id)
            if setting is None:
                raise RuntimeError("Frozen provider setting is missing")
            if setting.user_id != task.actor_user_id or setting.provider != task.provider:
                raise RuntimeError("Frozen provider setting does not match TaskRun provenance")
            if setting.config_version != task.provider_config_version:
                raise RuntimeError("Frozen provider setting version no longer matches TaskRun")
            if (
                setting.credential_status == "deleted"
                or setting.secret_ciphertext is None
                or setting.secret_nonce is None
                or setting.key_version is None
            ):
                raise RuntimeError("Frozen provider credential has been deleted")
            api_key = decrypt_api_key(
                setting.secret_ciphertext,
                setting.secret_nonce,
                user_id=setting.user_id,
                provider=setting.provider,
                key_version=setting.key_version,
            )
            session.expunge(task)
            return task, api_key

    def _load_strategy_options_request(
        self,
        task: TaskRun,
        api_key: str,
    ) -> BriefStrategyOptionsRequest:
        with self.session_factory() as session, session.begin():
            brief_version = (
                None
                if task.brief_version_id is None
                else session.get(BriefVersion, task.brief_version_id)
            )
            if brief_version is None:
                raise RuntimeError("Frozen strategy BriefVersion is missing")
            frozen_brief = _required_object(task.input_jsonb, "brief")
            if brief_version.content_hash != task.input_hash:
                raise RuntimeError("Frozen strategy BriefVersion hash changed")
            if _json_hash(frozen_brief) != task.input_hash:
                raise RuntimeError("Frozen strategy Brief payload does not match its hash")
            return BriefStrategyOptionsRequest(
                task_run_id=task.id,
                prompt_version=task.prompt_version,
                brief=frozen_brief,
                input_hash=task.input_hash,
                model_id=task.model_id,
                api_key=api_key,
                max_turns=int(task.budget_jsonb.get("max_turns", 12)),
                emit=lambda event_type, stage, payload: self._emit(
                    task.id, event_type, stage, payload
                ),
                network_retries=_network_retries(task),
            )

    def _load_generation_request(
        self,
        task: TaskRun,
        api_key: str,
    ) -> GenerationRequest:
        with self.session_factory() as session, session.begin():
            owned = ProjectRepository(session).get_owned(task.actor_user_id, task.project_id)
            brief_version = (
                None
                if task.brief_version_id is None
                else session.get(BriefVersion, task.brief_version_id)
            )
            if owned is None or brief_version is None:
                raise RuntimeError("Frozen generation dependencies are missing")
            brief = session.get(Brief, brief_version.brief_id)
            if brief is None:
                raise RuntimeError("Brief is missing")
            frozen_brief = _required_object(task.input_jsonb, "brief")
            if brief_version.content_hash != task.input_hash:
                raise RuntimeError("Frozen BriefVersion hash no longer matches TaskRun input")
            if _json_hash(frozen_brief) != task.input_hash:
                raise RuntimeError("Frozen TaskRun Brief payload does not match its input hash")
            frozen_version = _required_object(task.input_jsonb, "version")
            raw_strategy = task.input_jsonb.get(
                "candidate_strategy",
                CandidateStrategy.BALANCED.value,
            )
            try:
                candidate_strategy = CandidateStrategy(raw_strategy)
            except ValueError as error:
                raise RuntimeError("Frozen candidate strategy is invalid") from error
            candidate_strategy_version = task.input_jsonb.get(
                "candidate_strategy_version",
                CANDIDATE_STRATEGY_VERSION,
            )
            if candidate_strategy_version != CANDIDATE_STRATEGY_VERSION:
                raise RuntimeError("Frozen candidate strategy version is invalid")
            return GenerationRequest(
                task_run_id=task.id,
                prompt_version=task.prompt_version,
                brief=frozen_brief,
                schema_version=str(task.input_jsonb.get("schema_version", "1.0")),
                casefile_id=_required_string(task.input_jsonb, "casefile_id"),
                brief_id=_required_string(task.input_jsonb, "brief_public_id"),
                brief_version=_required_integer(
                    task.input_jsonb,
                    "brief_version_no",
                ),
                version_id=_required_string(frozen_version, "version_id"),
                version_no=_required_integer(frozen_version, "version_no"),
                parent_version_id=_optional_string(
                    frozen_version,
                    "parent_version_id",
                ),
                model_id=task.model_id,
                api_key=api_key,
                max_turns=int(task.budget_jsonb.get("max_turns", 12)),
                emit=lambda event_type, stage, payload: self._emit(
                    task.id, event_type, stage, payload
                ),
                network_retries=_network_retries(task),
                candidate_strategy=candidate_strategy,
                candidate_strategy_version=candidate_strategy_version,
                reusable_steps=_reusable_component_steps(session, task),
                repair_feedback=_previous_attempt_repair_feedback(session, task),
                agent_version=task.agent_version,
                toolset_version=task.toolset_version,
            )


__all__ = ["Worker", "WorkerConfig", "provider_for_task"]
