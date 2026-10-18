"""Explicit registry for known baseline and research-only strategy IDs."""
from dataclasses import dataclass
from typing import Iterable


@dataclass(frozen=True)
class StrategyCandidate:
    strategy_id: str
    version: str
    description: str
    status: str = "RESEARCH_ONLY"
    enabled_for_paper: bool = False

    def __post_init__(self):
        if not self.strategy_id or not self.version or not self.description.strip():
            raise ValueError("candidate ID, version, and description are required")
        if self.status not in {"FROZEN_BASELINE", "RESEARCH_ONLY", "PAPER_APPROVED"}:
            raise ValueError("unsupported candidate status")
        if self.enabled_for_paper and self.status != "PAPER_APPROVED":
            raise ValueError("only explicitly PAPER_APPROVED candidates can be paper-enabled")


class StrategyRegistry:
    def __init__(self, candidates: Iterable[StrategyCandidate] = ()):
        self._candidates: dict[str, StrategyCandidate] = {}
        for candidate in candidates:
            self.register(candidate)

    def register(self, candidate: StrategyCandidate) -> None:
        if candidate.strategy_id in self._candidates:
            raise ValueError(f"duplicate strategy ID: {candidate.strategy_id}")
        self._candidates[candidate.strategy_id] = candidate

    def require(self, strategy_id: str) -> StrategyCandidate:
        try:
            return self._candidates[strategy_id]
        except KeyError as error:
            raise ValueError("strategy ID is not registered") from error

    def available_ids(self, *, paper_only: bool = True) -> tuple[str, ...]:
        return tuple(sorted(
            key for key, candidate in self._candidates.items()
            if not paper_only or candidate.enabled_for_paper
        ))

    def snapshot(self) -> tuple[StrategyCandidate, ...]:
        return tuple(self._candidates[key] for key in sorted(self._candidates))


def default_registry() -> StrategyRegistry:
    """Register only the frozen V1 reference. It is not a V2 paper approval."""
    return StrategyRegistry((StrategyCandidate(
        "frozen_reversal_v1", "v1-frozen",
        "Reference candidate from the existing frozen V1; known negative/insufficient validation.",
        status="FROZEN_BASELINE", enabled_for_paper=False,
    ),))
