"""Strict loader for immutable, Git-backed CaseFile System Prompt versions."""

from __future__ import annotations

import json
import re
from dataclasses import dataclass
from functools import cache
from hashlib import sha256
from importlib.resources import files
from importlib.resources.abc import Traversable
from typing import Final, cast

PROMPT_RESOURCE_PACKAGE: Final = "casefile.agent_runtime.prompts"
PROMPT_REGISTRY_SCHEMA_VERSION: Final = 1
SUPPORTED_AGENT_IDS: Final = (
    "brief_polish",
    "brief_anchor_extract",
    "brief_intake_questions",
    "brief_intake_synthesize",
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
        resolved_version = (
            self.current_version(resolved_agent_id) if version is None else version
        )
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
                raise PromptRepositoryError(
                    f"Prompt Repository agent has no versions: {agent_id}"
                )

            loaded_versions: set[str] = set()
            agent_definitions: list[PromptDefinition] = []
            for version_directory in sorted(
                version_directories,
                key=lambda value: int(value[1:]),
            ):
                version_root = agent_root.joinpath(version_directory)
                version_resources = {
                    child.name
                    for child in version_root.iterdir()
                    if child.name != "__pycache__"
                }
                if version_resources != _VERSION_RESOURCE_FILES:
                    raise PromptRepositoryError(
                        f"Prompt version resources do not match the repository contract for "
                        f"{agent_id}/{version_directory}: "
                        f"expected={sorted(_VERSION_RESOURCE_FILES)!r}, "
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
                "Unsupported Prompt registry schema_version: "
                f"{schema_version!r}"
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
            raise PromptRepositoryError(
                f"Unknown Prompt version for {agent_id}: {requested}"
            )
        manifest = _read_json_object(
            version_root.joinpath("manifest.json"),
            f"Prompt manifest {agent_id}/{version_directory}",
        )
        manifest_label = f"Prompt manifest {agent_id}/{version_directory}"
        _require_exact_keys(manifest, _MANIFEST_KEYS, manifest_label)

        manifest_agent_id = _require_non_empty_string(
            manifest["agent_id"],
            f"{manifest_label} agent_id",
        )
        if manifest_agent_id != agent_id:
            raise PromptRepositoryError(
                f"{manifest_label} agent_id does not match its directory: "
                f"{manifest_agent_id!r}"
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
                raise PromptRepositoryError(
                    f"{manifest_label} cannot supersede itself"
                )
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


def _version_directory(agent_id: str, version: str) -> str:
    prefix = f"{agent_id.replace('_', '-')}-"
    if not version.startswith(prefix):
        raise PromptRepositoryError(
            f"Prompt version does not belong to {agent_id}: {version!r}"
        )
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
    "validate_prompt_repository",
]
