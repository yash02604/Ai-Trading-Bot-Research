"""Decision-to-paper orchestration with no live broker integration."""
from dataclasses import dataclass
from datetime import datetime

from .contracts import Action
from .paper import PaperOnlyExecutor
from .safety import SafetyGate
from .service import DecisionService


class DuplicateDecisionError(RuntimeError):
    pass


@dataclass(frozen=True)
class EvaluationResult:
    envelope: object
    risk_result: object
    final_decision: str
    paper_result: dict | None


class V2ResearchController:
    def __init__(self, *, service: DecisionService, safety_gate: SafetyGate,
                 audit_ledger, paper_executor: PaperOnlyExecutor | None = None):
        self.service, self.gate = service, safety_gate
        self.audit, self.paper = audit_ledger, paper_executor

    def evaluate(self, request, *, signal_id: str, now: datetime) -> EvaluationResult:
        if self.audit.seen(signal_id):
            raise DuplicateDecisionError("signal has already been recorded")
        envelope = self.service.decide(request, now=now)
        risk = self.gate.approve(request, envelope.recommendation, now=now, signal_id=signal_id)
        recorded = now.isoformat()
        final_decision = envelope.recommendation.action.value if risk.approved else "HOLD"
        if not self.audit.append_decision(signal_id, recorded, envelope, risk, final_decision):
            raise DuplicateDecisionError("signal has already been recorded")
        paper_result = None
        recommendation = envelope.recommendation
        if risk.approved and self.paper is not None:
            if recommendation.action is Action.BUY:
                paper_result = self.paper.open_position(
                    signal_id=signal_id, decision=recommendation,
                    gate_result=risk, recorded_at=now,
                )
            elif recommendation.action is Action.EXIT:
                open_position = self.gate.state.open_positions[recommendation.symbol]
                paper_result = self.paper.close_position(
                    position_signal_id=open_position["signal_id"], exit_signal_id=signal_id,
                    reference_exit=recommendation.entry_price, recorded_at=now,
                    exit_reason="AI_EXIT",
                )
        return EvaluationResult(envelope, risk, final_decision, paper_result)
