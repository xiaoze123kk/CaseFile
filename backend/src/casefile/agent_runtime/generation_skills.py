"""Release-bound skill discovery and per-call progressive disclosure."""

from __future__ import annotations

import json
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from dataclasses import asdict, dataclass, field, replace
from hashlib import sha256
from importlib.resources import files
from importlib.resources.abc import Traversable
from typing import Any

from casefile.agent_runtime.brief_to_draft_runtime import resolve_pipeline_spec
from casefile.agent_runtime.generation_hooks import (
    HookBinding,
    HookDispatcher,
    HookEvent,
    HookInput,
    HookResult,
)
from casefile.agent_runtime.prompt_package import (
    PromptPackage,
    RenderedPrompt,
    render_prompt_package,
)
from casefile.agent_runtime.skill_resources import read_skill_resource, skill_metadata


def execution_hash(value: object) -> str:
    return sha256(
        json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode()
    ).hexdigest()


@dataclass(frozen=True)
class SkillDescriptor:
    name: str
    description: str
    release: str
    content_hash: str


@dataclass
class SkillActivation:
    descriptor: SkillDescriptor
    bindings: tuple[HookBinding, ...]
    resources: tuple[str, ...] = ()
    reasons: dict[str, str] = field(default_factory=dict)
    records: list[dict[str, Any]] = field(default_factory=list)
    closed: bool = False

    def close(self) -> None:
        self.bindings = ()
        self.closed = True


def _input_conditions(payload: Any) -> dict[str, bool]:
    blueprint = payload.get("blueprint", {})
    resolutions = {item["local_key"] for item in blueprint.get("resolution_specs", [])}
    counts: dict[str, int] = {}
    for hypothesis in blueprint.get("hypotheses", []):
        for key in hypothesis.get("dependency_keys", []):
            if key in resolutions:
                counts[key] = counts.get(key, 0) + 1
    return {
        "always": True,
        "has_entities": bool(blueprint.get("entities")),
        "has_competition": any(count >= 2 for count in counts.values()),
    }


async def _select_resources(event: HookInput) -> HookResult:
    conditions = _input_conditions(event.payload)
    rules = event.payload["_skill_rules"]["conditional_resources"]
    return HookResult(
        resources=tuple(rule["resource"] for rule in rules if conditions[rule["condition"]])
    )


async def _select_repairs(event: HookInput) -> HookResult:
    rules = event.payload["_skill_rules"]
    resources = list(rules["repair_resources"])
    for issue in event.payload.get("targeted_repair_issues", []):
        resource = rules["issue_resources"].get(issue.get("code"))
        if resource:
            resources.append(resource)
    return HookResult(resources=tuple(dict.fromkeys(resources)))


SKILL_HANDLERS = {
    ("select_resources", "1"): _select_resources,
    ("select_repairs", "1"): _select_repairs,
}


class SkillLoader:
    """Read immutable package metadata; keep activation state out of the loader."""

    def __init__(
        self,
        release: str = "v17",
        root: Traversable | None = None,
    ) -> None:
        if not release.startswith("v") or not release[1:].isdigit():
            raise ValueError("Unsupported Skill release")
        self.release = release
        self.root = root or files(
            f"casefile.agent_runtime.brief_to_draft_{release}"
        ).joinpath("skill")
        self.manifest_text = self.root.joinpath("manifest.json").read_text(encoding="utf-8")
        self.manifest = json.loads(self.manifest_text)
        if self.manifest["schema_version"] != 1 or self.manifest["release"] != release:
            raise ValueError("Unsupported Skill release")
        self.dispatcher = HookDispatcher(SKILL_HANDLERS)

    def discover(self) -> SkillDescriptor:
        content = read_skill_resource(self.root, "SKILL.md", self.manifest["descriptor_sha256"])
        name, description = skill_metadata(content)
        return SkillDescriptor(
            name,
            description,
            self.release,
            sha256(content.encode()).hexdigest(),
        )

    def validate(self, package: PromptPackage) -> None:
        self.discover()
        if self.manifest["prompt_version"] != package.version:
            raise ValueError("Skill prompt version mismatch")
        if set(self.manifest["components"]) != set(package.components):
            raise ValueError("Skill component set mismatch")
        if set(self.manifest["resources"]) != set(package.fragments):
            raise ValueError("Skill resource set mismatch")
        for name, expected in self.manifest["resources"].items():
            fragment = package.fragments[name]
            if (
                fragment.sha256 != expected
                or sha256(fragment.content.encode()).hexdigest() != expected
            ):
                raise ValueError(f"Skill resource hash mismatch: {name}")
        for name, config in self.manifest["components"].items():
            component = package.components[name]
            if (config["input_contract_id"], config["output_schema_id"]) != (
                component.input_contract_id,
                component.output_schema_id,
            ):
                raise ValueError(f"Skill contract mismatch: {name}")
            if any(resource not in package.fragments for resource in config["base"]):
                raise ValueError(f"Unknown base resource: {name}")
            conditional = config["conditional_resources"]
            if any(
                rule["condition"] not in {"always", "has_entities", "has_competition"}
                for rule in conditional
            ):
                raise ValueError("Unknown Skill resource condition")
            references = [
                *config["repair_resources"],
                *config["issue_resources"].values(),
                *(rule["resource"] for rule in conditional),
            ]
            if any(resource not in package.fragments for resource in references):
                raise ValueError("Unknown conditional or repair resource")
            self.dispatcher.validate(self._bindings(name))

    def _bindings(self, component: str) -> tuple[HookBinding, ...]:
        return tuple(
            HookBinding(**{**entry, "event": HookEvent(entry["event"])})
            for entry in self.manifest["components"][component]["hooks"]
        )

    @asynccontextmanager
    async def activate(
        self,
        package: PromptPackage,
        component: str,
        payload: dict[str, Any],
        *,
        agent_version: str,
        toolset_version: str,
        input_contract_id: str | None = None,
    ) -> AsyncIterator[tuple[RenderedPrompt, dict[str, Any]]]:
        self.validate(package)
        activation = SkillActivation(self.discover(), self._bindings(component))
        try:
            yield await self._render(
                package,
                component,
                payload,
                agent_version=agent_version,
                toolset_version=toolset_version,
                input_contract_id=input_contract_id,
                activation=activation,
            )
        finally:
            activation.close()

    async def render(
        self,
        package: PromptPackage,
        component: str,
        payload: dict[str, Any],
        *,
        agent_version: str,
        toolset_version: str,
        input_contract_id: str | None = None,
    ) -> tuple[RenderedPrompt, dict[str, Any]]:
        async with self.activate(
            package,
            component,
            payload,
            agent_version=agent_version,
            toolset_version=toolset_version,
            input_contract_id=input_contract_id,
        ) as rendered:
            return rendered

    async def _render(
        self,
        package: PromptPackage,
        component: str,
        payload: dict[str, Any],
        *,
        agent_version: str,
        toolset_version: str,
        input_contract_id: str | None = None,
        activation: SkillActivation,
    ) -> tuple[RenderedPrompt, dict[str, Any]]:
        selected = list(self.manifest["components"][component]["base"])
        activation.reasons.update(dict.fromkeys(selected, "stage_required"))
        events = [HookEvent.BEFORE_COMPONENT]
        if payload.get("targeted_repair_issues"):
            events.append(HookEvent.BEFORE_REPAIR)
        for event in events:
            result = await self.dispatcher.dispatch(
                activation.bindings,
                HookInput(
                    event,
                    component,
                    component,
                    {**payload, "_skill_rules": self.manifest["components"][component]},
                ),
                records=activation.records,
            )
            for resource in result.resources:
                if resource not in package.fragments:
                    raise ValueError(f"Unknown selected Skill resource: {resource}")
                selected.append(resource)
                activation.reasons[resource] = (
                    "targeted_repair_issues"
                    if event == HookEvent.BEFORE_REPAIR
                    else "input_feature"
                )
        activation.resources = tuple(dict.fromkeys(selected))
        components = dict(package.components)
        components[component] = replace(
            components[component], instruction_fragments=activation.resources
        )
        rendered = render_prompt_package(
            replace(package, components=components),
            component,
            payload,
            agent_version=agent_version,
            toolset_version=toolset_version,
            input_contract_id=input_contract_id,
        )
        fingerprint = execution_hash(
            {
                "input": rendered.input_sha256,
                "prompt": rendered.prompt_sha256,
                "input_contract": rendered.input_contract_id,
                "output_schema": rendered.output_schema_id,
                "tool_policy": rendered.tool_policy_id,
                "agent": agent_version,
                "manifest": execution_hash(self.manifest),
                "pipeline_hooks": [
                    asdict(binding)
                    for binding in resolve_pipeline_spec(package.version).hook_bindings
                ],
                "skill": asdict(activation.descriptor),
            }
        )
        metadata = {
            "skill": asdict(activation.descriptor),
            "resources": list(activation.resources),
            "reasons": activation.reasons,
            "prompt_hash": rendered.prompt_sha256,
            "policy_version": self.manifest["policy_version"],
            "hooks": activation.records,
        }
        return replace(rendered, input_sha256=fingerprint), metadata
