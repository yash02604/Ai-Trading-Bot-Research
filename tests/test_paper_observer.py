"""Observer reads published safe records, never the recovery-capable engine."""
import copy
import importlib
import importlib.util
import json
from pathlib import Path

import pytest

from test_scheduled_forward_paper import api, config, fixture


def observer():
    assert importlib.util.find_spec("app.research.paper_observer") is not None, "observer module required"
    return importlib.import_module("app.research.paper_observer")


def test_observer_has_no_state_changing_engine_dependency(tmp_path):
    module = api(); reader = observer()
    summary = module._run_synthetic_fixture(config(), tmp_path, fixture(), as_of="2026-11-02T12:15:00+05:30")
    path = Path(summary["observer_path"])
    before = {str(p): p.read_bytes() for p in tmp_path.rglob("*") if p.is_file()}
    result = reader.read_snapshot(path, as_of="2026-11-02T12:15:01+05:30")
    assert result["observer_status"] == "AVAILABLE" and result["snapshot"]["mode"] == "FORWARD_PAPER"
    assert result["snapshot"]["simulation_only"] is True
    assert before == {str(p): p.read_bytes() for p in tmp_path.rglob("*") if p.is_file()}
    assert not any(name in reader.__dict__ for name in ("CausalForwardPaperV2", "PaperTrader", "get_settings"))
    assert "COMPLETED" in reader.human_summary(result)


@pytest.mark.parametrize("content", [None, b"{", b'{}', b'{"schema_version":"bad","api_key":"sk-secret"}', b'{"x":1,"x":2}'])
def test_missing_or_invalid_observer_returns_safe_failure_without_repair(tmp_path, content):
    reader = observer(); path = tmp_path / "snapshot.json"
    if content is not None: path.write_bytes(content)
    result = reader.read_snapshot(path, as_of="2026-11-02T12:15:00+05:30")
    assert result["observer_status"] == "UNAVAILABLE"
    assert "sk-secret" not in json.dumps(result)
    assert path.read_bytes() == content if content is not None else not path.exists()


def test_stale_heartbeat_is_not_reported_as_current_liveness(tmp_path):
    summary = api()._run_synthetic_fixture(config(), tmp_path, fixture(), as_of="2026-11-02T12:15:00+05:30")
    result = observer().read_snapshot(summary["observer_path"], as_of="2026-11-02T12:20:00+05:30")
    assert result["observer_status"] == "STALE"
    assert result["process_alive"] is None


def test_unknown_fields_and_secret_reason_are_not_exposed(tmp_path):
    summary = api()._run_synthetic_fixture(config(), tmp_path, fixture(), as_of="2026-11-02T12:15:00+05:30")
    path = Path(summary["observer_path"]); original = json.loads(path.read_text())
    for patch in ({"api_key": "sk-secret"}, {"reason_codes": ["bearer secret"]}):
        mutated = copy.deepcopy(original); mutated.update(patch); path.write_text(json.dumps(mutated))
        assert observer().read_snapshot(path, as_of="2026-11-02T12:15:01+05:30")["observer_status"] == "UNAVAILABLE"


def test_event_reader_is_read_only_and_rejects_tampered_fields(tmp_path):
    summary = api()._run_synthetic_fixture(config(), tmp_path, fixture(), as_of="2026-11-02T12:15:00+05:30")
    path = Path(summary["observer_path"]).with_name("events.jsonl"); before = path.read_bytes()
    assert observer().read_events(path)["observer_status"] == "AVAILABLE"
    assert path.read_bytes() == before
    path.write_bytes(before + b'{"api_key":"sk-secret"}\n')
    result = observer().read_events(path)
    assert result["observer_status"] == "UNAVAILABLE" and "sk-secret" not in json.dumps(result)

