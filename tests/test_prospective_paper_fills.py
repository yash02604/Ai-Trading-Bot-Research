import copy
import hashlib
import json
from pathlib import Path

import pandas as pd
import pytest

from app.cost_model import estimate_buy_side_costs, estimate_round_trip_costs
from app.research import prospective_paper_fills as fills
from app.research.offline_session_calendar import OfflineSessionCalendar
from strategy_backtest import STOCKS


def seal(data):
    data["depth_evidence"]["quote_sha256"] = hashlib.sha256(json.dumps(
        data["quote"], sort_keys=True, separators=(",", ":"), allow_nan=True).encode()).hexdigest()


@pytest.fixture
def inputs():
    data = {
        "policy": {"policy_id": fills.POLICY_ID, "max_spread_bps": 10, "max_quote_age_seconds": 5},
        "timestamps": {"market_observed_at": "2026-11-02T12:10:00+05:30",
            "opportunity_at": "2026-11-02T12:10:01+05:30", "provider_request_sent_at": "2026-11-02T12:10:02+05:30",
            "provider_response_received_at": "2026-11-02T12:10:03+05:30", "decision_ready_at": "2026-11-02T12:10:04+05:30",
            "earliest_entry_at": "2026-11-02T12:15:00+05:30", "entry_expires_at": "2026-11-02T12:20:00+05:30",
            "quote_request_sent_at": "2026-11-02T12:15:00+05:30", "quote_source_at": "2026-11-02T12:15:01+05:30",
            "quote_received_at": "2026-11-02T12:15:02+05:30", "fill_acceptance_at": "2026-11-02T12:15:03+05:30"},
        "proposal": {"symbol": "RELIANCE", "quantity": 1, "entry_price": 600,
                     "stop_loss": 594, "target_price": 612, "holding_bars": 6},
        "quote": {"instrument_key": STOCKS["RELIANCE"], "bid_price": 599.9, "ask_price": 600,
                  "bid_quantity": 100, "ask_quantity": 100, "source_quote_timestamp": "2026-11-02T12:15:01+05:30"},
        "depth_evidence": {"schema_version": "synthetic-depth-evidence-v1", "evidence_kind": "SYNTHETIC_FIXTURE",
                           "timestamp_semantics": "DEPTH_EVENT_TIME", "quote_sha256": ""},
        "account": {"capital": 1000, "balance": 1000, "available_cash": 1000,
                    "daily_loss": 0, "trades_today": 0, "open_positions": 0, "as_of": "2026-11-02T12:15:03+05:30"},
        "calendar": OfflineSessionCalendar(Path(__file__).resolve().parents[1] / "data/audits/nse_cm_session_manifest_v3.json"),
    }
    seal(data)
    return data


def check(data, reason):
    before = copy.deepcopy({k: v for k, v in data.items() if k != "calendar"})
    result = fills.validate_paper_fill(**data)
    assert result["reason_code"] == reason
    assert result["eligible"] is (reason == "ELIGIBLE_SIMULATED_FILL")
    assert result["taken"] is result["trade_placement_enabled"] is result["settlement_supported"] is False
    # NaN does not compare equal to itself; JSON preserves a deterministic view.
    assert json.dumps(before, sort_keys=True) == json.dumps({k: v for k, v in data.items() if k != "calendar"}, sort_keys=True)
    return result


def test_valid_synthetic_causal_quote_is_only_eligibility_and_is_deterministic(inputs):
    a = check(inputs, "ELIGIBLE_SIMULATED_FILL")
    assert fills.validate_paper_fill(**inputs) == a
    assert a["evidence_kind"] == "SYNTHETIC_FIXTURE" and a["validation_only"]
    assert a["entry_reference_price"] == 600 and a["quantity"] == 1
    assert a["stop_loss"] == 594 and a["target_price"] == 612


@pytest.mark.parametrize("field,value,reason", [
    ("market_observed_at", "2026-11-02T12:11:00+05:30", "CAUSAL_ORDER_INVALID"),
    ("provider_response_received_at", "2026-11-02T12:10:01+05:30", "CAUSAL_ORDER_INVALID"),
    ("decision_ready_at", "2026-11-02T12:15:01+05:30", "CAUSAL_ORDER_INVALID"),
    ("quote_request_sent_at", "2026-11-02T12:10:03+05:30", "CAUSAL_ORDER_INVALID"),
    ("quote_request_sent_at", "2026-11-02T12:14:59+05:30", "ENTRY_TOO_EARLY"),
    ("quote_source_at", "2026-11-02T12:15:04+05:30", "CAUSAL_ORDER_INVALID"),
    ("fill_acceptance_at", "2026-11-02T12:15:01+05:30", "CAUSAL_ORDER_INVALID"),
    ("fill_acceptance_at", "2026-11-02T12:10:03+05:30", "CAUSAL_ORDER_INVALID"),
    ("fill_acceptance_at", "2026-11-02T12:20:00+05:30", "ENTRY_WINDOW_EXPIRED"),
    ("earliest_entry_at", "2026-11-02T12:10:04+05:30", "ENTRY_WINDOW_INVALID"),
    ("entry_expires_at", "2026-11-02T12:21:00+05:30", "ENTRY_WINDOW_INVALID"),
    ("quote_source_at", None, "DEPTH_TIMESTAMP_MISSING"),
    ("quote_source_at", "2026-11-02T12:15:01", "INVALID_TIMESTAMPS"),
    ("quote_source_at", "not a timestamp", "INVALID_TIMESTAMPS"),
    ("quote_source_at", "now", "INVALID_TIMESTAMPS"),
    ("quote_source_at", "today", "INVALID_TIMESTAMPS"),
])
def test_causal_order_schedule_expiry_and_explicit_timestamps(inputs, field, value, reason):
    inputs["timestamps"][field] = value
    check(inputs, reason)


@pytest.mark.parametrize("at_receipt", [True, False])
def test_quote_age_must_pass_at_receipt_and_at_acceptance(inputs, at_receipt):
    if at_receipt:
        inputs["timestamps"]["quote_received_at"] = "2026-11-02T12:15:07+05:30"
    inputs["timestamps"]["fill_acceptance_at"] = "2026-11-02T12:15:08+05:30"
    inputs["account"]["as_of"] = inputs["timestamps"]["fill_acceptance_at"]
    check(inputs, "QUOTE_STALE")


@pytest.mark.parametrize("change,value,reason", [
    ("source_quote_timestamp", None, "DEPTH_TIMESTAMP_MISSING"),
    ("source_quote_timestamp", "2026-11-02T12:15:00+05:30", "DEPTH_TIMESTAMP_AMBIGUOUS"),
    ("instrument_key", STOCKS["HDFCBANK"], "INSTRUMENT_MISMATCH"),
    ("bid_price", 601, "CROSSED_MARKET"),
    ("bid_price", 590, "SPREAD_LIMIT_EXCEEDED"),
    ("bid_price", 0, "INVALID_QUOTE_PRICE"),
    ("ask_price", -1, "INVALID_QUOTE_PRICE"),
    ("ask_price", float("inf"), "INVALID_QUOTE_PRICE"),
    ("ask_price", float("nan"), "INVALID_QUOTE_PRICE"),
    ("ask_quantity", 0, "INVALID_QUOTE_DEPTH"),
    ("ask_quantity", 1.5, "INVALID_QUOTE_DEPTH"),
    ("bid_quantity", True, "INVALID_QUOTE_DEPTH"),
])
def test_quote_quality_and_identity(inputs, change, value, reason):
    inputs["quote"][change] = value
    seal(inputs)
    check(inputs, reason)


def test_insufficient_depth_never_resizes_proposal(inputs):
    inputs["account"].update(capital=10000, balance=10000, available_cash=10000)
    inputs["proposal"]["quantity"] = 2
    inputs["quote"]["ask_quantity"] = 1
    seal(inputs)
    check(inputs, "INSUFFICIENT_ASK_DEPTH")


@pytest.mark.parametrize("field,value", [("timestamp_semantics", "RESPONSE_GENERATED"),
    ("timestamp_semantics", "AMBIGUOUS"), ("evidence_kind", "LIVE_PROVIDER"), ("schema_version", "unknown")])
def test_depth_claims_cannot_be_upgraded_to_authenticated_real_evidence(inputs, field, value):
    inputs["depth_evidence"][field] = value
    check(inputs, "DEPTH_EVIDENCE_UNSUPPORTED")


def test_evidence_is_bound_to_exact_quote(inputs):
    inputs["quote"]["ask_price"] = 600.01
    check(inputs, "DEPTH_EVIDENCE_MISMATCH")


@pytest.mark.parametrize("field,value", [("max_spread_bps", None), ("max_spread_bps", float("inf")),
    ("max_spread_bps", -1), ("max_quote_age_seconds", 6), ("max_quote_age_seconds", True)])
def test_policy_parameters_are_explicit_finite_and_cannot_weaken_freshness(inputs, field, value):
    inputs["policy"][field] = value
    check(inputs, "INVALID_POLICY")


def test_historical_policy_is_not_silently_reinterpreted(inputs):
    inputs["policy"]["policy_id"] = fills.HISTORICAL_POLICY_ID
    check(inputs, "UNSUPPORTED_POLICY")


@pytest.mark.parametrize("field,value,reason", [("daily_loss", 20, "DAILY_LOSS_LIMIT"),
    ("trades_today", 3, "DAILY_TRADE_LIMIT"), ("open_positions", 1, "OPEN_POSITION_LIMIT"),
    ("trades_today", True, "INVALID_ACCOUNT"), ("available_cash", -1, "INVALID_ACCOUNT"),
    ("available_cash", 1001, "INVALID_ACCOUNT"), ("balance", float("nan"), "INVALID_ACCOUNT"),
    ("as_of", "2026-11-02T12:15:02+05:30", "INVALID_ACCOUNT"), ("available_cash", 0, "INSUFFICIENT_CASH")])
def test_account_validation_and_frozen_risk_limits(inputs, field, value, reason):
    inputs["account"][field] = value
    check(inputs, reason)


def test_increased_ask_can_fail_all_in_cash_without_changing_size(inputs):
    inputs["account"]["available_cash"] = 600.9
    inputs["proposal"]["stop_loss"] = 594.5
    inputs["quote"].update(ask_price=600.5, bid_price=600.4)
    seal(inputs)
    check(inputs, "INSUFFICIENT_CASH")


def test_increased_ask_can_exceed_existing_riskengine_quantity(inputs):
    inputs["proposal"].update(entry_price=100, stop_loss=99, target_price=103, quantity=10)
    inputs["quote"].update(ask_price=100.001, bid_price=100, ask_quantity=100)
    seal(inputs)
    # Use a stop slightly higher to retain the existing 1% distance ceiling.
    inputs["proposal"]["stop_loss"] = 99.001
    check(inputs, "RISKENGINE_QUANTITY_EXCEEDED")


def test_cost_inclusive_stop_risk_rejects_without_widening_stop(inputs):
    inputs["proposal"].update(entry_price=900, stop_loss=891, target_price=918)
    inputs["quote"].update(ask_price=900, bid_price=899.9)
    seal(inputs)
    check(inputs, "COST_INCLUSIVE_RISK_EXCEEDED")


def test_slippage_is_counted_once_on_ask_reference(inputs):
    result = check(inputs, "ELIGIBLE_SIMULATED_FILL")
    day = pd.Timestamp(inputs["timestamps"]["fill_acceptance_at"])
    buy = estimate_buy_side_costs(600, 1, day)
    stop = estimate_round_trip_costs(600, 594, 1, day)
    assert buy["slippage"] == pytest.approx(600 * .0005)
    assert result["entry_reference_price"] == 600
    assert result["cost_eligibility"]["entry_cash_required"] == pytest.approx(600 + buy["total_cost"], abs=1e-6)
    assert result["cost_eligibility"]["cost_inclusive_stop_risk"] == pytest.approx(6 + stop["total_cost"], abs=1e-6)


@pytest.mark.parametrize("capital", [1000, 2500, 5000, 10000, 100000])
def test_configurable_capital_above_1000_and_unchanged_proposal(inputs, capital):
    factor = capital / 1000
    inputs["account"].update(capital=capital, balance=capital, available_cash=capital)
    for key in ("entry_price", "stop_loss", "target_price"):
        inputs["proposal"][key] *= factor
    for key in ("ask_price", "bid_price"):
        inputs["quote"][key] *= factor
    seal(inputs)
    result = check(inputs, "ELIGIBLE_SIMULATED_FILL")
    assert result["quantity"] == 1
    assert result["cost_eligibility"]["max_trade_risk"] == capital * .01


@pytest.mark.parametrize("quantity", [0, -1, 1.5, True])
def test_invalid_quantities_never_become_fills(inputs, quantity):
    inputs["proposal"]["quantity"] = quantity
    check(inputs, "INVALID_PROPOSAL")


def test_missing_or_invalid_cost_inputs_fail_closed(inputs, monkeypatch):
    import app.research.deepseek_prospective as prospective
    monkeypatch.setattr(prospective, "estimate_buy_side_costs", lambda *a, **k: {})
    check(inputs, "INVALID_COST_INPUTS")


def test_unexpected_failure_does_not_echo_secret_or_provider_body(inputs, monkeypatch):
    monkeypatch.setattr(fills, "calculate_position_size", lambda *a: (_ for _ in ()).throw(RuntimeError("sk-private-secret Bearer PRIVATE raw-body")))
    result = check(inputs, "INVALID_INPUT")
    assert "private" not in json.dumps(result).lower() and "raw-body" not in json.dumps(result)


def test_no_clock_network_provider_or_state_access(inputs, monkeypatch):
    import builtins
    import socket
    from app import upstox_client
    from app.research.deepseek_prospective import PersistentRequestBudget
    from app.v2.provider import DeepSeekProvider
    def denied(*args, **kwargs):
        pytest.fail("pure validator attempted I/O, clock, provider or budget access")
    monkeypatch.setattr(builtins, "open", denied)
    monkeypatch.setattr(Path, "open", denied)
    monkeypatch.setattr(Path, "read_bytes", denied)
    monkeypatch.setattr(Path, "write_bytes", denied)
    monkeypatch.setattr(pd.Timestamp, "now", denied)
    monkeypatch.setattr(socket.socket, "connect", denied)
    monkeypatch.setattr(upstox_client, "_get", denied)
    monkeypatch.setattr(DeepSeekProvider, "complete", denied)
    monkeypatch.setattr(PersistentRequestBudget, "reserve", denied)
    check(inputs, "ELIGIBLE_SIMULATED_FILL")


@pytest.mark.parametrize("day", ["2026-11-07", "2026-11-08", "2027-01-01"])
def test_closed_unsupported_and_uncovered_sessions_fail_closed(inputs, day):
    for field, stamp in inputs["timestamps"].items():
        inputs["timestamps"][field] = day + stamp[10:]
    inputs["quote"]["source_quote_timestamp"] = inputs["timestamps"]["quote_source_at"]
    inputs["account"]["as_of"] = inputs["timestamps"]["fill_acceptance_at"]
    seal(inputs)
    check(inputs, "UNSUPPORTED_SESSION")


def test_final_regular_session_opportunity_has_no_permitted_quote_fill_window(inputs):
    replacements = {"market_observed_at": "15:05:00", "opportunity_at": "15:05:01",
        "provider_request_sent_at": "15:05:02", "provider_response_received_at": "15:05:03",
        "decision_ready_at": "15:05:04", "earliest_entry_at": "15:10:00", "entry_expires_at": "15:10:00",
        "quote_request_sent_at": "15:10:00", "quote_source_at": "15:10:01", "quote_received_at": "15:10:02",
        "fill_acceptance_at": "15:10:03"}
    inputs["timestamps"] = {k: "2026-11-02T" + v + "+05:30" for k, v in replacements.items()}
    check(inputs, "ENTRY_WINDOW_EXPIRED")


def test_later_session_close_uses_calendar_not_hard_coded_1515(inputs):
    replacements = {"market_observed_at": "15:15:00", "opportunity_at": "15:15:01",
        "provider_request_sent_at": "15:15:02", "provider_response_received_at": "15:15:03",
        "decision_ready_at": "15:15:04", "earliest_entry_at": "15:20:00", "entry_expires_at": "15:25:00",
        "quote_request_sent_at": "15:20:00", "quote_source_at": "15:20:01", "quote_received_at": "15:20:02",
        "fill_acceptance_at": "15:20:03"}
    inputs["timestamps"] = {k: "2026-12-07T" + v + "+05:30" for k, v in replacements.items()}
    inputs["quote"]["source_quote_timestamp"] = inputs["timestamps"]["quote_source_at"]
    inputs["account"]["as_of"] = inputs["timestamps"]["fill_acceptance_at"]
    seal(inputs)
    check(inputs, "ELIGIBLE_SIMULATED_FILL")


def test_invalid_stop_and_target_are_never_adjusted(inputs):
    inputs["proposal"]["stop_loss"] = 500
    check(inputs, "PRICE_STOP_TARGET_INVALID")


def test_current_quote_parser_cannot_establish_depth_freshness(inputs):
    from app.execution_observations import parse_upstox_full_quote
    received = pd.Timestamp(inputs["timestamps"]["quote_received_at"])
    response = {"data": {STOCKS["RELIANCE"]: {"timestamp": received.isoformat(),
        "depth": {"buy": [{"price": 599.9, "quantity": 100}], "sell": [{"price": 600, "quantity": 100}]}}}}
    parsed = parse_upstox_full_quote(response, received)
    assert parsed["quote_valid"] and parsed["source_quote_timestamp"] is None
    inputs["quote"]["source_quote_timestamp"] = parsed["source_quote_timestamp"]
    check(inputs, "DEPTH_TIMESTAMP_MISSING")


def test_stale_market_observation_is_not_made_fresh_by_a_later_quote(inputs):
    inputs["timestamps"]["market_observed_at"] = "2026-11-02T11:00:00+05:30"
    check(inputs, "MARKET_OBSERVATION_STALE")


def test_cost_estimator_nonfinite_components_fail_closed(inputs, monkeypatch):
    import app.research.deepseek_prospective as prospective
    original = prospective.estimate_buy_side_costs
    def invalid(*args, **kwargs):
        result = original(*args, **kwargs)
        result["slippage"] = float("nan")
        return result
    monkeypatch.setattr(prospective, "estimate_buy_side_costs", invalid)
    check(inputs, "INVALID_COST_INPUTS")
