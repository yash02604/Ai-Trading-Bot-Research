"""Synthetic offline policy regressions. Never use workspace accounts or live adapters."""
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
import sqlite3
import subprocess
import sys

import pandas as pd
import pytest

from app.cost_model import estimate_round_trip_costs
from app.research import causal_forward_paper as policy
from app.research import deepseek_prospective
from app.v2.provider import DeepSeekProvider
from app import upstox_client

CALENDAR = Path(__file__).resolve().parents[1] / "data/audits/nse_cm_session_manifest_v3.json"
READY = pd.Timestamp("2026-11-02T12:02:37+05:30")
START = READY.floor("5min") + policy.BAR
SYMBOL = "RELIANCE"
KEY = policy.core.STOCKS[SYMBOL]


def book(path, capital=1000, provenance="SYNTHETIC_TEST_DATA"):
    return policy.CausalForwardPaper(path, capital=capital, calendar_path=CALENDAR, provenance=provenance)


@pytest.fixture(autouse=True)
def offline_only(monkeypatch):
    def deny(*args, **kwargs):
        pytest.fail("unexpected network/provider/budget access")
    monkeypatch.setattr(upstox_client, "_get", deny)
    monkeypatch.setattr(DeepSeekProvider, "complete", deny)
    monkeypatch.setattr(deepseek_prospective.PersistentRequestBudget, "reserve", deny)


def submit(b, ready=READY, symbol=SYMBOL, identity=None, decision="D", quantity=1, down=None, **changes):
    proposal = {"symbol": symbol, "score": .84, "entry_price": 600, "stop_loss": 597,
                "target_price": 605, "holding_bars": 3, **changes}
    return b.propose(decision_id=decision, decision_ready_at=ready, proposal=proposal,
                     instrument_key=identity or policy.core.STOCKS[symbol], quantity=quantity,
                     four_index_down_count=down)


def evidence(start, symbol=SYMBOL, **changes):
    candle = {"timestamp": start.isoformat(), "open": 600, "high": 601, "low": 599, "close": 600,
              "volume": 240, **changes}
    return {"symbol": symbol, "instrument_key": policy.core.STOCKS[symbol],
            "observed_at": (start + policy.BAR).isoformat(), "candle": candle}


def step(b, start, decision="D", symbol=SYMBOL, delay="0s", **changes):
    return b.observe(decision, expected_start=start, as_of=start + policy.BAR + pd.Timedelta(delay),
                     observation=evidence(start, symbol, **changes))


def test_arbitrary_and_exact_boundary_start_only_after_ready(tmp_path):
    for i, ready in enumerate((READY, READY.floor("5min"))):
        b = book(tmp_path / str(i))
        pending = submit(b, ready=ready)
        assert pending["next_start"] == START.isoformat()
        with pytest.raises(ValueError, match="unexpected slot"):
            step(b, START - policy.BAR)
        with pytest.raises(ValueError, match="not completed"):
            b.observe("D", expected_start=START, observation=evidence(START), as_of=START + pd.Timedelta("299s"))
        opened = step(b, START, open=591, low=580, high=610, close=600)
        assert opened["entry_price"] == 600 and opened["state"] == "OPEN"
        assert opened["bars_held"] == opened["mfe_price"] == opened["mae_price"] == 0
        assert opened["entry_reference_at"] == opened["entry_accepted_at"] == (START + policy.BAR).isoformat()
        assert opened["expected_gross_target_pnl"] == 5 and opened["expected_net_target_pnl"] > 0


@pytest.mark.parametrize("bar,status,price", [
    ({"high": 605}, "CLOSED_TARGET", 605), ({"low": 597}, "CLOSED_STOP", 597),
    ({"high": 606, "low": 596}, "CLOSED_AMBIGUOUS_STOP_FIRST", 597),
    ({"open": 594, "low": 590, "high": 606}, "CLOSED_AMBIGUOUS_STOP_FIRST", 594),
    ({"open": 594, "low": 590}, "CLOSED_STOP", 594),
    ({"open": 610, "high": 612}, "CLOSED_TARGET", 605),
])
def test_exit_paths_and_single_slippage_charge(tmp_path, bar, status, price):
    b = book(tmp_path)
    submit(b)
    step(b, START)
    stamp = START + policy.BAR
    result = step(b, stamp, **bar)
    assert result["state"] == status and result["exit_price"] == price
    assert result["same_bar_ambiguous"] == ("AMBIGUOUS" in status)
    is_open_exit = bar.get("open", 600) <= 597 or (bar.get("open", 600) >= 605 and status == "CLOSED_TARGET")
    assert result["exit_time_kind"] == ("candle_open" if is_open_exit else "intrabar_time_unknown_end_bound")
    assert result["exit_time"] == (stamp if is_open_exit else stamp + policy.BAR).isoformat()
    costs = estimate_round_trip_costs(600, price, 1, stamp.date())
    assert result["cost_components"] == costs
    assert costs["slippage"] == pytest.approx((600 + price) * 5 / 10000)
    assert result["gross_pnl"] == price - 600 and result["net_pnl"] == price - 600 - costs["total_cost"]
    assert b.account(result["recorded_at"])["balance"] == 1000 + result["net_pnl"]
    proof = b.evidence(result["observation_hashes"][-1])
    assert proof["observation"]["candle"] == result["exit_bar"]
    assert 0 <= result["mfe_price"] <= result["mfe_upper_price"] <= result["exit_bar"]["high"] - 600
    assert 0 >= result["mae_price"] >= result["mae_lower_price"] >= result["exit_bar"]["low"] - 600


def test_horizon_counts_only_monitoring_bars(tmp_path):
    b = book(tmp_path)
    submit(b, holding_bars=2)
    step(b, START)
    assert step(b, START + policy.BAR)["state"] == "OPEN"
    result = step(b, START + 2 * policy.BAR, close=600.5)
    assert (result["state"], result["bars_held"], result["exit_price"]) == ("CLOSED_HORIZON", 2, 600.5)


@pytest.mark.parametrize("day,end", [("2026-11-02", "15:15"), ("2026-12-07", "15:30")])
def test_verified_earlier_close_and_entry_cutoff(tmp_path, day, end):
    close = pd.Timestamp(f"{day}T{end}:00+05:30")
    b = book(tmp_path / "allowed")
    ready = close - 4 * policy.BAR + pd.Timedelta(seconds=1)
    start = close - 3 * policy.BAR
    submit(b, ready=ready, holding_bars=12)
    assert step(b, start)["state"] == "OPEN"
    assert step(b, start + policy.BAR)["state"] == "OPEN"
    result = step(b, start + 2 * policy.BAR, close=599)
    assert (result["state"], result["exit_time"], result["exit_price"]) == ("CLOSED_SESSION", close.isoformat(), 599)
    late = book(tmp_path / "late")
    submit(late, ready=ready + policy.BAR)
    assert step(late, start + policy.BAR)["reason"] == "entry_cutoff"


@pytest.mark.parametrize("ready", ["2026-11-08T14:21:00+05:30", "2024-11-01T18:06:00+05:30",
                                   "2027-01-01T12:02:00+05:30"])
def test_muhurat_and_uncovered_sessions(tmp_path, ready):
    b = book(tmp_path)
    assert submit(b, ready=ready)["state"] == "ENTRY_REJECTED"
    assert b.account(ready)["open_positions"] == 0


@pytest.mark.parametrize("fault", ["absent", "wrong_symbol", "wrong_key", "premature", "stale", "wrong_label",
                                   "malformed", "nonfinite", "self_attest"])
def test_missing_or_invalid_evidence_never_enters(tmp_path, fault):
    b = book(tmp_path)
    submit(b)
    obs, at = evidence(START), START + policy.BAR
    if fault == "absent":
        obs = None
    elif fault == "wrong_symbol":
        obs["symbol"] = "HDFCBANK"
    elif fault == "wrong_key":
        obs["instrument_key"] = policy.core.STOCKS["HDFCBANK"]
    elif fault == "premature":
        obs["observed_at"] = READY.isoformat()
    elif fault == "stale":
        at += policy.BAR
    elif fault == "wrong_label":
        obs["candle"]["timestamp"] = (START - policy.BAR).isoformat()
    elif fault == "malformed":
        obs["candle"]["low"] = 610
    elif fault == "nonfinite":
        obs["candle"]["close"] = float("nan")
    else:
        obs["provenance"] = "REVIEWED_UPSTOX_CAPTURE"
    result = b.observe("D", expected_start=START, as_of=at, observation=obs)
    assert result["state"] == "OBSERVATION_MISSING" and "net_pnl" not in result
    account = b.account(at)
    assert account["reserved_cash"] == account["trades_today"] == 0
    conflict = step(b, START, delay="5min" if fault == "stale" else "0s", volume=241)
    assert conflict["state"] == "INVALID_OR_CONFLICTING" and "net_pnl" not in conflict


@pytest.mark.parametrize("kind", ["REVIEWED_UPSTOX_CAPTURE", "OFFLINE_REPLAY_FROM_REVIEWED_CAPTURE", "INVALID_OR_CONFLICTING"])
def test_caller_cannot_elevate_provenance(tmp_path, kind):
    with pytest.raises(ValueError):
        book(tmp_path, provenance=kind)


def test_missing_after_open_preserves_exposure_and_cannot_backfill(tmp_path):
    b = book(tmp_path)
    submit(b)
    step(b, START)
    s = START + policy.BAR
    result = b.observe("D", expected_start=s, as_of=s + policy.BAR, observation=None)
    assert "net_pnl" not in result and result["state"] == "OBSERVATION_MISSING"
    recovered = book(tmp_path)
    account = recovered.account(s + policy.BAR)
    assert account["open_positions"] == account["trades_today"] == 1
    assert account["balance"] == 1000 and account["reserved_cash"] > 600
    assert step(recovered, s)["state"] == "INVALID_OR_CONFLICTING"
    with pytest.raises(ValueError, match="quarantined"):
        recovered.account(s + policy.BAR)


@pytest.mark.parametrize("touch", [True, False])
def test_delayed_entry_receipt_cannot_use_pre_acceptance_extrema(tmp_path, touch):
    b = book(tmp_path)
    submit(b, holding_bars=1)
    step(b, START, delay="1s")
    result = step(b, START + policy.BAR, high=610 if touch else 604, close=601)
    assert result["partial_first_bar"]
    if touch:
        assert result["state"] == "OBSERVATION_MISSING" and "net_pnl" not in result
    else:
        assert result["state"] == "CLOSED_HORIZON" and result["mfe_price"] == 1


@pytest.mark.parametrize("finish,status,price", [(595, "CLOSED_STOP", 595), (606, "CLOSED_TARGET", 605)])
def test_partial_bar_close_proves_one_post_entry_threshold(tmp_path, finish, status, price):
    b = book(tmp_path)
    submit(b)
    step(b, START, delay="2s")
    result = step(b, START + policy.BAR, open=600, high=max(600,finish), low=min(600,finish), close=finish)
    assert (result["state"], result["exit_price"], result["exit_time_kind"]) == (status, price, "candle_close")


@pytest.mark.parametrize("capital,quantity,entry,target,stop,expected", [
    (1000, 1, 999, 1010, 995, "entry notional plus buy-side costs exceeds capital"),
    (1000, 1, 900, 918, 891, "cost-inclusive stop risk exceeds the trade-risk limit"),
    (1000, 1, 602, 603, 598, "target gain does not cover estimated round-trip costs"),
    (10000, 4, 600, 612, 597, None),
    (2500, 2, 600, 612, 597, None),
])
def test_revalidate_at_close_and_preserve_approved_quantity(tmp_path, capital, quantity, entry, target, stop, expected):
    b = book(tmp_path, capital=capital)
    submit(b, quantity=quantity, entry_price=entry - 1, stop_loss=stop, target_price=target)
    result = step(b, START, open=entry-1, low=entry-2, high=entry+1, close=entry)
    account = b.account(START + policy.BAR)
    if expected:
        assert result["state"] == "ENTRY_REJECTED" and result["reason"] == expected
        assert account["reserved_cash"] == account["trades_today"] == account["open_positions"] == 0
        assert account["available_cash"] == account["balance"] == capital
    else:
        assert result["state"] == "OPEN" and result["quantity"] == quantity
        assert result["expected_net_target_pnl"] == pytest.approx(
            (target-entry)*quantity - estimate_round_trip_costs(entry,target,quantity,START.date())["total_cost"])


def test_duplicates_concurrent_resume_and_conflicting_retries(tmp_path):
    b = book(tmp_path)
    pending = submit(b)
    assert submit(book(tmp_path)) == pending
    with pytest.raises(ValueError, match="duplicate instrument"):
        submit(b, decision="duplicate")
    for start, changes in [(START, {}), (START + policy.BAR, {"high": 606})]:
        with ThreadPoolExecutor(max_workers=2) as pool:
            results = list(pool.map(lambda _: step(book(tmp_path), start, **changes), range(2)))
        assert results[0] == results[1]
    assert [r["state"] for r in b.history("D")] == ["PROPOSED","ENTRY_PENDING","OPEN","CLOSED_TARGET"]
    conflict = submit(b, target_price=604)
    assert conflict["state"] == "INVALID_OR_CONFLICTING"
    with pytest.raises(ValueError, match="quarantined"):
        book(tmp_path).account(START + 2 * policy.BAR)
    with pytest.raises(ValueError, match="immutable causal configuration"):
        book(tmp_path, capital=2500)
    with pytest.raises(sqlite3.IntegrityError, match="immutable"):
        with sqlite3.connect(b._path) as db:
            db.execute("DELETE FROM evidence")


def test_open_position_risk_rechecked_between_two_pending_decisions(tmp_path):
    b = book(tmp_path, capital=10000)
    submit(b)
    submit(b, symbol="HDFCBANK", decision="B")
    assert step(b, START)["state"] == "OPEN"
    assert step(b, START, decision="B", symbol="HDFCBANK")["reason"] == "Maximum open positions reached"
    assert b.account(START + policy.BAR)["trades_today"] == 1


@pytest.mark.parametrize("large_loss", [False, True])
def test_daily_loss_and_trade_limit_are_derived_from_the_ledger(tmp_path, large_loss):
    b = book(tmp_path)
    completed = 1 if large_loss else 3
    for i in range(completed + 1):
        shift = i * 4 * policy.BAR
        submit(b, ready=READY + shift, decision=str(i), holding_bars=1)
        entry = step(b, START + shift, decision=str(i))
        if i == completed:
            assert entry["state"] == "ENTRY_REJECTED"
            assert entry["reason"] == ("Daily loss limit reached" if large_loss else "Maximum trades for today reached")
        else:
            step(b, START + shift + policy.BAR, decision=str(i), open=550 if large_loss else 600,
                 low=550 if large_loss else 599)
    assert book(tmp_path).account(entry["recorded_at"])["trades_today"] == completed


def test_exact_break_even_and_excess_quantity_fail_closed(tmp_path, monkeypatch):
    for quantity in (1, 2):
        b = book(tmp_path / str(quantity))
        submit(b, quantity=quantity)
        with monkeypatch.context() as m:
            m.setattr(policy.core, "estimate_round_trip_costs",
                      lambda entry, target, quantity, day: {"total_cost": (target - entry) * quantity})
            result = step(b, START)
        assert result["state"] == "ENTRY_REJECTED"
        if quantity == 1:
            assert result["entry_check"]["estimated_net_at_target"] == 0
        else:
            assert result["reason"] == "quantity exceeds current RiskEngine maximum"


def test_changed_observation_after_a_close_quarantines_without_second_outcome(tmp_path):
    b = book(tmp_path)
    submit(b)
    step(b, START)
    step(b, START + policy.BAR, high=607)
    result = step(b, START + policy.BAR, high=608)
    assert "net_pnl" not in result and result["state"] == "INVALID_OR_CONFLICTING"
    assert b.evidence(result["original_sha256"])["observation"]["candle"]["high"] == 607
    assert b.evidence(result["conflicting_sha256"])["observation"]["candle"]["high"] == 608
    assert step(book(tmp_path), START + policy.BAR, high=608) == result
    assert sum(e["state"].startswith("CLOSED_") for e in b.history("D")) == 1
    with pytest.raises(ValueError, match="quarantined"):
        book(tmp_path).account(START + 2 * policy.BAR)


@pytest.mark.parametrize("at_exit", [False, True])
def test_crash_after_evidence_write_rolls_back_and_retry_commits_once(tmp_path, monkeypatch, at_exit):
    b = book(tmp_path)
    submit(b)
    start = START
    if at_exit:
        step(b, START)
        start += policy.BAR
    previous = b.history("D")
    def counts():
        with sqlite3.connect(b._path) as db:
            return tuple(db.execute("SELECT (SELECT COUNT(*) FROM evidence),(SELECT COUNT(*) FROM journal)").fetchone())
    prior_counts = counts()
    def interrupt(*args):
        raise KeyboardInterrupt("injected after evidence insert")
    with monkeypatch.context() as m:
        m.setattr(policy, "_append", interrupt)
        with pytest.raises(KeyboardInterrupt):
            step(b, start, high=606)
    b = book(tmp_path)
    assert counts() == prior_counts and b.history("D") == previous
    assert step(b, start, high=606)["state"] == ("CLOSED_TARGET" if at_exit else "OPEN")


def test_abrupt_process_death_after_evidence_staging(tmp_path):
    b = book(tmp_path)
    submit(b)
    # Exit without finally/rollback, after the ordinary observe path stages its evidence.
    child = """
import os, runpy, socket, sys
import dotenv
dotenv.load_dotenv = lambda *a, **k: False
os.environ.update(UPSTOX_ACCESS_TOKEN='', DEEPSEEK_API_KEY='')
def deny(*args, **kwargs):
    raise AssertionError('network disabled')
socket.socket.connect = socket.create_connection = deny
helpers = runpy.run_path(sys.argv[1])
b = helpers['book'](sys.argv[2])
helpers['policy']._append = lambda *args: os._exit(83)
helpers['step'](b, helpers['START'])
"""
    completed = subprocess.run([sys.executable, "-c", child, str(Path(__file__).resolve()), str(tmp_path)],
                               cwd=Path(__file__).resolve().parents[1], capture_output=True, timeout=60)
    assert completed.returncode == 83, completed.stderr
    recovered = book(tmp_path)
    assert recovered.history("D")[-1]["state"] == "ENTRY_PENDING"
    with sqlite3.connect(b._path) as db:
        assert db.execute("SELECT COUNT(*) FROM evidence").fetchone()[0] == 1
    assert step(recovered, START)["state"] == "OPEN"


def test_four_index_comparator_is_only_metadata(tmp_path):
    outcomes = []
    for i in (None, 0, 2, 4):
        b = book(tmp_path / str(i), provenance="UNVERIFIED_CALLER_SUPPLIED")
        submit(b, down=i)
        step(b, START)
        result = step(b, START + policy.BAR, low=596)
        assert result["four_index_down_count"] == i and result["provenance"] == "UNVERIFIED_CALLER_SUPPLIED"
        assert result["trade_placement_enabled"] is False
        outcomes.append((result["state"], result["net_pnl"], result["quantity"]))
    assert len(set(outcomes)) == 1


def test_offline_entrypoint_has_no_order_or_transport_hook(tmp_path, monkeypatch):
    import inspect
    import requests
    import socket

    def forbidden(*args, **kwargs):
        pytest.fail("external transport or process reachable")
    monkeypatch.setattr(socket, "getaddrinfo", forbidden)
    monkeypatch.setattr(requests.sessions.Session, "request", forbidden)
    monkeypatch.setattr(subprocess, "Popen", forbidden)
    b = book(tmp_path)
    assert set(inspect.signature(b.observe).parameters) == {"decision_id", "expected_start", "as_of", "observation"}
    assert set(inspect.signature(b.propose).parameters) == {
        "decision_id", "decision_ready_at", "proposal", "instrument_key", "quantity", "four_index_down_count"}
    submit(b)
    step(b, START)
    assert step(b, START + policy.BAR, high=606)["trade_placement_enabled"] is False
