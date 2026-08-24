"""Provider-neutral proposal port for pure closure repair."""

from __future__ import annotations

from typing import Protocol

from casefile.domain.logical_mutation.repair.models import (
    ClosureRepairContextV1,
    RepairProposal,
)


class RepairProposer(Protocol):
    def propose(
        self,
        context: ClosureRepairContextV1,
        *,
        round_no: int,
    ) -> RepairProposal: ...


__all__ = ["RepairProposer"]
