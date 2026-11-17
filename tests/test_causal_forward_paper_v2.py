"""V2 remediation proofs: synthetic inputs and tampered copies only."""
from concurrent.futures import ThreadPoolExecutor
import json
import math
from pathlib import Path
import shutil
import sqlite3

import pandas as pd
import pytest

from app.research import causal_forward_paper_v2 as v2
from app.research import causal_forward_paper as v1
from app.cost_model import estimate_round_trip_costs
from app.risk_engine import RiskConfig, RiskState, check_trade_allowed

READY = pd.Timestamp("2026-11-02T12:02:37+05:30")
START = READY.floor("5min") + v1.BAR
KEY = v1.core.STOCKS["RELIANCE"]


def book(path, capital=1000):
    return v2.CausalForwardPaperV2(path, capital=capital, provenance="SYNTHETIC_TEST_DATA")


def propose(b, decision="D", shift=0, quantity=1, **prices):
    return b.propose(decision_id=decision, decision_ready_at=READY + shift*v1.BAR,
                     instrument_key=KEY, quantity=quantity,
                     proposal={"symbol":"RELIANCE","score":.84,"entry_price":600,"stop_loss":597,
                               "target_price":605,"holding_bars":1,**prices})


def observe(b, slot, decision="D", delay="0s", missing=False, **prices):
    return b.observe(decision, expected_start=slot, as_of=slot+v1.BAR+pd.Timedelta(delay),
                     observation=None if missing else {
                         "symbol":"RELIANCE","instrument_key":KEY,"observed_at":(slot+v1.BAR).isoformat(),
                         "candle":{"timestamp":slot.isoformat(),"open":600,"high":601,
                                   "low":599,"close":600,"volume":1000,**prices}})


def closed(path, loss=False):
    b=book(path)
    propose(b);observe(b,START)
    result=observe(b,START+v1.BAR,low=596 if loss else 599,high=601 if loss else 606)
    return b,result


def mutate(path, sql):
    with sqlite3.connect(path) as db:
        triggers=list(db.execute("SELECT name,sql FROM sqlite_master WHERE type='trigger'"))
        for name,_ in triggers:db.execute('DROP TRIGGER "'+name+'"')
        sql(db)
        for _,definition in triggers:db.execute(definition)


def reseal_chain(directory):
    """Attack helper: recalculate hashes and anchor to test semantic replay, on copies only."""
    path=directory/"causal_forward_v2.sqlite3"
    with sqlite3.connect(path) as db:
        config=json.loads(db.execute("SELECT body FROM config").fetchone()[0])
        previous=v2.digest(config)
        rows=list(db.execute("SELECT seq,body FROM events ORDER BY seq"))
    def update(db):
        nonlocal previous
        for seq,body in rows:
            row=json.loads(body)
            row["seq"]=seq;row["previous"]=previous
            row["hash"]=v2.digest({k:v for k,v in row.items() if k!="hash"})
            db.execute("UPDATE events SET body=? WHERE seq=?",(v2.encode(row),seq))
            previous=row["hash"]
    mutate(path,update)
    (directory/"anchor.json").write_bytes(v2.encode({"config":v2.digest(config),"seq":len(rows),"hash":previous}))


@pytest.mark.parametrize("kind",["exit","settlement","entry","middle","final","truncate","reorder","substitute"])
def test_deleted_or_altered_history_fails_closed(tmp_path,kind):
    b,_=closed(tmp_path/"original")
    copied=tmp_path/"copy";shutil.copytree(b.directory,copied)
    def attack(db):
        if kind in ("exit","settlement","middle"):
            role={"exit":"EXIT","settlement":"SETTLEMENT","middle":"COMMAND"}[kind]
            db.execute("DELETE FROM events WHERE seq=(SELECT seq FROM events WHERE json_extract(CAST(body AS TEXT),'$.kind')=? ORDER BY seq DESC LIMIT 1)",(role,))
        elif kind=="entry":
            db.execute("DELETE FROM events WHERE seq=2")
        elif kind=="final":db.execute("DELETE FROM events WHERE seq=(SELECT MAX(seq) FROM events)")
        elif kind=="reorder":
            db.execute("UPDATE events SET seq=seq+10 WHERE seq=4")
        elif kind=="substitute":
            seq,raw=db.execute("SELECT seq,body FROM events WHERE json_extract(CAST(body AS TEXT),'$.kind')='EXIT'").fetchone()
            event=json.loads(raw);event["payload"]["result"]["exit_price"]=900
            db.execute("UPDATE events SET body=? WHERE seq=?",(v2.encode(event),seq))
    if kind=="truncate":
        p=copied/b.path.name;p.write_bytes(p.read_bytes()[:p.stat().st_size//2])
    else:mutate(copied/b.path.name,attack)
    with pytest.raises((ValueError,sqlite3.DatabaseError)):
        book(copied)


@pytest.mark.parametrize("loss",[False,True])
def test_exactly_once_under_replay_concurrent_ack_loss_and_reopen(tmp_path,loss):
    b,r=closed(tmp_path,loss)
    equity=1000+r["net_pnl"]
    for _ in range(3):
        b=book(tmp_path)
        assert observe(b,START+v1.BAR,low=596 if loss else 599,high=601 if loss else 606)==r
        assert b.account(START+2*v1.BAR)["balance"]==equity
        assert b.performance()["settled_trades"]==1
    with ThreadPoolExecutor(2) as pool:
        results=list(pool.map(lambda _:observe(book(tmp_path),START+v1.BAR,low=596 if loss else 599,high=601 if loss else 606),range(2)))
    assert results==[r,r]
    with sqlite3.connect(b.path) as db:
        assert db.execute("SELECT COUNT(*) FROM settlements").fetchone()[0]==1


@pytest.mark.parametrize("kind",["copied_closure","copied_settlement","duplicate_command","row_identity","duplicate_loss"])
def test_appended_rows_cannot_duplicate_economic_effect(tmp_path,kind):
    b,_=closed(tmp_path,kind=="duplicate_loss")
    with sqlite3.connect(b.path) as db:
        if kind in ("copied_closure","copied_settlement","duplicate_loss"):
            role="SETTLEMENT" if kind=="copied_settlement" else "EXIT"
            raw=db.execute("SELECT body FROM events WHERE json_extract(CAST(body AS TEXT),'$.kind')=?",(role,)).fetchone()[0]
            seq=db.execute("SELECT MAX(seq)+1 FROM events").fetchone()[0]
            db.execute("INSERT INTO events VALUES (?,?,?)",(seq,"copied",raw))
        elif kind=="duplicate_command":
            raw=db.execute("SELECT body FROM events ORDER BY seq LIMIT 1").fetchone()[0]
            db.execute("INSERT INTO events VALUES (?,?,?)",(99,"duplicate-command",raw))
        else:
            raw=db.execute("SELECT body FROM settlements").fetchone()[0]
            db.execute("INSERT INTO settlements VALUES (?,?,?)",("other","other",raw))
    with pytest.raises(ValueError):
        book(tmp_path)


@pytest.mark.parametrize("field",["balance","eligible_equity","daily_loss","reserved_cash","open_positions",
                                  "net_pnl","quantity","entry_price","exit_price","modeled_costs","state",
                                  "recorded_at","entry_accepted_at","exit_time","settlement_count"])
def test_rehashed_cache_is_never_authoritative(tmp_path,field):
    b,_=closed(tmp_path)
    def attack(db):
        seq,raw=db.execute("SELECT seq,body FROM checkpoints ORDER BY seq DESC LIMIT 1").fetchone()
        cache=json.loads(raw)
        if field in cache["account"]:cache["account"][field]=2000
        elif field=="settlement_count":cache["performance"]["settled_trades"]=7
        else:cache["result"][field]="2026-11-01T10:00:00+05:30" if "at" in field or "time" in field else 2000
        db.execute("UPDATE checkpoints SET body=?,sha256=? WHERE seq=?",(v2.encode(cache),v2.digest(cache),seq))
    mutate(b.path,attack)
    with pytest.raises(ValueError,match="semantic checkpoint"):
        book(tmp_path)


@pytest.mark.parametrize("kind",["exit_economics","settlement_economics","settlement_time","entry_time"])
def test_rehashed_chain_semantics_checked_even_with_rewritten_anchor(tmp_path,kind):
    b,_=closed(tmp_path)
    def attack(db):
        role="EXIT" if kind=="exit_economics" else "SETTLEMENT"
        seq,raw=db.execute("SELECT seq,body FROM events WHERE json_extract(CAST(body AS TEXT),'$.kind')=?",(role,)).fetchone()
        event=json.loads(raw)
        if kind=="exit_economics":event["payload"]["result"]["net_pnl"]=1000000
        elif kind=="entry_time":event["payload"]["entry_id"]="fake"
        elif kind=="settlement_time":event["payload"]["settled_at"]="2026-11-01T12:00:00+05:30"
        else:event["payload"]["net_pnl"]=1000000
        db.execute("UPDATE events SET body=? WHERE seq=?",(v2.encode(event),seq))
    mutate(b.path,attack);reseal_chain(tmp_path)
    with pytest.raises(ValueError,match="replay"):
        book(tmp_path)


@pytest.mark.parametrize("point",["intent_before","intent_after","command","exit","settlement","checkpoint",
                                 "settlement_row","anchor_before","anchor_after","ack"])
def test_crash_windows_atomic_and_recoverable(tmp_path,monkeypatch,point):
    b=book(tmp_path);propose(b);observe(b,START)
    original_atomic=v2._atomic
    if point in ("intent_before","intent_after","anchor_before","anchor_after"):
        def atomic(path,value):
            selected=(path.name=="pending.json") if point.startswith("intent") else path.name=="anchor.json"
            if selected and point.endswith("before"):raise KeyboardInterrupt()
            original_atomic(path,value)
            if selected and point.endswith("after"):raise KeyboardInterrupt()
        monkeypatch.setattr(v2,"_atomic",atomic)
    elif point in ("command","exit","settlement","checkpoint","settlement_row"):
        original=b._insert
        def insert(db,events,checkpoints,settlements):
            if point in ("command","exit","settlement"):
                stop={"command":1,"exit":2,"settlement":3}[point]
                original(db,events[:stop],{}, {})
            elif point=="checkpoint":original(db,events,checkpoints,{})
            else:original(db,events,checkpoints,settlements)
            raise KeyboardInterrupt()
        monkeypatch.setattr(b,"_insert",insert)
    elif point=="ack":
        # A committed result whose caller loses its acknowledgement.
        r=observe(b,START+v1.BAR,high=606)
        assert r["state"]=="CLOSED_TARGET"
    if point!="ack":
        with pytest.raises(KeyboardInterrupt):
            observe(b,START+v1.BAR,high=606)
    monkeypatch.undo()
    recovered=book(tmp_path)
    r=observe(recovered,START+v1.BAR,high=606)
    assert recovered.account(START+2*v1.BAR)["balance"]==1000+r["net_pnl"]
    assert recovered.performance()["settled_trades"]==1
    assert not recovered.pending.exists()


def profit_target(entry=600,profit=5):
    lo,hi=entry,entry+30
    for _ in range(80):
        mid=(lo+hi)/2
        net=mid-entry-estimate_round_trip_costs(entry,mid,1,START.date())["total_cost"]
        if net<profit:lo=mid
        else:hi=mid
    return min((lo,hi),key=lambda x:abs(x-entry-estimate_round_trip_costs(entry,x,1,START.date())["total_cost"]-profit))


def test_balance_proof_1000_to_1005_once(tmp_path):
    target=profit_target()
    b=book(tmp_path);propose(b,target_price=target);observe(b,START)
    r=observe(b,START+v1.BAR,high=target)
    assert r["net_pnl"]==pytest.approx(5,abs=1e-12)
    assert b.account(START+2*v1.BAR)["balance"]==1005
    assert observe(book(tmp_path),START+v1.BAR,high=target)==r
    assert book(tmp_path).account(START+2*v1.BAR)["balance"]==1005
    with sqlite3.connect(b.path) as db:
        cache=json.loads(db.execute("SELECT body FROM checkpoints ORDER BY seq DESC LIMIT 1").fetchone()[0])
        assert cache["account"]["balance"]==1005
        assert len(cache["settlements"])==1


@pytest.mark.parametrize("capital",[1000,2500,10000,50000])
@pytest.mark.parametrize("history",["none","loss","multiple_losses","profit"])
def test_reconstructed_equity_matrix(tmp_path,capital,history):
    b=book(tmp_path,capital)
    net=0.
    n=2 if history=="multiple_losses" else 0 if history=="none" else 1
    for i in range(n):
        shift=4*i
        propose(b,decision=str(i),shift=shift)
        observe(b,START+shift*v1.BAR,decision=str(i))
        r=observe(b,START+(shift+1)*v1.BAR,decision=str(i),
                  low=596 if "loss" in history else 599, high=601 if "loss" in history else 606)
        net+=r["net_pnl"]
    at=START+(4*n)*v1.BAR
    account=book(tmp_path,capital).account(at)
    assert account["eligible_equity"]==account["balance"]==capital+net
    assert account["available_cash"]==capital+net
    propose(b,decision="next",shift=4*n)
    r=observe(b,at,decision="next")
    assert r["entry_account"]["eligible_equity"]==capital+net
    assert r["entry_check"]["max_trade_risk"]==round((capital+net)*.01,6)


@pytest.mark.parametrize("capital",[1000,2500,10000,50000])
@pytest.mark.parametrize("unresolved",[False,True])
def test_open_and_unresolved_reservations(tmp_path,capital,unresolved):
    b=book(tmp_path,capital);propose(b);observe(b,START)
    at=START+v1.BAR
    if unresolved:
        observe(b,at,missing=True);at+=v1.BAR
    r=book(tmp_path,capital).account(at)
    assert r["eligible_equity"]==capital
    assert r["available_cash"]==capital-r["reserved_cash"]
    assert r["reserved_cash"]>600 and r["open_positions"]==1
    p=b.performance()
    assert p["settled_trades"]==0 and p["realized_pnl"]==0
    assert p["win_rate"] is p["expectancy"] is None
    assert p["unresolved"]==int(unresolved)


def test_current_equity_rejects_the_audited_stale_initial_risk(tmp_path):
    b,r=closed(tmp_path,loss=True)
    equity=1000+r["net_pnl"]
    stop=893.3399048877327
    propose(b,decision="second",shift=4,entry_price=900,stop_loss=stop,target_price=918)
    r=observe(b,START+4*v1.BAR,decision="second",open=900,low=899,high=901,close=900)
    assert r["state"]=="ENTRY_REJECTED"
    assert r["entry_check"]["cost_inclusive_stop_risk"]==9.99
    assert r["entry_check"]["max_trade_risk"]==round(equity*.01,6)


@pytest.mark.parametrize("capital",[1000,2500,10000,50000])
@pytest.mark.parametrize("offset",[-1e-6,0,1e-6])
def test_cost_inclusive_one_percent_boundary(tmp_path,capital,offset,monkeypatch):
    # Supply exact arithmetic at the shared cost boundary to prove comparisons,
    # while independent normal-cost cases prove the retained fee calculation.
    from app.research import deepseek_prospective as costs
    base=capital*.01
    entry=capital*.5;stop=entry-base*.5
    amount=base*.5+offset
    def buying(*a,**k):
        return dict.fromkeys(("brokerage","exchange","ipft","sebi","stamp_duty","gst","slippage"),0.)|{"total_cost":0.}
    def roundtrip(*a,**k):
        return dict.fromkeys(("brokerage","stt","exchange","ipft","sebi","stamp_duty","gst","slippage"),0.)|{"total_cost":amount}
    monkeypatch.setattr(costs,"estimate_buy_side_costs",buying)
    monkeypatch.setattr(costs,"estimate_round_trip_costs",roundtrip)
    b=book(tmp_path,capital)
    propose(b,entry_price=entry,stop_loss=stop,target_price=entry*1.03)
    r=observe(b,START,open=entry,low=entry,high=entry,close=entry)
    assert (r["state"]=="OPEN")== (offset<=0)


def test_daily_limit_retains_initial_reference_and_replays_on_restart(tmp_path):
    b=book(tmp_path);propose(b);observe(b,START)
    r=observe(b,START+v1.BAR,open=550,low=549,high=600,close=550)
    account=book(tmp_path).account(START+2*v1.BAR)
    assert account["daily_loss"]==-r["net_pnl"]>20
    propose(b,decision="second",shift=4)
    assert observe(b,START+4*v1.BAR,decision="second")["reason"]=="Daily loss limit reached"
    nextday="2026-11-03T12:00:00+05:30"
    assert book(tmp_path).account(nextday)["daily_loss"]==0
    for loss,allowed in [(19.9999,True),(20.,False),(20.0001,False)]:
        assert check_trade_allowed(RiskConfig(capital=1000),RiskState(daily_loss=loss))[0] is allowed


@pytest.mark.parametrize("capital", [1000, 2500, 10000, 50000])
@pytest.mark.parametrize("prior_profit", [False, True])
@pytest.mark.parametrize("offset", [-1e-6, 1e-6])
def test_real_cost_risk_boundary_after_realized_outcome(tmp_path, capital, prior_profit, offset):
    b = book(tmp_path, capital)
    propose(b)
    observe(b, START)
    first = observe(b, START + v1.BAR, low=599 if prior_profit else 596,
                    high=606 if prior_profit else 601)
    equity = capital + first["net_pnl"]
    entry = equity * .9
    risk_target = equity * .01 + offset
    lo, hi = entry * .99, entry * .9995
    for _ in range(80):
        stop = (lo + hi) / 2
        risk = entry - stop + estimate_round_trip_costs(entry, stop, 1, START.date())["total_cost"]
        if risk > risk_target:
            lo = stop
        else:
            hi = stop
    propose(b, decision="next", shift=4, entry_price=entry, stop_loss=hi, target_price=entry*1.02)
    result = observe(b, START + 4*v1.BAR, decision="next",
                     open=entry, high=entry, low=entry, close=entry)
    assert (result["state"] == "OPEN") == (offset < 0)
    assert result["entry_account"]["eligible_equity"] == equity
    assert result["entry_check"]["cost_inclusive_stop_risk"] == pytest.approx(risk_target, abs=1e-6)


@pytest.mark.parametrize("operation", ["proposal", "entry"])
def test_crash_during_acceptance_and_reservation(tmp_path, monkeypatch, operation):
    b = book(tmp_path)
    if operation == "entry":
        propose(b)
    original = b._insert
    def interrupted(db, events, checkpoints, settlements):
        original(db, events, checkpoints, settlements)
        raise KeyboardInterrupt()
    with monkeypatch.context() as patch:
        patch.setattr(b, "_insert", interrupted)
        with pytest.raises(KeyboardInterrupt):
            propose(b) if operation == "proposal" else observe(b, START)
    recovered = book(tmp_path)
    if operation == "proposal":
        assert recovered.history("D") == []
        propose(recovered)
    else:
        assert recovered.account(START)["reserved_cash"] == 0
    assert observe(recovered, START)["state"] == "OPEN"
    assert recovered.account(START + v1.BAR)["trades_today"] == 1


def test_process_death_with_exit_and_settlement_uncommitted(tmp_path):
    import subprocess
    import sys
    b = book(tmp_path)
    propose(b)
    observe(b, START)
    child = """
import os,sys,socket,runpy
from pathlib import Path
import dotenv
dotenv.load_dotenv=lambda *a,**k:False
os.environ.update(UPSTOX_ACCESS_TOKEN='',DEEPSEEK_API_KEY='')
def denied(*a,**k): raise AssertionError('offline child')
socket.socket.connect=socket.create_connection=denied
sys.path.insert(0,str(Path.cwd()))
h=runpy.run_path(sys.argv[1])
b=h['book'](sys.argv[2])
original=b._insert
def interrupted(db,events,checkpoints,settlements):
    original(db,events,checkpoints,settlements)
    os._exit(83)
b._insert=interrupted
h['observe'](b,h['START']+h['v1'].BAR,high=606)
"""
    r = subprocess.run([sys.executable, "-c", child, str(Path(__file__).resolve()), str(tmp_path)],
                       capture_output=True, timeout=60)
    assert r.returncode == 83, r.stderr
    recovered = book(tmp_path)
    result = observe(recovered, START + v1.BAR, high=606)
    assert recovered.account(START + 2*v1.BAR)["balance"] == 1000 + result["net_pnl"]
    assert recovered.performance()["settled_trades"] == 1


@pytest.mark.parametrize("attack", ["complete_prefix_rollback", "missing_anchor"])
def test_completion_witness_detects_otherwise_valid_history_prefix(tmp_path, attack):
    b, _ = closed(tmp_path)
    if attack == "missing_anchor":
        b.anchor.unlink()
    else:
        # The remaining input chain and checkpoints are mutually valid. Only
        # the retained independent head proves that a closure used to exist.
        def rollback(db):
            db.execute("DELETE FROM events WHERE seq > 2")
            db.execute("DELETE FROM checkpoints WHERE seq > 2")
            db.execute("DELETE FROM settlements")
        mutate(b.path, rollback)
    with pytest.raises(ValueError, match="anchor|extent"):
        book(tmp_path)
