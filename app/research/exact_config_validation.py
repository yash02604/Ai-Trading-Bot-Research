"""Dedicated, process-confined offline validation; never a production authority.

The reviewed production controller and readiness globals are not changed. A
private function namespace binds closed mock ports; the live entry cannot see it.
Native working records sit in a marked sandbox with production-incompatible
storage binding. Exported artifacts always carry the validation domain label.
"""
import hashlib
import json
import os
from pathlib import Path
import subprocess
import sys

ROOT = Path(__file__).resolve().parents[2]
CANONICAL_CONFIG = ROOT / 'logs/forward-paper-v2/runtime-config.json'
TAG = 'OFFLINE_VALIDATION_ONLY'
SCENARIOS = ('no_opportunity', 'one_candidate', 'stale_quote', 'candidate_expiry',
             'provider_failure', 'market_failure', 'provider_exhaustion',
             'market_exhaustion', 'observer_failure', 'recovery', 'retry', 'serial_slow')


def _json(path, value):
    Path(path).write_text(json.dumps(value, sort_keys=True, indent=2, allow_nan=False), encoding='utf-8')


def _output_path(value):
    raw = Path(value)
    if not raw.is_absolute() or raw.exists():
        raise ValueError('new absolute validation output required')
    path = raw.resolve()
    for parent in (raw, *raw.parents):
        if parent.is_symlink() or parent.is_junction():
            raise ValueError('untrusted validation output')
    if path == ROOT or ROOT in path.parents:
        allowed = ROOT / 'logs/forward-paper-v2/validation'
        if allowed not in path.parents:
            raise ValueError('production/historical output forbidden')
    return path


def _worker_output(value):
    raw = Path(value)
    if not raw.is_absolute(): raise ValueError('absolute offline worker output required')
    path = raw.resolve()
    for parent in (raw, *raw.parents):
        if parent.is_symlink() or parent.is_junction(): raise ValueError('untrusted offline worker output')
    if path == ROOT or ROOT in path.parents:
        if ROOT / 'logs/forward-paper-v2/validation' not in path.parents:
            raise ValueError('production worker output forbidden')
    if {p.name for p in path.iterdir()} != {'OFFLINE_VALIDATION_ONLY.json'}:
        raise ValueError('new marked validation workspace required')
    if json.loads((path / 'OFFLINE_VALIDATION_ONLY.json').read_bytes()) != dict(validation_kind=TAG, production_approval_eligible=False):
        raise ValueError('invalid offline workspace marker')
    return path


def run_validation(output, *, config_path=CANONICAL_CONFIG, scenarios=SCENARIOS):
    """Only public entry: fixed config + closed scenarios; child owns all mocks."""
    if Path(config_path).resolve() != CANONICAL_CONFIG.resolve():
        raise ValueError('exact canonical configuration required')
    if not scenarios or len(set(scenarios)) != len(scenarios) or any(s not in SCENARIOS for s in scenarios):
        raise ValueError('closed offline scenario set required')
    output = _output_path(output)
    output.mkdir(parents=True)
    _json(output / 'OFFLINE_VALIDATION_ONLY.json', {'validation_kind': TAG, 'production_approval_eligible': False})
    env = os.environ.copy()
    for key in list(env):
        if any(token in key.upper() for token in ('TOKEN', 'SECRET', 'API_KEY', 'PASSWORD', 'CREDENTIAL')):
            env.pop(key, None)
    env['PYTHONDONTWRITEBYTECODE'] = '1'
    command = [sys.executable, '-B', str(Path(__file__).resolve()), '--worker', str(output), *scenarios]
    completed = subprocess.run(command, env=env, cwd=ROOT, capture_output=True, text=True)
    # Never persist arbitrary exceptions/provider strings from a failed child.
    if completed.returncode:
        raise ValueError('offline validation failed; inspect worker-error.json in validation output')
    return json.loads((output / 'validation-report.json').read_bytes())


def _guard(output):
    """Irremovable audit hook: deny network/processes and non-sandbox writes."""
    import socket
    def denied(*a, **k):
        raise PermissionError('OFFLINE_NETWORK_OR_CLIENT_FORBIDDEN')
    for name in ('create_connection', 'getaddrinfo', 'gethostbyname'):
        setattr(socket, name, denied)
    for name in ('connect', 'connect_ex', 'sendto'):
        setattr(socket.socket, name, denied)
    def inside(value):
        if isinstance(value, int):
            return
        path = Path(os.fsdecode(value)).resolve()
        if path != output and output not in path.parents:
            raise PermissionError('OFFLINE_WRITE_OUTSIDE_VALIDATION')
    def audit(event, args):
        if event.startswith('socket.') or event in {'subprocess.Popen', 'os.system', 'os.posix_spawn', 'os.spawn', 'os.exec', 'os.fork', 'os.startfile', '_winapi.CreateProcess'}:
            raise PermissionError('OFFLINE_NETWORK_OR_PROCESS_FORBIDDEN')
        if event == 'open':
            value, mode, flags = args
            if not isinstance(value, int) and Path(os.fsdecode(value)).name == '.env':
                raise PermissionError('OFFLINE_SECRET_SOURCE_FORBIDDEN')
            if (isinstance(mode, str) and any(c in mode for c in 'wax+')) or (
                    isinstance(flags, int) and flags & (os.O_WRONLY | os.O_RDWR | os.O_CREAT | os.O_TRUNC | os.O_APPEND)):
                inside(value)
        elif event in {'os.mkdir', 'os.remove', 'os.rmdir', 'os.truncate', 'os.chmod'}:
            inside(args[0])
        elif event in {'os.link', 'os.symlink'}:
            raise PermissionError('OFFLINE_LINK_CREATION_FORBIDDEN')
        elif event == 'os.rename':
            inside(args[0]); inside(args[1])
        elif event == 'sqlite3.connect':
            if args[0] != ':memory:': inside(args[0])
    sys.addaudithook(audit)
    import dotenv
    dotenv.load_dotenv = lambda *a, **k: False
    # Even accidental HTTP/SDK construction is denied, before any request.
    import requests
    requests.Session.__init__ = denied
    import httpx
    httpx.Client.__init__ = denied
    httpx.AsyncClient.__init__ = denied
    import multiprocessing.process
    multiprocessing.process.BaseProcess.start = denied
    return denied


def _worker(output, scenarios):
    import copy
    import types
    import pandas as pd
    from app.research import v2_transport_contract as contract, v2_live_transport as live
    from app.research import v2_transport_controller as production, scheduled_forward_paper as scheduler
    from app.research import ai_led_guarded as guarded, ai_led_offline as core, paper_observer as observer
    from app.research.causal_forward_paper_v2 import CausalForwardPaperV2
    from app.research.offline_session_calendar import OfflineSessionCalendar
    from app.research.prospective_preflight import CALENDAR_PATH
    from app.research.deepseek_prospective import PersistentRequestBudget
    from app.client_attestation import verify_client_attestation

    raw = CANONICAL_CONFIG.read_bytes()
    cfg = scheduler.validate_config(json.loads(raw, object_pairs_hook=observer.unique_fields))
    opts = contract.default_options(); opts['review_approval'] = cfg['transport']['review_approval']
    if (cfg['initial_capital'] != 1000 or cfg['market_data_cap'] != 1100 or cfg['provider_cap'] != 36
            or cfg['instrument_priority'] != list(observer.instruments()) or len(cfg['instrument_priority']) != 14
            or cfg['transport'] != opts):
        raise ValueError('canonical first-session policy mismatch')
    approval_path = Path(cfg['transport']['review_approval'])
    approval_raw = approval_path.read_bytes()
    source_id = contract.integration_identity(); config_id = contract.configuration_identity(cfg)
    dependency_hashes = {name: hashlib.sha256((ROOT / name).read_bytes()).hexdigest()
                         for name in contract.REVIEWED_DEPENDENCIES}
    if len(dependency_hashes) != 26 or contract.digest(dependency_hashes) != source_id:
        raise ValueError('reviewed dependency identity mismatch')
    expected = dict(schema_version='v22-live-transport-review-v1', integration_identity=source_id,
                    configuration_identity=config_id, independent_review_passed=False, network_exercise_authorized=False)
    if json.loads(approval_raw, object_pairs_hook=observer.unique_fields) != expected:
        raise ValueError('pending source/config identity mismatch')
    verify_client_attestation()
    calendar = OfflineSessionCalendar(CALENDAR_PATH)
    if scheduler.session_state('2026-11-16T09:05:00+05:30')['entry_cutoff'] != '2026-11-16T15:10:00+05:30':
        raise ValueError('target session mismatch')
    original_gate = contract.readiness
    original_cycle = production.run_cycle
    original_transport = production.LiveTransport
    canonical_control = ROOT / 'logs/forward-paper-v2/control'
    if list(canonical_control.iterdir()):
        raise ValueError('canonical control must remain pristine for first validation')
    before = scheduler.preflight(cfg, '2026-11-16T09:05:00+05:30')
    if before['status'] != 'NO_GO':
        raise ValueError('production gate unexpectedly open')
    harness_id = hashlib.sha256(Path(__file__).read_bytes()).hexdigest()
    fake_markers = ('VALIDATION_FAKE_TOKEN_NEVER_PERSIST_261115', 'VALIDATION_FAKE_PROVIDER_NEVER_PERSIST_261115')
    os.environ['UPSTOX_ACCESS_TOKEN'] = fake_markers[0]
    os.environ['DEEPSEEK_API_KEY'] = fake_markers[1]

    class OfflineInterruption(RuntimeError):
        """Recorded mock response, caller interrupted before selection commit."""

    class Context:
        def __init__(self, name):
            self.name = name
            self.root = output / TAG / name / 'control'
            self.base = self.root.parent
            self.root.mkdir(parents=True)
            _json(self.base / 'OFFLINE_VALIDATION_ONLY.json', {'validation_kind': TAG, 'canonical_config_identity': config_id})
            self.now = pd.Timestamp('2026-11-16T12:10:01+05:30')
            self.rows = []
            self.cache = {}
            self.used = {'market_data': 0, 'provider': 0}
            self.budgets = {k: PersistentRequestBudget(self.base / (TAG + '-' + k + '-budget.jsonl'), cfg[k + '_cap']) for k in self.used}
            self.attempts = 0
            self.first_quote = None
            self.quote_epoch = None
            self.max_batch_age = 0.0
            self.interrupted = False
            self.recovery_points = []
            self.domain = contract.digest(dict(validation_kind=TAG, canonical_config_identity=config_id,
                                               harness_identity=harness_id, scenario=name))
            self.binding = dict(schema_version='v22-transport-binding-v1', adapter_identity=source_id,
                                configuration_identity=self.domain)
            self.ports = OfflinePorts(self)

        def append(self, row):
            row = dict(validation_kind=TAG, canonical_config_identity=config_id, **row)
            with (self.base / 'offline-requests.jsonl').open('a', encoding='utf-8') as stream:
                stream.write(json.dumps(row, sort_keys=True, allow_nan=False) + '\n')
                stream.flush(); os.fsync(stream.fileno())
            self.rows.append(row)

        def reserve(self, stem, key, **fields):
            try:
                reserved = self.budgets[stem].reserve(key)
            except RuntimeError:
                raise contract.TransportError('BUDGET_EXHAUSTED')
            if not reserved: return
            self.append(dict(record_kind='RESERVATION', budget=stem, request_id=key, **fields))
            self.used[stem] = self.budgets[stem].used

        def consume_remaining(self, stem):
            # Explicit benign reservation-only interruption fixture, no fake send.
            for i in range(cfg[stem + '_cap'] - self.used[stem]):
                self.reserve(stem, 'offline-reservation-fixture-' + stem + str(i), fixture_only=True)

        def reopen(self):
            rows = [json.loads(line) for line in (self.base / 'offline-requests.jsonl').read_text().splitlines()]
            used = {'market_data': 0, 'provider': 0}; seen = set(); cache = {}
            for row in rows:
                if row['validation_kind'] != TAG or row['canonical_config_identity'] != config_id:
                    raise ValueError('invalid offline request journal')
                if row['record_kind'] == 'RESERVATION':
                    if row['request_id'] in seen: raise ValueError('duplicate offline reservation')
                    seen.add(row['request_id']); used[row['budget']] += 1
                if row['record_kind'] == 'RESULT': cache[row['request_id']] = row['result']
            if any(used[k] > cfg[k + '_cap'] for k in used): raise ValueError('offline cap overrun')
            self.budgets = {k: PersistentRequestBudget(self.base / (TAG + '-' + k + '-budget.jsonl'), cfg[k + '_cap']) for k in used}
            if any(self.budgets[k].used != used[k] for k in used): raise ValueError('offline reservation mismatch')
            self.rows, self.used, self.cache = rows, used, cache
            self.attempts = sum(row['record_kind'] == 'PHYSICAL_MOCK_ATTEMPT' for row in rows)
            self.ports = OfflinePorts(self)

    class OfflinePorts:
        # Reuse the exact production normalizers/strict provider parsing, never
        # LiveTransport.__init__, its send path, or the callback supplied by it.
        candles = live.LiveTransport.candles
        quote = live.LiveTransport.quote
        proposals = live.LiveTransport.proposals

        def __init__(self, context):
            if type(context) is not Context: raise TypeError('closed offline context required')
            self.ctx = context
            self.config = cfg
            self.clock = lambda: self.ctx.now

        def request_counts(self):
            return {k + '_requests': self.ctx.used[k] for k in self.ctx.used}

        def health(self):
            def last(kind):
                return max((r['received_at'] for r in self.ctx.cache.values() if r['kind'] == kind), default=None)
            return dict(market_data_enabled=False, provider_enabled=False, production_start_enabled=False,
                        last_quote_at=last('quote'), last_candle_at=last('candles'), last_provider_at=last('provider'),
                        **self.request_counts(), status='DISABLED', last_error=None)

        def _request(self, kind, symbol, slot, purpose, call, normalize, payload=None):
            ctx = self.ctx
            if type(self) is not OfflinePorts or contract.digest(cfg) != config_id:
                raise TypeError('closed exact-config offline ports required')
            slot = contract.stamp(slot)
            if purpose == 'entry' and not calendar.entry_allowed(ctx.now):
                raise contract.TransportError('ENTRY_WINDOW_CLOSED')
            if ctx.now >= slot + pd.Timedelta(minutes=10): raise contract.TransportError('RESPONSE_STALE')
            key = contract.digest([config_id, kind, symbol, slot.isoformat(), purpose])
            if key in ctx.cache: return ctx.cache[key]
            if any(r['record_kind'] == 'FAILURE' and r['request_id'] == key for r in ctx.rows):
                raise contract.TransportError('TRANSPORT_FAILURE')
            stem = 'provider' if kind == 'provider' else 'market_data'
            if kind == 'quote' and ctx.quote_epoch != (slot.isoformat(), purpose):
                ctx.quote_epoch = (slot.isoformat(), purpose); ctx.first_quote = None
            ctx.reserve(stem, key, kind=kind, symbol=symbol, slot=slot.isoformat(), purpose=purpose)
            requested = ctx.now
            def physical(retry):
                ctx.attempts += 1
                ctx.append(dict(record_kind='PHYSICAL_MOCK_ATTEMPT', request_id=key, retry_number=retry))
            try:
                if kind == 'provider':
                    physical(0)
                    response = self._response(kind, symbol, slot, payload)
                else:
                    # Reuse the actual bounded Upstox retry loop with only its
                    # lowest I/O function replaced in a private namespace.
                    upstox = live.upstox_client
                    retry_index = [0]
                    retry_first = ctx.name == 'retry' and ctx.attempts == 0
                    def before_attempt():
                        if ctx.now >= slot + pd.Timedelta(minutes=10): raise contract.TransportError('RESPONSE_STALE')
                    def before_send():
                        physical(retry_index[0]); retry_index[0] += 1
                    def bounded_get(url, headers, params, timeout, remaining):
                        if retry_first and retry_index[0] <= 2: return dict(status=503, retry_after='0')
                        return dict(status=200, data=self._response(kind, symbol, slot, payload))
                    settings = types.SimpleNamespace(request_timeout_seconds=cfg['transport']['request_timeout_seconds'],
                        upstox_total_deadline_seconds=cfg['transport']['total_deadline_seconds'],
                        upstox_max_retry_delay_seconds=cfg['transport']['max_retry_delay_seconds'])
                    namespace = dict(vars(upstox), _bounded_get=bounded_get, _utc_now=lambda: ctx.now.to_pydatetime(),
                        get_settings=lambda: settings, get_headers=lambda: {'Authorization': 'Bearer ' + fake_markers[0]})
                    get = types.FunctionType(upstox._get.__code__, namespace, '_get', upstox._get.__defaults__)
                    get.__kwdefaults__ = dict(upstox._get.__kwdefaults__ or {})
                    with upstox.request_window(end=(requested + pd.Timedelta(seconds=cfg['transport']['total_deadline_seconds'])).to_pydatetime(),
                            before_attempt=before_attempt, before_send=before_send, max_attempts=3):
                        response = get('OFFLINE_VALIDATION_ONLY://mock')
                if (ctx.now - requested).total_seconds() >= cfg['transport']['total_deadline_seconds']:
                    raise contract.TransportError('RESPONSE_STALE')
                normalized, source_at = normalize(response, ctx.now)
                row = dict(request_id=key, kind=kind, symbol=symbol, requested_at=requested.isoformat(),
                           received_at=ctx.now.isoformat(), validated_at=ctx.now.isoformat(),
                           source_at=source_at, normalized=normalized, response_hash=contract.digest(normalized))
                ctx.append(dict(record_kind='RESULT', request_id=key, result=row))
                ctx.cache[key] = row
                if ctx.name == 'recovery' and kind == 'provider' and not ctx.interrupted:
                    ctx.interrupted = True
                    raise OfflineInterruption()
                return row
            except OfflineInterruption:
                raise
            except contract.TransportError as exc:
                ctx.append(dict(record_kind='FAILURE', request_id=key, code=exc.code)); raise
            except Exception:
                ctx.append(dict(record_kind='FAILURE', request_id=key, code='TRANSPORT_FAILURE'))
                raise contract.TransportError('TRANSPORT_FAILURE') from None

        def _response(self, kind, symbol, slot, payload):
            ctx = self.ctx
            price = 608 if ctx.now >= pd.Timestamp('2026-11-16T12:25:00+05:30') else 600
            if kind == 'quote':
                if ctx.name == 'serial_slow': ctx.now += pd.Timedelta(seconds=.4)
                at = ctx.now - pd.Timedelta(seconds=6 if ctx.name == 'stale_quote' else 0)
                if ctx.first_quote is None: ctx.first_quote = at
                ctx.max_batch_age = max(ctx.max_batch_age, (ctx.now - ctx.first_quote).total_seconds())
                return dict(status='success', data={core.STOCKS[symbol]: dict(instrument_token=core.STOCKS[symbol],
                    timestamp=at.isoformat(), depth=dict(buy=[dict(price=price-.1, quantity=100)], sell=[dict(price=price+.1, quantity=100)]))})
            if kind == 'candles':
                if ctx.name == 'market_failure': return dict(status='error')
                times = pd.date_range('2026-11-16T09:15:00+05:30', ctx.now.floor('5min'), freq='5min')
                rows = [[t.isoformat(), 600, 608 if t >= pd.Timestamp('2026-11-16T12:20:00+05:30') else 601,
                         599, 608 if t == pd.Timestamp('2026-11-16T12:20:00+05:30') else 600, 10000, 0] for t in times]
                return dict(status='success', data=dict(candles=rows, instrument_key=core.STOCKS[symbol]))
            if ctx.name == 'provider_failure': raise ValueError(fake_markers[1])
            if ctx.name == 'candidate_expiry': ctx.now += pd.Timedelta(minutes=5)
            body = json.loads(payload['user_payload'])
            proposals = [dict(symbol=s, score=.9, entry_price=600, stop_loss=597, target_price=608,
                              holding_bars=1, rationale_codes=['momentum']) for s in reversed(cfg['instrument_priority'])]
            content = dict(schema_version=guarded.REVIEWED_SCHEMA, candidate_timestamp=body['candidate_timestamp'],
                           action='NO_TRADE' if ctx.name == 'no_opportunity' else 'BUY',
                           proposals=[] if ctx.name == 'no_opportunity' else proposals, rationale_codes=['trend'])
            return types.SimpleNamespace(provider_id='deepseek', model_id='deepseek-flash',
                                         requested_model_id='deepseek-flash', content=json.dumps(content))

    def controller(ctx):
        # Independent globals for existing function code, not monkeypatching the
        # production module, sys.modules, config, approval or live readiness.
        namespace = dict(vars(production))
        class OfflineContract:
            def __getattr__(self, name):
                if name == 'readiness':
                    def offline_ready(config, now, **kw):
                        if type(ctx.ports) is not OfflinePorts or contract.digest(config) != config_id:
                            raise TypeError('offline capability mismatch')
                        return dict(status='READY', reason_codes=[], validation_kind=TAG)
                    return offline_ready
                return getattr(contract, name)
        def port_factory(root, config, clock):
            if Path(root).resolve() != (ctx.root / 'transport').resolve() or contract.digest(config) != config_id:
                raise ValueError('offline transport root/config mismatch')
            return ctx.ports
        def engine(config, root):
            if Path(root).resolve() != ctx.root.resolve(): raise ValueError('offline engine root mismatch')
            return CausalForwardPaperV2(ctx.root / 'engine', capital=cfg['initial_capital'],
                                      provenance=contract.TRANSPORT_KIND, transport_binding=ctx.binding)
        def configuration(config, root, *, create):
            marker = dict(validation_kind=TAG, canonical_config_identity=config_id, storage_binding=ctx.binding)
            path = ctx.root / 'transport/controller/binding.json'
            if path.exists():
                if guarded._read(path) != marker: raise ValueError('validation binding changed')
            elif create: guarded._write(path, marker)
        for name, value in vars(production).items():
            if isinstance(value, types.FunctionType) and value.__module__ == production.__name__:
                namespace[name] = types.FunctionType(value.__code__, namespace, name, value.__defaults__, value.__closure__)
                namespace[name].__kwdefaults__ = dict(value.__kwdefaults__ or {})
        namespace.update(contract=OfflineContract(), LiveTransport=port_factory, _engine=engine, _configuration=configuration)
        return types.SimpleNamespace(**namespace)

    results = {}
    for name in scenarios:
        ctx = Context(name); ctl = controller(ctx)
        identity = scheduler._identity(cfg, ctx.now, True)
        directory = scheduler._initialize(ctx.root, identity)
        identity = scheduler._begin_attempt(directory, identity, ctx.now.isoformat())
        failures = 0; selected = None
        ticks = list(pd.date_range('2026-11-16T12:10:01+05:30', periods=36, freq='5min')) if name == 'no_opportunity' else [ctx.now]
        if name in {'one_candidate', 'provider_exhaustion', 'market_exhaustion', 'observer_failure', 'recovery', 'retry'}:
            ticks += [pd.Timestamp('2026-11-16T12:20:00.1+05:30'), pd.Timestamp('2026-11-16T12:25:00.1+05:30')]
        for i, at in enumerate(ticks):
            ctx.now = at
            try:
                result = ctl.run_cycle(cfg, ctx.root, lambda: ctx.now)
            except OfflineInterruption:
                counts_before = ctx.ports.request_counts(); attempts_before = ctx.attempts
                ctx.reopen(); ctl = controller(ctx)
                result = ctl.run_cycle(cfg, ctx.root, lambda: ctx.now)
                if ctx.ports.request_counts() != counts_before or ctx.attempts != attempts_before:
                    raise ValueError('recorded mock response reacquired after interruption')
                ctx.recovery_points.append('AFTER_RESPONSE_BEFORE_SELECTION')
            if result.get('proposal'): selected = result['proposal']['symbol']
            view = ctl.replay(cfg, ctx.root)
            account, candidates, settled = scheduler._projection(view, 1000, '2026-11-16')
            snapshot = scheduler._snapshot(identity, ticks[0].isoformat(), ctx.now, 'RUNNING', 'OBSERVATION', [], account, candidates,
                                           transport_health=ctx.ports.health(), request_counts=ctx.ports.request_counts())
            try:
                if name == 'observer_failure': raise OSError(fake_markers[0])
                scheduler._publish_observer(directory, snapshot)
            except Exception: failures += 1
            if i == 1 and name in {'provider_exhaustion', 'market_exhaustion'}:
                ctx.consume_remaining('provider' if name == 'provider_exhaustion' else 'market_data')
                ctx.reopen()
            if i == 1 and name == 'recovery':
                counts_before = ctx.ports.request_counts(); attempts_before = ctx.attempts
                ctx.reopen(); ctl = controller(ctx)
                if ctl.replay(cfg, ctx.root) != view or ctx.ports.request_counts() != counts_before or ctx.attempts != attempts_before:
                    raise ValueError('open-position restart changed recorded state')
                ctx.recovery_points.append('OPEN')
        view = ctl.replay(cfg, ctx.root)
        account, candidates, settled = scheduler._projection(view, 1000, '2026-11-16')
        unresolved = bool(account['open_positions'] or account['pending_proposals'] or view['performance']['unresolved'])
        status = 'FAILED_CLOSED' if unresolved else 'COMPLETED' if settled else 'NO_OPPORTUNITY'
        reason = ['RECOVERY_REQUIRED'] if unresolved else []
        summary = scheduler._finish(directory, identity, ticks[0].isoformat(), ctx.now, status, reason, account, candidates,
                                    settled=settled, observer_failures=failures, transport_health=ctx.ports.health(), request_counts=ctx.ports.request_counts())
        if name == 'recovery':
            counts_before = ctx.ports.request_counts(); attempts_before = ctx.attempts
            ctx.reopen(); recovered = controller(ctx).replay(cfg, ctx.root)
            if recovered != view or ctx.ports.request_counts() != counts_before or ctx.attempts != attempts_before:
                raise ValueError('offline replay changed financial/request state')
            ctx.recovery_points.append('SETTLED')
        try:
            production._configuration(cfg, ctx.root, create=False)
        except contract.TransportError:
            pass
        else:
            raise ValueError('production adopted validation binding')
        report = dict(validation_kind=TAG, canonical_config_identity=config_id, status=status, account=account,
                      settled_trades=settled, request_counts=ctx.ports.request_counts(), physical_attempts=ctx.attempts,
                      selected_symbol=selected, warnings=summary['warnings'], reason_codes=summary['reason_codes'],
                      recovery_no_reacquisition=name == 'recovery', first_quote_max_age_seconds=ctx.max_batch_age,
                      recovery_points=ctx.recovery_points, storage_binding=ctx.binding, last_cycle_reason=result.get('reason'))
        expected_status = 'FAILED_CLOSED' if name == 'market_exhaustion' else 'COMPLETED' if name in {
            'one_candidate','provider_exhaustion','observer_failure','recovery','retry'} else 'NO_OPPORTUNITY'
        expected_counts = {
            'no_opportunity':(1008,36), 'one_candidate':(32,1), 'stale_quote':(15,0),
            'candidate_expiry':(28,1), 'provider_failure':(28,1), 'market_failure':(1,0),
            'provider_exhaustion':(32,36), 'market_exhaustion':(1100,1),
            'observer_failure':(32,1), 'recovery':(32,1), 'retry':(32,1), 'serial_slow':(28,0)}[name]
        actual_counts = (ctx.used['market_data'], ctx.used['provider'])
        if status != expected_status or actual_counts != expected_counts or settled != int(expected_status == 'COMPLETED'):
            raise ValueError('offline scenario outcome/request invariant failed')
        if name == 'market_exhaustion' and (not account['reserved_cash'] or not account['open_positions']):
            raise ValueError('exhausted monitoring fabricated exposure release')
        if name == 'retry' and ctx.attempts != 35: raise ValueError('retry did not share one logical reservation')
        if name == 'recovery' and len(ctx.recovery_points) != 3: raise ValueError('required recovery boundaries missing')
        _json(ctx.base / 'OFFLINE_VALIDATION_RESULT.json', report)
        results[name] = report

    after = scheduler.preflight(cfg, '2026-11-16T09:05:00+05:30')
    unchanged = CANONICAL_CONFIG.read_bytes() == raw and approval_path.read_bytes() == approval_raw and not list(canonical_control.iterdir())
    modules_unchanged = contract.readiness is original_gate and production.run_cycle is original_cycle and production.LiveTransport is original_transport
    if not unchanged or not modules_unchanged or before != after or after['status'] != 'NO_GO':
        raise ValueError('production authority changed during validation')
    artifacts = []
    for path in sorted((output / TAG).rglob('*')):
        if path.is_file():
            artifacts.append(dict(validation_kind=TAG, production_approval_eligible=False,
                                  path=path.relative_to(output).as_posix(), sha256=hashlib.sha256(path.read_bytes()).hexdigest()))
    leaks = [a['path'] for a in artifacts if any(m.encode() in (output / a['path']).read_bytes() for m in fake_markers)]
    if leaks: raise ValueError('fake secret marker persisted')
    report = dict(schema_version='offline-exact-config-validation-v1', validation_kind=TAG, mode=TAG,
                  canonical_config_path=str(CANONICAL_CONFIG), canonical_config_sha256=hashlib.sha256(raw).hexdigest(),
                  canonical_config_identity=config_id, source_identity=source_id, harness_identity=harness_id,
                  dependency_hashes=dependency_hashes,
                  manifest_sha256=dependency_hashes['data/audits/nse_cm_session_manifest_v3.json'],
                  identity_status='IDENTITY_MATCHES_PENDING_APPROVAL', production_approval_eligible=False,
                  production_preflight_before=before, production_preflight_after=after,
                  approval_sha256_before=hashlib.sha256(approval_raw).hexdigest(),
                  approval_sha256_after=hashlib.sha256(approval_path.read_bytes()).hexdigest(),
                  production_artifacts_unchanged=unchanged, production_modules_unchanged=modules_unchanged,
                  storage_binding_is_production_incompatible=True, network_guard_checks='PASS',
                  guard_checks=json.loads((output / 'guard-checks.json').read_bytes())['checks'],
                  live_feasibility='LIVE FEASIBILITY UNPROVEN', secret_marker_leaks=leaks,
                  scenarios=results, artifacts=artifacts, broker_execution_enabled=False, external_requests=0)
    _json(output / 'validation-report.json', report)
    return report


def _worker_entry(output, scenarios):
    # This branch is not a generic capability injection surface: fixed config,
    # fixed mock class, fixed scenarios, fixed write sandbox, no import argument.
    output = _worker_output(output)
    if not scenarios or len(set(scenarios)) != len(scenarios) or any(s not in SCENARIOS for s in scenarios):
        raise ValueError('invalid offline worker invocation')
    sys.path.insert(0, str(ROOT))
    denied = _guard(output)
    try:
        import socket
        import requests
        import httpx
        import multiprocessing
        import urllib.request
        checks = {}
        operations = {
            'direct_socket': lambda: socket.socket(),
            'dns': lambda: socket.getaddrinfo('offline.invalid', 443),
            'requests_client': lambda: requests.Session(),
            'httpx_client': lambda: httpx.Client(),
            'httpx_async_client': lambda: httpx.AsyncClient(),
            'stdlib_http': lambda: urllib.request.urlopen('http://127.0.0.1:9', timeout=.01),
            'process_start': lambda: multiprocessing.Process().start(),
            'production_write': lambda: (ROOT / 'logs/forward-paper-v2/control/OFFLINE_FORBIDDEN').write_text(TAG),
        }
        for name, operation in operations.items():
            try: operation()
            except PermissionError: checks[name] = 'BLOCKED'
            except urllib.error.URLError as exc:
                if not isinstance(exc.reason, PermissionError): raise
                checks[name] = 'BLOCKED'
            else: raise ValueError('network guard inactive')
        _json(output / 'guard-checks.json', dict(validation_kind=TAG, checks=checks))
        _worker(output, scenarios)
    except Exception as exc:
        import traceback
        _json(output / 'worker-error.json', dict(validation_kind=TAG, error_type=type(exc).__name__,
              frames=[dict(file=f.filename, line=f.lineno, function=f.name) for f in traceback.extract_tb(exc.__traceback__)]))
        raise SystemExit(2)


if __name__ == '__main__':
    if len(sys.argv) < 4 or sys.argv[1] != '--worker':
        raise SystemExit('use scripts/validate_forward_paper_config.py')
    _worker_entry(sys.argv[2], tuple(sys.argv[3:]))
