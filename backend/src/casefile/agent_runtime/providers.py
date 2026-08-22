"""Stable public façade for CaseFile Agent provider adapters.

Owns compatibility exports for Provider protocols and implementations. Does
not own vendor protocols, generation orchestration, output normalization, or
test fixtures; those live in ``provider_adapters``.
"""

from casefile.agent_runtime.provider_adapters.deepseek import DeepSeekAgentsProvider
from casefile.agent_runtime.provider_adapters.fake import FakeProvider
from casefile.agent_runtime.provider_adapters.fake import (
    _add_fake_v10_matrix_plan as _add_fake_v10_matrix_plan,
)
from casefile.agent_runtime.provider_adapters.fake import (
    _fake_matrix_evaluation_output as _fake_matrix_evaluation_output,
)
from casefile.agent_runtime.provider_adapters.fake import (
    _fake_v8_output as _fake_v8_output,
)
from casefile.agent_runtime.provider_adapters.generation import (
    _allocate_plan_ids as _allocate_plan_ids,
)
from casefile.agent_runtime.provider_adapters.generation import (
    _partition_issues as _partition_issues,
)
from casefile.agent_runtime.provider_adapters.generation import (
    _prune_invalid_reference_list_items as _prune_invalid_reference_list_items,
)
from casefile.agent_runtime.provider_adapters.generation import (
    _retain_planned_objects as _retain_planned_objects,
)
from casefile.agent_runtime.provider_adapters.generation import (
    _validate_partitioned_candidate as _validate_partitioned_candidate,
)
from casefile.agent_runtime.provider_adapters.openai import OpenAIAgentsProvider
from casefile.agent_runtime.provider_adapters.protocols import (
    AgentProvider,
    GenerationProvider,
    ProviderProtocolError,
)
from casefile.agent_runtime.provider_adapters.shared import (
    _chat_tool_runtime as _chat_tool_runtime,
)
from casefile.agent_runtime.provider_adapters.shared import (
    _deepseek_json_object_text as _deepseek_json_object_text,
)
from casefile.agent_runtime.provider_adapters.shared import (
    _deepseek_v8_output_protocol as _deepseek_v8_output_protocol,
)
from casefile.agent_runtime.provider_adapters.shared import (
    _json_schema_instruction as _json_schema_instruction,
)
from casefile.agent_runtime.provider_adapters.shared import (
    _remove_absent_optional_fields as _remove_absent_optional_fields,
)
from casefile.agent_runtime.provider_adapters.shared import (
    _run_auxiliary_agent as _run_auxiliary_agent,
)
from casefile.agent_runtime.provider_adapters.shared import (
    _validate_generated_descriptions as _validate_generated_descriptions,
)

__all__ = [
    "AgentProvider",
    "DeepSeekAgentsProvider",
    "FakeProvider",
    "GenerationProvider",
    "OpenAIAgentsProvider",
    "ProviderProtocolError",
]
