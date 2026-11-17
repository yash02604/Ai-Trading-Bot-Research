"""Seven independent-audit regressions, isolated synthetic files and processes."""
import copy
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
from types import SimpleNamespace

import pytest

from app.research import scheduled_forward_paper as runner, paper_observer as reader
from test_scheduled_forward_paper import config, fixture
from test_forward_paper_scheduler_scripts import PS

NOW = '2026-11-02T12:15:00+05:30'


@pytest.fixture
def isolated_owner(tmp_path, monkeypatch):
    original = runner.ownership
    monkeypatch.setattr(runner, 'ownership', lambda project: original(tmp_path / 'project-owner'))


def launch(root, when, monkeypatch):
    monkeypatch.setattr(runner, '_utc_now', lambda: when)
    return runner.run_scheduled(config(), root)


@pytest.mark.parametrize('different_temp,different_cwd', [(False, False), (True, False), (False, True), (True, True)])
def test_project_owner_independent_of_temp_and_cwd(tmp_path, different_temp, different_cwd):
    first_temp = tmp_path / 'temp-a'; first_temp.mkdir()
    other_temp = tmp_path / 'temp-b'; other_temp.mkdir()
    project = tmp_path / 'project'
    child = ("import sys,tempfile; sys.path.insert(0,sys.argv[1]); tempfile.tempdir=sys.argv[3]; "
             "from app.research.scheduled_forward_paper import ownership,InstanceBusy;\n"
             "try:\n with ownership(sys.argv[2]): print('OWNED',flush=True);sys.stdin.readline()\n"
             "except InstanceBusy: print('BUSY',flush=True)")
    parent_temp = tempfile.tempdir
    tempfile.tempdir = str(first_temp)
    try:
        with runner.ownership(project):
            result = subprocess.run([sys.executable, '-B', '-c', child, str(runner.PROJECT_ROOT), project.name if different_cwd else str(project),
                                     str(other_temp if different_temp else first_temp)], input='\n', capture_output=True,
                                    text=True, timeout=30, cwd=tmp_path if different_cwd else runner.PROJECT_ROOT)
            assert result.returncode == 0, result.stderr
            assert result.stdout.strip() == 'BUSY'
        with runner.ownership(project):
            pass  # Persistent file is not stale PID authority.
    finally:
        tempfile.tempdir = parent_temp


def test_separate_projects_do_not_collide(tmp_path):
    with runner.ownership(tmp_path / 'a'):
        with runner.ownership(tmp_path / 'b'):
            pass


@pytest.mark.parametrize('when', ['2026-11-02T15:10:00+05:30', '2026-11-02T16:00:00+05:30', '2026-11-03T09:05:00+05:30'])
@pytest.mark.parametrize('terminal', ['NO_GO', 'COMPLETED'])
def test_later_orphan_not_hidden_by_terminal_summary(tmp_path, monkeypatch, isolated_owner, when, terminal):
    root = tmp_path / 'control'
    if terminal == 'NO_GO':
        first = launch(root, NOW, monkeypatch)
    else:
        first = runner._run_synthetic_fixture(config(), root, fixture(), as_of=NOW)
    identity = runner._identity(config(), NOW, terminal == 'COMPLETED')
    directory = runner._initialize(root, identity)
    orphan = runner._begin_attempt(directory, identity, NOW)
    before = {p:p.read_bytes() for p in root.rglob('*.final.json')}
    if when.startswith('2026-11-03'):
        with pytest.raises(ValueError, match='RECOVERY_REQUIRED'):
            runner._initialize(root, runner._identity(config(), when, terminal == 'COMPLETED'))
        assert not (root / 'runs' / '2026-11-03').exists()
    else:
        # Same-day restart must follow the orphan and finish without rewriting it.
        result = (runner._run_synthetic_fixture(config(), root, fixture(), as_of=when)
                  if terminal == 'COMPLETED' else launch(root, when, monkeypatch))
        assert result['previous_attempt_id'] == orphan['attempt_id']
        assert result['attempt_id'] != orphan['attempt_id']
        if terminal == 'COMPLETED': assert result['account'] == first['account']
    assert all(p.read_bytes() == raw for p,raw in before.items())


@pytest.mark.parametrize('policy', ['equal', 'rollback'])
def test_attempt_chain_uses_durable_order_not_wallclock(tmp_path, monkeypatch, policy):
    identity = runner._identity(config(), NOW, True)
    directory = runner._initialize(tmp_path / 'control', identity)
    ids = iter(['f'*32, '1'*32, '2'*32])
    monkeypatch.setattr(runner.uuid, 'uuid4', lambda: SimpleNamespace(hex=next(ids)))
    first = runner._begin_attempt(directory, identity, NOW)
    second = runner._begin_attempt(directory, identity, NOW if policy == 'equal' else '2026-11-02T12:14:00+05:30')
    third = runner._begin_attempt(directory, identity, NOW)
    assert second['previous_attempt_id'] == first['attempt_id']
    assert third['previous_attempt_id'] == second['attempt_id']


def test_completed_next_day_preserves_global_attempt_predecessor(tmp_path, monkeypatch, isolated_owner):
    first = launch(tmp_path / 'control', NOW, monkeypatch)
    second = launch(tmp_path / 'control', '2026-11-03T09:05:00+05:30', monkeypatch)
    assert first['status'] == second['status'] == 'NO_GO'
    assert second['previous_attempt_id'] == first['attempt_id']
    assert first['trading_date'] != second['trading_date']


@pytest.mark.parametrize('field,value', [('attempt_sequence', 0), ('previous_attempt_id', 'e'*32)])
def test_inconsistent_attempt_order_fails_closed_without_rewrite(tmp_path, field, value):
    identity = runner._identity(config(), NOW, True)
    directory = runner._initialize(tmp_path / 'control', identity)
    record = runner._begin_attempt(directory, identity, NOW)
    path = directory / 'attempts' / (record['attempt_id'] + '.begin.json')
    body = json.loads(path.read_bytes()); body[field] = value
    path.write_text(json.dumps(body)); before = path.read_bytes()
    with pytest.raises(ValueError, match='CONTROL_STATE_INVALID'):
        runner._initialize(tmp_path / 'control', identity)
    assert path.read_bytes() == before


def test_unresolved_prior_day_exposure_blocks_without_engine_mutation(tmp_path):
    root = tmp_path / 'control'
    first = runner._run_synthetic_fixture(config(), root, fixture(), as_of=NOW, stop_after=2)
    assert first['account']['reserved_cash'] > 0
    before = {p:p.read_bytes() for p in (root / 'engine').rglob('*') if p.is_file()}
    empty = fixture(); empty['commands'] = []
    second = runner._run_synthetic_fixture(config(), root, empty, as_of='2026-11-03T09:05:00+05:30')
    assert second['status'] == 'FAILED_CLOSED'
    assert not (root / 'runs' / '2026-11-03').exists()
    assert all(p.read_bytes() == data for p,data in before.items())


@pytest.mark.parametrize('before,after', [('2026-11-08T23:59:59+05:30','2026-11-09T00:00:00+05:30'),
    ('2026-11-02T23:59:59+05:30','2026-11-03T00:00:00+05:30'),
    ('2024-11-20T23:59:59+05:30','2024-11-21T00:00:00+05:30')])
def test_one_logical_clock_for_preflight_identity_and_reports(tmp_path, monkeypatch, isolated_owner, before, after):
    calls = []
    def clock():
        calls.append(1)
        return before if len(calls) == 1 else after
    monkeypatch.setattr(runner, '_utc_now', clock)
    result = runner.run_scheduled(config(), tmp_path / 'control')
    assert len(calls) == 1
    assert result['trading_date'] == before[:10]
    assert result['run_id'].startswith(before[:10])
    assert result['started_at'] == result['shutdown_at'] == before
    snapshot = json.loads(Path(result['observer_path']).read_bytes())
    assert snapshot['session'] == runner.session_state(before)
    assert snapshot['trading_date'] == before[:10]


@pytest.mark.parametrize('api', ['snapshot', 'events'])
def test_deep_json_returns_unavailable_without_writing(tmp_path, api):
    path = tmp_path / 'deep.json'; raw = b'['*20000 + b'0' + b']'*20000
    path.write_bytes(raw)
    result = (reader.read_snapshot if api == 'snapshot' else reader.read_events)(path)
    assert result['observer_status'] == 'UNAVAILABLE'
    assert path.read_bytes() == raw


@pytest.mark.parametrize('raw', [None, b'{', b'bad', b'{"x":1,"x":2}'])
def test_malformed_and_missing_observer_are_safe(tmp_path, raw):
    path = tmp_path / 'observer.json'
    if raw is not None: path.write_bytes(raw)
    assert reader.read_snapshot(path)['observer_status'] == 'UNAVAILABLE'
    assert (path.read_bytes() if path.exists() else None) == raw


def test_normal_and_stale_observer_and_shallow_large_events(tmp_path):
    identity = runner._identity(config(), NOW, True)
    identity.update(attempt_id='a'*32, previous_attempt_id=None)
    value = runner._snapshot(identity, NOW, NOW, 'COMPLETED', 'STOPPED', [], runner._empty_account(1000, True), [])
    path = tmp_path / 'observer.json'; path.write_text(json.dumps(value))
    assert reader.read_snapshot(path, as_of=NOW)['observer_status'] == 'AVAILABLE'
    assert reader.read_snapshot(path, as_of='2026-11-02T13:00:00+05:30')['observer_status'] == 'STALE'
    event = dict(at=NOW, status='COMPLETED', phase='STOPPED', reason_codes=[])
    events = tmp_path / 'events.jsonl'; events.write_text((json.dumps(event)+'\n')*4000)
    assert reader.read_events(events, limit=1)['events'] == [event]


@pytest.mark.parametrize('failed_phases,expected', [(set(),0), ({'PREFLIGHT'},1), ({'STOPPED'},1), ({'PREFLIGHT','STOPPED'},2)])
def test_publication_failures_accumulate(tmp_path, monkeypatch, isolated_owner, failed_phases, expected):
    original = runner._publish_observer
    def publish(directory, snapshot):
        if snapshot['phase'] in failed_phases: raise PermissionError('private failure detail')
        return original(directory, snapshot)
    monkeypatch.setattr(runner, '_publish_observer', publish)
    result = launch(tmp_path / 'control', NOW, monkeypatch)
    assert result['status'] == 'NO_GO' and result['account']['equity'] is None
    assert result['observer_failures'] == expected
    assert result['warnings'] == (['OBSERVER_UNAVAILABLE'] if expected else [])
    assert 'private failure detail' not in json.dumps(result)


@pytest.mark.parametrize('phases,expected', [({'RECOVERY'},1), ({'RECOVERY','STOPPED'},2)])
def test_duplicate_recovery_preserves_observer_failure_count(tmp_path, monkeypatch, phases, expected):
    first = runner._run_synthetic_fixture(config(), tmp_path / 'control', fixture(), as_of=NOW)
    original = runner._publish_observer
    def publish(directory, snapshot):
        if snapshot['phase'] in phases: raise PermissionError('private diagnostic')
        return original(directory, snapshot)
    monkeypatch.setattr(runner, '_publish_observer', publish)
    second = runner._run_synthetic_fixture(config(), tmp_path / 'control', fixture(), as_of=NOW)
    assert second['duplicate_launch'] and second['account'] == first['account']
    assert second['observer_failures'] == expected


@pytest.mark.parametrize('suffix', ['', '\\..\\foreign'])
def test_wrapper_refusal_leaves_foreign_directory_unchanged(tmp_path, suffix):
    foreign = tmp_path / 'foreign'; foreign.mkdir()
    sentinel = foreign / 'evidence.txt'; sentinel.write_bytes(b'preserve evidence')
    cfg = tmp_path / 'config.json'; cfg.write_text(json.dumps(config()))
    target = str(foreign) if not suffix else str(tmp_path / 'unused') + suffix
    result = subprocess.run([PS, '-NoProfile', '-NonInteractive', '-File', str(runner.PROJECT_ROOT / 'scripts/run_forward_paper_scheduled.ps1'),
                             '-Config', str(cfg), '-ControlRoot', target], capture_output=True, text=True, timeout=40)
    assert result.returncode != 0
    assert sorted(p.name for p in foreign.iterdir()) == ['evidence.txt']
    assert sentinel.read_bytes() == b'preserve evidence'


def test_valid_wrapper_logs_only_in_copied_project(tmp_path):
    project = tmp_path / 'project'; (project / 'scripts').mkdir(parents=True)
    shutil.copytree(runner.PROJECT_ROOT / 'app', project / 'app', ignore=shutil.ignore_patterns('__pycache__'))
    for name in ('run_scheduled_forward_paper.py','run_forward_paper_scheduled.ps1'):
        shutil.copy2(runner.PROJECT_ROOT / 'scripts' / name, project / 'scripts' / name)
    shutil.copy2(runner.PROJECT_ROOT / 'strategy_backtest.py', project / 'strategy_backtest.py')
    (project / '.venv/Scripts').mkdir(parents=True)
    shutil.copy2(sys.executable, project / '.venv/Scripts/python.exe')
    shutil.copy2(runner.PROJECT_ROOT / '.venv/pyvenv.cfg', project / '.venv/pyvenv.cfg')
    dependencies = project / '.venv/Lib/site-packages'
    dependencies.mkdir(parents=True)
    (dependencies / 'test-dependencies.pth').write_text(str(runner.PROJECT_ROOT / '.venv/Lib/site-packages'))
    env = os.environ.copy(); env.pop('PYTHONPATH', None)
    cfg = tmp_path / 'config.json'; cfg.write_text(json.dumps(config()))
    control = tmp_path / 'control'
    result = subprocess.run([PS, '-NoProfile', '-NonInteractive', '-File', str(project / 'scripts/run_forward_paper_scheduled.ps1'),
        '-Config', str(cfg), '-ControlRoot', str(control)], capture_output=True, text=True, timeout=40, env=env)
    assert result.returncode == 2, result.stderr
    logs = list((control / 'launcher-logs').glob('*.stdout.log'))
    assert len(logs) == 1
    report = json.loads(logs[0].read_text(encoding='utf-8-sig'))
    assert report['status'] == 'NO_GO' and report['external_requests'] == 0
    assert not (control / 'engine').exists()
