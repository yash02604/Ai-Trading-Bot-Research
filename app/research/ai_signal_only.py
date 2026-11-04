"""Guarded signal research using existing transports; no paper-account calls."""
from dataclasses import asdict
import hashlib
import json
import re
from pathlib import Path

import pandas as pd

from app.client_attestation import verify_client_attestation
from app.research import ai_led_guarded as guard, ai_led_offline as core, ai_led_reviewed as reviewed
from app.research.deepseek_prospective import PersistentRequestBudget, AppendOnlyJsonl
from app.research.deepseek_eval_v1 import _prepare_quant_candidates
from app.research.prospective_preflight import _evidence, _budget_config, CALENDAR_PATH
from app.research.offline_session_calendar import OfflineSessionCalendar
from app.research import market_observation_replay as observations
from app.research.ai_led_recovery import seal_response, verify_response_seal
from app.research.signal_evaluation import evaluation_policy
from app.research import signal_request_budget as request_slots

SCHEMA = "ai-signal-only-v1"
MANIFEST = "signal_experiment.json"
PROMPT = guard.PROMPT.replace(guard.SCHEMA, SCHEMA) + " Return JSON. Predictions only; no executable fills or account authority."


def preflight(binding, now=None):
    """Current session for sends; identity-only checks for explicit local resume."""
    if now is None:
        verify_client_attestation()
        calendar = OfflineSessionCalendar(CALENDAR_PATH)
    else:
        calendar, _ = _evidence(now)
        if not any(a <= pd.Timestamp(now) and pd.Timestamp(now).floor("5min") + pd.Timedelta(minutes=45) <= b
                   for a, b in calendar.intervals(now)):
            raise guard.GuardedBatchError("signal_future_horizon_outside_session")
        if not any(a <= binding.candidate and binding.candidate + pd.Timedelta(minutes=5) <= b
                   and binding.candidate - a >= pd.Timedelta(minutes=34 * 5)
                   for a, b in calendar.intervals(binding.candidate)):
            raise guard.GuardedBatchError("signal_insufficient_warmup")
    o = binding.options
    config = guard._configuration(o["priority"], o["capital"], o["model"], o["provider_cap"], o["market_data_cap"],
                                 o["min_turnover"], calendar, binding.policy)
    config.update(mode="AI_SIGNAL_ONLY", schema_version=SCHEMA, evaluation_policy=evaluation_policy(),
                  prompt_sha256=hashlib.sha256(PROMPT.encode("utf-8")).hexdigest(),
                  execution_enabled=False, evidence_kind="PROSPECTIVE_TRANSPORT_ASSERTED; local receipts only")
    config["account"]["prompt_sha256"] = config["prompt_sha256"]
    from app.research.signal_observations import capture_policy
    config["future_observation_policy"] = capture_policy()
    config["request_slot_policy"] = request_slots.slot_policy(o["priority"], binding.policy["include_quotes"])
    if len(config["request_slot_policy"]["slots"]) > o["market_data_cap"]:
        raise guard.GuardedBatchError("signal_budget_must_hold_initial_and_future_slots")
    config["signal_sources"] = {p.name: hashlib.sha256(p.read_bytes()).hexdigest() for p in (
        Path(__file__), Path(__file__).with_name("signal_evaluation.py"),
        Path(__file__).with_name("signal_observations.py"), Path(__file__).with_name("signal_request_budget.py"),
        Path(__file__).with_name("market_observation_replay.py"), Path(__file__).with_name("prospective_paper_fills.py"),
        Path(__file__).with_name("deepseek_eval_v1.py"), Path(__file__).parents[1] / "market_analysis.py",
        Path(__file__).parents[1] / "reversal_detector.py")}
    path = binding.output / MANIFEST
    if path.exists():
        saved_config = guard._read(path)
        if saved_config != config:
            raise guard.GuardedBatchError("signal_configuration_changed")
        # Equal numeric CLI values (1000.0 vs 1000) must retain the saved byte identity.
        config = saved_config
    elif now is None or any(p.name != ".prospective.lock" for p in binding.output.iterdir()):
        raise guard.GuardedBatchError("signal_requires_fresh_directory")
    else:
        guard._write(path, config)
    for kind, maximum in (("provider", o["provider_cap"]), ("market_data", o["market_data_cap"])):
        _budget_config(binding.output, kind, maximum)
    request_slots.initialize(binding, config)
    return config, calendar


def record_quote(binding, symbol, request_id, response):
    """Hash canonical decoded JSON, explicitly not original HTTP wire bytes."""
    rows = list((binding.output / "market_observations").glob("*.json"))
    event = observations.normalize_observation(core._encoded(response), transport="REST",
        instrument_key=core.STOCKS[symbol], connection_id="signal-capture", local_sequence=len(rows) + 1,
        received_at=reviewed._utc_now(), kind="SNAPSHOT", evidence_kind="RECORDED_UNAUTHENTICATED")
    guard._write(binding.output / "market_observations" / (request_id + ".json"),
                 {"payload_representation": "canonical_decoded_json_not_wire_bytes", "observation": asdict(event)})


def _prefix(output, calendar, now):
    events = []
    for path in (output / "market_observations").glob("*.json"):
        raw = guard._read(path)
        if raw["payload_representation"] != "canonical_decoded_json_not_wire_bytes":
            raise guard.GuardedBatchError("signal_observation_representation")
        record = raw["observation"]
        for field in ("bids", "asks", "provider_timestamps"):
            record[field] = tuple(tuple(row) for row in record[field])
        events.append(observations.Observation(**record))
    return observations.replay_observations(events, as_of=now, calendar=calendar).sha256


def _saved_inputs(output, intent, config, calendar):
    priority = config["account"]["instrument_priority"]
    if guard._read(output / "initial_capture.json") != {
            "candidate_timestamp": intent["candidate_timestamp"], "instrument_priority": priority}:
        raise guard.GuardedBatchError("signal_initial_candidate_changed")
    if (set(intent) != {"batch_id", "request_id", "candidate_timestamp", "decision_time", "input_sha256"}
            or any(type(intent[k]) is not str or not re.fullmatch(r"[0-9a-f]{64}", intent[k]) for k in ("batch_id", "request_id"))
            or type(intent["input_sha256"]) is not dict or set(intent["input_sha256"]) != set(priority)
            or any(type(h) is not str or not re.fullmatch(r"[0-9a-f]{64}", h) for h in intent["input_sha256"].values())):
        raise guard.GuardedBatchError("signal_intent_invalid")
    request = guard._read(output / "requests" / (intent["request_id"] + ".json"))
    payload = request["payload"]
    if (request["batch_id"] != intent["batch_id"] or request["request_id"] != intent["request_id"]
            or core._digest({"model": config["account"]["model_id"], "payload": payload}) != intent["request_id"]
            or payload["system_prompt"] != PROMPT
            or config["prompt_sha256"] != hashlib.sha256(payload["system_prompt"].encode("utf-8")).hexdigest()
            or config["account"]["prompt_sha256"] != config["prompt_sha256"]):
        raise guard.GuardedBatchError("signal_saved_request_invalid")
    body = json.loads(payload["user_payload"])
    paths = [output / "inputs" / (intent["input_sha256"][s] + ".json") for s in priority]
    guard._verify_frozen(paths)
    when = core._aware_timestamp(intent["decision_time"], "signal decision")
    statuses = {s: {"status": "READY", "snapshot": p} for s, p in zip(priority, paths)}
    frames, market = guard._validated_batch(statuses, priority, when, config["account"]["min_recent_turnover"], calendar)
    for symbol in priority:
        capture_id = core._digest({"candidate_timestamp": intent["candidate_timestamp"], "symbol": symbol,
                                  "universe": priority, "kind": "UPSTOX_CAPTURE"})
        source = guard._read(output / "capture_sources" / (capture_id + ".json"))
        source_hash = source["received_snapshot_sha256"]
        if type(source_hash) is not str or not re.fullmatch(r"[0-9a-f]{64}", source_hash):
            raise ValueError()
        original = output / "received" / (source_hash + ".json")
        guard._verify_frozen([original])
        raw = json.loads(original.read_bytes())
        if (source["frozen_snapshot_sha256"] != intent["input_sha256"][symbol]
                or raw["source"] != "upstox-v3-intraday-readonly" or raw["symbol"] != symbol
                or raw["instrument_key"] != core.STOCKS[symbol]
                or raw["observed_at"] != market[symbol]["observed_at"] or raw["candles"] != market[symbol]["candles"]):
            raise guard.GuardedBatchError("signal_market_source_changed")
    fields = {"schema_version", "mode", "candidate_timestamp", "decision_time", "instrument_priority", "market",
              "depth_source_freshness", "observation_prefix_sha256", "evaluation_policy_sha256"}
    if config["network_policy"]["include_quotes"]:
        fields.add("quotes")
        if set(body["quotes"]) != set(priority):
            raise ValueError()
        for symbol in priority:
            reviewed._validated_quote_row(body["quotes"][symbol], symbol, when)
    if (set(body) != fields or body["schema_version"] != SCHEMA or body["mode"] != "AI_SIGNAL_ONLY"
            or body["instrument_priority"] != priority or body["market"] != market
            or body["decision_time"] != intent["decision_time"] or body["candidate_timestamp"] != intent["candidate_timestamp"]
            or body["depth_source_freshness"] != "UNKNOWN"
            or body["evaluation_policy_sha256"] != core._digest(config["evaluation_policy"])
            or body["observation_prefix_sha256"] != _prefix(output, calendar, when)
            or core._digest({"candidate_timestamp": intent["candidate_timestamp"], "universe": priority}) != intent["batch_id"]
            or any(frames[s].iloc[-1].timestamp != pd.Timestamp(intent["candidate_timestamp"]) for s in priority)):
        raise guard.GuardedBatchError("signal_saved_inputs_invalid")
    return payload, body, frames, statuses


def authorize_request(binding, payload):
    """Called at the existing SDK boundary, including on direct complete calls."""
    config, calendar = binding._preflight()
    request_id = core._digest({"model": binding.options["model"], "payload": payload})
    request = guard._read(binding.output / "requests" / (request_id + ".json"))
    if type(request["batch_id"]) is not str or not re.fullmatch(r"[0-9a-f]{64}", request["batch_id"]):
        raise guard.GuardedBatchError("signal_batch_identity_invalid")
    intent = guard._read(binding.output / "intents" / (request["batch_id"] + ".json"))
    saved, body, _, statuses = _saved_inputs(binding.output, intent, config, calendar)
    now = core._aware_timestamp(reviewed._utc_now(), "signal request")
    when = core._aware_timestamp(body["decision_time"], "signal decision")
    deadline = when.floor("5min") + pd.Timedelta(minutes=5)
    if payload != saved or not when <= now < deadline:
        raise guard.GuardedBatchError("signal_request_changed_or_expired")
    guard._validated_batch(statuses, binding.options["priority"], now, binding.options["min_turnover"], calendar)
    if binding.policy["include_quotes"] and body["quotes"] != binding.market_context():
        raise guard.GuardedBatchError("signal_quote_changed")
    return deadline


def _publish(binding, config, calendar, intent):
    output = binding.output
    for symbol in binding.options["priority"]:
        capture_id = core._digest({"candidate_timestamp": intent["candidate_timestamp"], "symbol": symbol,
                                  "universe": binding.options["priority"], "kind": "UPSTOX_CAPTURE"})
        request_slots.verify_physical_attempt(binding, capture_id, symbol, "INITIAL_SNAPSHOT")
    payload, body, frames, _ = _saved_inputs(output, intent, config, calendar)
    rid, batch = intent["request_id"], intent["batch_id"]
    saved = guard._read(output / "responses" / (rid + ".json"))
    verify_response_seal(output, config, rid, saved, payload, batch)
    evidence = saved["evidence"]
    guard._validate_response_evidence(evidence, rid, batch, payload, binding.options["priority"], frames, output,
                                      reviewed=True, signal_only=True)
    valid = evidence["category"] == "validated_provider_proposal"
    received, ready = saved["response_received_at"], saved["decision_ready_at"]
    if valid:
        sent = [r for r in AppendOnlyJsonl(output / "transport_sent.jsonl", "request_id").records() if r["request_id"] == rid]
        receipts = [r for r in AppendOnlyJsonl(output / "transport_receipts.jsonl", "request_id").records() if r["request_id"] == rid]
        when = core._aware_timestamp(intent["decision_time"], "signal decision")
        if (len(sent) != 1 or len(receipts) != 1 or sent[0]["kind"] != "deepseek" or receipts[0]["status"] != "RECEIVED"
                or receipts[0]["response_sha256"] != evidence["response_sha256"]
                or received != receipts[0]["received_at_utc"]
                or not when <= core._aware_timestamp(sent[0]["sent_at_utc"], "send")
                <= core._aware_timestamp(received, "receipt") <= core._aware_timestamp(ready, "ready")
                < when.floor("5min") + pd.Timedelta(minutes=5)):
            raise guard.GuardedBatchError("signal_response_timing_invalid")
    prediction = core._parse(evidence["core_response"], binding.options["priority"], frames) if valid else None
    record = {"schema_version": SCHEMA, "mode": "AI_SIGNAL_ONLY", "decision_id": batch,
              "instrument_priority": binding.options["priority"], "candidate_timestamp": intent["candidate_timestamp"],
              "market_decision_time": intent["decision_time"], "provider_request_id": rid,
              "provider_id": "deepseek", "requested_model_id": config["account"]["model_id"],
              "returned_model_id": config["account"]["model_id"] if valid else None,
              "request_sha256": core._digest(payload), "response_sha256": evidence["response_sha256"],
              "provider_response_received_at": received, "decision_ready_at": ready,
              "status": "VALID_PREDICTION" if valid else evidence["category"].upper(),
              "prediction": prediction, "rationale": evidence["rationale"],
              "evaluation_policy": config["evaluation_policy"], "market_input_sha256": intent["input_sha256"],
              "observation_prefix_sha256": body["observation_prefix_sha256"], "depth_source_freshness": "UNKNOWN",
              "market_data_source": "upstox-v3-intraday-readonly; normalized decoded data, not wire-byte authentication",
              "tick_size_verification": "NOT_ESTABLISHED",
              "reference_prices": {s: float(frames[s].iloc[-1].close) for s in binding.options["priority"]},
              "baseline": {s: len(frames[s]) - 1 in _prepare_quant_candidates(frames[s])[1] for s in binding.options["priority"]},
              "taken": False, "execution_enabled": False, "trade_placement_enabled": False,
              "evidence_kind": config["evidence_kind"]}
    guard._write(output / "signals" / (batch + ".json"), record)
    return record


def _resume(binding, config, calendar, *, strict=False):
    intents = []
    for path in (binding.output / "intents").glob("*.json"):
        item = guard._read(path)
        if not re.fullmatch(r"[0-9a-f]{64}", path.stem) or item["batch_id"] != path.stem:
            raise guard.GuardedBatchError("signal_intent_identity_invalid")
        intents.append(item)
    pending = [i for i in intents if not (binding.output / "signals" / (i["batch_id"] + ".json")).exists()]
    if len(pending) > 1:
        raise guard.GuardedBatchError("ambiguous_signal_recovery")
    if pending:
        intent = pending[0]
        if not (binding.output / "responses" / (intent["request_id"] + ".json")).exists():
            # A potentially spent request is never repeated or reconstructed.
            raise guard.GuardedBatchError("signal_request_has_no_saved_response")
        return _publish(binding, config, calendar, intent)
    if strict:
        if not intents:
            return {"mode": "AI_SIGNAL_ONLY", "status": "NO_SAVED_SIGNAL", "trade_placement_enabled": False}
        return _publish(binding, config, calendar, max(intents, key=lambda i: i["decision_time"]))
    batch = core._digest({"candidate_timestamp": binding.candidate.isoformat(), "universe": binding.options["priority"]})
    match = next((i for i in intents if i["batch_id"] == batch), None)
    return _publish(binding, config, calendar, match) if match else None


def run_signal_only(*, output_dir, instrument_priority, capital, provider_cap, market_data_cap,
                    enable_signal_only=False, allow_market_data_requests=False, allow_provider_requests=False,
                    include_quotes=False, recover_only=False, preflight_only=False):
    if type(instrument_priority) not in (list, tuple):
        raise guard.GuardedBatchError("signal_universe_invalid")
    instrument_priority = list(instrument_priority)
    binding = reviewed._ReviewedRun(output_dir=output_dir, instrument_priority=instrument_priority, capital=capital,
        provider_cap=provider_cap, market_data_cap=market_data_cap, enable_signal_only=enable_signal_only,
        allow_market_data_requests=allow_market_data_requests, allow_provider_requests=allow_provider_requests,
        include_quotes=include_quotes, purpose="signal_recovery" if recover_only else "signal")
    with core._experiment_lock(binding.output), reviewed._binding_scope(binding, binding):
        if (binding.output / MANIFEST).exists() and any((binding.output / "intents").glob("*.json")):
            # A later wall-clock bucket never creates another initial acquisition.
            config, calendar = preflight(binding)
            if preflight_only:
                return {"mode": "AI_SIGNAL_ONLY", "status": "PREFLIGHT_ONLY", "trade_placement_enabled": False}
            if len(list((binding.output / "intents").glob("*.json"))) != 1:
                raise guard.GuardedBatchError("signal_experiment_has_multiple_candidates")
            return _resume(binding, config, calendar, strict=True)
        config, calendar = preflight(binding, None if recover_only else reviewed._utc_now())
        if preflight_only:
            return {"mode": "AI_SIGNAL_ONLY", "status": "PREFLIGHT_ONLY", "trade_placement_enabled": False}
        resumed = _resume(binding, config, calendar, strict=recover_only)
        if resumed is not None:
            return resumed
        guard._write(binding.output / "initial_capture.json", {
            "candidate_timestamp": binding.candidate.isoformat(), "instrument_priority": instrument_priority})
        statuses = {}
        for symbol in instrument_priority:
            binding._preflight()
            rid = binding._market_id(symbol)
            path = binding.output / "captures" / (rid + ".json")
            if path.exists():
                evidence = guard._read(path)
            else:
                if not request_slots.reserve(binding, rid, symbol, "INITIAL_SNAPSHOT"):
                    raise guard.GuardedBatchError("signal_capture_reserved_without_result")
                source = binding.fetch(symbol)
                _, snapshots = guard._validated_batch({symbol: {"status": "READY", "snapshot": source}},
                    [symbol], pd.Timestamp(reviewed._utc_now()), binding.options["min_turnover"], calendar)
                frozen = guard._freeze(binding.output, snapshots)[0]
                guard._write(binding.output / "capture_sources" / (rid + ".json"),
                    {"received_snapshot_sha256": source.stem, "frozen_snapshot_sha256": frozen.stem})
                evidence = {"symbol": symbol, "request_id": rid, "status": "READY", "category": "validated_upstox_capture",
                            "reservation_consumed": True, "input_sha256": frozen.stem, "snapshot": str(frozen.relative_to(binding.output))}
                guard._write(path, evidence)
            guard._capture_evidence(evidence, binding.output, symbol, rid, binding.candidate,
                pd.Timestamp(reviewed._utc_now()), binding.options["min_turnover"], calendar, True)
            statuses[symbol] = {"status": "READY", "snapshot": binding.output / evidence["snapshot"]}
        when = core._aware_timestamp(reviewed._utc_now(), "signal decision")
        frames, _ = guard._validated_batch(statuses, instrument_priority, when, binding.options["min_turnover"], calendar)
        paths = [statuses[s]["snapshot"] for s in instrument_priority]
        _, market = core._batch(paths, instrument_priority, when, binding.options["min_turnover"], calendar)
        body = {"schema_version": SCHEMA, "mode": "AI_SIGNAL_ONLY", "candidate_timestamp": binding.candidate.isoformat(),
                "decision_time": when.isoformat(), "instrument_priority": instrument_priority, "market": market,
                "depth_source_freshness": "UNKNOWN", "observation_prefix_sha256": _prefix(binding.output, calendar, when),
                "evaluation_policy_sha256": core._digest(config["evaluation_policy"])}
        if include_quotes:
            body["quotes"] = binding.market_context()
        payload = {"system_prompt": PROMPT, "user_payload": core._encoded(body).decode()}
        rid = core._digest({"model": binding.options["model"], "payload": payload})
        batch = core._digest({"candidate_timestamp": binding.candidate.isoformat(), "universe": instrument_priority})
        intent = {"batch_id": batch, "request_id": rid, "candidate_timestamp": binding.candidate.isoformat(),
                  "decision_time": when.isoformat(), "input_sha256": {s: Path(statuses[s]["snapshot"]).stem for s in instrument_priority}}
        guard._write(binding.output / "requests" / (rid + ".json"), {"request_id": rid, "batch_id": batch, "payload": payload})
        guard._write(binding.output / "intents" / (batch + ".json"), intent)
        evidence = {"request_id": rid, "batch_id": batch, "payload_sha256": core._digest(payload),
                    "core_response": None, "category": "provider_budget_exhausted", "response_sha256": None,
                    "rationale": None, "reservation_consumed": False}
        ready, received = None, None
        budget = PersistentRequestBudget(binding.output / "request_budget.jsonl", provider_cap)
        try:
            reserved = budget.reserve(rid)
        except RuntimeError:
            reserved = False
        if reserved:
            evidence.update(category="provider_failure", reservation_consumed=True)
            try:
                result = binding.complete(payload)
                if (type(result) is not guard.ProviderResult or result.provider_id != "deepseek"
                        or result.model_id != binding.options["model"] or result.requested_model_id != binding.options["model"]):
                    raise ValueError()
                evidence["response_sha256"] = hashlib.sha256(result.content.encode()).hexdigest()
                evidence["category"] = "invalid_proposal"
                converted, reasons = guard._proposal(result.content, instrument_priority, frames, binding.candidate, SCHEMA)
                if include_quotes:
                    for proposal in core._parse(converted, instrument_priority, frames)["proposals"]:
                        quote_id = binding._market_id(proposal["symbol"], "UPSTOX_QUOTE")
                        event = guard._read(binding.output / "market_observations" / (quote_id + ".json"))["observation"]
                        for value in (proposal["entry_price"], proposal["stop_loss"], proposal["target_price"]):
                            if ((event["lower_circuit"] is not None and value < event["lower_circuit"])
                                    or (event["upper_circuit"] is not None and value > event["upper_circuit"])):
                                raise ValueError()
                evidence.update(core_response=converted, rationale=reasons, category="validated_provider_proposal")
                received = next(r["received_at_utc"] for r in AppendOnlyJsonl(binding.output / "transport_receipts.jsonl", "request_id").records() if r["request_id"] == rid)
                ready = reviewed._utc_now().isoformat()
            except Exception:
                pass
        elif rid in budget.ledger._records:
            evidence.update(category="reserved_without_result", reservation_consumed=True)
        receipt = next((r for r in AppendOnlyJsonl(binding.output / "transport_receipts.jsonl", "request_id").records()
                        if r["request_id"] == rid and r["status"] == "RECEIVED"), None)
        received = receipt["received_at_utc"] if receipt else None
        saved = {"evidence": evidence, "response_received_at": received, "decision_ready_at": ready}
        guard._write(binding.output / "responses" / (rid + ".json"), saved)
        seal_response(binding.output, config, rid, saved)
        return _publish(binding, config, calendar, intent)
