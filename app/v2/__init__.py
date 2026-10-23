"""AI-led V2 research interfaces. No broker order API is exposed here."""

from .contracts import DecisionRequest, V2Recommendation, safe_hold
from .registry import StrategyCandidate, StrategyRegistry
from .safety import RiskLimits, RiskState, SafetyGate
from .ai_led_research import (
    AIHypothesisDiscoveryService, AIResearchController, AIResearchDecisionService,
    AIResearchRequest, ResearchAction, StrategyHypothesis,
)

__all__ = [
    "DecisionRequest", "V2Recommendation", "safe_hold", "StrategyCandidate",
    "StrategyRegistry", "RiskLimits", "RiskState", "SafetyGate",
    "AIResearchRequest", "ResearchAction", "StrategyHypothesis",
    "AIHypothesisDiscoveryService", "AIResearchDecisionService", "AIResearchController",
]
