"""V2.2 durability boundaries: offline, copied synthetic state only."""
import json
import os
from pathlib import Path
import shutil
import sqlite3
from types import SimpleNamespace

import pytest

import test_causal_forward_paper_v2 as f
import test_causal_forward_paper_v2_recovery as recovery

v2 = f.v2


class Interrupted(OSError):
    pass


@pytest.fixture(scope="module")
def oracle(tmp_path_factory):
    directory = tmp_path_factory.mktemp("oracle")
    book = recovery.opened(directory / "prefix")
    shutil.copytree(book.directory, directory / "closed")
    final = f.book(directory / "closed")
    f.observe(final, f.START + f.v1.BAR, high=608)
    for capital in (900, 1000):
        f.book(directory / str(capital), capital=capital)
    return directory


def copy_book(oracle, tmp_path, closed=False):
    directory = tmp_path / "copy"
    shutil.copytree(oracle / ("closed" if closed else "prefix"), directory)
    return f.book(directory)


def assert_once(book, oracle):
    expected = oracle / "closed"
    for _ in range(2):
        book = f.book(book.directory)
        result = f.observe(book, f.START + f.v1.BAR, high=608)
        assert result["net_pnl"] == pytest.approx(5)
        account = book.account(f.START + 2 * f.v1.BAR)
        assert account["balance"] == account["eligible_equity"] == account["available_cash"] == 1005
        assert account["reserved_cash"] == 0
        assert book.performance()["settled_trades"] == 1
        assert book.anchor.read_bytes() == (expected / "anchor.json").read_bytes()
        with sqlite3.connect(book.path) as actual, sqlite3.connect(expected / book.path.name) as reference:
            assert actual.execute("SELECT * FROM settlements").fetchall() == reference.execute(
                "SELECT * FROM settlements").fetchall()


POINTS = ["before_create", "after_create", "partial_write", "before_writing_fsync",
          "after_writing_fsync", "before_ready_rename", "after_ready_rename",
          "before_ready_sync", "after_ready_sync", "before_replace", "after_replace",
          "before_canonical_sync", "after_canonical_sync", "before_canonical_directory_sync",
          "after_canonical_directory_sync", "after_publication"]


def interrupt_publication(patch, target, point):
    """Throw only inside one selected publication, after real preceding operations."""
    original_atomic, original_open = v2._atomic, Path.open
    original_fsync, original_replace = os.fsync, os.replace
    original_directory, original_file = v2._sync_directory, v2._sync_file
    active = {"value": False, "writing": None, "directories": 0}

    def hit(label):
        if active["value"] and point == label:
            raise Interrupted(label)

    def atomic(path, value):
        active["value"] = path.name == target
        active["directories"] = 0
        try:
            hit("before_create")
            original_atomic(path, value)
            hit("after_publication")
        finally:
            active["value"] = False

    class Partial:
        def __init__(self, stream):
            self.stream = stream

        def __enter__(self):
            self.stream.__enter__()
            return self

        def __exit__(self, *args):
            return self.stream.__exit__(*args)

        def __getattr__(self, key):
            return getattr(self.stream, key)

        def write(self, data):
            self.stream.write(data[:17])
            self.stream.flush()
            raise Interrupted("partial_write")

    def opened(path, *args, **kwargs):
        stream = original_open(path, *args, **kwargs)
        if active["value"] and args and args[0] == "xb":
            active["writing"] = stream.fileno()
            if point == "after_create":
                stream.close()
                raise Interrupted(point)
            if point == "partial_write":
                return Partial(stream)
        return stream

    def fsync(descriptor):
        writing = active["value"] and descriptor == active["writing"]
        if writing:
            hit("before_writing_fsync")
        original_fsync(descriptor)
        if writing:
            hit("after_writing_fsync")
            active["writing"] = None

    def replace(source, destination):
        label = "ready_rename" if Path(destination).suffix == ".ready" else "replace"
        hit("before_" + label)
        original_replace(source, destination)
        hit("after_" + label)

    def directory(path):
        active["directories"] += 1
        label = "ready_sync" if active["directories"] == 1 else "canonical_directory_sync"
        hit("before_" + label)
        original_directory(path)
        hit("after_" + label)

    def file(path):
        hit("before_canonical_sync")
        original_file(path)
        hit("after_canonical_sync")

    patch.setattr(v2, "_atomic", atomic)
    patch.setattr(Path, "open", opened)
    patch.setattr(os, "fsync", fsync)
    patch.setattr(os, "replace", replace)
    patch.setattr(v2, "_sync_directory", directory)
    patch.setattr(v2, "_sync_file", file)


@pytest.mark.parametrize("target", ["pending.json", "anchor.json"])
@pytest.mark.parametrize("point", POINTS)
def test_restart_at_each_durability_boundary(oracle, tmp_path, monkeypatch, target, point):
    book = copy_book(oracle, tmp_path)
    with monkeypatch.context() as patch:
        interrupt_publication(patch, target, point)
        with pytest.raises(Interrupted, match=point):
            f.observe(book, f.START + f.v1.BAR, high=608)
    before_replace = POINTS.index(point) <= POINTS.index("before_replace")
    assert json.loads(book.anchor.read_bytes())["seq"] == (
        2 if target == "pending.json" or before_replace else 5)
    recovered = f.book(book.directory)
    # Intent publication precedes SQLite commit; only anchor publication has
    # an already committed settlement. A retry completes the former exactly once.
    assert recovered.account(f.START + 2 * f.v1.BAR)["balance"] == (1000 if target == "pending.json" else 1005)
    assert_once(recovered, oracle)


@pytest.mark.parametrize("point", ["before_cleanup", "after_unlink", "after_cleanup", "lost_response"])
def test_acknowledgement_requires_durable_cleanup(oracle, tmp_path, monkeypatch, point):
    book = copy_book(oracle, tmp_path)
    finish, atomic, unlink = v2.CausalForwardPaperV2._finish_publication, v2._atomic, Path.unlink
    advanced = False

    def publish(path, value):
        nonlocal advanced
        atomic(path, value)
        if path == book.anchor:
            advanced = True

    def cleanup(self):
        if advanced and point == "before_cleanup":
            raise Interrupted(point)
        finish(self)
        if advanced and point in ("after_cleanup", "lost_response"):
            raise Interrupted(point)

    def remove(path, *args, **kwargs):
        unlink(path, *args, **kwargs)
        if advanced and path == book.pending and point == "after_unlink":
            raise Interrupted(point)

    with monkeypatch.context() as patch:
        patch.setattr(v2, "_atomic", publish)
        patch.setattr(v2.CausalForwardPaperV2, "_finish_publication", cleanup)
        patch.setattr(Path, "unlink", remove)
        with pytest.raises(Interrupted, match=point):
            f.observe(book, f.START + f.v1.BAR, high=608)
    assert_once(book, oracle)


def test_atomic_order_and_verification(tmp_path, monkeypatch):
    value = {"config": "a" * 64, "seq": 5, "hash": "b" * 64}
    events = []
    original_replace, original_fsync = os.replace, os.fsync

    def replace(source, target):
        events.append("ready" if Path(target).suffix == ".ready" else "canonical")
        original_replace(source, target)

    def file(path):
        events.append("canonical_fsync")
        v2._sync_file_original(path)

    def fsync(fd):
        events.append("file_fsync")
        original_fsync(fd)

    monkeypatch.setattr(v2, "_sync_file_original", v2._sync_file, raising=False)
    monkeypatch.setattr(v2, "_sync_file", file)
    monkeypatch.setattr(v2, "_sync_directory", lambda p: events.append(("directory", p)))
    monkeypatch.setattr(os, "replace", replace)
    monkeypatch.setattr(os, "fsync", fsync)
    v2._atomic(tmp_path / "anchor.json", value)
    assert events == ["file_fsync", "ready", ("directory", tmp_path), "canonical",
                      "canonical_fsync", "file_fsync", ("directory", tmp_path)]


def test_ready_is_verified_after_directory_sync(tmp_path, monkeypatch):
    value = {"config": "a" * 64, "seq": 5, "hash": "b" * 64}
    canonical = tmp_path / "anchor.json"
    canonical.write_bytes(b"prior authoritative content")

    def changed_ready(directory):
        next(directory.glob("*.ready")).write_bytes(b"incomplete synthetic fixture")

    monkeypatch.setattr(v2, "_sync_directory", changed_ready)
    with pytest.raises(ValueError, match="corrupt ready"):
        v2._atomic(canonical, value)
    assert canonical.read_bytes() == b"prior authoritative content"


@pytest.mark.parametrize("phase", ["writing", "ready"])
@pytest.mark.parametrize("condition", ["valid", "partial", "corrupt", "foreign", "duplicate", "mixed",
                                      "older", "future", "wrong_identity"])
def test_staging_never_overrides_committed_head(oracle, tmp_path, phase, condition):
    book = copy_book(oracle, tmp_path, closed=True)
    head = json.loads(book.anchor.read_bytes())
    value = head
    if condition == "older":
        value = json.loads((oracle / "prefix/anchor.json").read_bytes())
    elif condition == "future":
        value = {**head, "seq": 8, "hash": "e" * 64}
    elif condition == "wrong_identity":
        value = {**head, "hash": "e" * 64}
    stage = v2._stage_name(book.anchor, value).with_suffix("." + phase)
    content = v2.encode(value)
    if condition == "partial":
        content = content[:17]
    elif condition == "corrupt":
        content = b"invalid synthetic artifact"
    stage.write_bytes(content)
    if condition == "foreign":
        stage.rename(stage.parent / ("foreign." + phase))
    elif condition in ("duplicate", "mixed"):
        other = v2._stage_name(book.anchor, value)
        other = other.with_suffix("." + (phase if condition == "duplicate" else
                                         "ready" if phase == "writing" else "writing"))
        other.write_bytes(v2.encode(value))
    before = book.anchor.read_bytes(), book.path.read_bytes()
    if condition == "valid" or (condition == "partial" and phase == "writing"):
        assert_once(book, oracle)
        retained = list((book.directory / v2.QUARANTINE).iterdir())
        assert len(retained) == 1 and retained[0].read_bytes() == content
    else:
        for _ in range(2):
            with pytest.raises(ValueError):
                f.book(book.directory)
            assert (book.anchor.read_bytes(), book.path.read_bytes()) == before
            assert not (book.directory / v2.QUARANTINE).exists()


def test_ready_descendant_requires_committed_sqlite_ancestry(oracle, tmp_path):
    book = copy_book(oracle, tmp_path, closed=True)
    final = json.loads(book.anchor.read_bytes())
    base = json.loads((oracle / "prefix/anchor.json").read_bytes())
    book.anchor.write_bytes(v2.encode(base))
    book.pending.write_bytes(v2.encode({"base": base, "final": final}))
    v2._stage_name(book.anchor, final).with_suffix(".ready").write_bytes(v2.encode(final))
    assert_once(book, oracle)


@pytest.mark.parametrize("boundary", ["parent_created", "destination_file", "destination_directory", "source_directory"])
def test_quarantine_failure_is_recoverable_without_new_economics(oracle, tmp_path, monkeypatch, boundary):
    book = copy_book(oracle, tmp_path, closed=True)
    head = json.loads(book.anchor.read_bytes())
    stage = v2._stage_name(book.anchor, head).with_suffix(".ready")
    stage.write_bytes(v2.encode(head))
    directory, file = v2._sync_directory, v2._sync_file
    calls = 0

    def sync_directory(path):
        nonlocal calls
        calls += 1
        directory(path)
        if (boundary == "parent_created" and calls == 1
                or boundary == "destination_directory" and calls == 2
                or boundary == "source_directory" and calls == 3):
            raise Interrupted(boundary)

    def sync_file(path):
        file(path)
        if boundary == "destination_file":
            raise Interrupted(boundary)

    with monkeypatch.context() as patch:
        patch.setattr(v2, "_sync_directory", sync_directory)
        patch.setattr(v2, "_sync_file", sync_file)
        with pytest.raises(Interrupted, match=boundary):
            f.book(book.directory)
    assert_once(book, oracle)


def test_invalid_ledger_is_rejected_before_durability_or_quarantine(oracle, tmp_path, monkeypatch):
    book = copy_book(oracle, tmp_path, closed=True)
    head = json.loads(book.anchor.read_bytes())
    stage = v2._stage_name(book.anchor, head).with_suffix(".ready")
    stage.write_bytes(v2.encode(head))
    f.mutate(book.path, lambda db: db.execute("DELETE FROM events WHERE seq=4"))
    monkeypatch.setattr(v2, "_sync_directory", lambda p: pytest.fail("invalid ledger must not reach sync"))
    with pytest.raises(ValueError):
        f.book(book.directory)
    assert stage.exists() and not (book.directory / v2.QUARANTINE).exists()


@pytest.mark.parametrize("failure", ["open", "flush", "none"])
def test_posix_directory_descriptor_is_closed_and_failures_propagate(tmp_path, monkeypatch, failure):
    calls = []

    def opened(path, flags):
        assert path == tmp_path and flags == 3
        calls.append("open")
        if failure == "open":
            raise OSError("unsupported directory")
        return 987

    def flush(fd):
        assert fd == 987
        calls.append("flush")
        if failure == "flush":
            raise OSError("directory durability failed")

    fake = SimpleNamespace(name="posix", O_RDONLY=1, O_DIRECTORY=2, open=opened,
                           fsync=flush, close=lambda fd: calls.append(("close", fd)))
    monkeypatch.setattr(v2, "os", fake)
    if failure == "none":
        v2._sync_directory(tmp_path)
    else:
        with pytest.raises(OSError):
            v2._sync_directory(tmp_path)
    assert calls == (["open"] if failure == "open" else ["open", "flush", ("close", 987)])


def test_native_directory_flush_is_not_mocked(tmp_path):
    # Unsupported local filesystems/permissions must fail, never become a skip.
    v2._sync_directory(tmp_path)


@pytest.mark.parametrize("equity", [900, 1000, 1005])
@pytest.mark.parametrize("offset", [-1e-6, 1e-6])
def test_exact_current_equity_risk_after_restart(oracle, tmp_path, equity, offset):
    directory = tmp_path / "risk"
    shutil.copytree(oracle / ("closed" if equity == 1005 else str(equity)), directory)
    capital = 1000 if equity == 1005 else equity
    book = f.book(directory, capital=capital)
    entry = equity * .9
    target_risk = equity * .01 + offset
    lo, hi = entry * .95, entry * .9999
    for _ in range(80):
        stop = (lo + hi) / 2
        cost = f.estimate_round_trip_costs(entry, stop, 1, f.START.date())["total_cost"]
        if entry - stop + cost > target_risk:
            lo = stop
        else:
            hi = stop
    proposed = f.propose(book, decision="risk", shift=4, entry_price=entry, stop_loss=hi,
                         target_price=entry * 1.03)
    assert proposed["state"] == "ENTRY_PENDING"
    result = f.observe(book, f.START + 4 * f.v1.BAR, decision="risk",
                       open=entry, high=entry, low=entry, close=entry)
    assert result["entry_account"]["eligible_equity"] == equity
    assert result["entry_check"]["max_trade_risk"] == {900: 9, 1000: 10, 1005: 10.05}[equity]
    assert (result["state"] == "OPEN") == (offset < 0)
    assert f.book(directory, capital=capital).account(f.START + 5 * f.v1.BAR)["eligible_equity"] == equity


@pytest.mark.parametrize("operation", ["file", "directory"])
def test_recovered_canonical_state_requires_successful_flush(oracle, tmp_path, monkeypatch, operation):
    book = copy_book(oracle, tmp_path, closed=True)
    before = book.anchor.read_bytes()

    def failed(path):
        raise OSError("durability unavailable")

    with monkeypatch.context() as patch:
        patch.setattr(v2, "_sync_" + operation, failed)
        for _ in range(2):
            with pytest.raises(OSError, match="durability unavailable"):
                f.book(book.directory)
    assert book.anchor.read_bytes() == before
    assert_once(book, oracle)


@pytest.mark.parametrize("failure", ["open", "flush", "close", "none"])
def test_windows_directory_handle_failures_propagate_and_close(tmp_path, monkeypatch, failure):
    if os.name != "nt":
        pytest.skip("native Windows error API")
    import ctypes

    calls = []

    class Function:
        def __init__(self, name):
            self.name = name

        def __call__(self, *args):
            calls.append(self.name)
            if self.name == "CreateFileW":
                assert args == (str(tmp_path), 0x40000000, 7, None, 3, 0x02000000, None)
                return ctypes.c_void_p(-1).value if failure == "open" else 987
            assert args == (987,)
            return not (failure == "flush" and self.name == "FlushFileBuffers"
                        or failure == "close" and self.name == "CloseHandle")

    kernel = SimpleNamespace(**{name: Function(name) for name in
                               ("CreateFileW", "FlushFileBuffers", "CloseHandle")})
    monkeypatch.setattr(ctypes, "WinDLL", lambda *a, **k: kernel)
    if failure == "none":
        v2._sync_directory(tmp_path)
    else:
        with pytest.raises(OSError):
            v2._sync_directory(tmp_path)
    assert calls == (["CreateFileW"] if failure == "open" else
                     ["CreateFileW", "FlushFileBuffers", "CloseHandle"])
