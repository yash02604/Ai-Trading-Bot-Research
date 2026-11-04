"""Bounded future candle capture and offline replay of locally asserted transport evidence.

Reviewed-path provenance is not an exchange signature or independent clock proof.
Raw numeric evaluation remains separate and cannot opt itself into this path.
"""
import copy
import hashlib
import json
import re
from pathlib import Path

import pandas as pd

from app.research import ai_led_guarded as guard, ai_led_offline as core, ai_led_reviewed as reviewed
from app.research import ai_signal_only as signal, signal_evaluation as evaluation
from app.research.deepseek_prospective import AppendOnlyJsonl, PersistentRequestBudget
from app.research.prospective_preflight import _evidence
from app.client_attestation import verify_client_attestation, ATTESTATION_SHA256
from app.research import signal_request_budget as request_slots

SCHEMA = "signal-future-observation-v1"
PROVENANCE_STATES = ("REVIEWED_UPSTOX_CAPTURE", "OFFLINE_REPLAY_FROM_REVIEWED_CAPTURE",
                     "UNVERIFIED_CALLER_SUPPLIED", "SYNTHETIC_TEST_DATA", "INVALID_OR_CONFLICTING",
                     "NO_VALIDATED_FUTURE_OBSERVATIONS")
POLICY = {"schema_version": SCHEMA, "purpose": "SIGNAL_OUTCOME_CANDLES_ONLY",
          "schedule": "one_intraday_snapshot_per_valid_prediction_per_configured_instrument",
          "horizon_minutes": 30, "bar_count": 6, "interval_minutes": 5,
          "capture_after_horizon_seconds": 5, "capture_before_horizon_seconds": 300,
          "missing_data_policy": "retain_denominator_no_outcome_no_refund_no_replacement_request",
          "quotes": False, "provider_requests": False, "execution_enabled": False}


def capture_policy():
    return copy.deepcopy(POLICY)


def predictions(binding, config, calendar):
    """Load every immutable intent; verify its original inputs/response/seal/signal."""
    intents = sorted((binding.output / "intents").glob("*.json"))
    if {p.stem for p in (binding.output / "signals").glob("*.json")} - {p.stem for p in intents}:
        raise guard.GuardedBatchError("unknown_saved_prediction")
    records = []
    request_ids = set()
    for path in intents:
        intent = guard._read(path)
        if path.stem != intent["batch_id"]:
            raise guard.GuardedBatchError("prediction_identity_changed")
        request_ids.add(intent["request_id"])
        # Recovery uses original timestamps, never authorizes an SDK send.
        records.append(signal._publish(binding, config, calendar, intent))
    if not records:
        raise guard.GuardedBatchError("no_saved_predictions")
    if ({p.stem for p in (binding.output / "requests").glob("*.json")} != request_ids
            or any(r["request_id"] not in request_ids for r in
                   AppendOnlyJsonl(binding.output / "request_budget.jsonl", "request_id").records())):
        raise guard.GuardedBatchError("prediction_request_inventory_changed")
    return records


def jobs(config, decisions, calendar):
    if config["future_observation_policy"] != POLICY or config["evaluation_policy"] != evaluation.evaluation_policy():
        raise guard.GuardedBatchError("future_policy_changed")
    result = []
    for d in decisions:
        if d["status"] != "VALID_PREDICTION":
            continue
        ready = pd.Timestamp(d["decision_ready_at"])
        start = ready.floor("5min") + pd.Timedelta(minutes=5)
        end = start + pd.Timedelta(minutes=30)
        if not any(a <= ready < end <= b for a, b in calendar.intervals(ready)):
            raise guard.GuardedBatchError("future_horizon_outside_session")
        for symbol in d["instrument_priority"]:
            job = {"schema_version": SCHEMA, "experiment_sha256": core._digest(config),
                   "prediction_id": d["decision_id"], "prediction_sha256": core._digest(d),
                   "symbol": symbol, "instrument_key": core.STOCKS[symbol], "segment": "NSE_EQ",
                   "purpose": POLICY["purpose"], "window_start": start.isoformat(), "window_end": end.isoformat(),
                   "capture_after": (end + pd.Timedelta(seconds=5)).isoformat(),
                   "capture_before": (end + pd.Timedelta(seconds=300)).isoformat()}
            job["request_id"] = core._digest(job)
            result.append(job)
    return result


def future_preflight(binding, now):
    config, calendar = signal.preflight(binding)
    current, _ = _evidence(now)
    checked = evaluate_experiment(binding, config, calendar, as_of=now)
    if "seal_ledger_state" in checked:
        raise guard.GuardedBatchError("future_seal_partial_publication")
    expected = jobs(config, predictions(binding, config, calendar), calendar)
    if binding.future_job not in expected:
        raise guard.GuardedBatchError("future_job_not_predeclared")
    job = binding.future_job
    if not pd.Timestamp(job["capture_after"]) <= pd.Timestamp(now) < pd.Timestamp(job["capture_before"]):
        raise guard.GuardedBatchError("outside_future_capture_schedule")
    if current.identity != calendar.identity:
        raise guard.GuardedBatchError("future_calendar_changed")
    return config, calendar


def _payload(response, symbol, received):
    """Retain canonical decoded JSON, accepting only safe candle/identity/time fields.

    Timestamp strings are preserved literally. No quote/book freshness is inferred.
    """
    if (type(response) is not dict or response.get("status") != "success"
            or set(response) - {"status", "data", "timestamp", "currentTs"}):
        raise ValueError("future_response_schema")
    data = response["data"]
    if type(data) is not dict or set(data) - {"candles", "instrument_key", "instrument_token", "timestamp", "currentTs"}:
        raise ValueError("future_response_data")
    for key in ("instrument_key", "instrument_token"):
        if key in data and data[key] != core.STOCKS[symbol]:
            raise ValueError("future_response_instrument")
    stamps = []
    for location, obj in (("response", response), ("data", data)):
        for key in ("timestamp", "currentTs"):
            if key in obj:
                value = obj[key]
                if type(value) is int:
                    pd.Timestamp(value, unit="ms", tz="UTC")
                elif type(value) is str:
                    core._aware_timestamp(value, "provider timestamp")
                else:
                    raise ValueError("future_provider_timestamp")
                stamps.append({"field": location + "." + key, "value": value,
                               "semantics": "PROVIDER_FIELD_UNAUTHENTICATED"})
    rows = data["candles"]
    if type(rows) is not list or not rows:
        raise ValueError("future_empty_candles")
    for row in rows:
        if (type(row) is not list or len(row) not in (6, 7)
                or type(row[0]) is not str
                or core._aware_timestamp(row[0], "candle start") > received
                or any(not core._number(v) for v in row[1:])):
            raise ValueError("future_numeric_candles_required")
    return stamps


def record_response(binding, request_id, response):
    """Only invoked immediately after the existing send/receipt boundary."""
    receipts = _ledger(binding.output, "transport_receipts.jsonl", request_id)
    _payload(response, binding.future_job["symbol"], pd.Timestamp(receipts["received_at_utc"]))
    if receipts["response_sha256"] != core._digest(response):
        raise guard.GuardedBatchError("future_receipt_payload_mismatch")
    guard._write(binding.output / "future_received" / (request_id + ".json"), response)


def _ledger(output, name, request_id):
    rows = [r for r in AppendOnlyJsonl(output / name, "request_id").records() if r["request_id"] == request_id]
    if len(rows) != 1:
        raise guard.GuardedBatchError("future_transport_evidence_missing_or_duplicate")
    return rows[0]


def _capture_record(binding, config, calendar, decision, job, snapshot):
    rid = job["request_id"]
    request_slots.verify_physical_attempt(binding, rid, job["symbol"], "FUTURE_OBSERVATION")
    reservation = _ledger(binding.output, "market_data_request_budget.jsonl", rid)
    sent = _ledger(binding.output, "transport_sent.jsonl", rid)
    receipt = _ledger(binding.output, "transport_receipts.jsonl", rid)
    raw_path = binding.output / "future_received" / (rid + ".json")
    raw = guard._read(raw_path)
    received = pd.Timestamp(receipt["received_at_utc"])
    if (receipt["status"] != "RECEIVED" or sent["kind"] != "upstox_candles"
            or receipt["kind"] != "upstox_candles" or sent["symbol"] != job["symbol"]
            or receipt["response_sha256"] != core._digest(raw)
            or not pd.Timestamp(job["capture_after"]) <= pd.Timestamp(sent["sent_at_utc"]) <= received
            < pd.Timestamp(job["capture_before"])):
        raise guard.GuardedBatchError("future_transport_binding_invalid")
    stamps = _payload(raw, job["symbol"], received)
    guard._verify_frozen([snapshot])
    body = json.loads(snapshot.read_bytes())
    frames, _ = guard._validated_batch({job["symbol"]: {"status": "READY", "snapshot": snapshot}},
        [job["symbol"]], pd.Timestamp(body["observed_at"]), config["account"]["min_recent_turnover"], calendar)
    # Reconstruct the exact existing fetch normalization at its original receipt.
    rows = reviewed.completed_frame(raw["data"]["candles"], pd.Timestamp(body["observed_at"]))
    rows = rows.loc[rows.timestamp <= pd.Timestamp(body["observed_at"]).floor("5min") - pd.Timedelta(minutes=5)]
    normalized = [{"timestamp": r.timestamp.isoformat(), **{k: float(getattr(r, k)) for k in
                   ("open", "high", "low", "close", "volume")}} for r in rows.itertuples(index=False)]
    if (body["source"] != "upstox-v3-intraday-readonly" or body["symbol"] != job["symbol"]
            or body["instrument_key"] != job["instrument_key"] or body["candles"] != normalized
            or not received <= pd.Timestamp(body["observed_at"]) < pd.Timestamp(job["capture_before"])):
        raise guard.GuardedBatchError("future_normalization_changed")
    outcome = evaluation.evaluate_signal(decision, {job["symbol"]: body}, as_of=body["observed_at"], calendar=calendar)
    status = next(r["status"] for r in outcome["rows"] if r["symbol"] == job["symbol"])
    client = verify_client_attestation()
    return {"identity": job, "schema_version": SCHEMA, "validation_status": status,
            "provenance": "REVIEWED_UPSTOX_CAPTURE" if status == "OBSERVED_PRICE_MOVEMENT" else "INVALID_OR_CONFLICTING",
            "source": "upstox-v3-intraday-readonly", "client_attestation_version": client["attestation_version"],
            "client_attestation_sha256": ATTESTATION_SHA256, "calendar_identity": calendar.identity,
            "reservation": reservation, "request_sent_at": sent["sent_at_utc"],
            "response_received_at": receipt["received_at_utc"], "local_receipt_at": body["observed_at"],
            "provider_timestamp_fields": stamps, "provider_candle_start_labels": [r[0] for r in raw["data"]["candles"]],
            "normalized_payload_sha256": core._digest(raw), "saved_payload_artifact_sha256": hashlib.sha256(raw_path.read_bytes()).hexdigest(),
            "snapshot_sha256": snapshot.stem, "body": body,
            "validation": {"session": "VERIFIED_NSE_INTERVAL", "freshness": "EXISTING_900_SECOND_LIMIT",
                           "required_completed_bars": 6, "interval_minutes": 5, "depth_freshness": "UNKNOWN"},
            "execution_enabled": False, "trade_placement_enabled": False}


def _seal(output, job, record):
    return {"request_id": job["request_id"], "experiment_sha256": job["experiment_sha256"],
            "observation_sha256": core._digest(record), "saved_artifact_sha256": hashlib.sha256(
                (output / "future_observations" / (job["request_id"] + ".json")).read_bytes()).hexdigest()}


def _unique_seal_fields(pairs):
    record = {}
    for key, value in pairs:
        if key in record:
            raise guard.GuardedBatchError("future_seal_ledger_corrupt")
        record[key] = value
    return record


def _read_future_seals(output):
    """Read complete seals without repairing an interrupted final append.

    Only an unterminated final object with an EOF/unterminated-string parse error
    is a possible torn append. Committed or internal malformed lines fail closed.
    The tail's bytes remain on disk and are explicitly identified in the result.
    """
    path = output / "future_observation_seals.jsonl"
    data = path.read_bytes() if path.exists() else b""
    lines = data.splitlines(keepends=True)
    records, seen, offset = [], set(), 0
    for index, line in enumerate(lines):
        try:
            text = line.decode("utf-8")
            record = json.loads(text, object_pairs_hook=_unique_seal_fields)
        except UnicodeDecodeError:
            raise guard.GuardedBatchError("future_seal_ledger_corrupt") from None
        except json.JSONDecodeError as exc:
            if (index == len(lines) - 1 and not line.endswith((b"\n", b"\r"))
                    and text.lstrip().startswith("{")
                    and (exc.pos >= len(text.rstrip()) or exc.msg.startswith("Unterminated string"))):
                return records, {"status": "UNRECOVERABLE_PARTIAL_PUBLICATION",
                    "reason": "INCOMPLETE_UNTERMINATED_FINAL_JSON_RECORD", "line_number": index + 1,
                    "byte_offset": offset, "byte_count": len(line), "sha256": hashlib.sha256(line).hexdigest(),
                    "complete_record_count": len(records)}
            raise guard.GuardedBatchError("future_seal_ledger_corrupt") from None
        if (type(record) is not dict or set(record) != {
                "request_id", "experiment_sha256", "observation_sha256", "saved_artifact_sha256"}
                or any(type(value) is not str or not re.fullmatch(r"[0-9a-f]{64}", value)
                       for value in record.values()) or record["request_id"] in seen):
            raise guard.GuardedBatchError("future_seal_ledger_corrupt")
        seen.add(record["request_id"])
        records.append(record)
        offset += len(line)
    return records, None


def evaluate_experiment(binding, config, calendar, *, as_of):
    """No caller-selected predictions, observations, or outcome objects accepted."""
    decisions = predictions(binding, config, calendar)
    expected = jobs(config, decisions, calendar)
    by_id = {j["request_id"]: j for j in expected}
    artifacts = list((binding.output / "future_observations").glob("*.json"))
    if any(p.stem not in by_id for p in artifacts):
        raise guard.GuardedBatchError("unknown_future_observation")
    bodies = {d["decision_id"]: {} for d in decisions}
    bindings = []
    seals, partial_seal = _read_future_seals(binding.output)
    artifact_ids = {p.stem for p in artifacts}
    for seal in seals:
        job = by_id.get(seal["request_id"])
        if (job is None or seal["request_id"] not in artifact_ids
                or seal["experiment_sha256"] != job["experiment_sha256"]):
            raise guard.GuardedBatchError("future_seal_identity_invalid")
    for path in artifacts:
        job = by_id[path.stem]
        record = guard._read(path)
        if record["identity"] != job:
            raise guard.GuardedBatchError("future_observation_identity_changed")
        matching_seals = [s for s in seals if s["request_id"] == path.stem]
        if not matching_seals:
            _ledger(binding.output, "market_data_request_budget.jsonl", job["request_id"])
            bindings.append({"request_id": path.stem, "observation_sha256": core._digest(record),
                             "validation_status": "UNRECOVERABLE_PARTIAL_PUBLICATION",
                             "provenance": "INVALID_OR_CONFLICTING"})
            continue  # Never invent a seal or use an unsealed observation as an outcome.
        d = next(d for d in decisions if d["decision_id"] == job["prediction_id"])
        if record["validation_status"] == "CAPTURE_FAILED":
            _ledger(binding.output, "market_data_request_budget.jsonl", job["request_id"])
            expected_record = {"identity": job, "schema_version": SCHEMA, "validation_status": "CAPTURE_FAILED",
                               "provenance": "INVALID_OR_CONFLICTING", "trade_placement_enabled": False}
        else:
            if type(record["snapshot_sha256"]) is not str or not re.fullmatch(r"[0-9a-f]{64}", record["snapshot_sha256"]):
                raise guard.GuardedBatchError("future_snapshot_identity_invalid")
            snapshot = binding.output / "received" / (record["snapshot_sha256"] + ".json")
            expected_record = _capture_record(binding, config, calendar, d, job, snapshot)
        if record != expected_record or matching_seals != [_seal(binding.output, job, record)]:
            raise guard.GuardedBatchError("future_observation_changed_or_unsealed")
        if record["validation_status"] != "CAPTURE_FAILED":
            bodies[job["prediction_id"]][job["symbol"]] = record["body"]
        bindings.append({"request_id": path.stem, "observation_sha256": core._digest(record),
                         "validation_status": record["validation_status"], "provenance": record["provenance"]})
    reserved = {r["request_id"] for r in AppendOnlyJsonl(
        binding.output / "market_data_request_budget.jsonl", "request_id").records()}
    published = {p.stem for p in artifacts}
    for job in expected:
        if job["request_id"] in reserved and job["request_id"] not in published:
            bindings.append({"request_id": job["request_id"], "observation_sha256": None,
                             "validation_status": "UNRECOVERABLE_PARTIAL_CAPTURE",
                             "provenance": "INVALID_OR_CONFLICTING"})
    outcomes = [evaluation.evaluate_signal(d, bodies[d["decision_id"]], as_of=as_of, calendar=calendar) for d in decisions]
    summary = evaluation.summarize_predictions(decisions, outcomes, as_of=as_of)
    summary.update(future_observation_provenance=("OFFLINE_REPLAY_FROM_REVIEWED_CAPTURE" if summary["total_completed_evaluations"]
                                                else "NO_VALIDATED_FUTURE_OBSERVATIONS"),
                   observation_bindings=sorted(bindings, key=lambda b: b["request_id"]),
                   experiment_sha256=core._digest(config), provenance_scope="local guarded path; not independent provider authentication")
    if partial_seal is not None:
        summary["seal_ledger_state"] = partial_seal
    return summary


def run_followup(*, output_dir, instrument_priority, capital, provider_cap, market_data_cap,
                 enable_signal_only=False, capture_future=False, enable_signal_future=False,
                 allow_market_data_requests=False, as_of=None):
    if not capture_future and (enable_signal_future or allow_market_data_requests):
        raise guard.GuardedBatchError("offline_evaluation_cannot_authorize_requests")
    saved = guard._read(Path(output_dir) / signal.MANIFEST)
    binding = reviewed._ReviewedRun(output_dir=output_dir, instrument_priority=list(instrument_priority), capital=capital,
        provider_cap=provider_cap, market_data_cap=market_data_cap, enable_signal_only=enable_signal_only,
        include_quotes=saved["network_policy"]["include_quotes"],
        enable_signal_future=enable_signal_future, allow_market_data_requests=allow_market_data_requests,
        purpose="signal_future" if capture_future else "signal_recovery")
    with core._experiment_lock(binding.output), reviewed._binding_scope(binding, binding):
        config, calendar = signal.preflight(binding)
        # Validate every complete seal and its existing evidence chain before any
        # new reservation or transmission. Reuse the normal evaluator's checks.
        cutoff = reviewed._utc_now() if as_of is None else as_of
        checked = evaluate_experiment(binding, config, calendar, as_of=cutoff)
        if not capture_future or "seal_ledger_state" in checked:
            return checked
        decisions = predictions(binding, config, calendar)
        if capture_future:
            for job in jobs(config, decisions, calendar):
                path = binding.output / "future_observations" / (job["request_id"] + ".json")
                if path.exists():
                    continue
                spent = AppendOnlyJsonl(binding.output / "market_data_request_budget.jsonl", "request_id")
                if job["request_id"] in spent._records:
                    continue  # Explicit unrecoverable state in evaluation; no replacement send.
                binding.future_job = job
                binding._preflight()
                if not request_slots.reserve(binding, job["request_id"], job["symbol"], "FUTURE_OBSERVATION"):
                    raise guard.GuardedBatchError("future_request_spent_without_observation")
                try:
                    snapshot = binding.fetch(job["symbol"])
                    d = next(d for d in decisions if d["decision_id"] == job["prediction_id"])
                    record = _capture_record(binding, config, calendar, d, job, snapshot)
                except Exception:
                    record = {"identity": job, "schema_version": SCHEMA, "validation_status": "CAPTURE_FAILED",
                              "provenance": "INVALID_OR_CONFLICTING", "trade_placement_enabled": False}
                guard._write(path, record)
                AppendOnlyJsonl(binding.output / "future_observation_seals.jsonl", "request_id").append(_seal(binding.output, job, record))
        cutoff = reviewed._utc_now() if as_of is None else as_of
        return evaluate_experiment(binding, config, calendar, as_of=cutoff)
