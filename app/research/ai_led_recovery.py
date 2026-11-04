"""Local evidence recovery and settlement transactions; no provider or HTTP client."""
import copy
from pathlib import Path
import re

import pandas as pd

from app.research import ai_led_guarded as guard, ai_led_offline as core
from app.research.prospective_preflight import _budget_config, _evidence
from app.research.deepseek_prospective import AppendOnlyJsonl


def verified_open_position(state):
    try:
        pos = state["position"]
        if type(pos) is not dict or pos["status"] != "OPEN" or type(pos["quantity"]) is not int or pos["quantity"] <= 0:
            raise ValueError()
        decision_id = pos["decision_id"]
        accepted = [e for e in state["events"] if e["decision_id"] == decision_id and e["type"] == "ENTRY_ACCEPTED"]
        if (len(accepted) != 1 or accepted[0]["cost_eligibility"]["eligible"] is not True
                or accepted[0]["entry_price"] != pos["entry_price"]
                or accepted[0]["cost_eligibility"]["quantity"] != pos["quantity"]
                or accepted[0]["timestamp"] != pos["expected_entry"]
                or type(pos["bars_seen"]) is not int or pos["bars_seen"] < 1
                or type(pos["bar_hashes"]) is not dict or len(pos["bar_hashes"]) != pos["bars_seen"]
                or pos["last_bar"] not in pos["bar_hashes"]
                or any(e["decision_id"] == decision_id and e["type"] in {"SETTLED", "ENTRY_REJECTED"} for e in state["events"])
                or len([d for d in state["decisions"] if d["decision_id"] == decision_id]) != 1):
            raise ValueError()
        core._aware_timestamp(pos["expected_entry"], "accepted entry")
        return pos
    except Exception:
        raise guard.GuardedBatchError("verified_unresolved_accepted_position_required") from None


def seal_response(output, config, request_id, evidence):
    request = guard._read(output / "requests" / (request_id + ".json"))
    record = {"request_id": request_id, "batch_id": request["batch_id"],
              "manifest_sha256": core._digest(config), "payload_sha256": core._digest(request["payload"]),
              "evidence_sha256": core._digest(evidence)}
    AppendOnlyJsonl(output / "decision_evidence_seals.jsonl", "request_id").append(record)


def verify_response_seal(output, config, request_id, evidence, payload, batch_id):
    seals = AppendOnlyJsonl(output / "decision_evidence_seals.jsonl", "request_id").records()
    expected = {"request_id": request_id, "batch_id": batch_id,
                "manifest_sha256": core._digest(config), "payload_sha256": core._digest(payload),
                "evidence_sha256": core._digest(evidence)}
    if [r for r in seals if r["request_id"] == request_id] != [expected]:
        raise guard.GuardedBatchError("saved_response_seal_invalid_or_missing")


def recorded_payload(output, intent, config, frames, snapshots):
    """Check exact saved request/data; never rebuild quotes from a fresh observation."""
    from app.research.ai_led_reviewed import _validated_quote_row
    try:
        matches = [(p, guard._read(p)) for p in (output / "requests").glob("*.json")]
        matches = [(p, r) for p, r in matches if r["batch_id"] == intent["batch_id"]]
        if len(matches) != 1:
            raise ValueError()
        path, saved = matches[0]
        payload = saved["payload"]
        import json
        request = json.loads(payload["user_payload"])
        quote_enabled = config["network_policy"]["include_quotes"]
        fields = {"schema_version", "candidate_timestamp", "decision_time", "instrument_priority", "market"}
        if quote_enabled: fields.add("quotes")
        priority = config["account"]["instrument_priority"]
        decision_time = core._aware_timestamp(intent["decision_time"], "recorded decision")
        if (set(request) != fields or request["schema_version"] != core.SCHEMA
                or payload["system_prompt"] != guard.PROMPT.replace(guard.SCHEMA, guard.REVIEWED_SCHEMA)
                or request["market"] != snapshots or core._digest(snapshots) != intent["market_sha256"]
                or request["instrument_priority"] != priority or intent["universe"] != priority
                or request["decision_time"] != intent["decision_time"]
                or request["candidate_timestamp"] != intent["candidate_timestamp"]
                or pd.Timestamp(intent["candidate_timestamp"]) != frames[priority[0]].iloc[-1].timestamp
                or core._digest({"candidate_timestamp": intent["candidate_timestamp"], "universe": priority}) != intent["batch_id"]
                or any(intent["validation"][s]["status"] != "READY"
                       or intent["validation"][s]["input_sha256"] != snapshots[s]["source_sha256"]
                       or intent["validation"][s]["observed_at"] != snapshots[s]["observed_at"] for s in priority)):
            raise ValueError()
        if quote_enabled:
            if set(request["quotes"]) != set(priority): raise ValueError()
            for symbol in priority:
                _validated_quote_row(request["quotes"][symbol], symbol, decision_time)
        request_id = core._digest({"model": config["account"]["model_id"], "payload": payload})
        if path.stem != request_id or saved["request_id"] != request_id:
            raise ValueError()
        return payload, request_id
    except Exception:
        raise guard.GuardedBatchError("saved_request_evidence_invalid") from None


def _recover_pending(output, config, now, *, strict=False):
    """Verify sealed evidence, then recover state without granting a late entry."""
    from app.research.ai_led_reviewed import _utc_now
    try:
        pending = []
        for path in (output / "intents").glob("*.json"):
            if not (output / "batches" / path.name).exists():
                intent = guard._read(path)
                if path.stem != intent["batch_id"]: raise ValueError()
                requests = [guard._read(p) for p in (output / "requests").glob("*.json")]
                requests = [r for r in requests if r["batch_id"] == intent["batch_id"]]
                if len(requests) != 1:
                    if strict: raise ValueError()
                    continue
                if (output / "responses" / (requests[0]["request_id"] + ".json")).exists():
                    pending.append((intent, requests[0]))
                elif strict:
                    raise ValueError()
        if not pending:
            return {"status": "NO_PENDING_RECOVERY", "idempotent_replay": True,
                    "provider_request_attempted": False, "trade_placement_enabled": False} if strict else None
        if len(pending) != 1: raise ValueError()
        intent, saved = pending[0]
        decision_time = core._aware_timestamp(intent["decision_time"], "recorded decision")
        if decision_time > pd.Timestamp(now): raise ValueError()
        calendar, _ = _evidence(decision_time)  # verify recorded entry session, not permission to send now
    except guard.GuardedBatchError:
        raise
    except Exception:
        raise guard.GuardedBatchError("saved_recovery_evidence_missing_or_invalid") from None
    # Construct the current immutable spec with explicit names (avoids any legacy inference).
    expected = guard._configuration(config["account"]["instrument_priority"], config["account"]["initial_capital"],
        config["account"]["model_id"], config["provider_cap"], config["market_data_cap"],
        config["account"]["min_recent_turnover"], calendar, config["network_policy"])
    if expected != config: raise guard.GuardedBatchError("recovery_experiment_identity_changed")
    priority = config["account"]["instrument_priority"]
    import json
    request = json.loads(saved["payload"]["user_payload"])
    if any(type(request["market"][s]["source_sha256"]) is not str
           or not re.fullmatch(r"[0-9a-f]{64}", request["market"][s]["source_sha256"]) for s in priority):
        raise guard.GuardedBatchError("saved_input_identity_invalid")
    paths = [output / "inputs" / (request["market"][s]["source_sha256"] + ".json") for s in priority]
    guard._verify_frozen(paths)
    frames, snapshots = core._batch(paths, priority, decision_time, config["account"]["min_recent_turnover"], calendar)
    payload, request_id = recorded_payload(output, intent, config, frames, snapshots)
    evidence = guard._read(output / "responses" / (request_id + ".json"))
    for kind, maximum in (("provider", config["provider_cap"]), ("market_data", config["market_data_cap"])):
        _budget_config(output, kind, maximum)
    guard._validate_response_evidence(evidence, request_id, intent["batch_id"], payload, priority, frames, output, True)
    verify_response_seal(output, config, request_id, evidence, payload, intent["batch_id"])
    if evidence["category"] == "validated_provider_proposal":
        receipts = AppendOnlyJsonl(output / "transport_receipts.jsonl", "request_id").records()
        sent = AppendOnlyJsonl(output / "transport_sent.jsonl", "request_id").records()
        receipt = [r for r in receipts if r["request_id"] == request_id]
        send = [r for r in sent if r["request_id"] == request_id]
        if (len(receipt) != 1 or len(send) != 1 or receipt[0]["kind"] != "deepseek"
                or receipt[0]["status"] != "RECEIVED" or receipt[0]["response_sha256"] != evidence["response_sha256"]
                or not decision_time <= core._aware_timestamp(send[0]["sent_at_utc"], "send time")
                <= core._aware_timestamp(receipt[0]["received_at_utc"], "receipt time")
                < decision_time.floor("5min") + pd.Timedelta(minutes=5)):
            raise guard.GuardedBatchError("saved_provider_receipt_invalid")
    entry_authorized = core._aware_timestamp(_utc_now(), "recovery clock") < decision_time.floor("5min") + pd.Timedelta(minutes=5)
    result = core.run_offline_batch(paths, output_dir=output / "paper_account", instrument_priority=priority,
        capital=config["account"]["initial_capital"], now=decision_time,
        provider=core.SavedResponseProvider(evidence["core_response"], config["account"]["model_id"]),
        model=config["account"]["model_id"], min_recent_turnover=config["account"]["min_recent_turnover"],
        calendar=calendar, entry_authorized=entry_authorized)
    result.update(batch_id=intent["batch_id"], provider_request_id=request_id, provider_category=evidence["category"],
                  provider_request_attempted=False, mock_provider_calls=0, entry_authorized=entry_authorized,
                  recovered=True, recovery_observed_at=pd.Timestamp(now).isoformat(), evidence_kind=config["evidence_kind"])
    guard._write(output / "batches" / (intent["batch_id"] + ".json"),
                 {**intent, "provider": evidence, "provider_request": payload, "result": result})
    return result


def resolve_expired_entry(output, config, calendar, now, *, replay_terminal=False):
    """Local rejection only; a completed-candle fill is never invented here.

    The caller holds the experiment lock and has verified the current immutable
    configuration. Expiry is the original verified interval's new-entry cutoff,
    not a universal wall-clock time or permission to send a request now.
    """
    try:
        state = guard._account(output, config)
        stamp = core._aware_timestamp(now, "resolution clock")
        pos = state["position"]
        if pos is None:
            matches = [e for e in state["events"] if e["type"] == "ENTRY_REJECTED"
                       and e.get("reason_code") == "ENTRY_WINDOW_EXPIRED"
                       and e.get("resolved_at") == state["last_time"]]
            if not replay_terminal or not matches:
                return None
            event = matches[0]
            if stamp < core._aware_timestamp(event["resolved_at"], "resolution time"):
                raise ValueError()
        elif pos["status"] == "OPEN":
            return None
        else:
            if pos["status"] != "PENDING":
                raise ValueError()
            decision_id = pos["decision_id"]
            if type(decision_id) is not str or not re.fullmatch(r"[0-9a-f]{64}", decision_id):
                raise ValueError()
            decisions = [d for d in state["decisions"] if d["decision_id"] == decision_id]
            if len(decisions) != 1 or any(e["decision_id"] == decision_id for e in state["events"]):
                raise ValueError()
            decision = decisions[0]
            decision_time = core._aware_timestamp(pos["decision_time"], "original decision")
            expected = core._aware_timestamp(pos["expected_entry"], "proposed entry")
            if (stamp < decision_time or decision["decision_time"] != pos["decision_time"]
                    or expected != decision_time.floor("5min") + pd.Timedelta(minutes=5)
                    or not calendar.entry_allowed(decision_time)):
                raise ValueError()
            end = calendar.candle_end(expected)
            cutoff = end - pd.Timedelta(minutes=5)
            if stamp < cutoff:
                return None
            batch = guard._read(output / "batches" / (decision_id + ".json"))
            request_id = batch["result"]["provider_request_id"]
            if (type(request_id) is not str or not re.fullmatch(r"[0-9a-f]{64}", request_id)
                    or batch["batch_id"] != decision_id or batch["result"]["decision"] != decision
                    or batch["result"]["position"] != pos
                    or guard._read(output / "responses" / (request_id + ".json")) != batch["provider"]):
                raise ValueError()
            verify_response_seal(output, config, request_id, batch["provider"], batch["provider_request"], decision_id)
            event = {"type": "ENTRY_REJECTED", "decision_id": decision_id,
                     "timestamp": expected.isoformat(), "expected_entry": expected.isoformat(),
                     "decision_time": decision_time.isoformat(), "resolved_at": stamp.isoformat(),
                     "entry_validation_cutoff": cutoff.isoformat(), "reason_code": "ENTRY_WINDOW_EXPIRED",
                     "taken": False, "cost_eligibility": {"eligible": False, "reason": "ENTRY_WINDOW_EXPIRED"}}
            state["events"].append(event)
            state["position"] = None
            state["last_time"] = stamp.isoformat()
            core._commit(output / "paper_account", state)
        return {"status": "ENTRY_REJECTED", "reason_code": "ENTRY_WINDOW_EXPIRED", "taken": False,
                "resolution": event, "position": None, "cash": state["cash"],
                "available_cash": state["cash"], "account_balance": state["balance"],
                "provider_request_attempted": False, "trade_placement_enabled": False}
    except Exception:
        raise guard.GuardedBatchError("pending_entry_resolution_evidence_invalid") from None


def recover_pending(output, config, now, *, strict=False):
    try:
        return _recover_pending(output, config, now, strict=strict)
    except guard.GuardedBatchError:
        raise
    except Exception:
        raise guard.GuardedBatchError("saved_recovery_evidence_missing_or_invalid") from None


def finish_settlement(output, config, calendar):
    """Recover/commit a prepared settlement transaction; never acquire data or open trades."""
    from app.research.prospective_preflight import settlement_acquisition_window
    records = [guard._read(p) for p in (output / "settlement_prepared").glob("*.json")
               if not (output / "settlements" / p.name).exists()]
    if not records:
        return None
    if len(records) != 1:
        raise guard.GuardedBatchError("ambiguous_settlement_recovery")
    record = records[0]
    try:
        if record["manifest_sha256"] != core._digest(config): raise ValueError()
        request_id = record["request_id"]
        if not re.fullmatch(r"[0-9a-f]{64}", record["input_sha256"]): raise ValueError()
        path = output / "inputs" / (record["input_sha256"] + ".json")
        guard._verify_frozen([path])
        recorded = core._aware_timestamp(record["recorded_at"], "settlement observation")
        start, _, end = settlement_acquisition_window(calendar, record["entry_timestamp"])
        if not start <= recorded <= end: raise ValueError()
        frames, _ = guard._validated_batch({record["symbol"]: {"status": "READY", "snapshot": path}},
            [record["symbol"]], recorded, config["account"]["min_recent_turnover"], calendar)
        expected_id = core._digest({"position_id": record["position_id"],
            "candidate_timestamp": frames[record["symbol"]].iloc[-1].timestamp.isoformat(),
            "symbol": record["symbol"], "kind": "UPSTOX_SETTLEMENT"})
        sent = AppendOnlyJsonl(output / "transport_sent.jsonl", "request_id").records()
        receipts = AppendOnlyJsonl(output / "transport_receipts.jsonl", "request_id").records()
        budget = AppendOnlyJsonl(output / "market_data_request_budget.jsonl", "request_id")
        if (request_id != expected_id or request_id not in budget._records
                or len([r for r in sent if r["request_id"] == request_id and r["kind"] == "upstox_candles"]) != 1
                or len([r for r in receipts if r["request_id"] == request_id and r["status"] == "RECEIVED"]) != 1):
            raise ValueError()
        state = guard._account(output, config)
        actual = core._digest(state)
        if actual == record["before_sha256"]:
            pos = verified_open_position(state)
            if pos["decision_id"] != record["position_id"]: raise ValueError()
            core._advance(state, frames, recorded, calendar)
            state["last_time"] = recorded.isoformat()
            if core._digest(state) != record["after_sha256"]: raise ValueError()
            core._commit(output / "paper_account", state)
        elif actual != record["after_sha256"]:
            raise ValueError()
        result = {"status": "SETTLEMENT_ONLY", "position_id": record["position_id"],
                  "position": state["position"], "provider_request_attempted": False,
                  "trade_placement_enabled": False, "account_balance": state["balance"]}
        guard._write(output / "settlements" / (request_id + ".json"), {**record, "result": result})
        return result
    except Exception:
        raise guard.GuardedBatchError("settlement_evidence_or_account_invalid") from None
