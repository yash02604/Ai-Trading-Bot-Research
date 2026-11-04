"""Guarded complete-batch paper adapter with separate reviewed transport binding.

The account and proposal engine remain in ai_led_offline. A durable request intent,
reservation, validated response and atomic account commit support crash recovery
without retrying a potentially spent request. Mock evidence is never AI performance.
"""
import copy
import hashlib
import json
import os
from pathlib import Path
import re
import tempfile

import pandas as pd

from app.config import get_settings
from app.research import ai_led_offline as core
from app.research.deepseek_prospective import (
    PROSPECTIVE_SCHEMA, AppendOnlyJsonl, PersistentRequestBudget, _experiment_lock,
)
from app.research.prospective_preflight import (
    _budget_config, _evidence, validate_request_snapshot,
)
from app.client_attestation import ATTESTATION_SHA256
from app.session_manifest_v3 import MANIFEST_SHA256
from app.v2.provider import ProviderResult

SCHEMA = "ai-led-guarded-mock-paper-v1"
REVIEWED_SCHEMA = "ai-led-guarded-prospective-paper-v1"
MANIFEST = "guarded_experiment.json"
REASONS = {"trend", "momentum", "volume", "risk_reward", "no_edge"}
PROMPT = (core.PROMPT + " Wrap the response using schema_version ai-led-guarded-mock-paper-v1, "
          "candidate_timestamp (exact supplied timestamp), action, proposals, rationale_codes. "
          "Each proposal also requires rationale_codes. Rationale codes are nonempty unique lists "
          "drawn from trend, momentum, volume, risk_reward, no_edge. No free text or extra fields.")


class GuardedBatchError(ValueError):
    def __init__(self, code):
        super().__init__("Guarded AI paper batch blocked; " + code)


class MockDecisionProvider:
    """Explicit saved mock proposal, optionally raising a simulated failure."""
    def __init__(self, content, *, model="deepseek-flash", error=None):
        self.content, self.model_id, self.error = content, model, error
        self.calls, self.payloads = 0, []

    def complete(self, payload):
        self.calls += 1
        self.payloads.append(copy.deepcopy(payload))
        if self.error is not None:
            raise self.error
        return ProviderResult(self.content, "synthetic-mock", self.model_id, self.model_id, 0)


class MockMarketDataClient:
    """Simulated read-only capture from local snapshots, never an Upstox client."""
    def __init__(self, snapshots, *, failures=()):
        self.snapshots, self.failures, self.calls = snapshots, set(failures), []

    def fetch(self, symbol):
        self.calls.append(symbol)
        if symbol in self.failures:
            raise RuntimeError("simulated market-data failure")
        return Path(self.snapshots[symbol])


def _read(path):
    envelope = json.loads(Path(path).read_bytes())
    if set(envelope) != {"sha256", "record"} or envelope["sha256"] != core._digest(envelope["record"]):
        raise GuardedBatchError("evidence_integrity")
    return envelope["record"]


def _write_bytes_once(path, data):
    """Atomic immutable publication under the experiment lock, no overwritten evidence."""
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    if path.exists():
        if path.read_bytes() != data:
            raise GuardedBatchError("immutable_evidence_changed")
        return
    temp = None
    try:
        with tempfile.NamedTemporaryFile(dir=path.parent, delete=False) as stream:
            temp = Path(stream.name)
            stream.write(data)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temp, path)
    finally:
        if temp is not None and temp.exists():
            temp.unlink()


def _write(path, record):
    _write_bytes_once(path, core._encoded({"sha256": core._digest(record), "record": record}))


def _configuration(priority, capital, model, provider_cap, market_data_cap, min_turnover, calendar,
                   network_policy=None):
    base = core._config(capital, priority, min_turnover, model, calendar)
    if (model != "deepseek-flash" or any(type(n) is not int or n < 0
                                        for n in (provider_cap, market_data_cap))):
        raise GuardedBatchError("invalid_configuration")
    sources = [Path(__file__), Path(__file__).with_name("prospective_preflight.py"),
               Path(__file__).parents[1] / "client_attestation.py",
               Path(__file__).parents[1] / "session_manifest_v3.py",
               Path(__file__).with_name("offline_session_calendar.py")]
    config = {"schema_version": SCHEMA, "mode": "GUARDED_MOCK_PAPER", "account": base,
            "completeness_policy": "ALL_CONFIGURED_INSTRUMENTS_READY",
            "provider_cap": provider_cap, "market_data_cap": market_data_cap,
            "calendar_sha256": MANIFEST_SHA256, "attestation_sha256": ATTESTATION_SHA256,
            "prompt_sha256": hashlib.sha256(PROMPT.encode()).hexdigest(),
            "adapter_sources": {p.name: hashlib.sha256(p.read_bytes()).hexdigest() for p in sources},
            "evidence_kind": "SYNTHETIC_MOCK; saved market-data provenance not authenticated",
            "trade_placement_enabled": False}
    if network_policy is not None:
        from app.research.ai_led_reviewed import _validated_policy
        _validated_policy(network_policy)
        config.update(schema_version=REVIEWED_SCHEMA, mode="REVIEWED_PROSPECTIVE_PAPER",
                      network_policy=network_policy,
                      evidence_kind="PROSPECTIVE_PROVIDER; local receipt time, not independent timestamp authentication",
                      prompt_sha256=hashlib.sha256(PROMPT.replace(SCHEMA, REVIEWED_SCHEMA).encode()).hexdigest())
        for p in (Path(__file__).with_name("ai_led_reviewed.py"),
                  Path(__file__).with_name("ai_led_recovery.py"),
                  Path(__file__).parents[1] / "v2/provider.py",
                  Path(__file__).parents[1] / "paper_session.py",
                  Path(__file__).parents[1] / "execution_observations.py"):
            config["adapter_sources"][p.name] = hashlib.sha256(p.read_bytes()).hexdigest()
    return config


def _preflight(output, *, priority, capital, model, provider_cap, market_data_cap, min_turnover, now,
               network_policy=None, purpose="entry", position_entry=None):
    try:
        if get_settings().paper_trading is not True:
            raise ValueError()
        calendar, _ = (_evidence(now) if purpose == "entry" else
                       _evidence(now, purpose=purpose, position_entry=position_entry))
        config = _configuration(priority, capital, model, provider_cap, market_data_cap, min_turnover, calendar,
                                network_policy)
        path = output / MANIFEST
        if path.exists():
            if _read(path) != config:
                raise ValueError()
        else:
            if any(p.name != ".prospective.lock" for p in output.iterdir()):
                raise ValueError()
            _write(path, config)
        for kind, maximum in (("provider", provider_cap), ("market_data", market_data_cap)):
            _budget_config(output, kind, maximum)
        _account(output, config)
        return config, calendar
    except Exception:
        raise GuardedBatchError("preflight_failed") from None


def _validated_batch(statuses, priority, now, min_turnover, calendar):
    try:
        if type(statuses) is not dict or set(statuses) != set(priority):
            raise ValueError()
        for status in statuses.values():
            if (type(status) is not dict or set(status) != {"status", "snapshot"}
                    or status["status"] != "READY"):
                raise ValueError()
        paths = [statuses[s]["snapshot"] for s in priority]
        # Existing complete-universe, OHLCV, liquidity and timestamp validators.
        frames, snapshots = core._batch(paths, priority, now, min_turnover, calendar)
        for symbol, path in zip(priority, paths):
            body, _, frame, observed_at, _ = core._load_snapshot(path, now=now)
            if body["symbol"].upper() != symbol:
                raise ValueError()
            validate_request_snapshot({"calendar": calendar, "policy": {"instrument_priority": priority}},
                                      body, frame, observed_at, now)
        return frames, snapshots
    except Exception:
        raise GuardedBatchError("incomplete_or_invalid_batch") from None


def _freeze(output, snapshots):
    """Persist normalized numeric candles only; original byte hashes stay in the intent."""
    paths = []
    for symbol, snapshot in snapshots.items():
        body = {k: v for k, v in snapshot.items() if k != "source_sha256"}
        body["schema_version"] = PROSPECTIVE_SCHEMA
        data = core._encoded(body)
        path = output / "inputs" / (hashlib.sha256(data).hexdigest() + ".json")
        _write_bytes_once(path, data)
        paths.append(path)
    return paths


def _rationale(codes):
    if (type(codes) is not list or not codes or any(type(c) is not str or c not in REASONS for c in codes)
            or len(set(codes)) != len(codes)):
        raise ValueError()
    return sorted(codes)


def _proposal(content, priority, frames, candidate_time, expected_schema=SCHEMA):
    def unique(pairs):
        result = {}
        for key, value in pairs:
            if key in result:
                raise ValueError()
            result[key] = value
        return result
    if type(content) is not str or len(content.encode()) > 16384:
        raise ValueError()
    raw = json.loads(content, object_pairs_hook=unique)
    if (type(raw) is not dict or set(raw) != {
            "schema_version", "candidate_timestamp", "action", "proposals", "rationale_codes"}
            or raw["schema_version"] != expected_schema
            or core._aware_timestamp(raw["candidate_timestamp"], "candidate_timestamp") != pd.Timestamp(candidate_time)
            or type(raw["proposals"]) is not list):
        raise ValueError()
    reasons = {"batch": _rationale(raw["rationale_codes"]), "proposals": {}}
    proposals = []
    fields = {"symbol", "score", "entry_price", "stop_loss", "target_price", "holding_bars", "rationale_codes"}
    for item in raw["proposals"]:
        if type(item) is not dict or set(item) != fields or type(item["symbol"]) is not str:
            raise ValueError()
        reasons["proposals"][item["symbol"]] = _rationale(item["rationale_codes"])
        proposals.append({k: v for k, v in item.items() if k != "rationale_codes"})
    converted = core._encoded({"schema_version": core.SCHEMA, "action": raw["action"], "proposals": proposals}).decode()
    core._parse(converted, priority, frames)
    return converted, reasons


def _no_trade():
    return core._encoded({"schema_version": core.SCHEMA, "action": "NO_TRADE", "proposals": []}).decode()


def _validate_response_evidence(evidence, request_id, batch_id, payload, priority, frames, output, reviewed=False,
                                signal_only=False):
    valid_category = "validated_provider_proposal" if reviewed else "validated_mock_proposal"
    failed_category = "provider_failure" if reviewed else "mock_provider_failure"
    try:
        if (type(evidence) is not dict or set(evidence) != {
                "request_id", "batch_id", "payload_sha256", "core_response", "category",
                "response_sha256", "rationale", "reservation_consumed"}
                or evidence["request_id"] != request_id or evidence["batch_id"] != batch_id
                or evidence["payload_sha256"] != core._digest(payload)
                or type(evidence["reservation_consumed"]) is not bool
                or evidence["category"] not in {valid_category, "invalid_proposal",
                    failed_category, "provider_budget_exhausted", "reserved_without_result", "account_blocked"}
                or (evidence["response_sha256"] is not None and
                    (type(evidence["response_sha256"]) is not str or not re.fullmatch(r"[0-9a-f]{64}", evidence["response_sha256"])))):
            raise ValueError()
        if evidence["category"] == valid_category:
            parsed = core._parse(evidence["core_response"], priority, frames)
            if evidence["response_sha256"] is None:
                raise ValueError()
            rationale = evidence["rationale"]
            if (type(rationale) is not dict or set(rationale) != {"batch", "proposals"}
                    or type(rationale["proposals"]) is not dict
                    or set(rationale["proposals"]) != {p["symbol"] for p in parsed["proposals"]}):
                raise ValueError()
            _rationale(rationale["batch"])
            for codes in rationale["proposals"].values():
                _rationale(codes)
        elif evidence["core_response"] != (None if signal_only else _no_trade()) or evidence["rationale"] is not None:
            raise ValueError()
        consumed = evidence["category"] in {
            valid_category, "invalid_proposal", failed_category, "reserved_without_result"}
        if consumed != evidence["reservation_consumed"]:
            raise ValueError()
        if consumed:
            ledger = output / "request_budget.jsonl"
            ids = {json.loads(line)["request_id"] for line in ledger.read_text().splitlines() if line.strip()}
            if request_id not in ids:
                raise ValueError()
    except Exception:
        raise GuardedBatchError("invalid_request_evidence") from None


def _verify_frozen(paths):
    if any(hashlib.sha256(p.read_bytes()).hexdigest() != p.stem for p in paths):
        raise GuardedBatchError("frozen_input_changed")


def _account(output, config):
    directory = output / "paper_account"
    directory.mkdir(exist_ok=True)
    if not (directory / "ai_led_offline_state.json").exists() and any((output / "batches").glob("*.json")):
        raise GuardedBatchError("committed_account_missing")
    return core._load_state(directory, config["account"])


def summarize_guarded(output_dir):
    """Pure summary under the account lock; no transport or budget operations."""
    output = Path(output_dir)
    with _experiment_lock(output):
        config = _read(output / MANIFEST)
        state = _account(output, config)
        completed = [e for e in state["events"] if e["type"] == "SETTLED"]
        position = state["position"]
        reserved = position.get("reserved_cash", state["cash"]) if position else 0
        return {"evidence_kind": config["evidence_kind"], "initial_capital": config["account"]["initial_capital"],
                "accepted_trades": sum(e["type"] == "ENTRY_ACCEPTED" for e in state["events"]),
                "rejected_entries": sum(e["type"] == "ENTRY_REJECTED" for e in state["events"]),
                "completed_trades": len(completed), "pending_entries": int(bool(position and position["status"] == "PENDING")),
                "open_positions": int(bool(position and position["status"] == "OPEN")),
                "gross_pnl": sum(e["gross_pnl"] for e in completed),
                "costs": sum(e["costs"]["total_cost"] for e in completed),
                "net_pnl": sum(e["net_pnl"] for e in completed), "account_balance": state["balance"],
                "available_cash": max(0, state["cash"] - reserved), "usable_funding": state["cash"],
                "trade_placement_enabled": False,
                "external_api_requests": (len((output / "transport_sent.jsonl").read_text().splitlines())
                                          if (output / "transport_sent.jsonl").exists() else 0),
                "warnings": [("Prospective paper evidence; no proven profitability."
                              if config.get("network_policy") else "Mock execution validation only; not AI predictive performance or profitability."),
                             "Sample size is insufficient for predictive-performance conclusions.",
                             "Stop gaps and execution uncertainty can exceed estimated risk."]}


def _finish(output, statuses, provider, config, calendar, now):
    reviewed = config.get("network_policy") is not None
    priority = config["account"]["instrument_priority"]
    minimum = config["account"]["min_recent_turnover"]
    try:
        frames, snapshots = _validated_batch(statuses, priority, now, minimum, calendar)
    except GuardedBatchError:
        declared = {}
        for symbol in priority:
            item = statuses.get(symbol) if type(statuses) is dict else None
            status = item.get("status") if type(item) is dict else None
            declared[symbol] = (status if type(status) is str and status in {"READY", "FAILED"}
                                else "MISSING" if item is None else "INVALID")
        failure = {"classification": "GUARDED_BATCH_VALIDATION_FAILURE", "universe": priority,
                   "attempted_at": now.isoformat(), "instrument_statuses": declared,
                   "reason_code": "incomplete_or_invalid_batch", "provider_request_attempted": False,
                   "trade_placement_enabled": False}
        failure["failure_id"] = core._digest(failure)
        AppendOnlyJsonl(output / "batch_failures.jsonl", "failure_id").append(failure)
        raise
    candidate = frames[priority[0]].iloc[-1].timestamp.isoformat()
    batch_id = core._digest({"candidate_timestamp": candidate, "universe": priority})
    market_hash = core._digest(snapshots)
    paths = _freeze(output, snapshots)
    event_path = output / "batches" / (batch_id + ".json")
    if event_path.exists():
        event = _read(event_path)
        if event["market_sha256"] != market_hash:
            raise GuardedBatchError("same_timestamp_market_changed")
        result = {**event["result"], "idempotent_replay": True, "mock_provider_calls": 0}
        if reviewed:
            result["provider_request_attempted"] = False
        return result
    intent_path = output / "intents" / (batch_id + ".json")
    if intent_path.exists():
        intent = _read(intent_path)
        if intent["market_sha256"] != market_hash:
            raise GuardedBatchError("same_timestamp_market_changed")
        decision_time = core._aware_timestamp(intent["decision_time"], "decision_time")
    else:
        decision_time = now
        intent = {"batch_id": batch_id, "candidate_timestamp": candidate, "decision_time": now.isoformat(),
                  "market_sha256": market_hash, "universe": priority,
                  "validation": {s: {"status": "READY", "input_sha256": snapshots[s]["source_sha256"],
                                     "observed_at": snapshots[s]["observed_at"]} for s in priority}}
        if reviewed:
            intent.update(provider_id="deepseek", model_id=config["account"]["model_id"],
                          evidence_kind=config["evidence_kind"])
        _write(intent_path, intent)
    # Reconstruct the exact core inputs from frozen copies, eliminating mutable-file
    # races between the provider's data view and the downstream account decision.
    frozen_frames, frozen = core._batch(paths, priority, decision_time, minimum, calendar)
    request = {"schema_version": core.SCHEMA, "candidate_timestamp": candidate,
               "decision_time": decision_time.isoformat(), "instrument_priority": priority,
               "market": {s: frozen[s] for s in priority}}
    existing_request = reviewed and any(_read(p)["batch_id"] == batch_id for p in (output / "requests").glob("*.json"))
    if existing_request:
        from app.research.ai_led_recovery import recorded_payload
        payload, _ = recorded_payload(output, intent, config, frozen_frames, frozen)
    else:
        if reviewed and config["network_policy"]["include_quotes"]:
            request["quotes"] = provider.market_context()
        prompt = PROMPT.replace(SCHEMA, REVIEWED_SCHEMA) if reviewed else PROMPT
        payload = {"system_prompt": prompt, "user_payload": core._encoded(request).decode()}
    request_id = core._digest({"model": config["account"]["model_id"], "payload": payload})
    _write(output / "requests" / (request_id + ".json"),
           {"request_id": request_id, "batch_id": batch_id, "payload": payload})
    cache_path = output / "responses" / (request_id + ".json")
    state = _account(output, config)
    preview = copy.deepcopy(state)
    prior = next((d for d in state["decisions"] if d["decision_id"] == batch_id), None)
    if prior is None:
        if state["last_time"] is not None and (decision_time <= pd.Timestamp(state["last_time"])
                or pd.Timestamp(candidate) <= pd.Timestamp(state["decisions"][-1]["candidate_timestamp"])):
            raise GuardedBatchError("account_clock_regression")
        core._advance(preview, frozen_frames, decision_time, calendar)
    allowed, _ = core.check_trade_allowed(core.RiskConfig(capital=config["account"]["initial_capital"]),
                                         core._risk_state(preview, decision_time.date()))
    called = 0
    if cache_path.exists():
        evidence = _read(cache_path)
    elif prior is not None:
        raise GuardedBatchError("committed_account_missing_request_evidence")
    else:
        evidence = {"request_id": request_id, "batch_id": batch_id, "payload_sha256": core._digest(payload),
                    "core_response": _no_trade(), "category": "account_blocked",
                    "response_sha256": None, "rationale": None, "reservation_consumed": False}
        if allowed and preview["cash"] > 0:
            # Full evidence/config/budget checks repeated at the request boundary.
            _preflight(output, priority=priority, capital=config["account"]["initial_capital"],
                       model=config["account"]["model_id"], provider_cap=config["provider_cap"],
                       market_data_cap=config["market_data_cap"], min_turnover=minimum, now=now,
                       network_policy=config.get("network_policy"))
            budget = PersistentRequestBudget(output / "request_budget.jsonl", config["provider_cap"])
            try:
                reserved = budget.reserve(request_id)
            except RuntimeError:
                reserved = False
                evidence["category"] = "provider_budget_exhausted"
            else:
                if not reserved:
                    evidence.update(category="reserved_without_result", reservation_consumed=True)
            if reserved:
                evidence.update(category="provider_failure" if reviewed else "mock_provider_failure", reservation_consumed=True)
                called = 1
                try:
                    result = provider.complete(payload)
                    if (type(result) is not ProviderResult or result.model_id != config["account"]["model_id"]
                            or result.requested_model_id != config["account"]["model_id"]
                            or (reviewed and result.provider_id != "deepseek")
                            or type(result.content) is not str or len(result.content.encode()) > 16384):
                        raise ValueError()
                    evidence["response_sha256"] = hashlib.sha256(result.content.encode()).hexdigest()
                    evidence["category"] = "invalid_proposal"
                    converted, rationale = _proposal(result.content, priority, frozen_frames, candidate,
                                                     REVIEWED_SCHEMA if reviewed else SCHEMA)
                    evidence.update(core_response=converted, rationale=rationale,
                                    category="validated_provider_proposal" if reviewed else "validated_mock_proposal")
                except Exception:
                    # Never preserve original exception text, raw bodies or free-text rationale.
                    pass
        _write(cache_path, evidence)
        if reviewed:
            from app.research.ai_led_recovery import seal_response
            seal_response(output, config, request_id, evidence)
    _validate_response_evidence(evidence, request_id, batch_id, payload, priority, frozen_frames, output, reviewed)
    if reviewed:
        from app.research.ai_led_recovery import verify_response_seal
        verify_response_seal(output, config, request_id, evidence, payload, batch_id)
    _verify_frozen(paths)
    saved = core.SavedResponseProvider(evidence["core_response"], config["account"]["model_id"])
    entry_authorized = True
    if reviewed:
        from app.research.ai_led_reviewed import _utc_now
        entry_authorized = core._aware_timestamp(_utc_now(), "entry clock") < decision_time.floor("5min") + pd.Timedelta(minutes=5)
    result = core.run_offline_batch(paths, output_dir=output / "paper_account", instrument_priority=priority,
                                    capital=config["account"]["initial_capital"], now=decision_time,
                                    provider=saved, model=config["account"]["model_id"],
                                    min_recent_turnover=minimum, calendar=calendar, entry_authorized=entry_authorized)
    result.update(batch_id=batch_id, provider_request_id=request_id, provider_category=evidence["category"],
                  mock_provider_calls=called, external_api_requests=0, evidence_kind=config["evidence_kind"])
    if reviewed:
        result.update(mock_provider_calls=0, provider_request_attempted=bool(called),
                      external_api_requests=len((output / "transport_sent.jsonl").read_text().splitlines())
                      if (output / "transport_sent.jsonl").exists() else 0)
    _write(event_path, {**intent, "provider": evidence, "provider_request": payload, "result": result})
    return result


def _capture_evidence(evidence, output, symbol, request_id, candidate, now, minimum, calendar, reviewed=False):
    try:
        base = {"symbol", "request_id", "status", "category", "reservation_consumed"}
        ready = evidence["status"] == "READY"
        expected = base | ({"input_sha256", "snapshot"} if ready else set())
        if (type(evidence) is not dict or set(evidence) != expected or evidence["symbol"] != symbol
                or evidence["request_id"] != request_id or type(evidence["reservation_consumed"]) is not bool):
            raise ValueError()
        if ready:
            if (evidence["category"] != ("validated_upstox_capture" if reviewed else "validated_mock_capture")
                    or type(evidence["input_sha256"]) is not str
                    or not re.fullmatch(r"[0-9a-f]{64}", evidence["input_sha256"])):
                raise ValueError()
            path = (output / evidence["snapshot"]).resolve()
            if path.parent != (output / "inputs").resolve() or not re.fullmatch(r"[0-9a-f]{64}\.json", path.name):
                raise ValueError()
            _verify_frozen([path])
            _, snapshots = _validated_batch({symbol: {"status": "READY", "snapshot": path}},
                                             [symbol], pd.Timestamp(now), minimum, calendar)
            if pd.Timestamp(snapshots[symbol]["candles"][-1]["timestamp"]) != candidate:
                raise ValueError()
        elif (evidence["status"] != "FAILED" or evidence["category"] not in {
                "market_data_budget_exhausted", "reserved_without_result",
                "upstox_failure" if reviewed else "mock_market_data_failure"}):
            raise ValueError()
        consumed = evidence["category"] != "market_data_budget_exhausted"
        if consumed != evidence["reservation_consumed"]:
            raise ValueError()
        if consumed:
            ledger = output / "market_data_request_budget.jsonl"
            if request_id not in {json.loads(line)["request_id"] for line in ledger.read_text().splitlines() if line.strip()}:
                raise ValueError()
    except Exception:
        raise GuardedBatchError("invalid_capture_evidence") from None


def run_mock_batch(statuses, *, output_dir, instrument_priority, capital, now, provider,
                   provider_cap, market_data_cap=0, model="deepseek-flash", min_turnover=100000.0):
    if type(provider) is not MockDecisionProvider or provider.model_id != model:
        raise GuardedBatchError("exact_mock_provider_required")
    output = Path(output_dir)
    with _experiment_lock(output):
        config, calendar = _preflight(output, priority=instrument_priority, capital=capital, model=model,
                                     provider_cap=provider_cap, market_data_cap=market_data_cap,
                                     min_turnover=min_turnover, now=now)
        return _finish(output, statuses, provider, config, calendar, core._aware_timestamp(now, "now"))


def capture_mock_batch(client, *, candidate_timestamp, output_dir, instrument_priority, capital,
                       now, provider, provider_cap, market_data_cap, model="deepseek-flash", min_turnover=100000.0):
    """Simulate an Upstox capture batch with persistent per-instrument reservations."""
    if (type(client) is not MockMarketDataClient or type(provider) is not MockDecisionProvider
            or provider.model_id != model):
        raise GuardedBatchError("exact_mock_transports_required")
    return _capture_batch(client, candidate_timestamp=candidate_timestamp, output_dir=output_dir,
                          instrument_priority=instrument_priority, capital=capital, now=now,
                          provider=provider, provider_cap=provider_cap, market_data_cap=market_data_cap,
                          model=model, min_turnover=min_turnover)


def _capture_batch(client, *, candidate_timestamp, output_dir, instrument_priority, capital,
                   now, provider, provider_cap, market_data_cap, model="deepseek-flash",
                   min_turnover=100000.0, network_policy=None):
    reviewed = network_policy is not None
    def clock():
        if reviewed:
            from app.research.ai_led_reviewed import _utc_now
            return core._aware_timestamp(_utc_now(), "request_clock")
        return core._aware_timestamp(now, "now")
    output = Path(output_dir)
    options = dict(candidate_timestamp=candidate_timestamp, output=output,
                   instrument_priority=instrument_priority, capital=capital, provider=provider,
                   provider_cap=provider_cap, market_data_cap=market_data_cap, model=model,
                   min_turnover=min_turnover, network_policy=network_policy, clock=clock)
    with _experiment_lock(output):
        if reviewed:
            from app.research.ai_led_reviewed import _binding_scope
            with _binding_scope(client, provider):
                client._preflight()
                return _capture_unlocked(client, **options)
        return _capture_unlocked(client, **options)


def _capture_unlocked(client, *, candidate_timestamp, output, instrument_priority, capital,
                      provider, provider_cap, market_data_cap, model, min_turnover, network_policy, clock):
    reviewed = network_policy is not None
    config, calendar = _preflight(output, priority=instrument_priority, capital=capital, model=model,
                                 provider_cap=provider_cap, market_data_cap=market_data_cap,
                                 min_turnover=min_turnover, now=clock(), network_policy=network_policy)
    try:
        candidate = core._aware_timestamp(candidate_timestamp, "candidate_timestamp")
        calendar.candle_end(candidate)
        if not 300 <= (clock() - candidate).total_seconds() <= 900:
            raise ValueError()
    except Exception:
        raise GuardedBatchError("invalid_capture_timestamp") from None
    statuses = {}
    for symbol in instrument_priority:
        _preflight(output, priority=instrument_priority, capital=capital, model=model,
                   provider_cap=provider_cap, market_data_cap=market_data_cap,
                   min_turnover=min_turnover, now=clock(), network_policy=network_policy)
        request_id = core._digest({"candidate_timestamp": candidate.isoformat(), "symbol": symbol,
                                   "universe": instrument_priority, "kind": "UPSTOX_CAPTURE" if reviewed else "MOCK_UPSTOX_CAPTURE"})
        cache = output / "captures" / (request_id + ".json")
        if cache.exists():
            evidence = _read(cache)
        else:
            budget = PersistentRequestBudget(output / "market_data_request_budget.jsonl", market_data_cap)
            evidence = {"symbol": symbol, "request_id": request_id, "status": "FAILED",
                        "category": "market_data_budget_exhausted", "reservation_consumed": False}
            try:
                reserved = budget.reserve(request_id)
            except RuntimeError:
                reserved = False
            else:
                if not reserved:
                    evidence.update(category="reserved_without_result", reservation_consumed=True)
            if reserved:
                evidence.update(category="upstox_failure" if reviewed else "mock_market_data_failure", reservation_consumed=True)
                try:
                    source = client.fetch(symbol)
                    one = {symbol: {"status": "READY", "snapshot": source}}
                    _, validated = _validated_batch(one, [symbol], clock(), min_turnover, calendar)
                    if pd.Timestamp(validated[symbol]["candles"][-1]["timestamp"]) != candidate:
                        raise ValueError()
                    paths = _freeze(output, validated)
                    evidence.update(status="READY", category="validated_upstox_capture" if reviewed else "validated_mock_capture",
                                    input_sha256=validated[symbol]["source_sha256"], snapshot=str(paths[0].relative_to(output)))
                except Exception:
                    pass
            _write(cache, evidence)
        _capture_evidence(evidence, output, symbol, request_id, candidate, clock(), min_turnover, calendar, reviewed)
        if evidence["status"] == "READY":
            statuses[symbol] = {"status": "READY", "snapshot": output / evidence["snapshot"]}
        else:
            statuses[symbol] = {"status": "FAILED"}
    return _finish(output, statuses, provider, config, calendar, clock())
