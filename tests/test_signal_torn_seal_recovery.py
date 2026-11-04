"""Offline torn-seal cases. Writes are confined to synthetic temporary fixtures."""
import hashlib
import json

import pytest

from test_signal_observation_chain import collected, capture, replay
from test_ai_led_reviewed import setup, used
from app.research import signal_observations as future, ai_led_guarded as guard
from app.research.deepseek_prospective import AppendOnlyJsonl


def seal_bytes(request_id="a" * 64):
    return json.dumps({"request_id": request_id, "experiment_sha256": "b" * 64,
                       "observation_sha256": "c" * 64, "saved_artifact_sha256": "d" * 64},
                      sort_keys=True, separators=(",", ":")).encode()


@pytest.mark.parametrize("ending", [b"\n", b"\r\n", b""])
def test_normal_valid_seals_remain_readable(tmp_path, ending):
    data = seal_bytes() + ending
    path = tmp_path / "future_observation_seals.jsonl"
    path.write_bytes(data)
    records, partial = future._read_future_seals(tmp_path)
    assert records == [json.loads(data)] and partial is None
    assert path.read_bytes() == data


@pytest.mark.parametrize("exists", [False, True])
def test_absent_or_empty_seal_ledger(tmp_path, exists):
    path = tmp_path / "future_observation_seals.jsonl"
    if exists:
        path.write_bytes(b"")
    assert future._read_future_seals(tmp_path) == ([], None)
    assert path.exists() is exists


@pytest.mark.parametrize("tail", [b'{', b'{"request_id":', b'{"request_id":"truncated',
                                  b'{"request_id":"abc",', b'{"request_id":"abc"',
                                  b'{"request_id": ', b'{"request_id":"abc", '])
@pytest.mark.parametrize("prefix", [b"", seal_bytes() + b"\n"])
def test_torn_final_object_or_string_reports_tail_and_preserves_prefix(tmp_path, tail, prefix):
    path = tmp_path / "future_observation_seals.jsonl"
    data = prefix + tail
    path.write_bytes(data)
    records, partial = future._read_future_seals(tmp_path)
    assert records == ([json.loads(prefix)] if prefix else [])
    assert partial == {"status": "UNRECOVERABLE_PARTIAL_PUBLICATION",
        "reason": "INCOMPLETE_UNTERMINATED_FINAL_JSON_RECORD", "line_number": 2 if prefix else 1,
        "byte_offset": len(prefix), "byte_count": len(tail), "sha256": hashlib.sha256(tail).hexdigest(),
        "complete_record_count": len(records)}
    assert future._read_future_seals(tmp_path) == (records, partial)
    assert path.read_bytes() == data


def test_every_truncated_prefix_of_an_actual_ascii_seal_is_detected(tmp_path):
    path = tmp_path / "future_observation_seals.jsonl"
    encoded = seal_bytes()
    for cut in range(1, len(encoded)):
        path.write_bytes(encoded[:cut])
        records, partial = future._read_future_seals(tmp_path)
        assert not records and partial["byte_count"] == cut


@pytest.mark.parametrize("data", [
    b'{"request_id":\n' + seal_bytes() + b'\n',
    seal_bytes() + b'\n{"request_id":"torn\n' + seal_bytes("e" * 64) + b'\n',
    b'{"request_id":}\n' + seal_bytes() + b'\n',
    b'{"request_id":}', b'{"request_id":"bad\\q', b'{"request_id":\n',
    b'\n', b'null\n', seal_bytes() + b'\n' + seal_bytes() + b'\n',
])
def test_internal_or_committed_corruption_is_not_ignored(tmp_path, data):
    path = tmp_path / "future_observation_seals.jsonl"
    path.write_bytes(data)
    with pytest.raises(guard.GuardedBatchError, match="future_seal_ledger_corrupt"):
        future._read_future_seals(tmp_path)
    assert path.read_bytes() == data


@pytest.mark.parametrize("tail", [b'{"request_id":', b'{"request_id":"truncated'])
def test_interrupted_seal_append_returns_stable_missing_result_without_replacement(collected, monkeypatch, tail):
    s, _, _, _ = collected
    original = AppendOnlyJsonl.append
    def interrupted(self, record):
        if self.path.name == "future_observation_seals.jsonl":
            with self.path.open("ab") as stream:
                stream.write(tail)
                stream.flush()
            raise KeyboardInterrupt()
        return original(self, record)
    monkeypatch.setattr(AppendOnlyJsonl, "append", interrupted)
    with pytest.raises(KeyboardInterrupt):
        capture(collected)
    before = {p: p.read_bytes() for p in s.output.rglob("*") if p.is_file()}
    result = replay(collected)
    assert result == replay(collected) == capture(collected)
    assert result["observation_bindings"][0]["validation_status"] == "UNRECOVERABLE_PARTIAL_PUBLICATION"
    assert result["seal_ledger_state"]["byte_count"] == len(tail)
    assert result["total_expected_prediction_evaluations"] == result["total_missing_insufficient"] == 1
    assert result["total_completed_evaluations"] == 0 and result["ai_up_frequency"] is None
    assert result["evaluation_accounting"][0]["status"] == "MISSING_FUTURE_DATA"
    assert used(s, "market_data_request_budget") == 2 and used(s, "physical_request_attempts") == 2
    assert len(s.http) == 2 and len(s.sdk) == 1 and not (s.output / "paper_account").exists()
    assert set(before) == {p for p in s.output.rglob("*") if p.is_file()}
    assert all(p.read_bytes() == data for p, data in before.items())


def test_valid_seal_preceding_torn_tail_remains_usable_and_unchanged(collected):
    s, _, _, _ = collected
    completed = capture(collected)
    path = s.output / "future_observation_seals.jsonl"
    prefix = path.read_bytes()
    with path.open("ab") as stream:
        stream.write(b'{"request_id":"unfinished')
    before = {p: p.read_bytes() for p in s.output.rglob("*") if p.is_file()}
    result = replay(collected)
    assert result["total_completed_evaluations"] == completed["total_completed_evaluations"] == 1
    assert result["seal_ledger_state"]["complete_record_count"] == 1
    assert future._read_future_seals(s.output)[0] == [json.loads(prefix)]
    assert result == replay(collected) == capture(collected)
    assert len(s.http) == 2 and len(s.sdk) == 1
    assert all(p.read_bytes() == data for p, data in before.items())


def test_torn_ledger_blocks_a_new_future_request_before_reservation(collected):
    s, _, _, _ = collected
    # Synthetic partial ledger exists before this still-unspent future job.
    path = s.output / "future_observation_seals.jsonl"
    path.write_bytes(b'{"request_id":"unfinished')
    before = {p: p.read_bytes() for p in s.output.rglob("*") if p.is_file()}
    result = capture(collected)
    assert result["seal_ledger_state"]["status"] == "UNRECOVERABLE_PARTIAL_PUBLICATION"
    assert result["total_expected_prediction_evaluations"] == result["total_missing_insufficient"] == 1
    assert result["total_completed_evaluations"] == 0 and result["ai_up_frequency"] is None
    assert used(s, "market_data_request_budget") == used(s, "physical_request_attempts") == 1
    assert len(s.http) == len(s.sdk) == 1 and result == replay(collected) == capture(collected)
    assert all(p.read_bytes() == data for p, data in before.items())


def test_internal_ledger_corruption_blocks_capture_before_any_new_request(collected):
    s, _, _, _ = collected
    path = s.output / "future_observation_seals.jsonl"
    data = b'{"request_id":\n' + seal_bytes() + b'\n'
    path.write_bytes(data)
    for action in (lambda: replay(collected), lambda: capture(collected)):
        with pytest.raises(guard.GuardedBatchError, match="future_seal_ledger_corrupt"):
            action()
    assert path.read_bytes() == data
    assert len(s.http) == len(s.sdk) == 1 and used(s, "market_data_request_budget") == 1
