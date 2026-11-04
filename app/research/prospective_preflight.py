"""Offline, mandatory request gates. No provider, broker or reservation capability."""
import hashlib
import json
import math
from pathlib import Path

import pandas as pd

from app.client_attestation import ATTESTATION_SHA256, ROOT, verify_client_attestation
from app.research.offline_session_calendar import OfflineSessionCalendar
from app.session_manifest_v3 import MANIFEST_SHA256, VERSION
from strategy_backtest import STOCKS

POLICY_FILE = "prospective_request_policy.json"
CALENDAR_PATH = ROOT / "data/audits/nse_cm_session_manifest_v3.json"
BUDGETS = {
    "market_data": "market_data_request_budget",
    "provider": "request_budget",
    "smoke": "provider_smoke_request_budget",
}


class ProspectivePreflightError(ValueError):
    """Fixed safe error; never propagates credentials or arbitrary source text."""


def _policy(capital, priority, model, caps):
    # The frozen prospective runner consumes ONE snapshot, not an AI-led batch.
    # Refuse a multi-stock policy rather than imply batch completeness/ranking.
    if (type(capital) not in (int, float) or not math.isfinite(capital) or capital <= 0
            or type(priority) is not list or len(priority) != 1
            or type(priority[0]) is not str or priority[0] not in STOCKS
            or type(model) is not str or not model or len(model) > 80
            or type(caps) is not dict or set(caps) != set(BUDGETS)
            or any(type(v) is not int or v < 0 for v in caps.values())):
        raise ValueError("invalid request policy")
    return {"schema_version": "prospective-request-policy-v1",
            "mode": "FROZEN_BASELINE_PROSPECTIVE_PAPER",
            "initial_capital": capital, "model_id": model,
            "instrument_priority": priority,
            "instrument_keys": {s: STOCKS[s] for s in priority},
            "priority_policy": "single_configured_instrument",
            "request_caps": caps,
            "calendar_sha256": MANIFEST_SHA256, "calendar_version": VERSION,
            "client_attestation_sha256": ATTESTATION_SHA256,
            "trade_placement_enabled": False}


def _policy_hash(policy):
    return hashlib.sha256(json.dumps(policy, sort_keys=True, separators=(",", ":"),
                                     allow_nan=False).encode()).hexdigest()


SETTLEMENT_WINDOW_POLICY = "verified-session-completion-within-900s-start-label-age-v1"


def settlement_acquisition_window(calendar, entry_timestamp):
    """Local read authorization, NOT an additional exchange execution session.

    Derive from the verified interval containing the accepted entry. The last
    START candle is end-5m; the unchanged 900s freshness bound ends at end+10m.
    Unsupported phases/sessions never acquire a window through this policy.
    """
    entry = pd.Timestamp(entry_timestamp)
    if pd.isna(entry) or entry.tzinfo is None:
        raise ValueError("verified accepted entry timestamp required")
    for start, end in calendar.intervals(entry):
        if start <= entry and entry + pd.Timedelta(minutes=5) <= end:
            return start, end, end - pd.Timedelta(minutes=5) + pd.Timedelta(seconds=900)
    raise ValueError("accepted entry outside supported verified session")


def _evidence(now, *, purpose="entry", position_entry=None):
    stamp = pd.Timestamp(now)
    if pd.isna(stamp) or stamp.tzinfo is None:
        raise ValueError("explicit request clock required")
    client = verify_client_attestation()
    calendar = OfflineSessionCalendar(CALENDAR_PATH)
    if calendar.identity["sha256"] != MANIFEST_SHA256:
        raise ValueError("unsupported request session")
    if purpose == "entry":
        if not calendar.entry_allowed(stamp):
            raise ValueError("unsupported entry session")
    elif purpose == "settlement":
        start, _, acquisition_end = settlement_acquisition_window(calendar, position_entry)
        if not start <= stamp < acquisition_end:
            raise ValueError("outside verified settlement acquisition window")
    else:
        raise ValueError("unsupported acquisition purpose")
    return calendar, client


def initialize_request_policy(output_dir, *, capital, instrument_priority, model,
                              market_data_cap, provider_cap, smoke_cap=0, now):
    """Explicit initialization only; never retrofit a historical experiment.

    Callers must use a fresh output. Repeated initialization checks exact identity.
    It creates configuration only, with no budgets, reservations or requests.
    """
    from app.research.deepseek_prospective import (
        _experiment_lock, ensure_prospective_experiment_capital,
    )
    try:
        _evidence(now)
        caps = dict(market_data=market_data_cap, provider=provider_cap, smoke=smoke_cap)
        policy = _policy(capital, instrument_priority, model, caps)
        output = Path(output_dir)
        with _experiment_lock(output):
            path = output / POLICY_FILE
            if not path.exists() and any(p.name != ".prospective.lock" for p in output.iterdir()):
                raise ValueError("new output required")
            ensure_prospective_experiment_capital(
                output, capital, model=model, request_policy_sha256=_policy_hash(policy))
            if path.exists():
                if json.loads(path.read_bytes()) != policy:
                    raise ValueError("request policy is immutable")
            else:
                with path.open("x", encoding="utf-8", newline="\n") as stream:
                    stream.write(json.dumps(policy, sort_keys=True, indent=2) + "\n")
        return policy
    except Exception:
        raise ProspectivePreflightError("Prospective request policy initialization blocked; verify evidence and fresh experiment configuration") from None


def _budget_config(output, kind, maximum):
    """Read-only validation; existing reservation accounting stays authoritative."""
    stem = BUDGETS[kind]
    path = output / (stem + "_config.json")
    ledger = output / (stem + ".jsonl")
    if path.exists():
        saved = json.loads(path.read_bytes())
        if (type(saved) is not dict or type(saved.get("max_total_requests")) is not int
                or saved["max_total_requests"] != maximum):
            raise ValueError("request cap changed")
    elif ledger.exists() and ledger.stat().st_size:
        raise ValueError("orphan request reservations")
    if ledger.exists():
        rows = [json.loads(line) for line in ledger.read_text(encoding="utf-8").splitlines() if line.strip()]
        ids = [r["request_id"] for r in rows]
        if (any(type(v) is not str or not v for v in ids) or len(set(ids)) != len(ids)
                or len(rows) > maximum):
            raise ValueError("invalid request ledger")
    # Exhaustion still uses PersistentRequestBudget.reserve, preserving its
    # diagnostic category and all already-spent reservations.


def prospective_preflight(*, output_dir, symbol, now, kind, maximum,
                          model=None, capital=None):
    """Revalidate disk evidence/config on EVERY entry, including restart/direct calls."""
    from app.config import get_settings
    from app.research.deepseek_prospective import (
        EXPERIMENT_CAPITAL_CONFIG, ensure_prospective_experiment_capital,
    )
    try:
        if get_settings().paper_trading is not True:
            raise ValueError("paper only")
        calendar, client = _evidence(now)
        output = Path(output_dir)
        saved = json.loads((output / POLICY_FILE).read_bytes())
        experiment = json.loads((output / EXPERIMENT_CAPITAL_CONFIG).read_bytes())
        expected = _policy(saved["initial_capital"], saved["instrument_priority"],
                           saved["model_id"], saved["request_caps"])
        if (saved != expected or type(symbol) is not str or symbol not in saved["instrument_priority"]
                or type(experiment.get("experiment_spec")) is not dict
                or experiment["experiment_spec"].get("model_id") != saved["model_id"]
                or experiment.get("currency") != "INR"
                or experiment["request_policy_sha256"] != _policy_hash(saved)
                or kind not in BUDGETS or type(maximum) is not int
                or maximum != saved["request_caps"][kind]
                or (model is not None and model != saved["model_id"])
                or (capital is not None and (type(capital) not in (int, float)
                                            or capital != saved["initial_capital"]))):
            raise ValueError("request policy mismatch")
        ensure_prospective_experiment_capital(
            output, saved["initial_capital"], model=saved["model_id"],
            request_policy_sha256=_policy_hash(saved))
        for budget_kind, cap in saved["request_caps"].items():
            _budget_config(output, budget_kind, cap)
        return {"calendar": calendar, "client": client, "policy": saved}
    except Exception:
        raise ProspectivePreflightError("Prospective preflight blocked; verify attestation, calendar, paper mode and immutable request configuration") from None


def validate_request_snapshot(context, snapshot, frame, observed_at, now):
    """Additional request-only gates; do not alter shared candle parsing/filtering."""
    try:
        calendar = context["calendar"]
        symbol = snapshot["symbol"].upper()
        if (symbol not in context["policy"]["instrument_priority"]
                or snapshot["instrument_key"] != STOCKS[symbol]):
            raise ValueError("snapshot identity")
        now, observed_at = pd.Timestamp(now), pd.Timestamp(observed_at)
        if now.tzinfo is None or observed_at.tzinfo is None:
            raise ValueError("snapshot clock")
        for stamp in frame["timestamp"]:
            calendar.candle_end(stamp)
            if stamp + pd.Timedelta(minutes=5) > observed_at:
                raise ValueError("incomplete candle")
        recent = frame.tail(35)["timestamp"]
        if (len(recent) != 35 or len({s.date() for s in recent}) != 1
                or recent.iloc[-1].tz_convert("Asia/Kolkata").date() != now.tz_convert("Asia/Kolkata").date()
                or any(recent.diff().dropna() != pd.Timedelta(minutes=5))
                or not 0 <= (now - observed_at).total_seconds() <= 900
                or not 0 <= (observed_at - recent.iloc[-1]).total_seconds() <= 900):
            raise ValueError("incomplete point-in-time single-instrument batch")
    except Exception:
        raise ProspectivePreflightError("Prospective snapshot request validation blocked; verify identity, completed candles, session continuity and freshness") from None
