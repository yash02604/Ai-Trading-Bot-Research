import json
from pathlib import Path

import pandas as pd
import pytest

from test_ai_led_reviewed import setup, options, used, PRIORITY
from app.research import ai_led_reviewed as reviewed, ai_led_guarded as guarded, ai_led_offline as core
from app.research.prospective_preflight import settlement_acquisition_window
from app.research.offline_session_calendar import OfflineSessionCalendar
from app.client_attestation import ROOT


def failed_first_commit(s, monkeypatch):
    original=core._commit
    def fail(*args):raise OSError("simulated account storage failure")
    monkeypatch.setattr(core,"_commit",fail)
    with pytest.raises(OSError):reviewed.run_reviewed_paper(**options(s,include_quotes=True))
    monkeypatch.setattr(core,"_commit",original)


def recovery_options(s):
    result=options(s,include_quotes=True)
    result.update(allow_market_data_requests=False,allow_provider_requests=False)
    return result


def test_recover_saved_response_after_quotes_expire_without_new_requests(setup,monkeypatch):
    failed_first_commit(setup,monkeypatch)
    cache=next((setup.output/"responses").glob("*.json"));before=cache.read_bytes()
    request=next((setup.output/"requests").glob("*.json"));request_before=request.read_bytes()
    setup.clock[0]+=pd.Timedelta(seconds=10)
    result=reviewed.run_reviewed_paper(**options(setup,include_quotes=True))
    assert result["recovered"] and result["entry_authorized"]
    assert result["position"]["status"]=="PENDING"
    assert len(setup.http)==4 and len(setup.sdk)==1 and used(setup,"request_budget")==1
    assert cache.read_bytes()==before and request.read_bytes()==request_before
    assert reviewed.recover_reviewed_paper(**recovery_options(setup))["idempotent_replay"]
    assert len(setup.sdk)==1


def test_expired_execution_window_records_original_ai_buy_but_rejects_entry(setup,monkeypatch):
    failed_first_commit(setup,monkeypatch)
    setup.clock[0]+=pd.Timedelta(minutes=6)
    result=reviewed.recover_reviewed_paper(**recovery_options(setup))
    assert result["entry_authorized"] is False and result["position"] is None
    assert result["decision"]["ai_response"]["action"]=="BUY"
    assert result["decision"]["proposals"][0]["eligibility"]["reason"]=="entry execution window expired during recovery"
    assert result["decision"]["status"]=="NO_TRADE"
    assert len(setup.http)==4 and len(setup.sdk)==1 and used(setup,"request_budget")==1


@pytest.mark.parametrize("kind",["cache", "seal", "request", "input", "receipt", "manifest", "missing_response"])
def test_recovery_refuses_corrupted_missing_or_conflicting_evidence(setup,monkeypatch,kind):
    failed_first_commit(setup,monkeypatch)
    folder={"cache":"responses","request":"requests","input":"inputs"}.get(kind)
    if folder:
        path=next((setup.output/folder).glob("*.json"))
        if kind=="cache":
            record=guarded._read(path);record["core_response"]=record["core_response"].replace('594','595')
            path.write_bytes(core._encoded({"record":record,"sha256":core._digest(record)}))
        else:path.write_text("{}")
    elif kind=="seal":(setup.output/"decision_evidence_seals.jsonl").write_text("")
    elif kind=="receipt":(setup.output/"transport_receipts.jsonl").write_text("")
    elif kind=="manifest":(setup.output/guarded.MANIFEST).write_text("{}")
    else:next((setup.output/"responses").glob("*.json")).unlink()
    with pytest.raises((guarded.GuardedBatchError,ValueError,KeyError)):
        reviewed.recover_reviewed_paper(**recovery_options(setup))
    assert len(setup.sdk)==1 and len(setup.http)==4
    assert not (setup.output/"paper_account/ai_led_offline_state.json").exists()


def at(s, stamp):
    now=pd.Timestamp(stamp);s.clock[0]=now
    candidate=now.floor("5min")-pd.Timedelta(minutes=5)
    end=now.normalize()+pd.Timedelta(hours=15,minutes=30)
    candidate=min(candidate,end-pd.Timedelta(minutes=5))
    times=pd.date_range(now.normalize()+pd.Timedelta(hours=9,minutes=15),candidate,freq="5min")
    for symbol in PRIORITY:
        s.data[symbol]={"status":"success","data":{"candles":[
            [t.isoformat(),600,601,599,600,10000,0] for t in times]}}
        next(iter(s.quotes[symbol]["data"].values()))["timestamp"]=now.isoformat()
    s.body[0]["candidate_timestamp"]=candidate.isoformat()
    s.body[0]["proposals"][0]["holding_bars"]=12


def open_position(s, day="2026-11-02"):
    at(s,day+"T14:45:01+05:30")
    opts=options(s,include_quotes=True);opts["market_data_cap"]=12
    reviewed.run_reviewed_paper(**opts)
    at(s,day+"T14:55:01+05:30")
    result=reviewed.run_reviewed_paper(**opts)
    assert result["position"]["status"]=="OPEN"
    return opts


def settlement_options(s):
    return dict(output_dir=s.output,instrument_priority=PRIORITY,capital=1000,provider_cap=1,market_data_cap=12,
                include_quotes=True,enable_settlement_only=True,allow_settlement_market_data_requests=True)


@pytest.mark.parametrize("day,close",[("2026-11-02","15:15"),("2026-12-07","15:30")])
def test_final_completed_candle_settles_after_entry_cutoff_no_quote_or_deepseek(setup,day,close):
    open_position(setup,day)
    at(setup,day+"T"+close+":01+05:30")
    before=len(setup.http);providers=len(setup.sdk)
    # Return only actual normal-session candles, not auction phases.
    final=pd.Timestamp(day+"T"+close+":00+05:30")-pd.Timedelta(minutes=5)
    for response in setup.data.values():
        response["data"]["candles"]=[r for r in response["data"]["candles"] if pd.Timestamp(r[0])<=final]
    result=reviewed.run_reviewed_settlement(**settlement_options(setup))
    assert result["position"] is None and result["status"]=="SETTLEMENT_ONLY"
    assert setup.http[before:]==[("candles","RELIANCE")] and len(setup.sdk)==providers
    assert used(setup,"market_data_request_budget")==9 and used(setup,"request_budget")==1
    state=guarded._account(setup.output,guarded._read(setup.output/guarded.MANIFEST))
    outcomes=[e for e in state["events"] if e["type"]=="SETTLED"]
    assert len(outcomes)==1 and outcomes[0]["exit_reason"]=="horizon_or_session_end"
    assert outcomes[0]["timestamp"]==final.isoformat()
    with pytest.raises(guarded.GuardedBatchError):reviewed.run_reviewed_settlement(**settlement_options(setup))
    assert len(setup.http)==before+1 and len(setup.sdk)==providers


@pytest.mark.parametrize("kind",["no_position","pending","expired","unauthorized","stale","invalid","future"])
def test_settlement_fails_closed_without_authorizing_ai_or_new_trades(setup,kind):
    opts=settlement_options(setup)
    if kind=="no_position":
        with pytest.raises((guarded.GuardedBatchError,OSError)):reviewed.run_reviewed_settlement(**opts)
        assert setup.http==setup.sdk==[];return
    if kind=="pending":
        at(setup,"2026-11-02T14:45:01+05:30")
        normal=options(setup,include_quotes=True);normal["market_data_cap"]=12
        reviewed.run_reviewed_paper(**normal)
    else:open_position(setup)
    at(setup,"2026-11-02T15:15:01+05:30")
    if kind=="pending":setup.clock[0]=pd.Timestamp("2026-11-02T15:09:01+05:30")
    if kind=="expired":setup.clock[0]=pd.Timestamp("2026-11-02T15:25:01+05:30")
    if kind=="unauthorized":opts["allow_settlement_market_data_requests"]=False
    if kind=="stale":setup.data["RELIANCE"]["data"]["candles"]=setup.data["RELIANCE"]["data"]["candles"][:-5]
    if kind=="invalid":setup.data["RELIANCE"]["data"]["candles"][-1][2]=-1
    if kind=="future":setup.data["RELIANCE"]["data"]["candles"][-1][0]="2026-11-02T15:20:00+05:30"
    calls=len(setup.http);sdk=len(setup.sdk)
    with pytest.raises(guarded.GuardedBatchError):reviewed.run_reviewed_settlement(**opts)
    assert len(setup.sdk)==sdk
    assert len(setup.http)==calls+(1 if kind in {"stale","invalid","future"} else 0)


def test_settlement_commit_failure_resumes_from_saved_input_without_second_get(setup,monkeypatch):
    open_position(setup);at(setup,"2026-11-02T15:15:01+05:30")
    commit=core._commit
    monkeypatch.setattr(core,"_commit",lambda *a:(_ for _ in ()).throw(OSError("simulated")))
    with pytest.raises(guarded.GuardedBatchError):reviewed.run_reviewed_settlement(**settlement_options(setup))
    calls=len(setup.http)
    monkeypatch.setattr(core,"_commit",commit)
    setup.clock[0]+=pd.Timedelta(seconds=10)
    result=reviewed.run_reviewed_settlement(**settlement_options(setup))
    assert result["position"] is None and len(setup.http)==calls and len(setup.sdk)==1


def test_unsupported_session_never_inherits_generic_settlement_hours():
    cal=OfflineSessionCalendar(ROOT/"data/audits/nse_cm_session_manifest_v3.json")
    with pytest.raises(ValueError):settlement_acquisition_window(cal,"2026-11-08T14:30:00+05:30")


def test_local_recovery_does_not_require_api_credentials(setup,monkeypatch):
    failed_first_commit(setup,monkeypatch)
    setup.clock[0]+=pd.Timedelta(seconds=10)
    setup.settings.upstox_access_token=None;setup.settings.deepseek_api_key=None
    result=reviewed.recover_reviewed_paper(**recovery_options(setup))
    assert result["position"]["status"]=="PENDING" and len(setup.http)==4 and len(setup.sdk)==1


def test_duplicate_partial_settlement_is_rejected_without_another_request(setup):
    open_position(setup);at(setup,"2026-11-02T15:05:01+05:30")
    result=reviewed.run_reviewed_settlement(**settlement_options(setup))
    assert result["position"]["status"]=="OPEN"
    calls=len(setup.http)
    with pytest.raises(guarded.GuardedBatchError,match="duplicate_settlement_observation"):
        reviewed.run_reviewed_settlement(**settlement_options(setup))
    assert len(setup.http)==calls and len(setup.sdk)==1


def test_settlement_needs_no_deepseek_key_and_direct_send_cannot_change_instrument(setup):
    open_position(setup);at(setup,"2026-11-02T15:15:01+05:30")
    setup.settings.deepseek_api_key=None
    opts=settlement_options(setup)
    binding=reviewed._ReviewedRun(purpose="settlement",**opts)
    with pytest.raises(guarded.GuardedBatchError,match="settlement_instrument_mismatch"):
        binding.fetch("HDFCBANK")
    result=reviewed.run_reviewed_settlement(**opts)
    assert result["position"] is None and len(setup.sdk)==1


def test_cli_purposes_cannot_mix_settlement_and_provider_authorization(setup,monkeypatch):
    import sys
    from scripts.run_ai_led_prospective_paper import main
    monkeypatch.setattr(sys,"argv",["runner","--output",str(setup.output),"--instrument-priority",*PRIORITY,
        "--paper-capital","1000","--max-provider-requests","1","--max-market-data-requests","12",
        "--settlement-only","--allow-settlement-market-data-requests","--allow-provider-requests"])
    with pytest.raises(SystemExit):main()
    assert setup.http==setup.sdk==[]


def test_missing_last_candle_does_not_publish_invalid_settlement_transaction(setup):
    open_position(setup);at(setup,"2026-11-02T15:15:01+05:30")
    setup.data["RELIANCE"]["data"]["candles"].pop()
    with pytest.raises(guarded.GuardedBatchError,match="latest_completed_settlement_candle_missing"):
        reviewed.run_reviewed_settlement(**settlement_options(setup))
    assert not list((setup.output/"settlement_prepared").glob("*.json"))
    assert guarded._account(setup.output,guarded._read(setup.output/guarded.MANIFEST))["position"]["status"]=="OPEN"
    assert used(setup,"market_data_request_budget")==9 and len(setup.sdk)==1


def test_settlement_candidate_is_fixed_across_clock_boundary(setup):
    open_position(setup);at(setup,"2026-11-02T15:04:59+05:30")
    binding=reviewed._ReviewedRun(purpose="settlement",**settlement_options(setup))
    with core._experiment_lock(setup.output):
        binding._preflight();candidate=binding.candidate
        setup.clock[0]+=pd.Timedelta(seconds=2)
        binding._preflight()
        assert binding.candidate==candidate
