"""Offline proof for the explicit V2 transport boundary, not real captures."""
import importlib.util
import copy
import pytest

def contract():
    assert importlib.util.find_spec('app.research.v2_transport_contract'), 'explicit transport contract required'
    from app.research import v2_transport_contract as c
    return c

def configuration():
    return dict(schema_version='scheduled-forward-paper-v2', mode='FORWARD_PAPER', initial_capital=1000,
        instrument_priority=['RELIANCE'], provider_cap=3, market_data_cap=30, transport=dict(
        schema_version='v22-live-transports-v1', market_data_enabled=False, provider_enabled=False,
        production_start_enabled=False, review_approval=None, model='deepseek-flash', min_turnover=100000,
        poll_seconds=1, max_cycles=3, request_timeout_seconds=10, total_deadline_seconds=20, max_retry_delay_seconds=1))

def engine(tmp_path, monkeypatch):
    c = contract()
    monkeypatch.setattr(c,'integration_identity',lambda:'1'*64)
    from app.research.causal_forward_paper_v2 import CausalForwardPaperV2
    return CausalForwardPaperV2(tmp_path,capital=1000,provenance=c.TRANSPORT_KIND,transport_binding=c.binding(configuration()))

def proposal():
    return dict(symbol='RELIANCE',score=.8,entry_price=600,stop_loss=597,target_price=608,holding_bars=1)

def evidence():
    return dict(schema_version='v22-proposal-evidence-v1',request_id='a'*64,provider_id='deepseek',model_id='deepseek-flash',
        requested_at='2026-11-02T12:01:00+05:30',received_at='2026-11-02T12:02:00+05:30',
        validated_at='2026-11-02T12:02:01+05:30',candidate_timestamp='2026-11-02T11:55:00+05:30',
        market_observed_at='2026-11-02T12:00:01+05:30',response_hash='b'*64)

def observation(start='12:05:00', received='12:10:00', high=601):
    at='2026-11-02T'+received+'+05:30'
    obs=dict(symbol='RELIANCE',instrument_key='NSE_EQ|INE002A01018',observed_at=at,
        candle=dict(timestamp='2026-11-02T'+start+'+05:30',open=600,high=high,low=599,close=600,volume=1000))
    quote=dict(instrument_key=obs['instrument_key'],provider_timestamp=at,received_at=at,ask_price=600.1,
        ask_quantity=100,bid_price=599.9,bid_quantity=100,spread_bps=3.3333333,source_quote_timestamp=None,
        timestamp_kind='response_generated_not_depth_timestamp',last_price=None)
    receipt=dict(schema_version='v22-observation-evidence-v1',request_id='c'*64,quote_request_id='d'*64,
        received_at=at,candle_response_hash='e'*64,quote_response_hash='f'*64,quote=quote)
    return obs,receipt,at

def select(e, *, as_of='2026-11-02T12:02:01+05:30', proof=None):
    return e.select_transport(decision_id='D',decision_ready_at='2026-11-02T12:02:01+05:30',as_of=as_of,
        proposal=proposal(),instrument_key='NSE_EQ|INE002A01018',evidence=proof or evidence())

def test_explicit_boundary_engine_sizes_only_at_actual_entry(tmp_path,monkeypatch):
    e=engine(tmp_path,monkeypatch)
    selected=select(e)
    assert selected['state']=='ENTRY_PENDING' and selected['quantity'] is None
    obs,proof,at=observation()
    entered=e.observe_transport('D',expected_start=obs['candle']['timestamp'],as_of=at,observation=obs,evidence=proof)
    assert entered['state']=='OPEN' and entered['quantity']==1 and entered['entry_price']==600
    assert entered['entry_price']!=proof['quote']['ask_price']
    obs,proof,at=observation('12:10:00','12:15:00',608)
    closed=e.observe_transport('D',expected_start=obs['candle']['timestamp'],as_of=at,observation=obs,evidence=proof)
    again=e.observe_transport('D',expected_start=obs['candle']['timestamp'],as_of='2026-11-02T12:20:00+05:30',observation=obs,evidence=proof)
    assert closed==again and e.performance()['settled_trades']==1
    assert e.account('2026-11-02T12:20:00+05:30')['balance']>1000

@pytest.mark.parametrize('case',['stale_quote','wrong_quote_symbol','future_quote','crossed_quote','late_candle'])
def test_external_admission_cannot_bypass_core_freshness(tmp_path,monkeypatch,case):
    e=engine(tmp_path,monkeypatch);select(e)
    obs,proof,at=observation()
    if case=='stale_quote':proof['quote']['provider_timestamp']='2026-11-02T12:09:50+05:30'
    if case=='wrong_quote_symbol':proof['quote']['instrument_key']='NSE_EQ|INVALID'
    if case=='future_quote':proof['quote']['provider_timestamp']='2026-11-02T12:11:00+05:30'
    if case=='crossed_quote':proof['quote']['bid_price']=610
    if case=='late_candle':at='2026-11-02T12:15:00+05:30'
    with pytest.raises(ValueError):
        e.observe_transport('D',expected_start=obs['candle']['timestamp'],as_of=at,observation=obs,evidence=proof)
    assert e.history('D')[-1]['state']=='ENTRY_PENDING' and e.performance()['settled_trades']==0

@pytest.mark.parametrize('case',['provider_future','decision_expired','wrong_provider','wrong_model'])
def test_provider_availability_not_backdated(tmp_path,monkeypatch,case):
    e=engine(tmp_path,monkeypatch);proof=evidence();now='2026-11-02T12:02:01+05:30'
    if case=='provider_future':proof['received_at']='2026-11-02T12:03:00+05:30'
    if case=='decision_expired':now='2026-11-02T12:05:00+05:30'
    if case=='wrong_provider':proof['provider_id']='other'
    if case=='wrong_model':proof['model_id']='other'
    with pytest.raises(ValueError):select(e,as_of=now,proof=proof)
    assert not e.history('D')

def test_new_provenance_cannot_use_legacy_quantity_or_observation_paths(tmp_path,monkeypatch):
    e=engine(tmp_path,monkeypatch)
    with pytest.raises(ValueError):e.propose(decision_id='D',decision_ready_at='2026-11-02T12:02:01+05:30',
        proposal=proposal(),instrument_key='NSE_EQ|INE002A01018',quantity=1000)
    select(e);obs,proof,at=observation()
    with pytest.raises(ValueError):e.observe('D',expected_start=obs['candle']['timestamp'],as_of=at,observation=obs)

def test_provenance_requires_source_pinned_binding(tmp_path):
    c=contract()
    from app.research.causal_forward_paper_v2 import CausalForwardPaperV2
    with pytest.raises(ValueError):CausalForwardPaperV2(tmp_path,capital=1000,provenance=c.TRANSPORT_KIND)

def test_cached_provider_receipt_cannot_be_assigned_a_later_decision_epoch(tmp_path,monkeypatch):
    e=engine(tmp_path,monkeypatch);proof=evidence()
    proof['validated_at']='2026-11-02T12:07:01+05:30'
    with pytest.raises(ValueError):
        e.select_transport(decision_id='late',decision_ready_at=proof['validated_at'],as_of=proof['validated_at'],
            proposal=proposal(),instrument_key='NSE_EQ|INE002A01018',evidence=proof)
    assert not e.history('late')

@pytest.mark.parametrize('dependency',['app/execution_observations.py','app/market_analysis.py',
    'app/research/deepseek_prospective.py','app/research/causal_forward_paper.py','strategy_backtest.py'])
def test_review_identity_covers_runtime_validation_dependencies(monkeypatch,dependency):
    from pathlib import Path
    c=contract();original=Path.read_bytes;before=c.integration_identity()
    def changed(path):
        raw=original(path)
        return raw+b'\n# synthetic source identity change\n' if path==c.ROOT/dependency else raw
    monkeypatch.setattr(Path,'read_bytes',changed)
    assert c.integration_identity()!=before

@pytest.mark.parametrize('flag',['market_data_enabled','provider_enabled','production_start_enabled'])
def test_three_permissions_independently_default_off(flag):
    c=contract();cfg=configuration()
    for key in ['market_data_enabled','provider_enabled','production_start_enabled']:cfg['transport'][key]=key!=flag
    result=c.readiness(cfg,'2026-11-02T12:00:00+05:30')
    assert result['status']=='NO_GO'
