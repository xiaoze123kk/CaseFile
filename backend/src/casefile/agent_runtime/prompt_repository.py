"""Strict loader for immutable, Git-backed CaseFile System Prompt versions."""

from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from functools import cache
from hashlib import sha256
from importlib.resources import files
from importlib.resources.abc import Traversable
from types import MappingProxyType
from typing import Final, cast

from casefile.agent_runtime.prompt_package import (
    PromptComponent,
    PromptFragment,
    PromptPackage,
    PromptPackageError,
    validate_prompt_package_bindings,
)

PROMPT_RESOURCE_PACKAGE: Final = "casefile.agent_runtime.prompts"
PROMPT_REGISTRY_SCHEMA_VERSION: Final = 1
SUPPORTED_AGENT_IDS: Final = (
    "brief_polish",
    "brief_anchor_extract",
    "brief_intake_questions",
    "brief_intake_synthesize",
    "brief_strategy_options",
    "brief_to_draft",
    "casefile_chat",
)

_VERSION_DIRECTORY = re.compile(r"^v[1-9][0-9]*$")
_SHA256 = re.compile(r"^[0-9a-f]{64}$")
_REGISTRY_KEYS = frozenset({"schema_version", "agents"})
_REGISTRY_AGENT_KEYS = frozenset({"current_version"})
_ROOT_RESOURCE_FILES = frozenset({"README.md", "__init__.py", "registry.json"})
_VERSION_RESOURCE_FILES = frozenset({"manifest.json", "system.md"})
_MANIFEST_KEYS = frozenset(
    {
        "agent_id",
        "version",
        "system_prompt_file",
        "system_prompt_sha256",
        "previous_version",
        "change_summary",
    }
)
_BUNDLE_MANIFEST_KEYS = frozenset(
    {"agent_id", "version", "previous_version", "change_summary", "components"}
)
_BUNDLE_COMPONENT_KEYS = frozenset({"planner", "story", "evidence", "governance"})
_BUNDLE_ENTRY_KEYS = frozenset({"file", "sha256"})
_PACKAGE_MANIFEST_KEYS = frozenset(
    {
        "schema_version",
        "agent_id",
        "version",
        "previous_version",
        "change_summary",
        "runtime",
        "fragments",
        "components",
    }
)
_PACKAGE_RUNTIME_KEYS = frozenset({"agent_version", "toolset_version"})
_PACKAGE_FRAGMENT_KEYS = frozenset({"file", "sha256"})
_PACKAGE_COMPONENT_KEYS = frozenset(
    {
        "instruction_fragments",
        "input_contract_id",
        "output_schema_id",
        "tool_policy_id",
    }
)
_PACKAGE_COMPONENT_IDS_BY_VERSION = {
    "brief-to-draft-v12": frozenset({"planner", "temporal", "story", "evidence", "governance"}),
}
_PACKAGE_SCHEMA_VERSION = 2
_IDENTIFIER = re.compile(r"^[a-z][a-z0-9_-]{0,79}$")
_PACKAGE_FILE = re.compile(r"^[a-z0-9][a-z0-9_/-]*\.(?:md|json)$")


class PromptRepositoryError(RuntimeError):
    """The packaged Prompt Repository violates its immutable resource contract."""


@dataclass(frozen=True, slots=True)
class PromptDefinition:
    """One validated, immutable Agent System Prompt version."""

    agent_id: str
    version: str
    system_prompt: str
    system_prompt_sha256: str
    previous_version: str | None
    change_summary: str
    component_prompts: dict[str, str] = field(default_factory=dict)
    component_sha256: dict[str, str] = field(default_factory=dict)
    package: PromptPackage | None = None


class PromptRepository:
    """Read and validate one Prompt Repository resource tree."""

    def __init__(
        self,
        root: Traversable,
        *,
        expected_agent_ids: tuple[str, ...] = SUPPORTED_AGENT_IDS,
    ) -> None:
        self._root = root
        self._expected_agent_ids = expected_agent_ids

    @property
    def expected_agent_ids(self) -> tuple[str, ...]:
        return self._expected_agent_ids

    def current_version(self, agent_id: str) -> str:
        """Return the explicit version selected for new tasks of one Agent."""

        return self._registry_versions()[self._require_agent(agent_id)]

    def load(self, agent_id: str, version: str | None = None) -> PromptDefinition:
        """Load one current or explicitly selected immutable System Prompt."""

        resolved_agent_id = self._require_agent(agent_id)
        resolved_version = self.current_version(resolved_agent_id) if version is None else version
        version_directory = _version_directory(resolved_agent_id, resolved_version)
        return self._load_version_directory(
            resolved_agent_id,
            version_directory,
            expected_version=resolved_version,
        )

    def validate(self) -> tuple[PromptDefinition, ...]:
        """Validate the complete repository, including inactive historical versions."""

        registry_versions = self._registry_versions()
        expected_agents = set(self._expected_agent_ids)
        actual_agents: set[str] = set()
        for child in self._root.iterdir():
            if child.name == "__pycache__":
                continue
            if child.is_dir():
                actual_agents.add(child.name)
                continue
            if not child.is_file() or child.name not in _ROOT_RESOURCE_FILES:
                raise PromptRepositoryError(
                    f"Unexpected resource in Prompt Repository root: {child.name}"
                )
        if actual_agents != expected_agents:
            raise PromptRepositoryError(
                "Prompt Repository agent directories do not match the supported Agent set: "
                f"expected={sorted(expected_agents)!r}, actual={sorted(actual_agents)!r}"
            )

        definitions: list[PromptDefinition] = []
        for agent_id in self._expected_agent_ids:
            agent_root = self._root.joinpath(agent_id)
            version_directories: list[str] = []
            for child in agent_root.iterdir():
                if child.name == "__pycache__":
                    continue
                if not child.is_dir() or _VERSION_DIRECTORY.fullmatch(child.name) is None:
                    raise PromptRepositoryError(
                        f"Unexpected resource in Prompt Repository agent directory: "
                        f"{agent_id}/{child.name}"
                    )
                version_directories.append(child.name)
            if not version_directories:
                raise PromptRepositoryError(f"Prompt Repository agent has no versions: {agent_id}")

            loaded_versions: set[str] = set()
            agent_definitions: list[PromptDefinition] = []
            for version_directory in sorted(
                version_directories,
                key=lambda value: int(value[1:]),
            ):
                version_root = agent_root.joinpath(version_directory)
                version_resources = _resource_files_relative(version_root)
                manifest = _read_json_object(
                    version_root.joinpath("manifest.json"),
                    f"Prompt manifest {agent_id}/{version_directory}",
                )
                if manifest.get("schema_version") == _PACKAGE_SCHEMA_VERSION:
                    fragments = _require_object(
                        manifest.get("fragments"), "Prompt Package fragments"
                    )
                    expected_resources = frozenset(
                        {"manifest.json"}
                        | {
                            _require_non_empty_string(
                                _require_object(value, "Prompt Package fragment")["file"],
                                "Prompt Package fragment file",
                            )
                            for value in fragments.values()
                        }
                    )
                else:
                    expected_resources = (
                        _VERSION_RESOURCE_FILES
                        if "components" not in manifest
                        else frozenset(
                            {"manifest.json"}
                            | {
                                _require_non_empty_string(value["file"], "bundle file")
                                for value in _bundle_entries(manifest).values()
                            }
                        )
                    )
                if version_resources != expected_resources:
                    raise PromptRepositoryError(
                        f"Prompt version resources do not match the repository contract for "
                        f"{agent_id}/{version_directory}: "
                        f"expected={sorted(expected_resources)!r}, "
                        f"actual={sorted(version_resources)!r}"
                    )
                definition = self._load_version_directory(agent_id, version_directory)
                if definition.version in loaded_versions:
                    raise PromptRepositoryError(
                        f"Duplicate Prompt version for {agent_id}: {definition.version}"
                    )
                loaded_versions.add(definition.version)
                agent_definitions.append(definition)
                definitions.append(definition)
            if agent_definitions[0].previous_version is not None:
                raise PromptRepositoryError(
                    f"Prompt baseline for {agent_id} must use a null previous_version"
                )
            for previous, current in zip(
                agent_definitions,
                agent_definitions[1:],
                strict=False,
            ):
                if current.previous_version != previous.version:
                    raise PromptRepositoryError(
                        f"Prompt version chain is invalid for {agent_id}: "
                        f"{current.version} must supersede {previous.version}"
                    )
            if registry_versions[agent_id] not in loaded_versions:
                raise PromptRepositoryError(
                    f"Current Prompt version is not present for {agent_id}: "
                    f"{registry_versions[agent_id]}"
                )

        return tuple(definitions)

    def _require_agent(self, agent_id: str) -> str:
        if agent_id not in self._expected_agent_ids:
            raise PromptRepositoryError(f"Unsupported Agent Prompt: {agent_id}")
        return agent_id

    def _registry_versions(self) -> dict[str, str]:
        registry = _read_json_object(
            self._root.joinpath("registry.json"),
            "Prompt registry",
        )
        _require_exact_keys(registry, _REGISTRY_KEYS, "Prompt registry")
        schema_version = registry["schema_version"]
        if type(schema_version) is not int or schema_version != PROMPT_REGISTRY_SCHEMA_VERSION:
            raise PromptRepositoryError(
                f"Unsupported Prompt registry schema_version: {schema_version!r}"
            )
        agents = _require_object(registry["agents"], "Prompt registry agents")
        expected_agents = set(self._expected_agent_ids)
        actual_agents = set(agents)
        if actual_agents != expected_agents:
            raise PromptRepositoryError(
                "Prompt registry does not map every supported Agent exactly once: "
                f"expected={sorted(expected_agents)!r}, actual={sorted(actual_agents)!r}"
            )

        versions: dict[str, str] = {}
        for agent_id in self._expected_agent_ids:
            entry = _require_object(
                agents[agent_id],
                f"Prompt registry entry for {agent_id}",
            )
            _require_exact_keys(
                entry,
                _REGISTRY_AGENT_KEYS,
                f"Prompt registry entry for {agent_id}",
            )
            version = _require_non_empty_string(
                entry["current_version"],
                f"Prompt registry current_version for {agent_id}",
            )
            _version_directory(agent_id, version)
            versions[agent_id] = version
        return versions

    def _load_version_directory(
        self,
        agent_id: str,
        version_directory: str,
        *,
        expected_version: str | None = None,
    ) -> PromptDefinition:
        version_root = self._root.joinpath(agent_id, version_directory)
        if not version_root.is_dir():
            requested = expected_version or _full_version(agent_id, version_directory)
            raise PromptRepositoryError(f"Unknown Prompt version for {agent_id}: {requested}")
        manifest = _read_json_object(
            version_root.joinpath("manifest.json"),
            f"Prompt manifest {agent_id}/{version_directory}",
        )
        manifest_label = f"Prompt manifest {agent_id}/{version_directory}"
        if manifest.get("schema_version") == _PACKAGE_SCHEMA_VERSION:
            return self._load_package_manifest(
                agent_id,
                version_directory,
                manifest,
                manifest_label,
                expected_version=expected_version,
            )
        if "components" in manifest:
            return self._load_bundle_manifest(
                agent_id,
                version_directory,
                manifest,
                manifest_label,
                expected_version=expected_version,
            )
        _require_exact_keys(manifest, _MANIFEST_KEYS, manifest_label)

        manifest_agent_id = _require_non_empty_string(
            manifest["agent_id"],
            f"{manifest_label} agent_id",
        )
        if manifest_agent_id != agent_id:
            raise PromptRepositoryError(
                f"{manifest_label} agent_id does not match its directory: {manifest_agent_id!r}"
            )
        version = _require_non_empty_string(
            manifest["version"],
            f"{manifest_label} version",
        )
        directory_from_version = _version_directory(agent_id, version)
        if directory_from_version != version_directory:
            raise PromptRepositoryError(
                f"{manifest_label} version does not match its directory: {version!r}"
            )
        if expected_version is not None and version != expected_version:
            raise PromptRepositoryError(
                f"{manifest_label} version does not match the requested version: "
                f"expected={expected_version!r}, actual={version!r}"
            )

        system_prompt_file = _require_non_empty_string(
            manifest["system_prompt_file"],
            f"{manifest_label} system_prompt_file",
        )
        if system_prompt_file != "system.md":
            raise PromptRepositoryError(
                f"{manifest_label} must use system.md as system_prompt_file"
            )
        expected_hash = _require_non_empty_string(
            manifest["system_prompt_sha256"],
            f"{manifest_label} system_prompt_sha256",
        )
        if _SHA256.fullmatch(expected_hash) is None:
            raise PromptRepositoryError(
                f"{manifest_label} has an invalid SHA-256: {expected_hash!r}"
            )

        previous_value = manifest["previous_version"]
        previous_version: str | None
        if previous_value is None:
            previous_version = None
        else:
            previous_version = _require_non_empty_string(
                previous_value,
                f"{manifest_label} previous_version",
            )
            _version_directory(agent_id, previous_version)
            if previous_version == version:
                raise PromptRepositoryError(f"{manifest_label} cannot supersede itself")
        change_summary = _require_non_empty_string(
            manifest["change_summary"],
            f"{manifest_label} change_summary",
        )

        prompt_resource = version_root.joinpath(system_prompt_file)
        prompt_bytes = _read_bytes(
            prompt_resource,
            f"System Prompt {agent_id}/{version_directory}",
        )
        if b"\r" in prompt_bytes:
            raise PromptRepositoryError(
                f"System Prompt {agent_id}/{version_directory} must use LF line endings"
            )
        try:
            system_prompt = prompt_bytes.decode("utf-8")
        except UnicodeDecodeError as error:
            raise PromptRepositoryError(
                f"System Prompt {agent_id}/{version_directory} must be UTF-8"
            ) from error
        if not system_prompt.strip():
            raise PromptRepositoryError(
                f"System Prompt {agent_id}/{version_directory} must not be empty"
            )
        actual_hash = sha256(prompt_bytes).hexdigest()
        if actual_hash != expected_hash:
            raise PromptRepositoryError(
                f"System Prompt hash mismatch for {agent_id}/{version_directory}: "
                f"expected={expected_hash}, actual={actual_hash}"
            )

        return PromptDefinition(
            agent_id=agent_id,
            version=version,
            system_prompt=system_prompt,
            system_prompt_sha256=actual_hash,
            previous_version=previous_version,
            change_summary=change_summary,
        )

    def _load_bundle_manifest(
        self,
        agent_id: str,
        version_directory: str,
        manifest: dict[str, object],
        manifest_label: str,
        *,
        expected_version: str | None,
    ) -> PromptDefinition:
        _require_exact_keys(manifest, _BUNDLE_MANIFEST_KEYS, manifest_label)
        manifest_agent_id = _require_non_empty_string(
            manifest["agent_id"], f"{manifest_label} agent_id"
        )
        if manifest_agent_id != agent_id:
            raise PromptRepositoryError(f"{manifest_label} agent_id does not match its directory")
        version = _require_non_empty_string(manifest["version"], f"{manifest_label} version")
        if _version_directory(agent_id, version) != version_directory:
            raise PromptRepositoryError(f"{manifest_label} version does not match its directory")
        if expected_version is not None and version != expected_version:
            raise PromptRepositoryError(
                f"{manifest_label} version does not match the requested version"
            )
        previous_raw = manifest["previous_version"]
        previous_version = (
            None
            if previous_raw is None
            else _require_non_empty_string(previous_raw, f"{manifest_label} previous_version")
        )
        if previous_version is not None:
            _version_directory(agent_id, previous_version)
        change_summary = _require_non_empty_string(
            manifest["change_summary"], f"{manifest_label} change_summary"
        )
        entries = _bundle_entries(manifest)
        prompts: dict[str, str] = {}
        hashes: dict[str, str] = {}
        version_root = self._root.joinpath(agent_id, version_directory)
        for component_id, entry in entries.items():
            file_name = _require_non_empty_string(
                entry["file"], f"{manifest_label} {component_id} file"
            )
            if file_name != f"{component_id}.md":
                raise PromptRepositoryError(
                    f"{manifest_label} component {component_id} must use {component_id}.md"
                )
            expected_hash = _require_non_empty_string(
                entry["sha256"], f"{manifest_label} {component_id} sha256"
            )
            if _SHA256.fullmatch(expected_hash) is None:
                raise PromptRepositoryError(
                    f"{manifest_label} component {component_id} has invalid SHA-256"
                )
            raw = _read_bytes(
                version_root.joinpath(file_name),
                f"Component Prompt {agent_id}/{version_directory}/{component_id}",
            )
            if b"\r" in raw:
                raise PromptRepositoryError(
                    f"Component Prompt {component_id} must use LF line endings"
                )
            try:
                prompt = raw.decode("utf-8")
            except UnicodeDecodeError as error:
                raise PromptRepositoryError(
                    f"Component Prompt {component_id} must be UTF-8"
                ) from error
            actual_hash = sha256(raw).hexdigest()
            if actual_hash != expected_hash:
                raise PromptRepositoryError(
                    f"Component Prompt hash mismatch for {component_id}: "
                    f"expected={expected_hash}, actual={actual_hash}"
                )
            if not prompt.strip():
                raise PromptRepositoryError(f"Component Prompt {component_id} must not be empty")
            prompts[component_id] = prompt
            hashes[component_id] = actual_hash
        return PromptDefinition(
            agent_id=agent_id,
            version=version,
            system_prompt=prompts["planner"],
            system_prompt_sha256=hashes["planner"],
            previous_version=previous_version,
            change_summary=change_summary,
            component_prompts=prompts,
            component_sha256=hashes,
        )

    def _load_package_manifest(
        self,
        agent_id: str,
        version_directory: str,
        manifest: dict[str, object],
        manifest_label: str,
        *,
        expected_version: str | None,
    ) -> PromptDefinition:
        _require_exact_keys(manifest, _PACKAGE_MANIFEST_KEYS, manifest_label)
        if manifest["schema_version"] != _PACKAGE_SCHEMA_VERSION:
            raise PromptRepositoryError(f"{manifest_label} has unsupported schema_version")
        manifest_agent_id = _require_non_empty_string(
            manifest["agent_id"], f"{manifest_label} agent_id"
        )
        if manifest_agent_id != agent_id:
            raise PromptRepositoryError(f"{manifest_label} agent_id does not match its directory")
        version = _require_non_empty_string(manifest["version"], f"{manifest_label} version")
        if _version_directory(agent_id, version) != version_directory:
            raise PromptRepositoryError(f"{manifest_label} version does not match its directory")
        if expected_version is not None and version != expected_version:
            raise PromptRepositoryError(
                f"{manifest_label} version does not match the requested version"
            )
        previous_version = _optional_previous_version(
            agent_id, version, manifest["previous_version"], manifest_label
        )
        change_summary = _require_non_empty_string(
            manifest["change_summary"], f"{manifest_label} change_summary"
        )

        runtime = _require_object(manifest["runtime"], f"{manifest_label} runtime")
        _require_exact_keys(runtime, _PACKAGE_RUNTIME_KEYS, f"{manifest_label} runtime")
        runtime_agent_version = _require_non_empty_string(
            runtime["agent_version"], f"{manifest_label} runtime agent_version"
        )
        runtime_toolset_version = _require_non_empty_string(
            runtime["toolset_version"], f"{manifest_label} runtime toolset_version"
        )

        version_root = self._root.joinpath(agent_id, version_directory)
        raw_fragments = _require_object(manifest["fragments"], f"{manifest_label} fragments")
        if not raw_fragments:
            raise PromptRepositoryError(f"{manifest_label} fragments must not be empty")
        fragments: dict[str, PromptFragment] = {}
        fragment_files: set[str] = set()
        for fragment_id, raw_entry in raw_fragments.items():
            _require_identifier(fragment_id, f"{manifest_label} fragment id")
            entry = _require_object(raw_entry, f"{manifest_label} fragment {fragment_id}")
            _require_exact_keys(
                entry, _PACKAGE_FRAGMENT_KEYS, f"{manifest_label} fragment {fragment_id}"
            )
            file_name = _require_package_file(
                entry["file"], f"{manifest_label} fragment {fragment_id} file"
            )
            if file_name in fragment_files:
                raise PromptRepositoryError(
                    f"{manifest_label} fragment files must be unique: {file_name}"
                )
            fragment_files.add(file_name)
            expected_hash = _require_sha256(
                entry["sha256"], f"{manifest_label} fragment {fragment_id} sha256"
            )
            raw = _read_bytes(
                version_root.joinpath(*file_name.split("/")),
                f"Prompt Package fragment {agent_id}/{version_directory}/{file_name}",
            )
            content, actual_hash = _decode_prompt_asset(
                raw, f"Prompt Package fragment {fragment_id}"
            )
            if actual_hash != expected_hash:
                raise PromptRepositoryError(
                    f"Prompt Package fragment hash mismatch for {fragment_id}: "
                    f"expected={expected_hash}, actual={actual_hash}"
                )
            fragments[fragment_id] = PromptFragment(
                fragment_id=fragment_id,
                file=file_name,
                content=content,
                sha256=actual_hash,
            )

        raw_components = _require_object(manifest["components"], f"{manifest_label} components")
        if not raw_components:
            raise PromptRepositoryError(f"{manifest_label} components must not be empty")
        expected_component_ids = _PACKAGE_COMPONENT_IDS_BY_VERSION.get(version)
        if expected_component_ids is not None and set(raw_components) != expected_component_ids:
            raise PromptRepositoryError(
                f"{manifest_label} components must define exactly "
                f"{sorted(expected_component_ids)!r}"
            )
        components: dict[str, PromptComponent] = {}
        referenced_fragments: set[str] = set()
        for component_id, raw_entry in raw_components.items():
            _require_identifier(component_id, f"{manifest_label} component id")
            entry = _require_object(raw_entry, f"{manifest_label} component {component_id}")
            _require_exact_keys(
                entry, _PACKAGE_COMPONENT_KEYS, f"{manifest_label} component {component_id}"
            )
            instruction_fragments = _require_string_list(
                entry["instruction_fragments"],
                f"{manifest_label} component {component_id} instruction_fragments",
            )
            unknown = set(instruction_fragments) - set(fragments)
            if unknown:
                raise PromptRepositoryError(
                    f"{manifest_label} component {component_id} references unknown fragments: "
                    f"{sorted(unknown)!r}"
                )
            referenced_fragments.update(instruction_fragments)
            components[component_id] = PromptComponent(
                component_id=component_id,
                instruction_fragments=instruction_fragments,
                input_contract_id=_require_non_empty_string(
                    entry["input_contract_id"],
                    f"{manifest_label} component {component_id} input_contract_id",
                ),
                output_schema_id=_require_non_empty_string(
                    entry["output_schema_id"],
                    f"{manifest_label} component {component_id} output_schema_id",
                ),
                tool_policy_id=_require_non_empty_string(
                    entry["tool_policy_id"],
                    f"{manifest_label} component {component_id} tool_policy_id",
                ),
            )
        unused_fragments = set(fragments) - referenced_fragments
        if unused_fragments:
            raise PromptRepositoryError(
                f"{manifest_label} contains unused fragments: {sorted(unused_fragments)!r}"
            )

        package = PromptPackage(
            agent_id=agent_id,
            version=version,
            previous_version=previous_version,
            change_summary=change_summary,
            runtime_agent_version=runtime_agent_version,
            runtime_toolset_version=runtime_toolset_version,
            fragments=MappingProxyType(fragments),
            components=MappingProxyType(components),
        )
        try:
            validate_prompt_package_bindings(package)
        except PromptPackageError as error:
            raise PromptRepositoryError(str(error)) from error

        component_prompts = {
            component_id: "\n\n".join(
                fragments[fragment_id].content.rstrip("\n")
                for fragment_id in component.instruction_fragments
            )
            + "\n"
            for component_id, component in components.items()
        }
        component_hashes = {
            component_id: sha256(prompt.encode("utf-8")).hexdigest()
            for component_id, prompt in component_prompts.items()
        }
        first_component_id = next(iter(components))
        return PromptDefinition(
            agent_id=agent_id,
            version=version,
            system_prompt=component_prompts[first_component_id],
            system_prompt_sha256=component_hashes[first_component_id],
            previous_version=previous_version,
            change_summary=change_summary,
            component_prompts=component_prompts,
            component_sha256=component_hashes,
            package=package,
        )


def _version_directory(agent_id: str, version: str) -> str:
    prefix = f"{agent_id.replace('_', '-')}-"
    if not version.startswith(prefix):
        raise PromptRepositoryError(f"Prompt version does not belong to {agent_id}: {version!r}")
    version_directory = version[len(prefix) :]
    if _VERSION_DIRECTORY.fullmatch(version_directory) is None:
        raise PromptRepositoryError(f"Invalid Prompt version: {version!r}")
    return version_directory


def _full_version(agent_id: str, version_directory: str) -> str:
    return f"{agent_id.replace('_', '-')}-{version_directory}"


def _read_json_object(resource: Traversable, label: str) -> dict[str, object]:
    raw = _read_bytes(resource, label)
    try:
        text = raw.decode("utf-8")
    except UnicodeDecodeError as error:
        raise PromptRepositoryError(f"{label} must be UTF-8") from error
    try:
        value: object = json.loads(text)
    except json.JSONDecodeError as error:
        raise PromptRepositoryError(f"{label} is not valid JSON") from error
    return _require_object(value, label)


def _read_bytes(resource: Traversable, label: str) -> bytes:
    if not resource.is_file():
        raise PromptRepositoryError(f"{label} is missing")
    try:
        return resource.read_bytes()
    except OSError as error:
        raise PromptRepositoryError(f"{label} could not be read") from error


def _resource_files_relative(root: Traversable) -> set[str]:
    resources: set[str] = set()

    def visit(directory: Traversable, prefix: str) -> None:
        for child in directory.iterdir():
            if child.name == "__pycache__":
                continue
            relative = f"{prefix}/{child.name}" if prefix else child.name
            if child.is_dir():
                visit(child, relative)
            elif child.is_file():
                resources.add(relative)
            else:
                raise PromptRepositoryError(f"Unexpected Prompt Repository resource: {relative}")

    visit(root, "")
    return resources


def _bundle_entries(manifest: dict[str, object]) -> dict[str, dict[str, object]]:
    components = _require_object(manifest.get("components"), "Prompt bundle components")
    if set(components) != _BUNDLE_COMPONENT_KEYS:
        raise PromptRepositoryError(
            "Prompt bundle must define planner, story, evidence, and governance exactly"
        )
    entries: dict[str, dict[str, object]] = {}
    for component_id, raw_entry in components.items():
        entry = _require_object(raw_entry, f"Prompt bundle component {component_id}")
        _require_exact_keys(entry, _BUNDLE_ENTRY_KEYS, f"Prompt bundle component {component_id}")
        entries[component_id] = entry
    return entries


def _require_object(value: object, label: str) -> dict[str, object]:
    if not isinstance(value, dict) or not all(isinstance(key, str) for key in value):
        raise PromptRepositoryError(f"{label} must be a JSON object with string keys")
    return cast(dict[str, object], value)


def _require_exact_keys(
    value: dict[str, object],
    expected_keys: frozenset[str],
    label: str,
) -> None:
    actual_keys = set(value)
    if actual_keys != expected_keys:
        raise PromptRepositoryError(
            f"{label} fields do not match the repository contract: "
            f"expected={sorted(expected_keys)!r}, actual={sorted(actual_keys)!r}"
        )


def _require_non_empty_string(value: object, label: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise PromptRepositoryError(f"{label} must be a non-empty string")
    return value


def _require_identifier(value: object, label: str) -> str:
    identifier = _require_non_empty_string(value, label)
    if _IDENTIFIER.fullmatch(identifier) is None:
        raise PromptRepositoryError(f"{label} is invalid: {identifier!r}")
    return identifier


def _require_package_file(value: object, label: str) -> str:
    file_name = _require_non_empty_string(value, label)
    if (
        _PACKAGE_FILE.fullmatch(file_name) is None
        or "//" in file_name
        or any(part in {".", ".."} for part in file_name.split("/"))
    ):
        raise PromptRepositoryError(f"{label} is not a safe package-relative path")
    return file_name


def _require_sha256(value: object, label: str) -> str:
    digest = _require_non_empty_string(value, label)
    if _SHA256.fullmatch(digest) is None:
        raise PromptRepositoryError(f"{label} is not a valid SHA-256")
    return digest


def _require_string_list(value: object, label: str) -> tuple[str, ...]:
    if not isinstance(value, list) or not value:
        raise PromptRepositoryError(f"{label} must be a non-empty JSON array")
    result = tuple(_require_identifier(item, label) for item in value)
    if len(result) != len(set(result)):
        raise PromptRepositoryError(f"{label} must not contain duplicates")
    return result


def _optional_previous_version(
    agent_id: str,
    version: str,
    value: object,
    manifest_label: str,
) -> str | None:
    if value is None:
        return None
    previous_version = _require_non_empty_string(value, f"{manifest_label} previous_version")
    _version_directory(agent_id, previous_version)
    if previous_version == version:
        raise PromptRepositoryError(f"{manifest_label} cannot supersede itself")
    return previous_version


def _decode_prompt_asset(raw: bytes, label: str) -> tuple[str, str]:
    if b"\r" in raw:
        raise PromptRepositoryError(f"{label} must use LF line endings")
    try:
        content = raw.decode("utf-8")
    except UnicodeDecodeError as error:
        raise PromptRepositoryError(f"{label} must be UTF-8") from error
    if not content.strip():
        raise PromptRepositoryError(f"{label} must not be empty")
    return content, sha256(raw).hexdigest()


@cache
def packaged_prompt_repository() -> PromptRepository:
    """Return the process-wide repository backed by packaged application resources."""

    return PromptRepository(files(PROMPT_RESOURCE_PACKAGE))


@cache
def load_prompt(agent_id: str, version: str | None = None) -> PromptDefinition:
    """Load and cache one packaged System Prompt version."""

    return packaged_prompt_repository().load(agent_id, version)


def prompt_version_for_task(task_type: str) -> str:
    """Resolve the current System Prompt version selected for a new TaskRun."""

    version = packaged_prompt_repository().current_version(task_type)
    return load_prompt(task_type, version).version


def system_prompt_for_task(task_type: str, version: str) -> str:
    """Load the exact System Prompt version frozen on an existing TaskRun."""

    return load_prompt(task_type, version).system_prompt


def component_prompt_for_task(task_type: str, version: str, component_id: str) -> str:
    """Load one immutable component prompt from an atomic Prompt Bundle."""

    definition = load_prompt(task_type, version)
    try:
        return definition.component_prompts[component_id]
    except KeyError as error:
        raise PromptRepositoryError(
            f"Prompt version {version} has no component prompt {component_id!r}"
        ) from error


def validate_prompt_repository() -> tuple[PromptDefinition, ...]:
    """Validate every packaged Agent and version in the Prompt Repository."""

    return packaged_prompt_repository().validate()


__all__ = [
    "PROMPT_REGISTRY_SCHEMA_VERSION",
    "PromptDefinition",
    "PromptRepository",
    "PromptRepositoryError",
    "SUPPORTED_AGENT_IDS",
    "load_prompt",
    "packaged_prompt_repository",
    "prompt_version_for_task",
    "system_prompt_for_task",
    "component_prompt_for_task",
    "validate_prompt_repository",
]
