"""Observation orchestration only; V2 owns every financial transition.

Only explicit, source-bound opt-ins permit acquisition. Replay never constructs a
transport. Observer publication is an isolated projection, never financial input.
"""
from pathlib import Path
import time
import pandas as pd

from app.research import ai_led_guarded as guarded, ai_led_offline as core
from app.research import v2_transport_contract as contract
from app.research.v2_live_transport import LiveTransport, SYSTEM_PROMPT
from app.research.causal_forward_paper_v2 import CausalForwardPaperV2
from app.research.deepseek_prospective import _experiment_lock, PROSPECTIVE_SCHEMA
from app.research.offline_session_calendar import OfflineSessionCalendar
from app.research.prospective_preflight import CALENDAR_PATH


def _engine(config, root):
    return CausalForwardPaperV2(Path(root)/'engine', capital=config['initial_capital'],
        provenance=contract.TRANSPORT_KIND, transport_binding=contract.binding(config))


def _configuration(config, root, *, create):
    expected=contract.binding(config)
    path=Path(root)/'transport'/'controller'/'binding.json'
    if path.exists():
        if guarded._read(path)!=expected: raise contract.TransportError('SOURCE_IDENTITY_MISMATCH')
    elif create:
        # An existing ledger must never be silently adopted by a different path.
        if (Path(root)/'engine').exists(): raise contract.TransportError('RECOVERY_REQUIRED')
        guarded._write(path,expected)
    elif (Path(root)/'engine').exists():
        raise contract.TransportError('RECOVERY_REQUIRED')


def replay(config, root):
    """Verify recorded V2 commands locally; never acquire or redate observations."""
    config=contract.validate_options(config)
    _configuration(config,root,create=False)
    engine=_engine(config,root)
    view=engine._view()
    return view or dict(history=[],settlements=[],performance=engine.performance(),
                        account=engine.account('2000-01-01T00:00:00+05:30'))


def _batch(config, root, records, now, candidate):
    paths=[]
    for symbol,record in records.items():
        data=record['normalized']
        snapshot=dict(schema_version=PROSPECTIVE_SCHEMA,symbol=symbol,instrument_key=data['instrument_key'],
            source='validated-readonly-transport',observed_at=record['received_at'],candles=data['candles'])
        path=Path(root)/'transport'/'controller'/'snapshots'/(record['request_id']+'.json')
        guarded._write_bytes_once(path,core._encoded(snapshot));paths.append(path)
    frames,market=core._batch(paths,config['instrument_priority'],now,config['transport']['min_turnover'],
                             OfflineSessionCalendar(CALENDAR_PATH))
    if any(contract.stamp(f.iloc[-1].timestamp)!=candidate for f in frames.values()):
        raise contract.TransportError('RESPONSE_STALE')
    for row in market.values(): row['source']='validated-readonly-transport'
    return frames,market


def _monitor(engine, transport, root, row, clock):
    now=contract.stamp(clock());slot=contract.stamp(row['next_start'])
    if now<slot+pd.Timedelta(minutes=5): return row
    if now>=slot+pd.Timedelta(minutes=10):
        return engine.observe(row['decision_id'],expected_start=slot,as_of=now,observation=None)
    path=Path(root)/'transport'/'controller'/'observations'/(contract.digest([row['decision_id'],slot.isoformat()])+'.json')
    if path.exists():
        args=guarded._read(path)
    else:
        try:
            candles=transport.candles(row['proposal']['symbol'],slot,purpose='monitor')
            candle=next(c for c in candles['normalized']['candles'] if contract.stamp(c['timestamp'])==slot)
            quote=transport.quote(row['proposal']['symbol'],slot,purpose='monitor')
            args=dict(decision_id=row['decision_id'],expected_start=slot.isoformat(),
                observation=dict(symbol=row['proposal']['symbol'],instrument_key=row['instrument_key'],
                    observed_at=candles['received_at'],candle=candle),
                evidence=dict(schema_version='v22-observation-evidence-v1',request_id=candles['request_id'],
                    quote_request_id=quote['request_id'],received_at=candles['received_at'],
                    candle_response_hash=candles['response_hash'],quote_response_hash=quote['response_hash'],quote=quote['normalized']))
            contract.validate_observation_evidence(args['evidence'],args['observation'],contract.stamp(clock()))
            guarded._write(path,args)
        except (contract.TransportError,ValueError,KeyError,StopIteration):
            # Missing evidence never becomes a fabricated exit or released cash.
            return engine.observe(row['decision_id'],expected_start=slot,as_of=contract.stamp(clock()),observation=None)
    return engine.observe_transport(**args,as_of=contract.stamp(clock()))


def run_cycle(config, root, clock):
    """One bounded acquisition cycle; all send methods independently recheck gates."""
    config=contract.validate_options(config);root=Path(root)
    gate=contract.readiness(config,clock(),purpose='startup')
    if gate['status']!='READY': raise contract.TransportError(gate['reason_codes'][0])
    with _experiment_lock(root/'transport'/'controller'):
        _configuration(config,root,create=True)
        engine=_engine(config,root);view=engine._view()
        latest={r['decision_id']:r for r in view['history']} if view else {}
        if any(r['state'] in {'OBSERVATION_MISSING','INVALID_OR_CONFLICTING'} for r in latest.values()):
            raise contract.TransportError('RECOVERY_REQUIRED')
        transport=LiveTransport(root/'transport',config,clock)
        active=[r for r in latest.values() if r['state'] in {'ENTRY_PENDING','OPEN'}]
        if len(active)>1: raise contract.TransportError('RECOVERY_REQUIRED')
        if active: return _monitor(engine,transport,root,active[0],clock)
        now=contract.stamp(clock());calendar=OfflineSessionCalendar(CALENDAR_PATH)
        if not calendar.entry_allowed(now): return dict(state='NO_VALID_PROPOSAL',reason='ENTRY_WINDOW_CLOSED')
        candidate=now.floor('5min')-pd.Timedelta(minutes=5)
        start=next(a for a,b in calendar.intervals(now) if a<=now<b)
        if candidate-start<pd.Timedelta(minutes=34*5):
            return dict(state='NO_VALID_PROPOSAL',reason='CANDLE_INVALID')
        key=contract.digest(candidate.isoformat())
        directory=root/'transport'/'controller'/'candidates'
        done=directory/(key+'.done.json');plan_path=directory/(key+'.plan.json');selection_path=directory/(key+'.selection.json')
        if done.exists(): return guarded._read(done)
        if selection_path.exists():
            args=guarded._read(selection_path)
        else:
            try:
                if plan_path.exists():
                    plan=guarded._read(plan_path)
                    frames,market=_batch(config,root,plan['candles'],now,candidate)
                else:
                    candles={s:transport.candles(s,candidate) for s in config['instrument_priority']}
                    frames,market=_batch(config,root,candles,contract.stamp(clock()),candidate)
                    quotes={s:transport.quote(s,candidate)['normalized'] for s in config['instrument_priority']}
                    at=contract.stamp(clock())
                    for s,q in quotes.items(): contract.validate_quote(q,s,at)
                    body=dict(schema_version=core.SCHEMA,candidate_timestamp=candidate.isoformat(),decision_time=at.isoformat(),
                        instrument_priority=config['instrument_priority'],market=market,quotes=quotes)
                    plan=dict(candles=candles,payload=dict(system_prompt=SYSTEM_PROMPT,user_payload=core._encoded(body).decode()))
                    guarded._write(plan_path,plan)
                # Revalidate the original observations before any new provider call.
                import json
                body=json.loads(plan['payload']['user_payload'])
                for s,q in body['quotes'].items(): contract.validate_quote(q,s,contract.stamp(clock()))
                response=transport.proposals(plan['payload'],frames=frames,candidate_time=candidate)
                ready=contract.stamp(clock())
                proposals=response['normalized']['proposals']
                if not proposals: raise contract.TransportError('NO_VALID_PROPOSAL')
                proposal=sorted(proposals,key=lambda p:(-p['score'],config['instrument_priority'].index(p['symbol'])))[0]
                evidence=dict(schema_version='v22-proposal-evidence-v1',request_id=response['request_id'],provider_id='deepseek',
                    model_id=config['transport']['model'],requested_at=response['requested_at'],received_at=response['received_at'],
                    validated_at=ready.isoformat(),candidate_timestamp=candidate.isoformat(),
                    market_observed_at=max(r['received_at'] for r in plan['candles'].values()),response_hash=response['response_hash'])
                contract.validate_proposal_evidence(evidence,ready,ready)
                args=dict(decision_id=response['request_id'],decision_ready_at=ready.isoformat(),proposal=proposal,
                          instrument_key=core.STOCKS[proposal['symbol']],evidence=evidence)
                guarded._write(selection_path,args)
            except (contract.TransportError,ValueError,KeyError) as exc:
                if isinstance(exc,contract.TransportError) and exc.code in {'REQUEST_INCOMPLETE','REQUEST_CONFLICT','SOURCE_IDENTITY_MISMATCH'}: raise
                result=dict(state='NO_VALID_PROPOSAL',reason=exc.code if isinstance(exc,contract.TransportError) else 'PROVIDER_INVALID')
                guarded._write(done,result);return result
        result=engine.select_transport(**args,as_of=contract.stamp(clock()))
        guarded._write(done,result)
        return result


def _diagnostics(transport):
    """Optional projections cannot change a verified financial outcome."""
    from app.research.paper_observer import validate_transport_health
    fields={'market_data_requests','provider_requests'}
    failures=0
    try:
        counts=transport.request_counts()
        if (type(counts) is not dict or set(counts)!=fields or
                any(type(v) is not int or v<0 for v in counts.values())):
            raise ValueError('invalid request count projection')
        counts=dict(counts)
    except Exception:
        counts={key:None for key in fields};failures+=1
    try:
        health=validate_transport_health(transport.health())
    except Exception:
        health=None;failures+=1
    if any(value is None for value in counts.values()):
        # Optional health payloads cannot substitute defaults for unavailable
        # or contradictory durable request accounting.
        health=None
    return counts,health,failures


def run_session(config,root,directory,identity,started,clock,sleep=time.sleep,*,observer_failures=0,duplicate=False):
    """Bounded scheduler loop. No registration, activation or broker capability."""
    from app.research import scheduled_forward_paper as scheduler
    failures=observer_failures;recovered=(Path(root)/'engine').exists()
    transport=LiveTransport(Path(root)/'transport',config,clock)
    reason=None;view=None
    counts,health,diagnostic_failures=_diagnostics(transport)
    failures+=diagnostic_failures
    for index in range(config['transport']['max_cycles']):
        now=contract.stamp(clock());session=scheduler.session_state(now)
        if session['state']=='BEFORE_SESSION':
            sleep(config['transport']['poll_seconds']);continue
        if session['state'] in {'AFTER_CLOSE','NO_SESSION'} and not (Path(root)/'engine').exists(): break
        try:
            result=run_cycle(config,root,clock)
            view=replay(config,root)
            account,candidates,settled=scheduler._projection(view,config['initial_capital'],identity['trading_date'])
            counts,health,diagnostic_failures=_diagnostics(transport)
            failures+=diagnostic_failures
            try:
                snapshot=scheduler._snapshot(identity,started,clock(),'RUNNING','OBSERVATION' if result['state']!='ENTRY_PENDING' else 'PROPOSAL',
                    [],account,candidates[-100:],transport_health=health,request_counts=counts)
                scheduler._publish_observer(directory,snapshot)
            except Exception: failures+=1
            if result['state'] in {'OBSERVATION_MISSING','INVALID_OR_CONFLICTING'}:
                reason='RECOVERY_REQUIRED';break
            if session['state'] in {'AFTER_CLOSE','NO_SESSION'}: break
        except contract.TransportError as exc:
            reason=exc.code;break
        except Exception:
            reason='RECOVERY_REQUIRED';break
        if index+1<config['transport']['max_cycles']: sleep(config['transport']['poll_seconds'])
    try:
        view=replay(config,root) if (Path(root)/'engine').exists() else None
        account,candidates,settled=scheduler._projection(view,config['initial_capital'],identity['trading_date'])
        unfinished=account['open_positions'] or account['pending_proposals'] or view and view['performance']['unresolved']
        if unfinished: reason='RECOVERY_REQUIRED'
    except Exception:
        account=scheduler._empty_account(config['initial_capital'],False);candidates=[];settled=None
        reason='RECOVERY_REQUIRED'
    counts,health,diagnostic_failures=_diagnostics(transport)
    failures+=diagnostic_failures
    status='FAILED_CLOSED' if reason else 'COMPLETED' if settled else 'NO_OPPORTUNITY'
    return scheduler._finish(directory,identity,started,clock(),status,[reason] if reason else [],account,candidates,
        settled=settled,observer_failures=failures,recovered=recovered,duplicate=duplicate,transport_health=health,request_counts=counts)
