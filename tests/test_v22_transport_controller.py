"""Mocked real-client orchestration, no external requests or real account data."""
import importlib.util
import json
from types import SimpleNamespace
import pandas as pd
import pytest
import requests
from test_v22_transport_engine import configuration

def api():
    assert importlib.util.find_spec('app.research.v2_transport_controller'), 'V2 transport controller required'
    from app.research import v2_transport_controller as controller
    return controller

@pytest.fixture
def world(tmp_path,monkeypatch):
    ctl=api()
    from app.research import v2_transport_contract as contract, v2_live_transport as transport
    from app.research import scheduled_forward_paper as scheduler
    from app import config as settings_module
    cfg=configuration()
    cfg['transport'].update(market_data_enabled=True,provider_enabled=True,production_start_enabled=True,
                            review_approval=str(tmp_path/'fixture-approval.json'))
    settings=SimpleNamespace(paper_trading=True,deepseek_model='deepseek-flash',request_timeout_seconds=10,
        upstox_total_deadline_seconds=20,upstox_max_retry_delay_seconds=1,upstox_access_token='fixture-private-token',deepseek_api_key='fixture-private-key')
    for module in (settings_module,transport,transport.upstox_client): monkeypatch.setattr(module,'get_settings',lambda:settings)
    now=[pd.Timestamp('2026-11-02T12:10:01+05:30')]
    monkeypatch.setattr(transport.upstox_client,'_utc_now',lambda:now[0].to_pydatetime())
    calls=[]; behavior={'mode':'BUY','late':False,'wrong_model':False}
    def quote_data():
        price=608 if now[0]>=pd.Timestamp('2026-11-02T12:25:00+05:30') else 600
        return {'status':'success','data':{'NSE_EQ|INE002A01018':{'instrument_token':'NSE_EQ|INE002A01018','timestamp':now[0].isoformat(),
            'depth':{'buy':[{'price':price-.1,'quantity':100}], 'sell':[{'price':price+.1,'quantity':100}]}}}}
    def get(session,url,**kwargs):
        kind='quote' if 'market-quote' in url else 'candles';calls.append(kind)
        if kind=='quote': body=quote_data()
        else:
            times=pd.date_range('2026-11-02T09:15:00+05:30',now[0].floor('5min'),freq='5min')
            rows=[[t.isoformat(),600,608 if t>=pd.Timestamp('2026-11-02T12:20:00+05:30') else 601,599,
                   608 if t==pd.Timestamp('2026-11-02T12:20:00+05:30') else 600,10000,0] for t in times]
            body={'status':'success','data':{'candles':rows,'instrument_key':'NSE_EQ|INE002A01018'}}
        r=requests.Response();r.status_code=200;r._content=json.dumps(body).encode();return r
    monkeypatch.setattr(requests.Session,'get',get)
    def sdk():
        def create(**kwargs):
            calls.append('provider')
            request=json.loads(kwargs['messages'][1]['content'])
            prop={'symbol':'RELIANCE','score':.9,'entry_price':600,'stop_loss':597,'target_price':608,'holding_bars':1,'rationale_codes':['momentum']}
            from app.research.ai_led_guarded import REVIEWED_SCHEMA
            body=dict(schema_version=REVIEWED_SCHEMA,candidate_timestamp=request['candidate_timestamp'],action=behavior['mode'],
                      proposals=[prop] if behavior['mode']=='BUY' else [],rationale_codes=['trend'])
            if behavior['late']: now[0]+=pd.Timedelta(minutes=5)
            return SimpleNamespace(choices=[SimpleNamespace(message=SimpleNamespace(content=json.dumps(body)),finish_reason='stop')],
                                   model='wrong' if behavior['wrong_model'] else 'deepseek-flash',usage=None)
        return SimpleNamespace(chat=SimpleNamespace(completions=SimpleNamespace(create=create)),close=lambda:None)
    def get_client(obj):
        if obj._client is None:obj._client=sdk()
        return obj._client
    monkeypatch.setattr(transport.DeepSeekProvider,'_get_client',get_client)
    def approve():
        record=dict(schema_version='v22-live-transport-review-v1',integration_identity=contract.integration_identity(),
                    configuration_identity=contract.configuration_identity(cfg),independent_review_passed=True,network_exercise_authorized=True)
        (tmp_path/'fixture-approval.json').write_text(json.dumps(record))
    approve()
    return SimpleNamespace(ctl=ctl,contract=contract,transport=transport,scheduler=scheduler,cfg=cfg,now=now,calls=calls,
        root=tmp_path/'control',behavior=behavior,approve=approve,settings=settings)

def test_end_to_end_proposal_entry_settlement_and_offline_replay(world,monkeypatch):
    first=world.ctl.run_cycle(world.cfg,world.root,lambda:world.now[0])
    assert first['state']=='ENTRY_PENDING'
    assert first['quantity'] is None
    world.now[0]=pd.Timestamp('2026-11-02T12:20:00.1+05:30')
    entered=world.ctl.run_cycle(world.cfg,world.root,lambda:world.now[0])
    assert entered['state']=='OPEN' and entered['quantity']==1
    world.now[0]=pd.Timestamp('2026-11-02T12:25:00.1+05:30')
    closed=world.ctl.run_cycle(world.cfg,world.root,lambda:world.now[0])
    assert closed['state'].startswith('CLOSED_')
    calls=list(world.calls)
    def forbidden(*args,**kwargs):pytest.fail('replay attempted acquisition')
    monkeypatch.setattr(world.transport.LiveTransport,'candles',forbidden)
    monkeypatch.setattr(world.transport.LiveTransport,'quote',forbidden)
    monkeypatch.setattr(world.transport.LiveTransport,'proposals',forbidden)
    view=world.ctl.replay(world.cfg,world.root)
    assert view['performance']['settled_trades']==1 and view['account']['eligible_equity']>1000
    assert world.calls==calls

@pytest.mark.parametrize('field',['market_data_enabled','provider_enabled','production_start_enabled'])
def test_every_optin_required_without_writes_or_requests(world,field):
    world.cfg['transport'][field]=False
    gate=world.contract.readiness(world.cfg,world.now[0])
    assert gate['status']=='NO_GO'
    with pytest.raises(world.contract.TransportError): world.ctl.run_cycle(world.cfg,world.root,lambda:world.now[0])
    assert not world.calls and not world.root.exists()

@pytest.mark.parametrize('fault',['absent','wrong_source','wrong_configuration','not_authorized'])
def test_independent_review_and_network_authorization_are_not_implicit(world,fault):
    path=world.cfg['transport']['review_approval']
    from pathlib import Path
    if fault=='absent':Path(path).unlink()
    else:
        body=json.loads(Path(path).read_text())
        body[{'wrong_source':'integration_identity','wrong_configuration':'configuration_identity','not_authorized':'network_exercise_authorized'}[fault]] = False if fault=='not_authorized' else '0'*64
        Path(path).write_text(json.dumps(body))
    assert world.contract.readiness(world.cfg,world.now[0])['status']=='NO_GO'
    assert not world.calls

@pytest.mark.parametrize('mode',['NO_TRADE','wrong_model','late'])
def test_provider_failure_never_creates_a_proposal(world,mode):
    if mode=='NO_TRADE':world.behavior['mode']=mode
    elif mode=='wrong_model':world.behavior['wrong_model']=True
    else:world.behavior['late']=True
    result=world.ctl.run_cycle(world.cfg,world.root,lambda:world.now[0])
    assert result['state']=='NO_VALID_PROPOSAL'
    assert not world.ctl.replay(world.cfg,world.root)['history']
    assert world.calls.count('provider')==1

def test_no_repeat_requests_for_duplicate_decision_and_pending_wait(world):
    first=world.ctl.run_cycle(world.cfg,world.root,lambda:world.now[0]);calls=list(world.calls)
    second=world.ctl.run_cycle(world.cfg,world.root,lambda:world.now[0])
    assert second['state']=='ENTRY_PENDING' and world.calls==calls and second['decision_id']==first['decision_id']

def test_missing_due_observation_retains_reservation(world):
    world.ctl.run_cycle(world.cfg,world.root,lambda:world.now[0])
    world.now[0]=pd.Timestamp('2026-11-02T12:20:00.1+05:30');world.ctl.run_cycle(world.cfg,world.root,lambda:world.now[0])
    before=world.ctl.replay(world.cfg,world.root)['account']['reserved_cash']
    world.now[0]=pd.Timestamp('2026-11-02T15:30:00+05:30');calls=list(world.calls)
    result=world.ctl.run_cycle(world.cfg,world.root,lambda:world.now[0])
    assert result['state']=='OBSERVATION_MISSING' and world.calls==calls
    assert world.ctl.replay(world.cfg,world.root)['account']['reserved_cash']==before

def test_later_fill_never_reuses_the_decision_quote(world):
    world.ctl.run_cycle(world.cfg,world.root,lambda:world.now[0]);assert world.calls.count('quote')==1
    world.now[0]=pd.Timestamp('2026-11-02T12:20:00.1+05:30')
    world.ctl.run_cycle(world.cfg,world.root,lambda:world.now[0])
    assert world.calls.count('quote')==2

def test_transport_health_is_sanitized_and_observer_remains_read_only(world,monkeypatch):
    from app.research import paper_observer as observer
    owner=world.scheduler.ownership
    monkeypatch.setattr(world.scheduler,'ownership',lambda root:owner(world.root.with_name('owner')))
    monkeypatch.setattr(world.scheduler,'_utc_now',lambda:world.now[0])
    world.cfg['transport']['max_cycles']=1;world.approve()
    result=world.scheduler.run_scheduled(world.cfg,world.root)
    assert result['status']=='FAILED_CLOSED' and result['transport']['provider_requests']==1
    before={p:p.read_bytes() for p in world.root.rglob('*') if p.is_file()}
    read=observer.read_snapshot(result['observer_path'],as_of=world.now[0])
    assert read['observer_status']=='AVAILABLE' and read['snapshot']['transport']['last_quote_at']
    assert all(p.read_bytes()==raw for p,raw in before.items())
    assert not any('fixture-private' in p.read_text(errors='ignore') for p in world.root.rglob('*') if p.is_file())

@pytest.mark.parametrize('boundary',['before_request','reserved','market_response','provider_response','proposal'])
def test_restart_retains_request_and_proposal_identity(world,monkeypatch,boundary):
    from app.research import ai_led_guarded as guarded
    from app.research.causal_forward_paper_v2 import CausalForwardPaperV2
    crashed=[False]
    class Interruption(BaseException): pass
    original_write=guarded._write
    def write(path,record):
        match=(boundary=='before_request' and path.parent.name=='intents' or
               boundary=='market_response' and record.get('kind')=='candles' and path.parent.name=='results' or
               boundary=='provider_response' and record.get('kind')=='provider' and path.parent.name=='results')
        if match and not crashed[0]:
            crashed[0]=True;original_write(path,record);raise Interruption()
        return original_write(path,record)
    monkeypatch.setattr(guarded,'_write',write)
    original_reserve=world.transport.PersistentRequestBudget.reserve
    def reserve(obj,key):
        result=original_reserve(obj,key)
        if boundary=='reserved' and not crashed[0]:crashed[0]=True;raise Interruption()
        return result
    monkeypatch.setattr(world.transport.PersistentRequestBudget,'reserve',reserve)
    original_select=CausalForwardPaperV2.select_transport
    def select(obj,**kwargs):
        result=original_select(obj,**kwargs)
        if boundary=='proposal' and not crashed[0]:crashed[0]=True;raise Interruption()
        return result
    monkeypatch.setattr(CausalForwardPaperV2,'select_transport',select)
    with pytest.raises(Interruption):world.ctl.run_cycle(world.cfg,world.root,lambda:world.now[0])
    assert crashed[0]
    result=world.ctl.run_cycle(world.cfg,world.root,lambda:world.now[0])
    assert result['state']=='ENTRY_PENDING'
    assert world.calls.count('candles')==world.calls.count('quote')==world.calls.count('provider')==1
    history=world.ctl.replay(world.cfg,world.root)['history']
    assert sum(r['state']=='ENTRY_PENDING' for r in history)==1

def test_unknown_provider_outcome_blocks_without_resend(world,monkeypatch):
    original=world.transport.DeepSeekProvider.complete
    class Interruption(BaseException):pass
    def complete(obj,payload):original(obj,payload);raise Interruption()
    monkeypatch.setattr(world.transport.DeepSeekProvider,'complete',complete)
    with pytest.raises(Interruption):world.ctl.run_cycle(world.cfg,world.root,lambda:world.now[0])
    with pytest.raises(world.contract.TransportError,match='REQUEST_INCOMPLETE'):
        world.ctl.run_cycle(world.cfg,world.root,lambda:world.now[0])
    assert world.calls.count('provider')==1 and not world.ctl.replay(world.cfg,world.root)['history']

@pytest.mark.parametrize('delay',['client_initialization','monotonic_response'])
def test_provider_deadline_covers_initialization_and_total_elapsed_time(world,monkeypatch,delay):
    elapsed=[0.0]
    monkeypatch.setattr(world.transport,'time',SimpleNamespace(monotonic=lambda:elapsed[0]))
    original_client=world.transport.DeepSeekProvider._get_client
    original_complete=world.transport.DeepSeekProvider.complete
    if delay=='client_initialization':
        def get_client(obj):
            if obj._client is None:world.now[0]+=pd.Timedelta(seconds=30)
            return original_client(obj)
        monkeypatch.setattr(world.transport.DeepSeekProvider,'_get_client',get_client)
    else:
        def complete(obj,payload):
            result=original_complete(obj,payload);elapsed[0]+=30;return result
        monkeypatch.setattr(world.transport.DeepSeekProvider,'complete',complete)
    result=world.ctl.run_cycle(world.cfg,world.root,lambda:world.now[0])
    assert result['state']=='NO_VALID_PROPOSAL' and not world.ctl.replay(world.cfg,world.root)['history']
    assert world.calls.count('provider')==(0 if delay=='client_initialization' else 1)

def test_observer_failure_is_recorded_without_changing_financial_flow(world,monkeypatch):
    owner=world.scheduler.ownership
    monkeypatch.setattr(world.scheduler,'ownership',lambda root:owner(world.root.with_name('owner')))
    monkeypatch.setattr(world.scheduler,'_publish_observer',lambda *a,**k:(_ for _ in ()).throw(OSError('fixture-private')))
    clocks=iter(['2026-11-02T12:20:00.1+05:30','2026-11-02T12:25:00.1+05:30'])
    def advance(seconds):world.now[0]=pd.Timestamp(next(clocks))
    result=world.scheduler.run_scheduled(world.cfg,world.root,clock=lambda:world.now[0],sleep=advance)
    assert result['status']=='COMPLETED' and result['settled_trades']==1
    assert result['observer_failures']==5 and result['warnings']==['OBSERVER_UNAVAILABLE']
    assert result['account']['equity']>1000

@pytest.mark.parametrize('when',['2026-11-02T09:05:00+05:30','2026-11-02T15:30:00+05:30'])
def test_outside_session_never_acquires(world,when):
    world.now[0]=pd.Timestamp(when)
    result=world.ctl.run_cycle(world.cfg,world.root,lambda:world.now[0])
    assert result['state']=='NO_VALID_PROPOSAL' and not world.calls

def test_unknown_calendar_date_fails_closed_without_state(world):
    world.now[0]=pd.Timestamp('2028-01-03T12:10:00+05:30')
    with pytest.raises(world.contract.TransportError):world.ctl.run_cycle(world.cfg,world.root,lambda:world.now[0])
    assert not world.calls and not world.root.exists()

def test_stale_uncommitted_observation_is_not_backdated_on_restart(world,monkeypatch):
    from app.research.causal_forward_paper_v2 import CausalForwardPaperV2
    world.ctl.run_cycle(world.cfg,world.root,lambda:world.now[0])
    world.now[0]=pd.Timestamp('2026-11-02T12:20:00.1+05:30')
    original=CausalForwardPaperV2.observe_transport
    class Interruption(BaseException):pass
    monkeypatch.setattr(CausalForwardPaperV2,'observe_transport',lambda *a,**k:(_ for _ in ()).throw(Interruption()))
    with pytest.raises(Interruption):world.ctl.run_cycle(world.cfg,world.root,lambda:world.now[0])
    monkeypatch.setattr(CausalForwardPaperV2,'observe_transport',original)
    world.now[0]+=pd.Timedelta(seconds=6);calls=list(world.calls)
    with pytest.raises(ValueError):world.ctl.run_cycle(world.cfg,world.root,lambda:world.now[0])
    assert world.calls==calls
    assert world.ctl.replay(world.cfg,world.root)['history'][-1]['state']=='ENTRY_PENDING'

def test_integration_has_no_broker_order_capability():
    import ast
    from pathlib import Path
    root=Path(__file__).resolve().parents[1]
    for name in ['v2_live_transport','v2_transport_controller','v2_transport_contract']:
        tree=ast.parse((root/'app'/'research'/(name+'.py')).read_text())
        for node in ast.walk(tree):
            if isinstance(node,ast.Attribute):assert node.attr not in {'place_order','modify_order','cancel_order'}
            if isinstance(node,ast.ImportFrom):assert 'broker' not in (node.module or '').lower()
