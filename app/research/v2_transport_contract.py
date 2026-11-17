"""Pure validation/identity for V2 transport input; no send or execution capability."""
import hashlib
import json
import math
from pathlib import Path
import re
import pandas as pd
from app.research.paper_observer import timestamp, unique_fields, instruments

ROOT = Path(__file__).resolve().parents[2]
TRANSPORT_KIND = 'VALIDATED_TRANSPORT_INPUT'
MODEL = 'deepseek-flash'
# Reviewed runtime validation paths, including inherited/dynamically used helpers.
# Relative names + exact bytes: checkout location and enumeration order are not identity.
# Coverage is checked against the V2 engine's financial source set in regression tests.
REVIEWED_DEPENDENCIES = (
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
CODES = {'NETWORK_DISABLED','PROVIDER_DISABLED','PRODUCTION_START_DISABLED','TRANSPORT_REVIEW_REQUIRED',
         'CALENDAR_UNVERIFIED','MARKET_CLOSED','CONFIG_INVALID','CREDENTIALS_INVALID','BUDGET_EXHAUSTED',
         'OBSERVATION_MISSING','NO_VALID_PROPOSAL','TRANSPORT_FAILURE','REQUEST_INCOMPLETE','RESPONSE_STALE',
         'RECOVERY_REQUIRED','SOURCE_IDENTITY_MISMATCH','QUOTE_INVALID','CANDLE_INVALID','PROVIDER_INVALID',
         'ENTRY_WINDOW_CLOSED','REQUEST_CONFLICT'}

class TransportError(ValueError):
    def __init__(self, code):
        self.code = code if code in CODES else 'TRANSPORT_FAILURE'
        super().__init__(self.code)

def encoded(value):
    return json.dumps(value, sort_keys=True, separators=(',', ':'), allow_nan=False).encode('utf-8')

def digest(value):
    return hashlib.sha256(encoded(value)).hexdigest()

def stamp(value):
    return pd.Timestamp(timestamp(value)).tz_convert('Asia/Kolkata')

def _hash(value):
    return type(value) is str and re.fullmatch('[0-9a-f]{64}', value) is not None

def default_options():
    return dict(schema_version='v22-live-transports-v1',market_data_enabled=False,provider_enabled=False,
        production_start_enabled=False,review_approval=None,model=MODEL,min_turnover=100000,
        poll_seconds=5,max_cycles=10000,request_timeout_seconds=10,total_deadline_seconds=20,max_retry_delay_seconds=1)

def validate_options(config):
    if (type(config) is not dict or set(config) != {'schema_version','mode','initial_capital','instrument_priority','provider_cap','market_data_cap','transport'}
            or config['schema_version'] != 'scheduled-forward-paper-v2' or config['mode'] != 'FORWARD_PAPER'
            or type(config['initial_capital']) not in (int,float) or not math.isfinite(config['initial_capital']) or config['initial_capital'] <= 0
            or type(config['instrument_priority']) is not list or not config['instrument_priority']
            or any(type(s) is not str or s not in instruments() for s in config['instrument_priority'])
            or len(set(config['instrument_priority'])) != len(config['instrument_priority'])
            or any(type(config[k]) is not int or config[k] < 0 for k in ('provider_cap','market_data_cap'))):
        raise TransportError('CONFIG_INVALID')
    options = config['transport']
    if (type(options) is not dict or set(options) != set(default_options())
            or options['schema_version'] != 'v22-live-transports-v1' or options['model'] != MODEL
            or any(type(options[k]) is not bool for k in ('market_data_enabled','provider_enabled','production_start_enabled'))
            or (options['review_approval'] is not None and (type(options['review_approval']) is not str or not Path(options['review_approval']).is_absolute()))
            or type(options['max_cycles']) is not int or not 1 <= options['max_cycles'] <= 10000):
        raise TransportError('CONFIG_INVALID')
    for key, minimum, maximum in [('poll_seconds',1,60),('min_turnover',0,1e15),('request_timeout_seconds',.001,20),
                                  ('total_deadline_seconds',.001,60),('max_retry_delay_seconds',0,5)]:
        value=options[key]
        if type(value) not in (int,float) or not math.isfinite(value) or not minimum <= value <= maximum:
            raise TransportError('CONFIG_INVALID')
    return json.loads(encoded(config))

def configuration_identity(config):
    return digest(validate_options(config))

def integration_identity():
    return digest({name:hashlib.sha256((ROOT/name).read_bytes()).hexdigest() for name in REVIEWED_DEPENDENCIES})

def binding(config):
    return dict(schema_version='v22-transport-binding-v1',adapter_identity=integration_identity(),
                configuration_identity=configuration_identity(config))

def validate_binding(value):
    if (type(value) is not dict or set(value) != {'schema_version','adapter_identity','configuration_identity'}
            or value['schema_version'] != 'v22-transport-binding-v1' or not _hash(value['configuration_identity'])
            or value['adapter_identity'] != integration_identity()):
        raise ValueError('invalid transport source binding')
    return json.loads(encoded(value))

def readiness(config, now, purpose='entry', expected_start=None):
    result=dict(status='NO_GO',reason_codes=[],mode='FORWARD_PAPER',trade_placement_enabled=False,external_requests=0,
                transport_integration='NOT_REVIEWED',market_data_permission=False,provider_permission=False,
                session=dict(state='UNVERIFIED',start=None,end=None,entry_cutoff=None,next_cutoff=None))
    try:
        cfg=validate_options(config);opts=cfg['transport'];now=stamp(now)
        for key,code in [('market_data_enabled','NETWORK_DISABLED'),('production_start_enabled','PRODUCTION_START_DISABLED')]:
            if not opts[key]: result['reason_codes'].append(code)
        if purpose != 'monitor' and not opts['provider_enabled']: result['reason_codes'].append('PROVIDER_DISABLED')
        if result['reason_codes']: return result
        path=opts['review_approval']
        if not path: raise TransportError('TRANSPORT_REVIEW_REQUIRED')
        raw=Path(path).read_bytes()
        if len(raw)>16384: raise TransportError('TRANSPORT_REVIEW_REQUIRED')
        approval=json.loads(raw,object_pairs_hook=unique_fields)
        expected=dict(schema_version='v22-live-transport-review-v1',integration_identity=integration_identity(),
                      configuration_identity=configuration_identity(cfg),independent_review_passed=True,network_exercise_authorized=True)
        if approval != expected or any(approval[k] is not True for k in ('independent_review_passed','network_exercise_authorized')):
            raise TransportError('TRANSPORT_REVIEW_REQUIRED')
        from app.client_attestation import verify_client_attestation
        from app.research.offline_session_calendar import OfflineSessionCalendar
        verify_client_attestation()
        calendar=OfflineSessionCalendar(ROOT/'data/audits/nse_cm_session_manifest_v3.json')
        intervals=calendar.intervals(now)
        if not intervals:
            result.update(status='NO_SESSION',reason_codes=['MARKET_CLOSED']);return result
        start,end=next(((a,b) for a,b in intervals if now < b),intervals[-1])
        state='BEFORE_SESSION' if now<start else 'AFTER_CLOSE' if now>=end else 'ENTRY_WINDOW' if calendar.entry_allowed(now) else 'NO_NEW_ENTRIES'
        result['session']=dict(state=state,start=start.isoformat(),end=end.isoformat(),entry_cutoff=(end-pd.Timedelta(minutes=5)).isoformat(),
                               next_cutoff=start.isoformat() if now<start else None if now>=end else end.isoformat())
        if purpose=='entry' and not calendar.entry_allowed(now): raise TransportError('ENTRY_WINDOW_CLOSED')
        if purpose=='monitor':
            slot=stamp(expected_start);calendar.candle_end(slot)
            if not slot+pd.Timedelta(minutes=5) <= now < slot+pd.Timedelta(minutes=10): raise TransportError('RESPONSE_STALE')
        if purpose not in {'entry','monitor','startup'}: raise TransportError('CONFIG_INVALID')
        from app.config import get_settings
        settings=get_settings()
        if (settings.paper_trading is not True or settings.deepseek_model != MODEL
                or not isinstance(settings.upstox_access_token,str) or not settings.upstox_access_token.strip()
                or (purpose != 'monitor' and (not isinstance(settings.deepseek_api_key,str) or not settings.deepseek_api_key.strip()))
                or settings.request_timeout_seconds != opts['request_timeout_seconds']
                or getattr(settings,'upstox_total_deadline_seconds',20) != opts['total_deadline_seconds']
                or getattr(settings,'upstox_max_retry_delay_seconds',1) != opts['max_retry_delay_seconds']):
            raise TransportError('CREDENTIALS_INVALID')
        result.update(status='READY',transport_integration='APPROVED_READONLY_V1',market_data_permission=True,
                      provider_permission=opts['provider_enabled']);return result
    except TransportError as exc: result['reason_codes']=[exc.code]
    except (OSError,ValueError,TypeError,KeyError,RecursionError): result['reason_codes']=['CALENDAR_UNVERIFIED']
    return result

def validate_quote(quote,symbol,at):
    fields={'instrument_key','provider_timestamp','received_at','ask_price','ask_quantity','bid_price','bid_quantity',
            'spread_bps','source_quote_timestamp','timestamp_kind','last_price'}
    if (type(quote) is not dict or set(quote)!=fields or quote['instrument_key']!=instruments().get(symbol)
            or quote['source_quote_timestamp'] is not None or quote['timestamp_kind']!='response_generated_not_depth_timestamp'
            or any(type(quote[k]) not in (int,float) or not math.isfinite(quote[k]) or quote[k]<=0 for k in ('bid_price','ask_price'))
            or any(type(quote[k]) is not int or quote[k]<=0 for k in ('bid_quantity','ask_quantity'))
            or quote['bid_price']>quote['ask_price'] or type(quote['spread_bps']) not in (int,float)
            or not math.isfinite(quote['spread_bps']) or quote['spread_bps']<0):
        raise ValueError('invalid transport quote')
    if quote['last_price'] is not None and (type(quote['last_price']) not in (int,float) or not math.isfinite(quote['last_price']) or quote['last_price']<=0):
        raise ValueError('invalid last price')
    source,received,at=stamp(quote['provider_timestamp']),stamp(quote['received_at']),stamp(at)
    if not source<=received<=at or not 0 <= (at-source).total_seconds() <= 5:
        raise ValueError('stale or future transport quote')

def validate_proposal_evidence(e,ready,at):
    fields={'schema_version','request_id','provider_id','model_id','requested_at','received_at','validated_at',
            'candidate_timestamp','market_observed_at','response_hash'}
    if (type(e) is not dict or set(e)!=fields or e['schema_version']!='v22-proposal-evidence-v1'
            or e['provider_id']!='deepseek' or e['model_id']!=MODEL or not _hash(e['request_id']) or not _hash(e['response_hash'])):
        raise ValueError('invalid provider evidence')
    ready,at=stamp(ready),stamp(at)
    if at>=stamp(e['candidate_timestamp'])+pd.Timedelta(minutes=10):
        raise ValueError('provider candidate window expired')
    if not (stamp(e['candidate_timestamp'])+pd.Timedelta(minutes=5) <= stamp(e['market_observed_at'])
            <= stamp(e['requested_at']) <= stamp(e['received_at']) <= stamp(e['validated_at']) == ready <= at
            < ready.floor('5min')+pd.Timedelta(minutes=5)):
        raise ValueError('provider evidence unavailable or expired')
    if (ready-stamp(e['market_observed_at'])).total_seconds()>900: raise ValueError('stale market evidence')

def validate_observation_evidence(e,observation,at):
    fields={'schema_version','request_id','quote_request_id','received_at','candle_response_hash','quote_response_hash','quote'}
    if (type(e) is not dict or set(e)!=fields or e['schema_version']!='v22-observation-evidence-v1'
            or any(not _hash(e[k]) for k in ('request_id','quote_request_id','candle_response_hash','quote_response_hash'))
            or stamp(e['received_at']) != stamp(observation['observed_at'])):
        raise ValueError('invalid observation evidence')
    if stamp(e['received_at'])>stamp(e['quote']['received_at']): raise ValueError('quote predates candle receipt')
    validate_quote(e['quote'],observation['symbol'],at)
