import json
from pathlib import Path

import pandas as pd
import pytest

from app.research import ai_led_offline as account
from app.research.ai_led_replay import ROOT, _plan, create_synthetic_demo, replay, summarize
from app.research.offline_session_calendar import OfflineSessionCalendar

CALENDAR = ROOT / "data/audits/nse_cm_session_manifest_v2.json"


@pytest.fixture(autouse=True)
def no_network(monkeypatch):
    import socket
    def denied(*args, **kwargs):
        raise AssertionError("offline replay must never connect to the network")
    monkeypatch.setattr(socket.socket, "connect", denied)
    monkeypatch.setattr(socket, "create_connection", denied)


def read(path):
    return json.loads(Path(path).read_text(encoding="utf-8"))


def write(path, value):
    Path(path).write_text(json.dumps(value), encoding="utf-8")


@pytest.mark.parametrize("capital", [1000, 10000, 100000, 2500])
def test_shared_account_replay_capitals_costs_metrics_and_resume(tmp_path, capital):
    plan = create_synthetic_demo(tmp_path / "fixtures", capital)
    output = tmp_path / "account"
    result = replay(plan, output_dir=output)
    saved = read(output / "ai_led_offline_state.json")["state"]
    assert result["total_accepted_trades"] == result["completed_trades"] == 3
    assert result["win_rate_pct"] == pytest.approx(200 / 3)
    assert result["transaction_costs"] > 0
    assert result["net_pnl"] == pytest.approx(result["gross_pnl"] - result["transaction_costs"])
    assert result["average_net_expectancy"] == pytest.approx(result["net_pnl"] / 3)
    assert result["ending_account_balance"] == capital + result["net_pnl"]
    assert result["ending_account_equity"] == result["ending_account_balance"]
    assert result["ending_available_cash"] == min(capital, result["ending_account_balance"])
    assert result["maximum_drawdown"] > 0
    assert result["profit_factor"] > 0
    assert result["evidence_kind"] == "SYNTHETIC_MOCK"
    assert result["data_quality"] == "SYNTHETIC_FIXTURE"
    assert result["provider_requests"] == 0
    assert result["trade_placement_enabled"] is False
    accepted = [e for e in saved["events"] if e["type"] == "ENTRY_ACCEPTED"]
    assert all(e["cost_eligibility"]["cost_inclusive_stop_risk"] <= capital * 0.01 for e in accepted)
    assert all(e["quantity"] == 1 for e in saved["events"] if e["type"] == "SETTLED")
    first_loss = next(e for e in saved["events"] if e["type"] == "SETTLED")
    assert first_loss["available_cash"] < capital
    assert saved["decisions"][1]["proposals"][0]["eligibility"]["available_cash"] < capital
    before = (output / "ai_led_offline_state.json").read_bytes()
    assert replay(plan, output_dir=output) == result
    assert (output / "ai_led_offline_state.json").read_bytes() == before
    assert not list(output.glob("*budget*"))


def test_order_independence_and_repeated_runs_have_identical_account_bytes(tmp_path):
    plan = create_synthetic_demo(tmp_path / "fixtures", 1000)
    a = replay(plan, output_dir=tmp_path / "a")
    raw = read(plan)
    for step in raw["steps"]:
        step["snapshots"].reverse()
    write(plan, raw)
    b = replay(plan, output_dir=tmp_path / "b")
    assert a == b
    assert (tmp_path / "a/ai_led_offline_state.json").read_bytes() == (tmp_path / "b/ai_led_offline_state.json").read_bytes()
    assert replay(plan, output_dir=tmp_path / "a") == a


def test_two_intraday_sessions_reset_quota_without_resetting_account(tmp_path):
    plan = create_synthetic_demo(tmp_path / "fixtures", 1000)
    raw = read(plan)
    next_day = []
    for original in raw["steps"]:
        step = {**original, "as_of": (pd.Timestamp(original["as_of"]) + pd.Timedelta(days=1)).isoformat(),
                "snapshots": []}
        for name in original["snapshots"]:
            snap = read(plan.parent / name)
            snap["observed_at"] = step["as_of"]
            for bar in snap["candles"]:
                bar["timestamp"] = (pd.Timestamp(bar["timestamp"]) + pd.Timedelta(days=1)).isoformat()
            filename = "next-day-" + name
            write(plan.parent / filename, snap)
            step["snapshots"].append(filename)
        next_day.append(step)
    raw["steps"].extend(next_day)
    write(plan, raw)
    report = replay(plan, output_dir=tmp_path / "account")
    saved = read(tmp_path / "account/ai_led_offline_state.json")["state"]
    assert report["total_accepted_trades"] == report["completed_trades"] == 6
    assert report["ending_account_balance"] == 1000 + report["net_pnl"]
    assert account._risk_state(saved, pd.Timestamp("2026-09-28").date()).trades_today == 3
    assert account._risk_state(saved, pd.Timestamp("2026-09-29").date()).trades_today == 3


def test_point_in_time_requests_never_contain_later_completed_bars(tmp_path):
    plan = create_synthetic_demo(tmp_path / "fixtures", 1000)
    replay(plan, output_dir=tmp_path / "account")
    saved = read(tmp_path / "account/ai_led_offline_state.json")["state"]
    for d in saved["decisions"]:
        as_of = pd.Timestamp(d["decision_time"])
        for market in d["inputs"]["market"].values():
            assert pd.Timestamp(market["observed_at"]) <= as_of
            assert all(pd.Timestamp(b["timestamp"]) + pd.Timedelta(minutes=5) <= as_of
                       for b in market["candles"])
        assert d["evidence_kind"] == "SYNTHETIC_MOCK"
    first = saved["decisions"][0]
    first_settled = next(e for e in saved["events"] if e["type"] == "SETTLED")
    assert pd.Timestamp(first_settled["timestamp"]) > pd.Timestamp(first["decision_time"])


def test_partial_restart_with_open_position_blocks_ai_and_settles_once(tmp_path):
    plan = create_synthetic_demo(tmp_path / "fixtures", 1000)
    raw = read(plan)
    intermediate = dict(raw["steps"][1])
    intermediate["as_of"] = "2026-09-28T12:20:00+05:30"
    intermediate["snapshots"] = []
    for old in raw["steps"][1]["snapshots"]:
        snap = read(plan.parent / old)
        snap["candles"] = snap["candles"][:37]
        snap["observed_at"] = intermediate["as_of"]
        name = "intermediate-" + old
        write(plan.parent / name, snap)
        intermediate["snapshots"].append(name)
    raw["steps"].insert(1, intermediate)
    write(plan, raw)
    spec, calendar, steps, context = _plan(plan)
    output = tmp_path / "account"
    for now, paths, provider, evidence in steps[:2]:
        result = account.run_offline_batch(paths, output_dir=output, instrument_priority=spec["instrument_priority"],
                                          capital=1000, now=now, provider=provider, calendar=calendar,
                                          replay_context=context, recorded_evidence=evidence)
    assert result["position"]["status"] == "OPEN"
    assert provider.calls == 0
    assert result["decision"]["reason"] == "Maximum open positions reached"
    report = replay(plan, output_dir=output)
    assert report["total_accepted_trades"] == report["completed_trades"] == 3
    assert replay(plan, output_dir=output) == report


def test_missing_continuation_and_overnight_never_fabricate_outcome(tmp_path):
    plan = create_synthetic_demo(tmp_path / "fixtures", 1000)
    spec, calendar, steps, context = _plan(plan)
    now, paths, provider, evidence = steps[0]
    output = tmp_path / "account"
    account.run_offline_batch(paths, output_dir=output, instrument_priority=spec["instrument_priority"],
                             capital=1000, now=now, provider=provider, calendar=calendar)
    # Accept one entry bar and persist it before attempting an overnight continuation.
    intermediate = []
    for old in steps[1][1]:
        snap = read(old); snap["candles"] = snap["candles"][:37]
        snap["observed_at"] = "2026-09-28T12:20:00+05:30"
        new = old.parent / ("entry-only-" + old.name); write(new, snap); intermediate.append(new)
    account.run_offline_batch(intermediate, output_dir=output, instrument_priority=spec["instrument_priority"],
                             capital=1000, now="2026-09-28T12:20:00+05:30", provider=account.SavedResponseProvider("{}"),
                             calendar=calendar)
    before = (output / "ai_led_offline_state.json").read_bytes()
    for old in steps[2][1]:
        snap = read(old)
        for b in snap["candles"]:
            b["timestamp"] = (pd.Timestamp(b["timestamp"]) + pd.Timedelta(days=1)).isoformat()
        snap["observed_at"] = (pd.Timestamp(snap["observed_at"]) + pd.Timedelta(days=1)).isoformat()
        new = old.parent / ("overnight-" + old.name); write(new, snap)
        intermediate[0 if snap["symbol"] == "RELIANCE" else 1] = new
    with pytest.raises(ValueError, match="accepted entry"):
        account.run_offline_batch(intermediate, output_dir=output, instrument_priority=spec["instrument_priority"],
                                 capital=1000, now="2026-09-29T12:40:00+05:30", provider=account.SavedResponseProvider("{}"),
                                 calendar=calendar)
    assert (output / "ai_led_offline_state.json").read_bytes() == before
    # A missing same-day bar also blocks progression and preserves the account.
    missing = []
    for old in steps[3][1]:
        snap = read(old); snap["candles"].pop(37)
        new = old.parent / ("missing-" + old.name); write(new, snap); missing.append(new)
    with pytest.raises(ValueError):
        account.run_offline_batch(missing, output_dir=output, instrument_priority=spec["instrument_priority"],
                                 capital=1000, now=steps[3][0], provider=account.SavedResponseProvider("{}"),
                                 calendar=calendar)
    assert (output / "ai_led_offline_state.json").read_bytes() == before


@pytest.mark.parametrize("bad", ["missing", "duplicate", "stale", "out_of_order", "future", "missing_bar"])
def test_bad_replay_data_fails_closed(tmp_path, bad):
    plan = create_synthetic_demo(tmp_path / "fixtures", 1000)
    raw = read(plan)
    if bad == "missing":
        raw["steps"][0]["snapshots"].pop()
    elif bad == "duplicate":
        raw["steps"][0]["snapshots"].append(raw["steps"][0]["snapshots"][0])
    elif bad == "out_of_order":
        raw["steps"].reverse()
    else:
        path = plan.parent / raw["steps"][0]["snapshots"][0]
        snapshot = read(path)
        if bad == "stale":
            snapshot["observed_at"] = (pd.Timestamp(raw["steps"][0]["as_of"]) - pd.Timedelta(minutes=16)).isoformat()
        elif bad == "future":
            snapshot["candles"][-1]["timestamp"] = (pd.Timestamp(raw["steps"][0]["as_of"]) + pd.Timedelta(minutes=5)).isoformat()
        else:
            snapshot["candles"].pop(5)
        write(path, snapshot)
    write(plan, raw)
    with pytest.raises(ValueError):
        replay(plan, output_dir=tmp_path / "account")
    assert not (tmp_path / "account/ai_led_offline_state.json").exists()


def test_calendar_holidays_special_sessions_and_coverage_are_explicit():
    cal = OfflineSessionCalendar(CALENDAR)
    assert cal.intervals("2024-11-20T12:00:00+05:30") == []
    with pytest.raises(ValueError, match="outside"):
        cal.candle_end("2024-11-20T12:00:00+05:30")
    assert cal.candle_end("2024-11-01T18:00:00+05:30").hour == 19
    assert cal.entry_allowed("2024-11-01T18:30:00+05:30")
    assert not cal.entry_allowed("2024-11-01T18:55:00+05:30")
    assert not cal.entry_allowed("2024-03-02T10:30:00+05:30")
    assert cal.candle_end("2024-03-02T11:30:00+05:30").hour == 12
    with pytest.raises(ValueError, match="absent"):
        cal.intervals("2026-10-26T12:00:00+05:30")
    assert not cal.entry_allowed("2026-09-28T08:30:00+05:30")
    assert not cal.entry_allowed("2026-09-28T15:25:00+05:30")


def test_real_short_special_session_does_not_bypass_35_bar_requirement(tmp_path):
    plan = create_synthetic_demo(tmp_path / "fixtures", 1000)
    path = plan.parent / read(plan)["steps"][0]["snapshots"][0]
    snap = read(path)
    times = pd.date_range("2024-11-01T18:00:00+05:30", periods=12, freq="5min")
    snap["candles"] = snap["candles"][:12]
    for bar, stamp in zip(snap["candles"], times):
        bar["timestamp"] = stamp.isoformat()
    snap["observed_at"] = "2024-11-01T19:00:00+05:30"
    write(path, snap)
    with pytest.raises(ValueError, match="35 candles"):
        account.run_offline_batch([path], output_dir=tmp_path / "account", instrument_priority=["RELIANCE"],
                                 capital=1000, now=snap["observed_at"], provider=account.SavedResponseProvider("{}"),
                                 calendar=OfflineSessionCalendar(CALENDAR))


def test_explicit_session_end_forces_observed_close(tmp_path):
    plan = create_synthetic_demo(tmp_path / "fixtures", 1000)
    raw = read(plan)
    raw["steps"] = raw["steps"][:3]
    write(plan, raw)
    shift_plan(plan, pd.Timedelta(hours=3))
    raw = read(plan)
    raw["steps"][-1]["as_of"] = "2026-09-28T15:30:00+05:30"
    for index, step in enumerate(raw["steps"]):
        for name in step["snapshots"]:
            path = plan.parent / name; snap = read(path)
            if index == 2:
                snap["candles"] = snap["candles"][:39]
                snap["observed_at"] = step["as_of"]
            for bar in snap["candles"]:
                bar.update(open=600, high=600.3, low=599.7, close=600)
            write(path, snap)
        response = read(plan.parent / step["response"])
        for p in response["proposals"]:
            p["holding_bars"] = 12
        write(plan.parent / step["response"], response)
    write(plan, raw)
    report = replay(plan, output_dir=tmp_path / "account")
    saved = read(tmp_path / "account/ai_led_offline_state.json")["state"]
    exit_event = next(e for e in saved["events"] if e["type"] == "SETTLED")
    assert exit_event["timestamp"] == "2026-09-28T15:25:00+05:30"
    assert exit_event["exit_reason"] == "horizon_or_session_end"
    assert report["completed_trades"] == 1
    assert report["open_position_count"] == report["pending_entry_count"] == 0


def shift_plan(plan, delta):
    raw = read(plan)
    for step in raw["steps"]:
        step["as_of"] = (pd.Timestamp(step["as_of"]) + delta).isoformat()
        for filename in step["snapshots"]:
            path = plan.parent / filename
            snapshot = read(path)
            snapshot["observed_at"] = (pd.Timestamp(snapshot["observed_at"]) + delta).isoformat()
            for bar in snapshot["candles"]:
                bar["timestamp"] = (pd.Timestamp(bar["timestamp"]) + delta).isoformat()
            write(path, snapshot)
    write(plan, raw)


def test_synthetic_special_evening_session_uses_explicit_intervals(tmp_path):
    plan = create_synthetic_demo(tmp_path / "fixtures", 1000)
    manifest = read(CALENDAR)
    special = next(s for s in manifest["sessions"] if s["date"] == "2026-09-26")
    special.update(session_type="SPECIAL", special_session=True, verification_status="VERIFIED",
                   normal_market_intervals=[{"start": "18:00", "end": "22:00"}], source_ids=["TEST_ONLY"])
    manifest["sources"].append({"source_id": "TEST_ONLY", "effective_dates": "2026-09-26",
                                "url": "fixture://SYNTHETIC-NOT-NSE"})
    calendar_path = tmp_path / "SYNTHETIC-calendar.json"
    write(calendar_path, manifest)
    shift_plan(plan, pd.Timedelta(days=-2, hours=8, minutes=45))
    raw = read(plan); raw["calendar"] = str(calendar_path); raw["calendar_evidence_kind"] = "SYNTHETIC_FIXTURE"
    write(plan, raw)
    report = replay(plan, output_dir=tmp_path / "account")
    assert report["completed_trades"] == 3
    assert report["calendar"]["evidence_kind"] == "SYNTHETIC_FIXTURE"


def test_holiday_and_outside_hours_do_not_create_paper_account(tmp_path):
    plan = create_synthetic_demo(tmp_path / "fixtures", 1000)
    delta = pd.Timestamp("2024-11-20") - pd.Timestamp("2026-09-28")
    shift_plan(plan, delta)
    with pytest.raises(ValueError, match="outside"):
        replay(plan, output_dir=tmp_path / "account")
    assert not (tmp_path / "account/ai_led_offline_state.json").exists()


def test_invalid_response_no_trade_and_rejections_have_separate_metrics(tmp_path):
    plan = create_synthetic_demo(tmp_path / "fixtures", 1000)
    raw = read(plan)
    (plan.parent / raw["steps"][0]["response"]).write_text("secret arbitrary invalid text")
    write(plan.parent / raw["steps"][1]["response"], {"schema_version": account.SCHEMA, "action": "NO_TRADE", "proposals": []})
    # Third opportunity is unaffordable at an entry equal to account capital.
    for step in raw["steps"][2:]:
        for name in step["snapshots"]:
            path = plan.parent / name; snap = read(path)
            for bar in snap["candles"]:
                bar.update(open=1000, high=1001, low=999, close=1000)
            write(path, snap)
        response = read(plan.parent / step["response"])
        for p in response["proposals"]:
            p.update(entry_price=1000, stop_loss=990, target_price=1020)
        write(plan.parent / step["response"], response)
    report = replay(plan, output_dir=tmp_path / "account")
    assert report["total_accepted_trades"] == 0
    assert report["rejected_actual_entries"] == 0
    assert report["rejected_proposals"] == 4
    assert report["profit_factor"] is report["win_rate_pct"] is None
    assert report["net_pnl"] == 0
    assert "invalid AI response; details redacted" in report["no_trade_reasons"]
    assert "secret arbitrary" not in (tmp_path / "account/ai_led_offline_state.json").read_text()


def test_plan_and_provider_budget_isolation(tmp_path):
    plan = create_synthetic_demo(tmp_path / "fixtures", 1000)
    unrelated = tmp_path / "request_budget.jsonl"
    unrelated.write_text("unchanged")
    replay(plan, output_dir=tmp_path / "account")
    raw = read(plan); raw["capital"] = 10000; write(plan, raw)
    with pytest.raises(ValueError, match="immutable"):
        replay(plan, output_dir=tmp_path / "account")
    assert unrelated.read_text() == "unchanged"


def test_loaded_plan_binds_snapshot_bytes_and_saved_ai_content(tmp_path):
    plan = create_synthetic_demo(tmp_path / "fixtures", 1000)
    spec, calendar, steps, context = _plan(plan)
    now, paths, provider, evidence = steps[0]
    provider.content = "{}"
    with pytest.raises(ValueError, match="frozen plan"):
        account.run_offline_batch(paths, output_dir=tmp_path / "changed-response",
                                 instrument_priority=spec["instrument_priority"], capital=1000,
                                 now=now, provider=provider, calendar=calendar, replay_context=context)
    spec, calendar, steps, context = _plan(plan)
    now, paths, provider, evidence = steps[0]
    snap = read(paths[0]); snap["candles"][-1]["volume"] += 1; write(paths[0], snap)
    with pytest.raises(ValueError, match="frozen plan"):
        account.run_offline_batch(paths, output_dir=tmp_path / "changed-snapshot",
                                 instrument_priority=spec["instrument_priority"], capital=1000,
                                 now=now, provider=provider, calendar=calendar, replay_context=context)


def test_duplicate_outcome_record_cannot_be_counted_again(tmp_path):
    plan = create_synthetic_demo(tmp_path / "fixtures", 1000)
    replay(plan, output_dir=tmp_path / "account")
    path = tmp_path / "account/ai_led_offline_state.json"
    envelope = read(path)
    duplicate = next(e for e in envelope["state"]["events"] if e["type"] == "SETTLED")
    envelope["state"]["events"].append(duplicate)
    envelope["sha256"] = account._digest(envelope["state"])
    write(path, envelope)
    with pytest.raises(ValueError, match="duplicate"):
        replay(plan, output_dir=tmp_path / "account")


def test_recorded_historical_mode_requires_exact_original_request_binding(tmp_path):
    plan = create_synthetic_demo(tmp_path / "fixtures", 1000)
    raw = read(plan)
    raw.update(ai_evidence_kind="RECORDED_HISTORICAL_ASSERTED", market_data_kind="SAVED_UNVERIFIED")
    for index, step in enumerate(raw["steps"]):
        stamp = pd.Timestamp(step["as_of"])
        paths = [plan.parent / p for p in step["snapshots"]]
        frames, snapshots = account._batch(paths, raw["instrument_priority"], stamp, 100000,
                                            OfflineSessionCalendar(CALENDAR))
        request = {"schema_version": account.SCHEMA,
                   "candidate_timestamp": frames["RELIANCE"].iloc[-1].timestamp.isoformat(),
                   "decision_time": stamp.isoformat(), "instrument_priority": raw["instrument_priority"], "market": snapshots}
        response = (plan.parent / step["response"]).read_text()
        import hashlib
        artifact = {"recorded_at": stamp.isoformat(), "model_id": raw["model_id"],
                    "request_sha256": account._digest({"system_prompt": account.PROMPT, "user_payload": account._encoded(request).decode()}),
                    "response_sha256": hashlib.sha256(response.encode()).hexdigest()}
        step["record_artifact"] = f"TEST-ASSERTED-record-{index}.json"
        write(plan.parent / step["record_artifact"], artifact)
    write(plan, raw)
    result = replay(plan, output_dir=tmp_path / "account")
    assert result["evidence_kind"] == "RECORDED_HISTORICAL_ASSERTED"
    assert any("not independently authenticated" in w for w in result["warnings"])
    artifact = plan.parent / raw["steps"][0]["record_artifact"]
    wrong = read(artifact); wrong["recorded_at"] = "2026-10-30T12:00:00+05:30"; write(artifact, wrong)
    with pytest.raises(ValueError, match="historical AI evidence"):
        replay(plan, output_dir=tmp_path / "wrong")
    assert not (tmp_path / "wrong/ai_led_offline_state.json").exists()


def test_replay_cli_offline_demo(tmp_path, monkeypatch, capsys):
    from scripts.replay_ai_led_offline import main
    monkeypatch.setattr("sys.argv", ["replay", "--synthetic-demo", str(tmp_path / "fixtures"),
                        "--paper-capital", "2500", "--output", str(tmp_path / "account")])
    assert main() == 0
    assert json.loads(capsys.readouterr().out)["initial_capital"] == 2500
