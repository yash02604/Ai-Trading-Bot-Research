"""Infrastructure preparation only; real transports never run."""
import copy
import importlib
import importlib.util
import json
import subprocess
import sys
from pathlib import Path

import pytest


@pytest.fixture(autouse=True)
def isolated_project_ownership(tmp_path, monkeypatch):
    # Production ownership is persistent under project logs; never use real logs.
    module = api()
    original = module.ownership
    def isolated(project_root=module.PROJECT_ROOT):
        return original(tmp_path.with_name(tmp_path.name + '.test-project') if Path(project_root).resolve() == module.PROJECT_ROOT else project_root)
    monkeypatch.setattr(module, 'ownership', isolated)


def api():
    assert importlib.util.find_spec("app.research.scheduled_forward_paper") is not None, "scheduler module required"
    return importlib.import_module("app.research.scheduled_forward_paper")


def config():
    return {"schema_version": "scheduled-forward-paper-v1", "mode": "FORWARD_PAPER",
            "initial_capital": 1000, "instrument_priority": ["RELIANCE"],
            "provider_cap": 1, "market_data_cap": 10}


def fixture():
    ready = "2026-11-02T12:02:37+05:30"
    start = "2026-11-02T12:05:00+05:30"
    def observation(slot, at, high):
        return {"operation": "observe", "args": {"decision_id": "D", "expected_start": slot, "as_of": at,
            "observation": {"symbol": "RELIANCE", "instrument_key": "NSE_EQ|INE002A01018", "observed_at": at,
                "candle": {"timestamp": slot, "open": 600, "high": high, "low": 599, "close": 600, "volume": 1000}}}}
    return {"schema_version": "synthetic-scheduled-v2-fixture-v1", "provenance": "SYNTHETIC_TEST_DATA", "commands": [
        {"operation": "propose", "args": {"decision_id": "D", "decision_ready_at": ready,
            "instrument_key": "NSE_EQ|INE002A01018", "quantity": 1,
            "proposal": {"symbol": "RELIANCE", "score": .84, "entry_price": 600,
                         "stop_loss": 597, "target_price": 608, "holding_bars": 1}}},
        observation(start, "2026-11-02T12:10:00+05:30", 601),
        observation("2026-11-02T12:10:00+05:30", "2026-11-02T12:15:00+05:30", 608)]}


@pytest.mark.parametrize("mode", ["LIVE", "BROKER", "REAL_ORDER", "forward_paper", "", None])
def test_wrong_mode_never_creates_state_or_enters_engine(tmp_path, mode):
    module = api(); cfg = config(); cfg["mode"] = mode
    result = module.run_scheduled(cfg, tmp_path / "control")
    assert result["status"] == "NO_GO" and result["reason_codes"] == ["MODE_NOT_PAPER"]
    assert not (tmp_path / "control").exists()


@pytest.mark.parametrize("clock,phase", [
    ("08:59:00", "BEFORE_SESSION"), ("09:05:00", "BEFORE_SESSION"),
    ("09:10:00", "BEFORE_SESSION"), ("09:15:00", "ENTRY_WINDOW"),
    ("12:00:00", "ENTRY_WINDOW"), ("15:09:59", "ENTRY_WINDOW"),
    ("15:10:00", "NO_NEW_ENTRIES"), ("15:15:00", "AFTER_CLOSE"),
    ("15:24:59", "AFTER_CLOSE"), ("15:25:00", "AFTER_CLOSE"), ("15:30:00", "AFTER_CLOSE"),
    ("16:00:00", "AFTER_CLOSE")])
def test_simulated_session_clock_does_not_grant_transport(clock, phase):
    result = api().preflight(config(), f"2026-11-02T{clock}+05:30")
    assert result["session"]["state"] == phase
    assert result["status"] == "NO_GO"
    assert "V2_TRANSPORT_NOT_REVIEWED" in result["reason_codes"]
    assert result["external_requests"] == 0 and result["trade_placement_enabled"] is False


@pytest.mark.parametrize("day", ["2026-11-07", "2024-11-20", "2026-11-08"])
def test_verified_closed_or_unsupported_session_is_clean_no_session(day):
    result = api().preflight(config(), day + "T09:05:00+05:30")
    assert result["status"] == "NO_SESSION"
    assert result["external_requests"] == 0


def test_unknown_calendar_and_naive_clock_fail_closed():
    assert api().preflight(config(), "2027-03-01T12:00:00+05:30")["status"] == "NO_GO"
    assert api().preflight(config(), "2026-11-02T12:00:00")["status"] == "NO_GO"


def test_production_preflight_block_cannot_be_overridden_by_config(tmp_path, monkeypatch):
    module = api(); cfg = config()
    monkeypatch.setattr(module, "_utc_now", lambda: "2026-11-02T12:00:00+05:30")
    result = module.run_scheduled(cfg, tmp_path / "production")
    assert result["status"] == "NO_GO" and "V2_TRANSPORT_NOT_REVIEWED" in result["reason_codes"]
    assert not list(tmp_path.rglob("*.sqlite3"))
    cfg["allow_market_data_requests"] = True
    assert module.run_scheduled(cfg, tmp_path / "override")["status"] == "NO_GO"


def test_lock_is_global_to_project_not_control_root_and_has_no_stale_pid_authority(tmp_path):
    module = api(); project = tmp_path / "project"
    with module.ownership(project):
        with pytest.raises(module.InstanceBusy):
            with module.ownership(project):
                pass
    with module.ownership(project):
        pass


def test_actual_child_death_releases_owner(tmp_path):
    module = api(); project = tmp_path / "project"
    child = "import os,sys; from app.research.scheduled_forward_paper import ownership;\nwith ownership(sys.argv[1]): os._exit(17)"
    result = subprocess.run([sys.executable, "-B", "-c", child, str(project)], capture_output=True, timeout=20)
    assert result.returncode == 17
    with module.ownership(project):
        pass


@pytest.mark.parametrize("stop_after", [0, 1, 2])
def test_synthetic_v2_restart_and_same_day_duplicate_are_exactly_once(tmp_path, stop_after):
    module = api(); root = tmp_path / "control"
    first = module._run_synthetic_fixture(config(), root, fixture(), as_of="2026-11-02T12:15:00+05:30", stop_after=stop_after)
    assert first["status"] == "INTERRUPTED"
    second = module._run_synthetic_fixture(config(), root, fixture(), as_of="2026-11-02T12:15:00+05:30")
    third = module._run_synthetic_fixture(config(), root, fixture(), as_of="2026-11-02T12:16:00+05:30")
    assert second["status"] == "RECOVERED_AND_COMPLETED"
    assert third["duplicate_launch"] is True
    assert second["account"] == third["account"]
    assert second["account"]["equity"] > 1000 and second["settled_trades"] == third["settled_trades"] == 1


@pytest.mark.parametrize("stop_after", [1, 2])
@pytest.mark.parametrize("when", ["2026-11-02T15:26:00+05:30", "2026-11-02T16:00:00+05:30"])
def test_missing_due_slot_fails_closed_without_fabricating_exit(tmp_path, stop_after, when):
    module = api(); trace = fixture()
    module._run_synthetic_fixture(config(), tmp_path, trace, as_of="2026-11-02T12:10:00+05:30", stop_after=stop_after)
    trace["commands"] = []
    result = module._run_synthetic_fixture(config(), tmp_path, trace, as_of=when)
    assert result["status"] == "FAILED_CLOSED" and result["settled_trades"] == 0
    assert "RECOVERY_REQUIRED" in result["reason_codes"]
    if stop_after == 2:
        assert result["account"]["reserved_cash"] > 0


@pytest.mark.parametrize("failure_point", ["_publish_observer", "_snapshot"])
def test_observer_publication_failure_does_not_change_economic_result(tmp_path, monkeypatch, failure_point):
    module = api(); trace = fixture(); before = copy.deepcopy(trace)
    healthy = module._run_synthetic_fixture(config(), tmp_path / "good", trace, as_of="2026-11-02T12:15:00+05:30")
    def unavailable(*args, **kwargs): raise OSError("sk-DO-NOT-LEAK bearer secret")
    monkeypatch.setattr(module, failure_point, unavailable)
    degraded = module._run_synthetic_fixture(config(), tmp_path / "bad", trace, as_of="2026-11-02T12:15:00+05:30")
    assert healthy["account"] == degraded["account"] and degraded["status"] == "COMPLETED"
    assert degraded["observer_failures"] > 0 and trace == before
    assert "DO-NOT-LEAK" not in json.dumps(degraded)


def test_changed_identity_is_not_silently_resumed(tmp_path):
    module = api(); module._run_synthetic_fixture(config(), tmp_path, fixture(), as_of="2026-11-02T12:15:00+05:30", stop_after=1)
    cfg = config(); cfg["initial_capital"] = 2000
    result = module._run_synthetic_fixture(cfg, tmp_path, fixture(), as_of="2026-11-02T12:15:00+05:30")
    assert result["status"] == "FAILED_CLOSED"


def test_control_root_does_not_adopt_or_modify_foreign_history(tmp_path, monkeypatch):
    module = api(); old = tmp_path / "historical.csv"; old.write_bytes(b"preserved history")
    monkeypatch.setattr(module, "_utc_now", lambda: "2026-11-02T12:00:00+05:30")
    result = module.run_scheduled(config(), tmp_path)
    assert result["status"] == "NO_GO"
    assert old.read_bytes() == b"preserved history"
    assert sorted(p.name for p in tmp_path.iterdir()) == ["historical.csv"]


def test_future_fixture_events_are_not_applied_before_simulated_clock(tmp_path):
    result = api()._run_synthetic_fixture(config(), tmp_path, fixture(), as_of="2026-11-02T12:03:00+05:30")
    assert result["status"] == "FAILED_CLOSED"


def test_daily_equity_continues_in_one_v2_ledger(tmp_path):
    module = api(); first = module._run_synthetic_fixture(config(), tmp_path, fixture(), as_of="2026-11-02T12:15:00+05:30")
    trace = json.loads(json.dumps(fixture()).replace("2026-11-02", "2026-11-03").replace('"D"', '"DAY2"'))
    second = module._run_synthetic_fixture(config(), tmp_path, trace, as_of="2026-11-03T12:15:00+05:30")
    assert second["account"]["opening_equity"] == first["account"]["equity"]
    assert second["account"]["equity"] > first["account"]["equity"]
    assert len(list(tmp_path.rglob("causal_forward_v2.sqlite3"))) == 1


def test_second_process_fails_closed_while_first_owns_project(tmp_path):
    module = api(); project = tmp_path / "owned-project"
    child = "from app.research.scheduled_forward_paper import ownership,InstanceBusy; import sys;\ntry:\n with ownership(sys.argv[1]): sys.exit(9)\nexcept InstanceBusy: sys.exit(23)"
    with module.ownership(project):
        result = subprocess.run([sys.executable, "-B", "-c", child, str(project)], capture_output=True, timeout=20)
    assert result.returncode == 23


@pytest.mark.parametrize("kind", ["stale", "wrong_symbol", "invalid_ohlc"])
def test_existing_v2_observation_gates_remain_authoritative(tmp_path, kind):
    trace = fixture(); trace["commands"] = trace["commands"][:2]
    observation = trace["commands"][1]["args"]
    if kind == "stale": observation["as_of"] = "2026-11-02T12:16:00+05:30"
    elif kind == "wrong_symbol": observation["observation"]["symbol"] = "HDFCBANK"
    else: observation["observation"]["candle"]["high"] = 590
    result = api()._run_synthetic_fixture(config(), tmp_path, trace, as_of="2026-11-02T12:17:00+05:30")
    assert result["status"] == "FAILED_CLOSED"
    assert result["settled_trades"] == 0
    assert result["candidates"][0]["reason_code"] == "OBSERVATION_MISSING"


def test_final_calendar_candle_settles_without_new_entry_after_cutoff(tmp_path):
    trace = fixture()
    trace["commands"][0]["args"]["decision_ready_at"] = "2026-11-02T14:59:00+05:30"
    trace["commands"][0]["args"]["proposal"]["holding_bars"] = 12
    template = copy.deepcopy(trace["commands"][1])
    trace["commands"] = trace["commands"][:1]
    for start, received in [("15:00:00", "15:05:00"), ("15:05:00", "15:10:00"), ("15:10:00", "15:15:00")]:
        row = copy.deepcopy(template); args = row["args"]
        args["expected_start"] = "2026-11-02T" + start + "+05:30"
        args["as_of"] = args["observation"]["observed_at"] = "2026-11-02T" + received + "+05:30"
        args["observation"]["candle"]["timestamp"] = args["expected_start"]
        trace["commands"].append(row)
    result = api()._run_synthetic_fixture(config(), tmp_path, trace, as_of="2026-11-02T15:30:00+05:30")
    assert result["status"] == "COMPLETED" and result["settled_trades"] == 1
    assert result["candidates"][0]["outcome"] == "CLOSED_SESSION"


def test_cli_cannot_select_a_fixture_or_print_config_secrets(tmp_path, capsys):
    api()
    from scripts.run_scheduled_forward_paper import main
    cfg = config(); cfg["api_key"] = "sk-NOT-FOR-LOGS"
    path = tmp_path / "config.json"; path.write_text(json.dumps(cfg))
    assert main(["--config", str(path), "--control-root", str(tmp_path / "runtime")]) == 2
    assert "NOT-FOR-LOGS" not in capsys.readouterr().out
    with pytest.raises(SystemExit):
        main(["--config", str(path), "--control-root", str(tmp_path / "runtime"), "--synthetic-fixture", "anything"])


def test_v2_interrupted_anchor_publication_uses_existing_recovery(tmp_path, monkeypatch):
    module = api()
    from app.research import causal_forward_paper_v2 as v2
    original = v2._atomic
    def stopped(path, value):
        if path.name == "anchor.json" and value.get("seq", 0) >= 5:
            raise OSError("fixture publication interrupted")
        return original(path, value)
    with monkeypatch.context() as patch:
        patch.setattr(v2, "_atomic", stopped)
        interrupted = module._run_synthetic_fixture(config(), tmp_path, fixture(), as_of="2026-11-02T12:15:00+05:30")
    assert interrupted["status"] == "FAILED_CLOSED"
    recovered = module._run_synthetic_fixture(config(), tmp_path, fixture(), as_of="2026-11-02T12:16:00+05:30")
    assert recovered["settled_trades"] == 1 and recovered["account"]["equity"] > 1000


def test_production_duplicate_has_immutable_attempt_history_and_no_engine(tmp_path, monkeypatch):
    module = api(); monkeypatch.setattr(module, "_utc_now", lambda: "2026-11-02T09:05:00+05:30")
    first = module.run_scheduled(config(), tmp_path)
    finals = list(tmp_path.rglob("*.final.json")); before = {str(p): p.read_bytes() for p in finals}
    second = module.run_scheduled(config(), tmp_path)
    assert first["status"] == second["status"] == "NO_GO"
    assert first["run_id"] == second["run_id"] and first["attempt_id"] != second["attempt_id"]
    assert second["previous_attempt_id"] == first["attempt_id"] and second["duplicate_launch"]
    assert all(Path(p).read_bytes() == value for p, value in before.items())
    assert len(list(tmp_path.rglob("*.begin.json"))) == len(list(tmp_path.rglob("*.final.json"))) == 2
    assert not (tmp_path / "engine").exists()

