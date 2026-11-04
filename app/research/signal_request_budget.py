"""Fixed purpose slots and durable physical-attempt accounting for signal research.

The experiment lock is held by every caller. A slot is held from initialization,
bound once to an existing request ID, and never released or replaced.
"""
import os

from app.research import ai_led_guarded as guard, ai_led_offline as core
from app.research.deepseek_prospective import AppendOnlyJsonl, PersistentRequestBudget


def slot_policy(priority, include_quotes):
    purposes = ["INITIAL_SNAPSHOT", "FUTURE_OBSERVATION"]
    if include_quotes:
        purposes.append("INITIAL_QUOTE")
    return {"version": "signal-purpose-slots-v1", "physical_attempts_per_slot": 1,
            "slots": [{"purpose": p, "symbol": s} for s in priority for p in purposes]}


def initialize(binding, config):
    policy = slot_policy(binding.options["priority"], binding.policy["include_quotes"])
    if config["request_slot_policy"] != policy or len(policy["slots"]) > config["market_data_cap"]:
        raise guard.GuardedBatchError("signal_budget_must_hold_initial_and_future_slots")
    path = binding.output / "signal_request_slots.json"
    expected = {"experiment_sha256": core._digest(config), **policy}
    if path.exists():
        if guard._read(path) != expected:
            raise guard.GuardedBatchError("signal_request_slots_changed")
    else:
        # Only complete an interrupted initialization, never retrofit spent evidence.
        if any((binding.output / "purpose_reservations").glob("*.json")) or any(
                (binding.output / name).exists() for name in (
                    "market_data_request_budget.jsonl", "physical_request_attempts.jsonl", "transport_sent.jsonl")):
            raise guard.GuardedBatchError("signal_request_slots_missing")
        guard._write(path, expected)
    return expected


def _reservation(binding, request_id, symbol, purpose):
    saved = guard._read(binding.output / "signal_request_slots.json")
    slot = {"purpose": purpose, "symbol": symbol}
    if slot not in saved["slots"]:
        raise guard.GuardedBatchError("signal_request_purpose_not_reserved")
    slot_id = core._digest({"experiment_sha256": saved["experiment_sha256"], **slot})
    return binding.output / "purpose_reservations" / (slot_id + ".json"), {
        "slot_id": slot_id, "request_id": request_id, **slot}


def reserve(binding, request_id, symbol, purpose):
    path, record = _reservation(binding, request_id, symbol, purpose)
    guard._write(path, record)  # An alternate request ID cannot take this purpose slot.
    return PersistentRequestBudget(binding.output / "market_data_request_budget.jsonl",
                                   binding.options["market_data_cap"]).reserve(request_id)


def record_physical_attempt(binding, request_id, symbol, purpose):
    path, expected = _reservation(binding, request_id, symbol, purpose)
    if guard._read(path) != expected:
        raise guard.GuardedBatchError("signal_attempt_purpose_mismatch")
    reservations = AppendOnlyJsonl(binding.output / "market_data_request_budget.jsonl", "request_id")
    sent = AppendOnlyJsonl(binding.output / "transport_sent.jsonl", "request_id")
    if request_id not in reservations._records or request_id not in sent._records:
        raise guard.GuardedBatchError("signal_attempt_requires_reserved_send")
    ledger = AppendOnlyJsonl(binding.output / "physical_request_attempts.jsonl", "request_id")
    if request_id in ledger._records or len(ledger._records) >= binding.options["market_data_cap"]:
        raise guard.GuardedBatchError("signal_physical_attempt_already_spent_or_exhausted")
    ledger.append({**expected, "attempt": 1})
    with ledger.path.open("ab") as stream:
        stream.flush()
        os.fsync(stream.fileno())


def verify_physical_attempt(binding, request_id, symbol, purpose):
    path, expected = _reservation(binding, request_id, symbol, purpose)
    ledger = AppendOnlyJsonl(binding.output / "physical_request_attempts.jsonl", "request_id")
    if (guard._read(path) != expected or len(ledger._records) > binding.options["market_data_cap"]
            or [r for r in ledger.records() if r["request_id"] == request_id] != [{**expected, "attempt": 1}]):
        raise guard.GuardedBatchError("signal_physical_attempt_evidence_invalid")
