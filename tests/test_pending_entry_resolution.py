import pandas as pd
import pytest

from test_ai_led_reviewed import setup, options, used
from test_prospective_lifecycle_fixes import at, recovery_options, settlement_options
from app.research import ai_led_reviewed as reviewed, ai_led_guarded as guard, ai_led_offline as core


def pending(s, stamp="2026-11-02T15:05:01+05:30", capital=1000):
    at(s, stamp)
    factor = capital / 1000
    for response in s.data.values():
        for row in response["data"]["candles"]:
            row[1:5] = [value * factor for value in row[1:5]]
    for response in s.quotes.values():
        depth = next(iter(response["data"].values()))["depth"]
        for side in ("buy", "sell"):
            depth[side][0]["price"] *= factor
    for field in ("entry_price", "stop_loss", "target_price"):
        s.body[0]["proposals"][0][field] *= factor
    opts = options(s, include_quotes=True)
    opts.update(capital=capital, market_data_cap=12)
    result = reviewed.run_reviewed_paper(**opts)
    assert result["position"]["status"] == "PENDING"
    return opts


def local_options(s, capital=1000):
    opts = recovery_options(s)
    opts.update(capital=capital, market_data_cap=12)
    return opts


def test_final_candle_pending_becomes_terminal_without_requests(setup):
    pending(setup)
    at(setup, "2026-11-02T15:15:01+05:30")
    result = reviewed.recover_reviewed_paper(**local_options(setup))
    assert result["status"] == "ENTRY_REJECTED"
    assert result["taken"] is False and result["reason_code"] == "ENTRY_WINDOW_EXPIRED"
    assert result["position"] is None


@pytest.mark.parametrize("resume", ["normal", "settlement", "local"])
def test_resolution_is_local_idempotent_and_preserves_evidence_account_and_quota(setup, resume):
    opts = pending(setup)
    folders = ("requests", "responses", "batches", "inputs")
    saved = {p: p.read_bytes() for folder in folders for p in (setup.output / folder).glob("*.json")}
    ledgers = {p: p.read_bytes() for p in setup.output.glob("*budget*")}
    before = (len(setup.http), len(setup.sdk))
    at(setup, "2026-11-02T15:15:01+05:30")
    call = {"normal": lambda: reviewed.run_reviewed_paper(**opts),
            "settlement": lambda: reviewed.run_reviewed_settlement(**settlement_options(setup)),
            "local": lambda: reviewed.recover_reviewed_paper(**local_options(setup))}[resume]
    result = call()
    state_path = setup.output / "paper_account/ai_led_offline_state.json"
    snapshot = state_path.read_bytes()
    setup.clock[0] += pd.Timedelta(seconds=10)
    assert call() == result
    assert state_path.read_bytes() == snapshot
    state = guard._account(setup.output, guard._read(setup.output / guard.MANIFEST))
    assert len(state["decisions"]) == len(state["events"]) == 1
    event = state["events"][0]
    assert event["type"] == "ENTRY_REJECTED" and event["taken"] is False
    assert not {"entry_price", "exit_price", "net_pnl", "costs"}.intersection(event)
    assert state["cash"] == state["balance"] == result["available_cash"] == 1000
    risk = core._risk_state(state, pd.Timestamp("2026-11-02").date())
    assert risk.trades_today == risk.open_positions == risk.daily_loss == 0
    summary = guard.summarize_guarded(setup.output)
    assert summary["rejected_entries"] == 1
    assert summary["accepted_trades"] == summary["completed_trades"] == summary["pending_entries"] == summary["open_positions"] == 0
    assert summary["costs"] == summary["net_pnl"] == 0
    assert all(p.read_bytes() == data for p, data in {**saved, **ledgers}.items())
    assert (len(setup.http), len(setup.sdk)) == before == (4, 1)
    assert used(setup, "request_budget") == 1 and used(setup, "market_data_request_budget") == 4


@pytest.mark.parametrize("restart", ["2026-11-02T15:10:00+05:30", "2026-11-02T16:00:00+05:30",
    "2026-11-07T12:00:00+05:30", "2026-11-08T14:30:00+05:30", "2026-11-10T12:00:00+05:30",
    "2027-01-01T12:00:00+05:30"])
def test_restart_resolution_uses_original_verified_session_not_current_session(setup, restart):
    pending(setup)
    setup.clock[0] = pd.Timestamp(restart)  # No new candles at all, even outside calendar coverage.
    result = reviewed.recover_reviewed_paper(**local_options(setup))
    assert result["reason_code"] == "ENTRY_WINDOW_EXPIRED" and result["taken"] is False
    assert result["resolution"]["entry_validation_cutoff"] == "2026-11-02T15:10:00+05:30"
    assert len(setup.http) == 4 and len(setup.sdk) == 1


@pytest.mark.parametrize("capital", [1000, 2500, 10000, 100000])
def test_unexpired_pending_and_configurable_capital_are_preserved(setup, capital):
    # Keep a whole-share, cost-eligible proposal at each configured capital.
    pending(setup, capital=capital)
    state_path = setup.output / "paper_account/ai_led_offline_state.json"
    before = state_path.read_bytes()
    setup.clock[0] = pd.Timestamp("2026-11-02T15:09:59+05:30")
    result = reviewed.recover_reviewed_paper(**local_options(setup, capital))
    assert result["status"] == "NO_PENDING_RECOVERY" and state_path.read_bytes() == before
    setup.clock[0] += pd.Timedelta(seconds=1)
    result = reviewed.recover_reviewed_paper(**local_options(setup, capital))
    assert result["available_cash"] == result["account_balance"] == capital


def test_later_session_close_is_derived_from_calendar(setup):
    pending(setup, "2026-12-07T15:20:01+05:30")
    setup.clock[0] = pd.Timestamp("2026-12-07T15:24:59+05:30")
    assert reviewed.recover_reviewed_paper(**local_options(setup))["status"] == "NO_PENDING_RECOVERY"
    setup.clock[0] += pd.Timedelta(seconds=1)
    result = reviewed.recover_reviewed_paper(**local_options(setup))
    assert result["resolution"]["entry_validation_cutoff"] == "2026-12-07T15:25:00+05:30"


@pytest.mark.parametrize("after_commit", [False, True])
def test_interrupted_resolution_commit_restarts_without_duplicate_or_cash_change(setup, monkeypatch, after_commit):
    pending(setup)
    setup.clock[0] = pd.Timestamp("2026-11-02T15:15:01+05:30")
    commit = core._commit
    def interrupted(*args):
        if after_commit:
            commit(*args)
        raise OSError("offline interrupted resolution")
    monkeypatch.setattr(core, "_commit", interrupted)
    with pytest.raises(guard.GuardedBatchError):
        reviewed.recover_reviewed_paper(**local_options(setup))
    monkeypatch.setattr(core, "_commit", commit)
    result = reviewed.recover_reviewed_paper(**local_options(setup))
    assert result["taken"] is False and result["available_cash"] == 1000
    state = guard._account(setup.output, guard._read(setup.output / guard.MANIFEST))
    assert len(state["events"]) == 1 and len(setup.sdk) == 1 and len(setup.http) == 4


def test_corrupt_pending_evidence_blocks_resolution_without_network(setup):
    pending(setup)
    setup.clock[0] = pd.Timestamp("2026-11-02T15:15:01+05:30")
    next((setup.output / "responses").glob("*.json")).write_text("{}")
    with pytest.raises(guard.GuardedBatchError):
        reviewed.recover_reviewed_paper(**local_options(setup))
    assert len(setup.http) == 4 and len(setup.sdk) == 1


def test_intermediate_no_trade_decision_does_not_break_terminal_resume_or_later_batch(setup):
    opts = pending(setup, "2026-11-02T14:55:01+05:30")
    at(setup, "2026-11-02T15:00:01+05:30")
    assert reviewed.run_reviewed_paper(**opts)["position"]["status"] == "PENDING"
    setup.clock[0] = pd.Timestamp("2026-11-02T15:15:01+05:30")
    result = reviewed.recover_reviewed_paper(**local_options(setup))
    assert reviewed.recover_reviewed_paper(**local_options(setup)) == result
    at(setup, "2026-11-03T12:10:01+05:30")
    later = reviewed.run_reviewed_paper(**opts)
    assert later["provider_category"] == "provider_budget_exhausted"
    assert len(setup.sdk) == 1 and used(setup, "market_data_request_budget") == 12
    state = guard._account(setup.output, guard._read(setup.output / guard.MANIFEST))
    assert len(state["events"]) == 1 and state["position"] is None
