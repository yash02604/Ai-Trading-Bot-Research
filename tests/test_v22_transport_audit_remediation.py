"""Narrow audit fixes: source coverage and non-financial health failures."""
import ast
from pathlib import Path

import pandas as pd
import pytest

from test_v22_transport_controller import world


DEPENDENCIES = (
    'app/research/v2_transport_contract.py', 'app/research/v2_live_transport.py',
    'app/research/v2_transport_controller.py', 'app/research/causal_forward_paper_v2.py',
    'app/research/causal_forward_paper.py', 'app/research/prospective_paper_fills.py',
    'app/research/scheduled_forward_paper.py', 'app/research/paper_observer.py',
    'app/upstox_client.py', 'app/v2/provider.py', 'app/client_attestation.py',
    'app/candle_integrity.py', 'app/offline_market_data_v1.py', 'app/risk_engine.py',
    'app/cost_model.py', 'app/research/ai_led_guarded.py', 'app/research/ai_led_offline.py',
    'app/research/offline_session_calendar.py', 'app/execution_observations.py',
    'app/market_analysis.py', 'app/research/deepseek_prospective.py', 'strategy_backtest.py',
    'app/config.py', 'app/research/prospective_preflight.py', 'app/session_manifest_v3.py',
    'data/audits/nse_cm_session_manifest_v3.json',
)


@pytest.mark.parametrize('dependency', DEPENDENCIES)
def test_each_financial_dependency_invalidates_approval_before_new_ledger(world, monkeypatch, dependency):
    c=world.contract;original=Path.read_bytes;target=c.ROOT/dependency
    approved=c.integration_identity()
    def changed(path):
        raw=original(path)
        return raw+b'\n# benign synthetic reviewed dependency change\n' if path==target else raw
    monkeypatch.setattr(Path,'read_bytes',changed)
    assert c.integration_identity()!=approved
    assert c.readiness(world.cfg,world.now[0])['status']=='NO_GO'
    with pytest.raises(c.TransportError):world.ctl.run_cycle(world.cfg,world.root,lambda:world.now[0])
    assert not world.root.exists() and not world.calls
    monkeypatch.setattr(Path,'read_bytes',original)
    assert c.integration_identity()==approved
    assert c.readiness(world.cfg,world.now[0])['status']=='READY'


def test_approval_dependency_set_covers_engine_financial_sources(world):
    c=world.contract
    assert set(c.REVIEWED_DEPENDENCIES)==set(DEPENDENCIES)
    assert len(c.REVIEWED_DEPENDENCIES)==len(set(c.REVIEWED_DEPENDENCIES))
    tree=ast.parse((c.ROOT/'app/research/causal_forward_paper_v2.py').read_text())
    sources=[ast.literal_eval(n.value) for n in ast.walk(tree) if isinstance(n,ast.Assign)
             and any(isinstance(t,ast.Name) and t.id=='sources' for t in n.targets)]
    assert len(sources)==1
    assert {'app/'+name for name in sources[0]}<=set(c.REVIEWED_DEPENDENCIES)


def test_unrelated_source_and_reports_do_not_change_approval(world,monkeypatch):
    c=world.contract;original=Path.read_bytes;approved=c.integration_identity()
    unrelated={c.ROOT/'app/trade_logger.py',c.ROOT/'README.md',c.ROOT/'docs/V2_2_LIVE_TRANSPORT_INTEGRATION.md'}
    monkeypatch.setattr(Path,'read_bytes',lambda path:original(path)+(b'\n# unrelated\n' if path in unrelated else b''))
    assert c.integration_identity()==approved
    assert c.readiness(world.cfg,world.now[0])['status']=='READY'


@pytest.mark.parametrize('field,value',[('market_data_cap',31),('provider_cap',4),
    ('transport.model','wrong'),('transport.request_timeout_seconds',11),('transport.market_data_enabled',False)])
def test_relevant_config_and_provider_market_changes_require_new_review(world,field,value):
    if '.' in field:world.cfg['transport'][field.split('.')[1]]=value
    else:world.cfg[field]=value
    assert world.contract.readiness(world.cfg,world.now[0])['status']!='READY'
    assert not world.calls


def test_copied_approval_at_changed_configured_path_is_stale(world,tmp_path):
    target=tmp_path/'copied-approval.json'
    target.write_bytes(Path(world.cfg['transport']['review_approval']).read_bytes())
    world.cfg['transport']['review_approval']=str(target)
    assert world.contract.readiness(world.cfg,world.now[0])['status']=='NO_GO'


def test_source_identity_is_relative_byte_exact_and_order_canonical(world,monkeypatch,tmp_path):
    c=world.contract;approved=c.integration_identity();relocated=tmp_path/'same-source'
    for name in c.REVIEWED_DEPENDENCIES:
        target=relocated/name;target.parent.mkdir(parents=True,exist_ok=True)
        target.write_bytes((c.ROOT/name).read_bytes())
    monkeypatch.setattr(c,'ROOT',relocated)
    assert c.integration_identity()==approved
    monkeypatch.setattr(c,'REVIEWED_DEPENDENCIES',tuple(reversed(c.REVIEWED_DEPENDENCIES)))
    assert c.integration_identity()==approved
    target=relocated/'app/research/prospective_paper_fills.py';raw=target.read_bytes()
    # Hash actual source bytes, not accidental OS path/newline normalization.
    target.write_bytes(raw.replace(b'\r\n',b'\n') if b'\r\n' in raw else raw.replace(b'\n',b'\r\n'))
    assert c.integration_identity()!=approved
    target.write_bytes(raw)
    assert c.integration_identity()==approved


def session(world,monkeypatch,health_fault=None,recovery_fault=False,budget_fault=None):
    owner=world.scheduler.ownership
    monkeypatch.setattr(world.scheduler,'ownership',lambda root:owner(world.root.with_name('owner')))
    original=world.transport.LiveTransport.health;calls=[0]
    def health(obj):
        calls[0]+=1
        if budget_fault and calls[0]==4:
            budget=world.root/'transport/market_data_budget.jsonl'
            if budget_fault=='missing':budget.unlink()
            elif budget_fault=='empty':budget.write_bytes(b'')
            else:budget.write_text('corrupt synthetic budget record')
        fail=(health_fault=='all' or health_fault=='startup' and calls[0]==1
              or health_fault in {'final','malformed'} and calls[0]==5)
        if fail:
            if health_fault=='malformed':return {'raw_error':'fixture-private-token'}
            raise OSError('fixture-private-token')
        return original(obj)
    monkeypatch.setattr(world.transport.LiveTransport,'health',health)
    clocks=iter(['2026-11-02T12:20:00.1+05:30','2026-11-02T12:25:00.1+05:30'])
    def advance(seconds):world.now[0]=pd.Timestamp(next(clocks))
    if recovery_fault:
        original_replay=world.ctl.replay
        def failed_replay(*args,**kwargs):
            view=original_replay(*args,**kwargs)
            if view['performance']['settled_trades']:raise ValueError('synthetic financial verification failure')
            return view
        monkeypatch.setattr(world.ctl,'replay',failed_replay)
    result=world.scheduler.run_scheduled(world.cfg,world.root,clock=lambda:world.now[0],sleep=advance)
    return result,calls[0]


@pytest.mark.parametrize('fault',[None,'startup','final','all','malformed'])
def test_verified_financial_completion_survives_health_failures(world,monkeypatch,fault):
    result,health_calls=session(world,monkeypatch,fault)
    assert result['status']=='COMPLETED' and result['reason_codes']==[]
    assert health_calls==5  # startup, three cycles, final; no network side effect
    assert result['settled_trades']==1 and result['external_requests']==7
    assert result['request_counts']=={'market_data_requests':6,'provider_requests':1}
    view=world.ctl.replay(world.cfg,world.root)
    expected,candidates,settled=world.scheduler._projection(view,1000,'2026-11-02')
    assert result['account']==expected and result['candidates']==candidates
    assert expected['equity']==pytest.approx(1005.755590992)
    assert expected['reserved_cash']==0 and expected['open_positions']==0
    expected_failures=5 if fault=='all' else 0 if fault is None else 1
    assert result['observer_failures']==expected_failures
    assert result['warnings']==(['OBSERVER_UNAVAILABLE'] if fault else [])
    next_day=world.scheduler._identity(world.cfg,pd.Timestamp('2026-11-03T12:10:01+05:30'),False)
    world.scheduler._validate_control_root(world.root,next_day)
    calls=list(world.calls)
    world.now[0]=pd.Timestamp('2026-11-02T15:30:00+05:30')
    again=world.scheduler.run_scheduled(world.cfg,world.root,clock=lambda:world.now[0],sleep=lambda seconds:None)
    assert again['settled_trades']==1 and again['account']==expected
    assert world.calls==calls and world.ctl.replay(world.cfg,world.root)['performance']['settled_trades']==1
    assert not any(b'fixture-private' in p.read_bytes() for p in world.root.rglob('*') if p.is_file())


@pytest.mark.parametrize('health_fault',[None,'all'])
def test_genuine_recovery_failure_remains_primary_with_or_without_health_failure(world,monkeypatch,health_fault):
    result,health_calls=session(world,monkeypatch,health_fault,recovery_fault=True)
    assert result['status']=='FAILED_CLOSED' and result['reason_codes']==['RECOVERY_REQUIRED']
    assert result['settled_trades'] is None and result['account']['equity'] is None
    assert result['external_requests']==7
    assert result['observer_failures']==(health_calls if health_fault else 0)
    assert result['warnings']==(['OBSERVER_UNAVAILABLE'] if health_fault else [])
    next_day=world.scheduler._identity(world.cfg,pd.Timestamp('2026-11-03T12:10:01+05:30'),False)
    with pytest.raises(ValueError,match='RECOVERY_REQUIRED'):world.scheduler._validate_control_root(world.root,next_day)


def test_unreadable_counts_are_unknown_not_false_zero_or_financial_failure(world,monkeypatch):
    def unavailable(obj):raise OSError('fixture-private-token')
    monkeypatch.setattr(world.transport.LiveTransport,'request_counts',unavailable,raising=False)
    result,_=session(world,monkeypatch,'all')
    assert result['status']=='COMPLETED' and result['settled_trades']==1
    assert result['external_requests'] is None
    assert result['request_counts']=={'market_data_requests':None,'provider_requests':None}
    assert result['warnings']==['OBSERVER_UNAVAILABLE']


@pytest.mark.parametrize('fault',['missing','corrupt','empty'])
def test_counter_projection_cannot_report_false_zero_and_acquisition_still_fails_closed(world,monkeypatch,fault):
    result,_=session(world,monkeypatch,budget_fault=fault)
    assert result['status']=='COMPLETED' and result['settled_trades']==1
    assert result['external_requests'] is None
    assert result['request_counts']=={'market_data_requests':None,'provider_requests':None}
    assert result['warnings']==['OBSERVER_UNAVAILABLE']
    assert world.ctl.replay(world.cfg,world.root)['performance']['settled_trades']==1
    calls=list(world.calls)
    transport=world.transport.LiveTransport(world.root/'transport',world.cfg,lambda:world.now[0])
    with pytest.raises(world.contract.TransportError,match='REQUEST_CONFLICT'):
        transport.quote('RELIANCE','2026-11-02T12:20:00+05:30')
    assert world.calls==calls
