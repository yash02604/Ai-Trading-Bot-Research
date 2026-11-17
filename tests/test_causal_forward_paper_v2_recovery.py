"""Defensive recovery invariants on isolated, copied synthetic V2 ledgers."""
import json
from pathlib import Path
import shutil
import sqlite3
import subprocess
import sys

import pytest

import test_causal_forward_paper_v2 as fixtures

v2 = fixtures.v2
START = fixtures.START
BAR = fixtures.v1.BAR


def opened(directory):
    book = fixtures.book(directory)
    fixtures.propose(book, target_price=fixtures.profit_target())
    fixtures.observe(book, START)
    return book


def completed(directory):
    book = opened(directory)
    fixtures.observe(book, START + BAR, high=608)
    assert book.account(START + 2 * BAR)["balance"] == 1005
    return book


def heads(book):
    with sqlite3.connect(book.path) as db:
        events = [json.loads(row[0]) for row in db.execute("SELECT body FROM events ORDER BY seq")]
    return book._head(events[:2]), book._head(events)


@pytest.mark.parametrize("condition", [
    "five_to_two", "older_database", "same_sequence", "wrong_ancestor",
    "future_base", "wrong_final", "wrong_config", "reordered", "substituted",
    "boolean_sequence", "negative_sequence", "malformed_hash",
])
def test_contradictory_recovery_cannot_change_committed_head(tmp_path, condition):
    source = opened(tmp_path / "prefix")
    base = json.loads(source.anchor.read_bytes())
    candidate = tmp_path / "candidate"
    shutil.copytree(source.directory, candidate)
    fixtures.observe(source, START + BAR, high=608)
    final = json.loads(source.anchor.read_bytes())
    if condition != "older_database":
        shutil.rmtree(candidate)
        shutil.copytree(source.directory, candidate)
    else:
        shutil.copyfile(source.anchor, candidate / "anchor.json")
    pending = {"base": base, "final": final}
    if condition in ("five_to_two", "older_database"):
        pending = {"base": final, "final": base}
    elif condition == "same_sequence":
        pending["base"] = final
    elif condition == "wrong_ancestor":
        pending["base"] = {**base, "hash": "f" * 64}
    elif condition == "future_base":
        pending = {"base": {**final, "seq": 8}, "final": {**final, "seq": 11, "hash": "a" * 64}}
    elif condition == "wrong_final":
        pending["final"] = {**final, "hash": "a" * 64}
    elif condition == "wrong_config":
        pending["base"] = {**base, "config": "a" * 64}
    elif condition == "boolean_sequence":
        pending["final"] = {**final, "seq": True}
    elif condition == "negative_sequence":
        pending["base"] = {**base, "seq": -1}
    elif condition == "malformed_hash":
        pending["final"] = {**final, "hash": "invalid"}
    elif condition == "reordered":
        fixtures.mutate(candidate / source.path.name,
                        lambda db: db.execute("UPDATE events SET seq=seq+20 WHERE seq=4"))
    elif condition == "substituted":
        fixtures.mutate(candidate / source.path.name,
                        lambda db: db.execute("UPDATE events SET event_id='substitute' WHERE seq=2"))
    (candidate / "pending.json").write_bytes(v2.encode(pending))
    anchor = (candidate / "anchor.json").read_bytes()
    for _ in range(2):
        with pytest.raises((ValueError, sqlite3.DatabaseError)):
            fixtures.book(candidate)
        assert (candidate / "anchor.json").read_bytes() == anchor
        assert (candidate / "pending.json").exists()
        assert not (candidate / v2.QUARANTINE).exists()


@pytest.mark.parametrize("committed", [False, True])
def test_valid_publication_recovery_is_idempotent(tmp_path, committed):
    source = opened(tmp_path / "prefix")
    base = json.loads(source.anchor.read_bytes())
    candidate = tmp_path / "candidate"
    shutil.copytree(source.directory, candidate)
    fixtures.observe(source, START + BAR, high=608)
    final = json.loads(source.anchor.read_bytes())
    if committed:
        shutil.rmtree(candidate)
        shutil.copytree(source.directory, candidate)
        (candidate / "anchor.json").write_bytes(v2.encode(base))
    (candidate / "pending.json").write_bytes(v2.encode({"base": base, "final": final}))
    for _ in range(2):
        recovered = fixtures.book(candidate)
        expected = 1005 if committed else 1000
        assert recovered.account(START + 2 * BAR)["balance"] == expected
        assert json.loads(recovered.anchor.read_bytes()) == (final if committed else base)
        assert not recovered.pending.exists()
    result = fixtures.observe(recovered, START + BAR, high=608)
    assert result["net_pnl"] == pytest.approx(5)
    assert recovered.account(START + 2 * BAR)["balance"] == 1005
    assert recovered.performance()["settled_trades"] == 1


@pytest.mark.parametrize("target", ["pending.json", "anchor.json"])
@pytest.mark.parametrize("point", ["before_create", "during_write", "after_fsync", "before_replace", "after_replace"])
def test_abrupt_death_inside_publication_recovers_once(tmp_path, target, point):
    book = opened(tmp_path)
    child = """
import os,sys,socket,runpy
from pathlib import Path
import dotenv
dotenv.load_dotenv=lambda *a,**k:False
os.environ.update(UPSTOX_ACCESS_TOKEN='',DEEPSEEK_API_KEY='',PYTHONDONTWRITEBYTECODE='1')
sys.dont_write_bytecode=True
def deny(*a,**k):raise AssertionError('offline child')
socket.socket.connect=socket.create_connection=socket.getaddrinfo=deny
sys.path.insert(0,str(Path.cwd()/'tests'))
h=runpy.run_path(sys.argv[1])
f=h['fixtures'];v=h['v2'];book=f.book(sys.argv[2])
target,point=sys.argv[3:5]
letter='p' if target=='pending.json' else 'a'
original_name=v._stage_name
original_open=Path.open
original_fsync=os.fsync
original_replace=os.replace
selected=set()
def stage_name(path,value):
    result=original_name(path,value)
    if path.name==target and point=='before_create':os._exit(83)
    return result
class Partial:
    def __init__(self,stream):self.stream=stream
    def __enter__(self):self.stream.__enter__();return self
    def __exit__(self,*args):return self.stream.__exit__(*args)
    def __getattr__(self,key):return getattr(self.stream,key)
    def write(self,data):
        self.stream.write(data[:len(data)//2])
        self.stream.flush();original_fsync(self.stream.fileno());os._exit(83)
def opened(path,*args,**kwargs):
    stream=original_open(path,*args,**kwargs)
    mode=args[0] if args else kwargs.get('mode','r')
    if 'x' in mode and path.name.startswith('.cfv2-'+letter+'-'):
        selected.add(stream.fileno())
        if point=='during_write':return Partial(stream)
    return stream
def fsync(fd):
    original_fsync(fd)
    if point=='after_fsync' and fd in selected:os._exit(83)
def replace(source,destination):
    match=Path(destination).name==target
    if match and point=='before_replace':os._exit(83)
    original_replace(source,destination)
    if match and point=='after_replace':os._exit(83)
v._stage_name=stage_name;Path.open=opened;os.fsync=fsync;os.replace=replace
f.observe(book,f.START+f.v1.BAR,high=608)
"""
    result = subprocess.run([sys.executable, "-B", "-c", child, str(Path(__file__).resolve()),
                             str(tmp_path), target, point], capture_output=True, timeout=90)
    assert result.returncode == 83, result.stderr
    for _ in range(2):
        recovered = fixtures.book(tmp_path)
        assert recovered.account(START + 2 * BAR)["balance"] == (1000 if target == "pending.json" else 1005)
    quarantine = tmp_path / v2.QUARANTINE
    artifacts = list(quarantine.iterdir()) if quarantine.exists() else []
    assert len(artifacts) == int(point in ("during_write", "after_fsync", "before_replace"))
    fixtures.observe(recovered, START + BAR, high=608)
    assert recovered.account(START + 2 * BAR)["balance"] == 1005
    assert recovered.performance()["settled_trades"] == 1
    assert json.loads(recovered.anchor.read_bytes())["seq"] == 5
    assert not list(tmp_path.glob(".cfv2-*.writing"))
    assert not list(tmp_path.glob(".cfv2-*.ready"))


def test_acknowledgement_loss_does_not_repeat_financial_effect(tmp_path):
    book = completed(tmp_path)
    before = book.anchor.read_bytes()
    result = fixtures.observe(fixtures.book(tmp_path), START + BAR, high=608)
    assert result["net_pnl"] == pytest.approx(5)
    assert book.anchor.read_bytes() == before
    assert book.account(START + 2 * BAR)["balance"] == 1005
    assert book.performance()["settled_trades"] == 1


@pytest.mark.parametrize("condition", [
    "foreign", "corrupt_writing", "corrupt_ready", "truncated_ready",
    "ambiguous", "wrong_state", "regressive_intent", "invalid_ledger",
])
def test_untrusted_or_ambiguous_staging_fails_without_mutation(tmp_path, condition):
    book = completed(tmp_path)
    head = json.loads(book.anchor.read_bytes())
    stage = v2._stage_name(book.anchor, head)
    stage.write_bytes(v2.encode(head))
    if condition == "foreign":
        stage.rename(tmp_path / "unrelated.tmp")
    elif condition == "corrupt_writing":
        stage.write_bytes(b"foreign-content")
    elif condition in ("corrupt_ready", "truncated_ready"):
        stage = stage.rename(stage.with_suffix(".ready"))
        stage.write_bytes(b"foreign" if condition == "corrupt_ready" else v2.encode(head)[:20])
    elif condition == "ambiguous":
        other = v2._stage_name(book.anchor, head)
        other.write_bytes(v2.encode(head))
    elif condition == "wrong_state":
        stage.unlink()
        wrong = {**head, "seq": head["seq"] + 1, "hash": "b" * 64}
        stage = v2._stage_name(book.anchor, wrong)
        stage.write_bytes(v2.encode(wrong))
    elif condition == "regressive_intent":
        stage.unlink()
        base, final = heads(book)
        value = {"base": final, "final": base}
        stage = v2._stage_name(book.pending, value)
        stage.write_bytes(v2.encode(value))
    elif condition == "invalid_ledger":
        fixtures.mutate(book.path, lambda db: db.execute("DELETE FROM events WHERE seq=4"))
    before = book.anchor.read_bytes()
    files = {p.name: p.read_bytes() for p in tmp_path.iterdir() if p.name.startswith(".cfv2-")}
    for _ in range(2):
        with pytest.raises(ValueError):
            fixtures.book(tmp_path)
        assert book.anchor.read_bytes() == before
        assert not (tmp_path / v2.QUARANTINE).exists()
        assert files == {p.name: p.read_bytes() for p in tmp_path.iterdir() if p.name.startswith(".cfv2-")}


@pytest.mark.parametrize("length", [0, 17, None])
def test_verified_owned_staging_is_retained_without_becoming_authority(tmp_path, length):
    book = completed(tmp_path)
    head = json.loads(book.anchor.read_bytes())
    stage = v2._stage_name(book.anchor, head)
    content = v2.encode(head) if length is None else v2.encode(head)[:length]
    stage.write_bytes(content)
    for _ in range(2):
        recovered = fixtures.book(tmp_path)
        assert recovered.account(START + 2 * BAR)["balance"] == 1005
        assert recovered.performance()["settled_trades"] == 1
    retained = list((tmp_path / v2.QUARANTINE).iterdir())
    assert len(retained) == 1 and retained[0].read_bytes() == content


def test_joint_database_and_anchor_rollback_limit_is_preserved(tmp_path):
    source = opened(tmp_path / "source")
    copy = tmp_path / "copy"
    shutil.copytree(source.directory, copy)
    fixtures.observe(source, START + BAR, high=608)
    # Both retained local witnesses describe the old state, so no local evidence
    # can establish the independently newer completion. This is not solved here.
    old = fixtures.book(copy)
    assert old.account(START + 2 * BAR)["balance"] == 1000
    assert old.history("D")[-1]["state"] == "OPEN"


def test_interrupted_recovery_can_be_recovered_again(tmp_path, monkeypatch):
    book = completed(tmp_path)
    base, final = heads(book)
    book.anchor.write_bytes(v2.encode(base))
    book.pending.write_bytes(v2.encode({"base": base, "final": final}))
    first = v2._stage_name(book.anchor, final)
    first.write_bytes(v2.encode(final))

    def interrupted(path, value):
        stage = v2._stage_name(path, value)
        stage.write_bytes(v2.encode(value)[:20])
        raise KeyboardInterrupt()

    with monkeypatch.context() as patch:
        patch.setattr(v2, "_atomic", interrupted)
        with pytest.raises(KeyboardInterrupt):
            fixtures.book(tmp_path)
    assert json.loads(book.anchor.read_bytes()) == base
    assert len(list((tmp_path / v2.QUARANTINE).iterdir())) == 1
    for _ in range(2):
        recovered = fixtures.book(tmp_path)
        assert recovered.account(START + 2 * BAR)["balance"] == 1005
        assert recovered.performance()["settled_trades"] == 1
    assert len(list((tmp_path / v2.QUARANTINE).iterdir())) == 2
    assert json.loads(book.anchor.read_bytes()) == final


@pytest.mark.parametrize("kind", ["file", "junction"])
def test_quarantine_must_be_an_owned_plain_directory(tmp_path, monkeypatch, kind):
    book = completed(tmp_path)
    location = tmp_path / v2.QUARANTINE
    if kind == "file":
        location.write_bytes(b"foreign")
    else:
        location.mkdir()
        original = Path.is_junction
        monkeypatch.setattr(Path, "is_junction", lambda path: path == location or original(path))
    before = book.anchor.read_bytes()
    with pytest.raises(ValueError, match="quarantine"):
        fixtures.book(tmp_path)
    assert book.anchor.read_bytes() == before


@pytest.mark.parametrize("missing", [False, True])
def test_recovery_artifacts_cannot_authorize_reinitialization(tmp_path, missing):
    source = completed(tmp_path / "source")
    candidate = tmp_path / "copy"
    shutil.copytree(source.directory, candidate)
    head = json.loads(source.anchor.read_bytes())
    stage = v2._stage_name(candidate / "anchor.json", head)
    stage.write_bytes(v2.encode(head))
    (candidate / "anchor.json").unlink()
    database = candidate / source.path.name
    if missing:
        database.unlink()
    else:
        database.write_bytes(b"")
    with pytest.raises(ValueError, match="missing anchored database"):
        fixtures.book(candidate)
    assert not (candidate / "anchor.json").exists()
    assert stage.exists() and not (candidate / v2.QUARANTINE).exists()
    assert database.exists() is (not missing)


def test_interrupted_genesis_publication_preserves_empty_state(tmp_path, monkeypatch):
    def interrupted(path, value):
        stage = v2._stage_name(path, value)
        stage.write_bytes(v2.encode(value))
        raise KeyboardInterrupt()
    with monkeypatch.context() as patch:
        patch.setattr(v2, "_atomic", interrupted)
        with pytest.raises(KeyboardInterrupt):
            fixtures.book(tmp_path)
    for _ in range(2):
        recovered = fixtures.book(tmp_path)
        assert recovered.account(START)["balance"] == 1000
        assert recovered.performance()["settled_trades"] == 0
        assert json.loads(recovered.anchor.read_bytes())["seq"] == 0
