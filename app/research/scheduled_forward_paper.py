"""Owned paper orchestration; versioned transport starts require explicit readiness.

The original configuration retains its transport block. Version two delegates
only after source/config approval and separate network/start permission checks.
"""
from contextlib import contextmanager
from datetime import datetime, timezone
import hashlib
import json
import math
import os
from pathlib import Path
import threading
import time
import uuid

import pandas as pd

from app.research import paper_observer as observer

PROJECT_ROOT = Path(__file__).resolve().parents[2]
_THREAD_GUARD = threading.Lock()
_THREAD_LOCKS = {}
CONFIG_FIELDS = {"schema_version", "mode", "initial_capital", "instrument_priority", "provider_cap", "market_data_cap"}


class InstanceBusy(RuntimeError):
    pass


def _utc_now():
    return datetime.now(timezone.utc)


def _encoded(value):
    return json.dumps(value, sort_keys=True, separators=(",", ":"), allow_nan=False).encode("utf-8")


def _digest(value):
    return hashlib.sha256(_encoded(value)).hexdigest()


def validate_config(value):
    if type(value) is not dict or value.get("mode") != "FORWARD_PAPER":
        raise ValueError("MODE_NOT_PAPER")
    if value.get("schema_version") == "scheduled-forward-paper-v2":
        from app.research.v2_transport_contract import validate_options
        return validate_options(value)
    if (set(value) != CONFIG_FIELDS or value["schema_version"] != "scheduled-forward-paper-v1"
            or type(value["initial_capital"]) not in (int, float) or not math.isfinite(value["initial_capital"]) or value["initial_capital"] <= 0
            or type(value["instrument_priority"]) is not list or not value["instrument_priority"]
            or any(type(s) is not str or s not in observer.instruments() for s in value["instrument_priority"])
            or len(set(value["instrument_priority"])) != len(value["instrument_priority"])
            or any(type(value[k]) is not int or value[k] < 0 for k in ("provider_cap", "market_data_cap"))):
        raise ValueError("CONFIG_INVALID")
    return json.loads(_encoded(value))


@contextmanager
def ownership(project_root=PROJECT_ROOT):
    """Nonblocking OS ownership, shared across control roots; death releases it.

    The persistent one-byte artifact is not PID authority. This uses the same
    native locking primitives as the existing experiment lock, at a distinct
    project-wide scope so no engine-directory lock is nested on itself.
    """
    key = hashlib.sha256(os.path.normcase(str(Path(project_root).resolve())).encode()).hexdigest()
    with _THREAD_GUARD:
        local = _THREAD_LOCKS.setdefault(key, threading.Lock())
    if not local.acquire(blocking=False):
        raise InstanceBusy("INSTANCE_BUSY")
    try:
        directory = Path(project_root).resolve() / "logs" / "scheduled-ownership"
        directory.mkdir(parents=True, exist_ok=True)
        with (directory / (key + ".lock")).open("a+b") as stream:
            stream.seek(0, os.SEEK_END)
            if stream.tell() == 0:
                stream.write(b"\0"); stream.flush()
            stream.seek(0)
            try:
                if os.name == "nt":
                    import msvcrt
                    msvcrt.locking(stream.fileno(), msvcrt.LK_NBLCK, 1)
                else:
                    import fcntl
                    fcntl.flock(stream.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
            except OSError:
                raise InstanceBusy("INSTANCE_BUSY") from None
            try:
                yield
            finally:
                stream.seek(0)
                if os.name == "nt":
                    msvcrt.locking(stream.fileno(), msvcrt.LK_UNLCK, 1)
                else:
                    fcntl.flock(stream.fileno(), fcntl.LOCK_UN)
    finally:
        local.release()


def session_state(now):
    from app.research.offline_session_calendar import OfflineSessionCalendar
    stamp = pd.Timestamp(observer.timestamp(now)).tz_convert("Asia/Kolkata")
    calendar = OfflineSessionCalendar(PROJECT_ROOT / "data/audits/nse_cm_session_manifest_v3.json")
    intervals = calendar.intervals(stamp)
    result = dict(state="NO_SESSION", start=None, end=None, entry_cutoff=None, next_cutoff=None)
    if not intervals:
        return result
    start, end = next(((a, b) for a, b in intervals if stamp < b), intervals[-1])
    cursor = start
    while cursor < end and calendar.entry_allowed(cursor):
        cursor += pd.Timedelta(minutes=5)
    result.update(start=start.isoformat(), end=end.isoformat(), entry_cutoff=cursor.isoformat())
    if stamp < start:
        result.update(state="BEFORE_SESSION", next_cutoff=start.isoformat())
    elif stamp >= end:
        result.update(state="AFTER_CLOSE", next_cutoff=None)
    elif calendar.entry_allowed(stamp):
        result.update(state="ENTRY_WINDOW", next_cutoff=cursor.isoformat())
    else:
        result.update(state="NO_NEW_ENTRIES", next_cutoff=end.isoformat())
    return result


def preflight(config, now=None):
    """Read-only offline checks, never request permission or engine initialization."""
    result = {"status": "NO_GO", "reason_codes": [], "session": dict(state="UNVERIFIED", start=None, end=None, entry_cutoff=None, next_cutoff=None),
              "mode": "FORWARD_PAPER", "external_requests": 0, "trade_placement_enabled": False,
              "transport_integration": "NOT_REVIEWED", "market_data_permission": False, "provider_permission": False}
    try:
        validate_config(config)
    except (ValueError, TypeError, KeyError) as error:
        result["reason_codes"] = ["MODE_NOT_PAPER" if str(error) == "MODE_NOT_PAPER" else "CONFIG_INVALID"]
        return result
    if config["schema_version"] == "scheduled-forward-paper-v2":
        from app.research.v2_transport_contract import readiness
        return readiness(config, now or _utc_now(), purpose="startup")
    try:
        result["session"] = session_state(now or _utc_now())
        from app.client_attestation import verify_client_attestation
        verify_client_attestation()
    except Exception:
        result["reason_codes"] = ["CALENDAR_UNVERIFIED"]
        return result
    if result["session"]["state"] == "NO_SESSION":
        result.update(status="NO_SESSION", reason_codes=["NO_SESSION"])
    else:
        result["reason_codes"] = ["V2_TRANSPORT_NOT_REVIEWED", "EXPERIMENT_NOT_AUTHORIZED"]
    return result


def _atomic_json(path, value):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    data = _encoded(value)
    stage = path.with_name("." + path.name + "." + uuid.uuid4().hex + ".writing")
    try:
        with stage.open("xb") as stream:
            stream.write(data); stream.flush(); os.fsync(stream.fileno())
        os.replace(stage, path)
    finally:
        if stage.exists(): stage.unlink()


def _identity(config, now, synthetic):
    date = pd.Timestamp(observer.timestamp(now)).tz_convert("Asia/Kolkata").date().isoformat()
    sources = [Path(__file__), Path(observer.__file__), PROJECT_ROOT / "app/research/causal_forward_paper_v2.py"]
    identity = {"schema_version": "scheduled-paper-run-v1", "trading_date": date, "mode": "FORWARD_PAPER",
            "config_identity": _digest(config), "code_identity": _digest({p.name: hashlib.sha256(p.read_bytes()).hexdigest() for p in sources}),
            "simulation_only": synthetic, "run_id": date + "-" + _digest(config)[:16]}
    if config["schema_version"] == "scheduled-forward-paper-v2":
        from app.research.v2_transport_contract import integration_identity
        identity["adapter_identity"] = integration_identity()
        identity["code_identity"] = _digest({"scheduler": identity["code_identity"], "adapter": identity["adapter_identity"]})
    return identity


def _attempt_records(runs):
    """Immutable scheduler journal order, independent of clock/UUID ordering."""
    records = []
    for path in runs.glob("*/attempts/*.begin.json"):
        record = json.loads(path.read_bytes(), object_pairs_hook=observer.unique_fields)
        attempt = record.get("attempt_id")
        if (type(attempt) is not str or len(attempt) != 32 or any(c not in "0123456789abcdef" for c in attempt)
                or path.name != attempt + ".begin.json" or record.get("trading_date") != path.parents[1].name
                or type(record.get("attempt_sequence")) is not int or record["attempt_sequence"] <= 0):
            raise ValueError("CONTROL_STATE_INVALID")
        observer.timestamp(record["started_at"])
        records.append(record)
    records.sort(key=lambda r: r["attempt_sequence"])
    previous = None
    for sequence, record in enumerate(records, 1):
        if (record["attempt_sequence"] != sequence or record.get("previous_attempt_id") != previous
                or record["run_id"] != record["trading_date"] + "-" + record["config_identity"][:16]):
            raise ValueError("CONTROL_STATE_INVALID")
        previous = record["attempt_id"]
    by_id = {record["attempt_id"]: record for record in records}
    if len(by_id) != len(records):
        raise ValueError("CONTROL_STATE_INVALID")
    for path in runs.glob("*/attempts/*.final.json"):
        final = json.loads(path.read_bytes(), object_pairs_hook=observer.unique_fields)
        begin = by_id.get(final.get("attempt_id"))
        if (begin is None or path.name != begin["attempt_id"] + ".final.json"
                or path.parents[1].name != begin["trading_date"]
                or any(final.get(k) != begin[k] for k in ("attempt_sequence", "previous_attempt_id", "run_id", "trading_date", "started_at"))
                or final.get("status") not in observer.STATUSES - {"RUNNING"}):
            raise ValueError("CONTROL_STATE_INVALID")
    return records


def _validate_control_root(root, identity):
    """Read-only validation shared by launcher preflight and owned initialization."""
    root = Path(root)
    if root.is_symlink() or root.is_junction():
        raise ValueError("CONTROL_STATE_INVALID")
    allowed = {".scheduler-control.json", "runs", "engine", "launcher-logs"}
    if "adapter_identity" in identity:
        allowed.add("transport")
    if root.exists() and any(p.name not in allowed or p.is_symlink() or p.is_junction() for p in root.iterdir()):
        raise ValueError("CONTROL_STATE_INVALID")
    marker = root / ".scheduler-control.json"
    expected = {"schema_version": "scheduled-paper-control-v1", "project": _digest(str(PROJECT_ROOT)),
                **{k: identity[k] for k in ("config_identity", "code_identity", "simulation_only")}}
    if "adapter_identity" in identity:
        expected["adapter_identity"] = identity["adapter_identity"]
    if marker.exists():
        if json.loads(marker.read_bytes(), object_pairs_hook=observer.unique_fields) != expected:
            raise ValueError("CONTROL_STATE_INVALID")
    elif root.exists() and any(root.iterdir()):
        raise ValueError("CONTROL_STATE_INVALID")
    runs = root / "runs"
    records = _attempt_records(runs)
    latest = {record["trading_date"]: record for record in records}
    # A mutable/stale daily summary cannot conceal a newer unfinished attempt.
    if runs.exists():
        for directory in runs.iterdir():
            if directory.is_dir() and directory.name != identity["trading_date"]:
                record = latest.get(directory.name)
                final = directory / "attempts" / (record["attempt_id"] + ".final.json") if record else None
                if (final is None or not final.exists()
                        or json.loads(final.read_bytes())["status"] in {"INTERRUPTED", "FAILED_CLOSED", "RUNNING"}):
                    raise ValueError("RECOVERY_REQUIRED")
    directory = runs / identity["trading_date"]
    path = directory / "identity.json"
    if path.exists():
        if json.loads(path.read_bytes(), object_pairs_hook=observer.unique_fields) != identity:
            raise ValueError("CONTROL_STATE_INVALID")
    else:
        if directory.exists() and any(directory.iterdir()):
            raise ValueError("CONTROL_STATE_INVALID")
    return directory, marker, expected


def _initialize(root, identity):
    directory, marker, expected = _validate_control_root(root, identity)
    if not marker.exists():
        _atomic_json(marker, expected)
    if not (directory / "identity.json").exists():
        _atomic_json(directory / "identity.json", identity)
    return directory


def prepare_launch(config, control_root):
    """Validate before creating owned metadata/logs; never grant execution permission."""
    cfg = validate_config(config)
    identity = _identity(cfg, _utc_now(), False)
    _validate_control_root(control_root, identity)
    with ownership(PROJECT_ROOT):
        _initialize(control_root, identity)
    return {"status": "CONTROL_PREPARED", "mode": "FORWARD_PAPER", "external_requests": 0,
            "trade_placement_enabled": False, "transport_integration": "NOT_REVIEWED"}


def _empty_account(capital, synthetic):
    value = {k: None for k in observer.ACCOUNT_FIELDS}
    if synthetic:
        value.update(opening_equity=capital, equity=capital, available_cash=capital, reserved_cash=0,
                     open_positions=0, pending_proposals=0, paper_entries=0, planned_risk=0,
                     realized_pnl=0, gross_pnl=0, modeled_costs=0, modeled_slippage=0)
    return value


def _write_once(path, value):
    data = _encoded(value)
    if path.exists():
        if path.read_bytes() != data:
            raise ValueError("CONTROL_STATE_INVALID")
        return
    with path.open("xb") as stream:
        stream.write(data); stream.flush(); os.fsync(stream.fileno())


def _begin_attempt(directory, identity, started):
    folder = directory / "attempts"
    folder.mkdir(exist_ok=True)
    records = _attempt_records(directory.parent)
    previous = records[-1]["attempt_id"] if records else None
    attempt = uuid.uuid4().hex
    value = {**identity, "attempt_id": attempt, "attempt_sequence": len(records) + 1,
             "previous_attempt_id": previous, "started_at": started, "pid": os.getpid()}
    _write_once(folder / (attempt + ".begin.json"), value)
    return {**identity, "attempt_id": attempt, "attempt_sequence": len(records) + 1, "previous_attempt_id": previous}


def _reason(value):
    reason = value.get("reason")
    if value.get("state") == "OBSERVATION_MISSING": return "OBSERVATION_MISSING"
    if value.get("state") == "INVALID_OR_CONFLICTING": return "ENGINE_INVARIANT_FAILED"
    return {"entry_cutoff": "ENTRY_WINDOW_CLOSED", "unsupported_session_or_entry_cutoff": "ENTRY_WINDOW_CLOSED",
            "insufficient_available_cash": "INSUFFICIENT_EQUITY", "invalid_causal_entry_prices": "ENGINE_REJECTED",
            "entry notional plus buy-side costs exceeds capital": "INSUFFICIENT_EQUITY",
            "entry notional plus buy-side costs exceeds available account cash": "INSUFFICIENT_EQUITY",
            "cost-inclusive stop risk exceeds the trade-risk limit": "RISK_LIMIT",
            "quantity exceeds current RiskEngine maximum": "RISK_LIMIT",
            "Daily loss limit reached": "RISK_LIMIT", "Maximum open positions reached": "RISK_LIMIT",
            "Maximum trades for today reached": "RISK_LIMIT",
            "target gain does not cover estimated round-trip costs": "EXPECTED_NET_PNL_NON_POSITIVE"}.get(reason, "ENGINE_REJECTED" if reason else None)


def _projection(view, capital, trading_date):
    account = _empty_account(capital, True)
    if not view: return account, [], 0
    values = view["account"]
    account.update(equity=values["eligible_equity"], available_cash=values["available_cash"], reserved_cash=values["reserved_cash"],
                   open_positions=values["open_positions"])
    latest = {row["decision_id"]: row for row in view["history"]}
    account["pending_proposals"] = sum(row["state"] == "ENTRY_PENDING" for row in latest.values())
    account["paper_entries"] = len({row["decision_id"] for row in view["history"] if row.get("entry_accepted_at")
                                  and pd.Timestamp(row["entry_accepted_at"]).tz_convert("Asia/Kolkata").date().isoformat() == trading_date})
    account["planned_risk"] = math.fsum(row["entry_check"]["cost_inclusive_stop_risk"] for row in latest.values()
                                       if row.get("entry_accepted_at") and not row["state"].startswith("CLOSED_"))
    account["opening_equity"] = max(0, capital + math.fsum(s["net_pnl"] for s in view["settlements"] if pd.Timestamp(s["exit_time"]).tz_convert("Asia/Kolkata").date().isoformat() < trading_date))
    settlements = [s for s in view["settlements"] if pd.Timestamp(s["exit_time"]).tz_convert("Asia/Kolkata").date().isoformat() == trading_date]
    account["realized_pnl"] = math.fsum(s["net_pnl"] for s in settlements)
    account["gross_pnl"] = sum(s["gross_pnl"] for s in settlements)
    account["modeled_costs"] = sum(s["modeled_costs"] for s in settlements)
    # V2 exposes components on closed result rows, not its settlement cache.
    account["modeled_slippage"] = sum(s["cost_components"]["slippage"] for s in settlements)
    candidates = []
    for row in latest.values():
        if pd.Timestamp(row["decision_ready_at"]).tz_convert("Asia/Kolkata").date().isoformat() != trading_date:
            continue
        candidate = {k: None for k in observer.CANDIDATE_FIELDS}
        proposal = row["proposal"]
        check = row.get("entry_check", {})
        candidate.update(decision_ref=hashlib.sha256(row["decision_id"].encode()).hexdigest(), symbol=proposal["symbol"],
                         proposal_at=row["decision_ready_at"], action="BUY", proposal_score=proposal["score"], current_price=row.get("entry_price"),
                         target=proposal["target_price"], stop=proposal["stop_loss"], outcome=row["state"], reason_code=_reason(row),
                         available_equity=row.get("entry_account", {}).get("eligible_equity"), required_capital=check.get("entry_cash_required"),
                         estimated_gross_reward=row.get("expected_gross_target_pnl"), modeled_net_reward=row.get("expected_net_target_pnl"),
                         risk_amount=check.get("cost_inclusive_stop_risk"))
        if candidate["risk_amount"] and candidate["modeled_net_reward"] is not None:
            candidate["reward_risk"] = candidate["modeled_net_reward"] / candidate["risk_amount"]
        # Do not infer rank, quote/depth age, provider confidence or upstream
        # trade-quality checks that this offline V2 interface does not supply.
        candidate["ai_confidence"] = None
        candidates.append(candidate)
    return account, candidates, len(settlements)


def _snapshot(identity, started, now, status, phase, reasons, account, candidates, transport_health=None, request_counts=None):
    try: session = session_state(now)
    except Exception: session = dict(state="UNVERIFIED", start=None, end=None, entry_cutoff=None, next_cutoff=None)
    snapshot = {"schema_version": observer.SCHEMA, "mode": "FORWARD_PAPER", "run_id": identity["run_id"],
        "attempt_id": identity["attempt_id"], "previous_attempt_id": identity["previous_attempt_id"],
        "trading_date": identity["trading_date"], "started_at": started, "updated_at": observer.timestamp(now).isoformat(),
        "pid": os.getpid(), "simulation_only": identity["simulation_only"], "status": status, "phase": phase,
        "reason_codes": reasons, "session": session,
        "health": {"reported_alive": status == "RUNNING", "last_loop_at": observer.timestamp(now).isoformat(), "shutdown_state": "RUNNING" if status == "RUNNING" else "STOPPED"},
        "activity": {"last_market_data_update": None, "last_provider_activity": None, "market_data_requests": 0, "provider_requests": 0},
        "account": account, "candidates": candidates}
    if transport_health is not None:
        snapshot.update(schema_version=observer.TRANSPORT_SCHEMA, transport=transport_health)
        snapshot["activity"] = {"last_market_data_update": transport_health["last_candle_at"],
            "last_provider_activity": transport_health["last_provider_at"],
            "market_data_requests": transport_health["market_data_requests"], "provider_requests": transport_health["provider_requests"]}
    if request_counts is not None:
        snapshot['activity'].update(request_counts)
    return observer.validate_snapshot(snapshot)


def _publish_observer(directory, snapshot):
    _atomic_json(directory / "observer.json", snapshot)
    record = {k: snapshot[k] for k in ("status", "phase", "reason_codes")}
    record["at"] = snapshot["updated_at"]
    with (directory / "events.jsonl").open("ab") as stream:
        stream.write(_encoded(record) + b"\n"); stream.flush(); os.fsync(stream.fileno())


def _finish(directory, identity, started, now, status, reasons, account, candidates, settled=0, observer_failures=0, recovered=False, duplicate=False, transport_health=None, request_counts=None):
    try:
        snapshot = _snapshot(identity, started, now, status, "STOPPED", reasons, account, candidates[-100:], transport_health, request_counts)
        _publish_observer(directory, snapshot)
    except Exception: observer_failures += 1
    summary = {"schema_version": "scheduled-paper-summary-v1", "mode": "FORWARD_PAPER", "run_id": identity["run_id"],
        "attempt_id": identity["attempt_id"], "attempt_sequence": identity["attempt_sequence"], "previous_attempt_id": identity["previous_attempt_id"],
        "trading_date": identity["trading_date"], "started_at": started, "shutdown_at": observer.timestamp(now).isoformat(),
        "status": status, "reason_codes": reasons, "simulation_only": identity["simulation_only"], "external_requests": 0,
        "trade_placement_enabled": False, "account": account, "settled_trades": settled, "candidates": candidates,
        "observer_failures": observer_failures, "recovered": recovered, "duplicate_launch": duplicate,
        "observer_path": str(directory / "observer.json")}
    if transport_health is not None:
        summary["transport"] = observer.validate_transport_health(transport_health)
        summary["external_requests"] = transport_health["market_data_requests"] + transport_health["provider_requests"]
    if request_counts is not None:
        summary['request_counts']={key:request_counts[key] for key in ('market_data_requests','provider_requests')}
        values=summary['request_counts'].values()
        summary['external_requests']=sum(values) if all(value is not None for value in values) else None
    rejected = [c for c in candidates if c["outcome"] == "ENTRY_REJECTED"]
    summary.update(symbols_considered=sorted({c["symbol"] for c in candidates}), recorded_proposals=len(candidates),
                   opportunities_generated=None, rejected_candidates=len(rejected),
                   rejection_reason_counts={reason: sum(c["reason_code"] == reason for c in rejected) for reason in sorted({c["reason_code"] for c in rejected if c["reason_code"]})},
                   paper_exits=settled, paper_entries=account["paper_entries"],
                   recoveries=int(recovered), warnings=["OBSERVER_UNAVAILABLE"] if observer_failures else [],
                   errors=reasons if status == "FAILED_CLOSED" else [],
                   modeled_charges=account["modeled_costs"] - account["modeled_slippage"] if account["modeled_costs"] is not None and account["modeled_slippage"] is not None else None)
    _write_once(directory / "attempts" / (identity["attempt_id"] + ".final.json"), summary)
    _atomic_json(directory / "summary.json", summary)
    text = (f"# Paper infrastructure report: {identity['trading_date']}\n\n"
            f"Status: {status}\n\nRun: {identity['run_id']}\n\nSynthetic fixture evidence: {identity['simulation_only']}\n\n"
            f"Equity: {account['equity']}; settled trades: {settled}\n\n"
            f"Reasons: {', '.join(reasons) or 'none'}\n\nNo real-market experiment or broker execution authorized.\n")
    (directory / "summary.md").write_text(text, encoding="utf-8")
    return summary


def run_scheduled(config, control_root, *, clock=None, sleep=None):
    """Hold project ownership before delegating a reviewed version-two session."""
    clock = clock or _utc_now
    sleep = sleep or time.sleep
    now = clock()
    gate = preflight(config, now)
    if "MODE_NOT_PAPER" in gate["reason_codes"] or "CONFIG_INVALID" in gate["reason_codes"]:
        return gate
    try:
        identity = _identity(config, now, False)
        with ownership(PROJECT_ROOT):
            directory = _initialize(control_root, identity)
            previous_summary = directory / "summary.json"
            duplicate = previous_summary.exists()
            started = observer.timestamp(now).isoformat()
            identity = _begin_attempt(directory, identity, started)
            failures = 0
            try: _publish_observer(directory, _snapshot(identity, started, now, "RUNNING", "PREFLIGHT", [], _empty_account(config["initial_capital"], False), []))
            except Exception: failures += 1
            if config["schema_version"] == "scheduled-forward-paper-v2" and gate["status"] == "READY":
                from app.research.v2_transport_controller import run_session
                return run_session(config, control_root, directory, identity, started, clock, sleep,
                                   observer_failures=failures, duplicate=duplicate)
            # Presence is not authority to replay a real or historical ledger.
            if (Path(control_root) / "engine").exists():
                gate.update(status="NO_GO", reason_codes=["RECOVERY_REQUIRED", "V2_TRANSPORT_NOT_REVIEWED"])
            return _finish(directory, identity, started, now, gate["status"], gate["reason_codes"], _empty_account(config["initial_capital"], False), [], observer_failures=failures, duplicate=duplicate)
    except InstanceBusy:
        return {**gate, "status": "NO_GO", "reason_codes": ["INSTANCE_BUSY"]}
    except Exception as error:
        reason = "RECOVERY_REQUIRED" if str(error) == "RECOVERY_REQUIRED" else "CONTROL_STATE_INVALID"
        return {**gate, "status": "NO_GO", "reason_codes": [reason]}


def _run_synthetic_fixture(config, control_root, fixture, *, as_of, stop_after=None):
    """Private offline verification harness, deliberately absent from CLI options."""
    cfg = validate_config(config)
    if (type(fixture) is not dict or set(fixture) != {"schema_version", "provenance", "commands"}
            or fixture["schema_version"] != "synthetic-scheduled-v2-fixture-v1" or fixture["provenance"] != "SYNTHETIC_TEST_DATA"
            or type(fixture["commands"]) is not list):
        raise ValueError("explicit synthetic fixture required")
    identity = _identity(cfg, as_of, True)
    started = observer.timestamp(as_of).isoformat()
    account, candidates, settled = _empty_account(cfg["initial_capital"], True), [], 0
    failures = 0
    directory = None
    try:
        # Fixture roots are isolated; production always uses the fixed project key.
        root = Path(control_root).resolve()
        with ownership(root.with_name(root.name + ".scheduler-owner")):
            directory = _initialize(control_root, identity)
            identity = _begin_attempt(directory, identity, started)
            ledger = Path(control_root) / "engine"
            recovered = ledger.exists()
            try: _publish_observer(directory, _snapshot(identity, started, as_of, "RUNNING", "RECOVERY" if recovered else "STARTUP", [], _empty_account(cfg["initial_capital"], False), []))
            except Exception: failures += 1
            from app.research.causal_forward_paper_v2 import CausalForwardPaperV2
            engine = CausalForwardPaperV2(ledger, capital=cfg["initial_capital"], provenance="SYNTHETIC_TEST_DATA")
            view = engine._view()
            if view:
                latest = {row["decision_id"]: row for row in view["history"]}
                if any(row["state"] in {"ENTRY_PENDING", "OPEN", "OBSERVATION_MISSING", "INVALID_OR_CONFLICTING"}
                       and pd.Timestamp(row["decision_ready_at"]).date().isoformat() < identity["trading_date"] for row in latest.values()):
                    raise ValueError("RECOVERY_REQUIRED")
            account, candidates, settled = _projection(view, cfg["initial_capital"], identity["trading_date"])
            previous = directory / "summary.json"
            if previous.exists() and json.loads(previous.read_bytes())["status"] in {"COMPLETED", "RECOVERED_AND_COMPLETED", "NO_OPPORTUNITY"}:
                if account["open_positions"] or account["pending_proposals"] or view and view["performance"]["unresolved"]:
                    raise ValueError("CONTROL_STATE_INVALID")
                return _finish(directory, identity, started, as_of, "COMPLETED" if settled else "NO_OPPORTUNITY", [], account, candidates, settled, observer_failures=failures, recovered=recovered, duplicate=True)
            for index, command in enumerate(fixture["commands"]):
                if stop_after is not None and index >= stop_after:
                    return _finish(directory, identity, started, as_of, "INTERRUPTED", ["INTERRUPTED", "RECOVERY_REQUIRED"], account, candidates, settled, failures, recovered)
                if type(command) is not dict or set(command) != {"operation", "args"} or command["operation"] not in {"propose", "observe"}:
                    raise ValueError("invalid fixture command")
                command_clock = command["args"]["decision_ready_at" if command["operation"] == "propose" else "as_of"]
                if observer.timestamp(command_clock) > observer.timestamp(as_of):
                    raise ValueError("future fixture command")
                getattr(engine, command["operation"])(**command["args"])
                view = engine._view()
                account, candidates, settled = _projection(view, cfg["initial_capital"], identity["trading_date"])
                phase = "PROPOSAL" if command["operation"] == "propose" else "OBSERVATION"
                try: _publish_observer(directory, _snapshot(identity, started, as_of, "RUNNING", phase, [], account, candidates[-100:]))
                except Exception: failures += 1
            view = engine._view()
            latest = {r["decision_id"]: r for r in view["history"]} if view else {}
            for row in latest.values():
                if row["state"] in {"ENTRY_PENDING", "OPEN"} and pd.Timestamp(as_of) >= pd.Timestamp(row["next_start"]) + pd.Timedelta(minutes=5):
                    engine.observe(row["decision_id"], expected_start=row["next_start"], as_of=as_of, observation=None)
            view = engine._view()
            account, candidates, settled = _projection(view, cfg["initial_capital"], identity["trading_date"])
            unfinished = account["open_positions"] or account["pending_proposals"] or view and view["performance"]["unresolved"]
            status = "FAILED_CLOSED" if unfinished else "RECOVERED_AND_COMPLETED" if recovered else "COMPLETED" if settled else "NO_OPPORTUNITY"
            return _finish(directory, identity, started, as_of, status, ["RECOVERY_REQUIRED"] if unfinished else [], account, candidates, settled, failures, recovered)
    except Exception:
        # Never propagate arbitrary exception text or rewrite engine evidence.
        if directory is not None:
            try:
                return _finish(directory, identity, started, as_of, "FAILED_CLOSED", ["ENGINE_INVARIANT_FAILED", "RECOVERY_REQUIRED"],
                               _empty_account(cfg["initial_capital"], False), [], settled=None, observer_failures=failures)
            except Exception:
                pass
        return {"status": "FAILED_CLOSED", "mode": "FORWARD_PAPER", "reason_codes": ["ENGINE_INVARIANT_FAILED"], "simulation_only": True,
                "trade_placement_enabled": False, "external_requests": 0}
