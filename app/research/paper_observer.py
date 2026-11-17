"""Read-only safe publications. No engine, configuration, transport or write API."""
import ast
from datetime import datetime, timezone
import json
import math
from pathlib import Path
import re

SCHEMA = "scheduled-paper-observer-v1"
TRANSPORT_SCHEMA = "scheduled-paper-observer-v2"
TRANSPORT_ERRORS = {"NETWORK_DISABLED", "PROVIDER_DISABLED", "PRODUCTION_START_DISABLED", "TRANSPORT_REVIEW_REQUIRED",
    "CALENDAR_UNVERIFIED", "MARKET_CLOSED", "CONFIG_INVALID", "CREDENTIALS_INVALID", "BUDGET_EXHAUSTED",
    "OBSERVATION_MISSING", "NO_VALID_PROPOSAL", "TRANSPORT_FAILURE", "REQUEST_INCOMPLETE", "RESPONSE_STALE",
    "RECOVERY_REQUIRED", "SOURCE_IDENTITY_MISMATCH", "QUOTE_INVALID", "CANDLE_INVALID", "PROVIDER_INVALID",
    "ENTRY_WINDOW_CLOSED", "REQUEST_CONFLICT"}
STATUSES = {"RUNNING", "NO_GO", "NO_SESSION", "COMPLETED", "NO_OPPORTUNITY",
            "RECOVERED_AND_COMPLETED", "FAILED_CLOSED", "INTERRUPTED"}
PHASES = {"STARTUP", "PREFLIGHT", "RECOVERY", "PROPOSAL", "OBSERVATION", "STOPPED"}
REASONS = {"V2_TRANSPORT_NOT_REVIEWED", "EXPERIMENT_NOT_AUTHORIZED", "MODE_NOT_PAPER",
           "CONFIG_INVALID", "INSTANCE_BUSY", "CALENDAR_UNVERIFIED", "NO_SESSION",
           "ENTRY_WINDOW_CLOSED", "RECOVERY_REQUIRED", "CONTROL_STATE_INVALID",
           "ENGINE_INVARIANT_FAILED", "NO_VALID_PROPOSAL", "OBSERVATION_MISSING",
           "QUOTE_STALE", "QUOTE_INVALID", "RISK_LIMIT", "EXPECTED_NET_PNL_NON_POSITIVE",
           "INSUFFICIENT_EQUITY", "PROVIDER_FAILURE", "REQUEST_BUDGET_EXHAUSTED",
           "INTERRUPTED", "OBSERVER_UNAVAILABLE", "ENGINE_REJECTED"}
REASONS |= TRANSPORT_ERRORS
SESSION_STATES = {"BEFORE_SESSION", "ENTRY_WINDOW", "NO_NEW_ENTRIES", "AFTER_CLOSE", "NO_SESSION", "UNVERIFIED"}
ENGINE_STATES = {"ENTRY_PENDING", "ENTRY_REJECTED", "OPEN", "OBSERVATION_MISSING", "INVALID_OR_CONFLICTING",
                 "CLOSED_STOP", "CLOSED_AMBIGUOUS_STOP_FIRST", "CLOSED_TARGET", "CLOSED_HORIZON", "CLOSED_SESSION"}
ACCOUNT_FIELDS = {"opening_equity", "equity", "available_cash", "reserved_cash", "open_positions",
                  "pending_proposals", "paper_entries", "planned_risk", "realized_pnl", "gross_pnl", "modeled_costs", "modeled_slippage", "maximum_drawdown"}
CANDIDATE_FIELDS = {"decision_ref", "symbol", "proposal_at", "action", "proposal_score", "rank", "ai_confidence", "current_price", "quote_age_seconds",
                    "target", "stop", "estimated_gross_reward", "estimated_charges", "modeled_slippage", "modeled_net_reward",
                    "risk_amount", "reward_risk", "available_equity", "required_capital", "outcome", "reason_code"}


def timestamp(value):
    stamp = datetime.fromisoformat(value.replace("Z", "+00:00")) if type(value) is str else value
    if not isinstance(stamp, datetime) or stamp.tzinfo is None or stamp.utcoffset() is None:
        raise ValueError("explicit clock required")
    return stamp


def unique_fields(pairs):
    value = {}
    for key, item in pairs:
        if key in value:
            raise ValueError("duplicate field")
        value[key] = item
    return value


def instruments():
    """Read the existing constant without importing its transport dependencies."""
    tree = ast.parse((Path(__file__).resolve().parents[2] / "strategy_backtest.py").read_text(encoding="utf-8"))
    for node in tree.body:
        if isinstance(node, ast.Assign) and any(isinstance(t, ast.Name) and t.id == "STOCKS" for t in node.targets):
            result = ast.literal_eval(node.value)
            if type(result) is dict and all(type(k) is type(v) is str for k, v in result.items()):
                return result
    raise ValueError("instrument registry unavailable")


def _keys(value, fields):
    if type(value) is not dict or set(value) != fields:
        raise ValueError("invalid observer schema")


def _number(value):
    if value is not None and (type(value) not in (int, float) or not math.isfinite(value)):
        raise ValueError("invalid observer number")


def _time_or_none(value):
    if value is not None:
        timestamp(value)


def validate_transport_health(value):
    _keys(value, {"market_data_enabled", "provider_enabled", "production_start_enabled", "last_quote_at",
                  "last_candle_at", "last_provider_at", "market_data_requests", "provider_requests", "status", "last_error"})
    if any(type(value[k]) is not bool for k in ("market_data_enabled", "provider_enabled", "production_start_enabled")):
        raise ValueError("invalid transport permission")
    for key in ("last_quote_at", "last_candle_at", "last_provider_at"):
        _time_or_none(value[key])
    if any(type(value[k]) is not int or value[k] < 0 for k in ("market_data_requests", "provider_requests")):
        raise ValueError("invalid transport request count")
    if value["status"] not in {"DISABLED", "READY", "FAILED"} or (value["last_error"] is not None and value["last_error"] not in TRANSPORT_ERRORS):
        raise ValueError("invalid transport status")
    return value


def validate_snapshot(value):
    fields = {"schema_version", "mode", "run_id", "attempt_id", "previous_attempt_id", "trading_date", "started_at", "updated_at", "pid",
              "simulation_only", "status", "phase", "reason_codes", "session", "health", "activity", "account", "candidates"}
    transport = type(value) is dict and value.get("schema_version") == TRANSPORT_SCHEMA
    _keys(value, fields | ({"transport"} if transport else set()))
    if (value["schema_version"] not in {SCHEMA, TRANSPORT_SCHEMA} or value["mode"] != "FORWARD_PAPER"
            or value["status"] not in STATUSES or value["phase"] not in PHASES
            or type(value["simulation_only"]) is not bool or type(value["pid"]) is not int or value["pid"] <= 0
            or type(value["run_id"]) is not str or re.fullmatch(r"\d{4}-\d{2}-\d{2}-[0-9a-f]{16}", value["run_id"]) is None):
        raise ValueError("invalid observer identity")
    day = datetime.strptime(value["trading_date"], "%Y-%m-%d").date().isoformat()
    for field in ("attempt_id", "previous_attempt_id"):
        item = value[field]
        if item is None and field == "previous_attempt_id":
            continue
        if type(item) is not str or re.fullmatch(r"[0-9a-f]{32}", item) is None:
            raise ValueError("invalid attempt identity")
    if not value["run_id"].startswith(day + "-"):
        raise ValueError("observer date mismatch")
    timestamp(value["started_at"]); timestamp(value["updated_at"])
    if type(value["reason_codes"]) is not list or any(r not in REASONS for r in value["reason_codes"]):
        raise ValueError("invalid observer reason")
    _keys(value["session"], {"state", "start", "end", "entry_cutoff", "next_cutoff"})
    if value["session"]["state"] not in SESSION_STATES:
        raise ValueError("invalid session")
    for field in ("start", "end", "entry_cutoff", "next_cutoff"):
        _time_or_none(value["session"][field])
    _keys(value["health"], {"reported_alive", "last_loop_at", "shutdown_state"})
    if type(value["health"]["reported_alive"]) is not bool or value["health"]["shutdown_state"] not in {"RUNNING", "STOPPED"}:
        raise ValueError("invalid health")
    timestamp(value["health"]["last_loop_at"])
    if transport:
        validate_transport_health(value["transport"])
    _keys(value["activity"], {"last_market_data_update", "last_provider_activity", "market_data_requests", "provider_requests"})
    for field in ("last_market_data_update", "last_provider_activity"):
        _time_or_none(value["activity"][field])
    for field in ("market_data_requests", "provider_requests"):
        if value["activity"][field] is not None and (type(value["activity"][field]) is not int or value["activity"][field] < 0):
            raise ValueError("invalid request count")
    _keys(value["account"], ACCOUNT_FIELDS)
    for number in value["account"].values(): _number(number)
    if type(value["candidates"]) is not list or len(value["candidates"]) > 100:
        raise ValueError("invalid candidates")
    stocks = instruments()
    for candidate in value["candidates"]:
        _keys(candidate, CANDIDATE_FIELDS)
        if (candidate["symbol"] not in stocks or candidate["outcome"] not in ENGINE_STATES or candidate["action"] != "BUY"
                or candidate["reason_code"] is not None and candidate["reason_code"] not in REASONS
                or type(candidate["decision_ref"]) is not str
                or re.fullmatch(r"[0-9a-f]{64}", candidate["decision_ref"]) is None):
            raise ValueError("invalid candidate identity")
        timestamp(candidate["proposal_at"])
        for field in CANDIDATE_FIELDS - {"symbol", "outcome", "reason_code", "decision_ref", "proposal_at", "action"}:
            _number(candidate[field])
    return value


def _bounded_bytes(path):
    with Path(path).open("rb") as stream:
        raw = stream.read(1024 * 1024 + 1)
    if len(raw) > 1024 * 1024:
        raise ValueError("observer size exceeded")
    return raw


def read_snapshot(path, *, as_of=None, max_age_seconds=120):
    """No directory creation, locks, recovery calls or repair, even on failure."""
    try:
        if type(max_age_seconds) not in (int, float) or not 0 < max_age_seconds <= 3600:
            raise ValueError("invalid age policy")
        raw = _bounded_bytes(path)
        value = validate_snapshot(json.loads(raw, object_pairs_hook=unique_fields))
        now = timestamp(as_of or datetime.now(timezone.utc))
        age = (now - timestamp(value["updated_at"])).total_seconds()
        if age < 0:
            raise ValueError("future observer timestamp")
        stale = age > max_age_seconds
        return {"observer_status": "STALE" if stale else "AVAILABLE", "snapshot": value,
                "heartbeat_age_seconds": age, "process_alive": None,
                "liveness_basis": "LAST_REPORTED_HEARTBEAT_NOT_OS_VERIFIED"}
    except (OSError, ValueError, TypeError, KeyError, OverflowError, RecursionError):
        return {"observer_status": "UNAVAILABLE", "snapshot": None, "process_alive": None,
                "reason_code": "OBSERVER_UNAVAILABLE"}


def read_events(path, *, limit=50):
    try:
        if type(limit) is not int or not 1 <= limit <= 100:
            raise ValueError("invalid event limit")
        raw = _bounded_bytes(path)
        events = []
        for line in raw.splitlines():
            item = json.loads(line, object_pairs_hook=unique_fields)
            _keys(item, {"at", "status", "phase", "reason_codes"})
            timestamp(item["at"])
            if item["status"] not in STATUSES or item["phase"] not in PHASES or type(item["reason_codes"]) is not list or any(r not in REASONS for r in item["reason_codes"]):
                raise ValueError("invalid event")
            events.append(item)
        return {"observer_status": "AVAILABLE", "events": events[-limit:]}
    except (OSError, ValueError, TypeError, KeyError, RecursionError):
        return {"observer_status": "UNAVAILABLE", "events": [], "reason_code": "OBSERVER_UNAVAILABLE"}


def human_summary(result):
    if result.get("snapshot") is None:
        return "Observer unavailable; no trading state was changed."
    value = result["snapshot"]
    return (f"Run {value['run_id']}: {value['status']} ({result['observer_status']})\n"
            f"Session: {value['session']['state']}; phase: {value['phase']}\n"
            f"Paper equity: {value['account']['equity']}; reserved: {value['account']['reserved_cash']}\n"
            f"Reasons: {', '.join(value['reason_codes']) or 'none'}\n"
            f"Synthetic fixture evidence: {value['simulation_only']}; liveness is heartbeat-reported only.")
