"""Offline native Windows launcher/task previews; never register or start a task."""
import json
import os
from pathlib import Path
import subprocess
import xml.etree.ElementTree as ET

import pytest


ROOT = Path(__file__).resolve().parents[1]
SCRIPTS = ROOT / "scripts"
PS = r"C:\Windows\System32\WindowsPowerShell\v1.0\powershell.exe"
NS = {"t": "http://schemas.microsoft.com/windows/2004/02/mit/task"}


def invoke(script, *arguments):
    env = os.environ.copy()
    for key in env:
        if any(word in key.upper() for word in ("TOKEN", "SECRET", "PASSWORD", "API_KEY", "CREDENTIAL")):
            env[key] = ""
    env["PYTHONDONTWRITEBYTECODE"] = "1"
    return subprocess.run(
        [PS, "-NoLogo", "-NoProfile", "-NonInteractive", "-File", str(SCRIPTS / script), *map(str, arguments)],
        capture_output=True, text=True, env=env, timeout=30,
    )


@pytest.fixture
def paths(tmp_path):
    config = tmp_path / "config with spaces.json"
    config.write_text("{}", encoding="utf-8")
    return config, tmp_path / "control with spaces"


def preview(script, paths, *extra):
    config, control = paths
    result = invoke(script, "-Config", config, "-ControlRoot", control, *extra)
    assert result.returncode == 0, result.stderr
    return json.loads(result.stdout)


@pytest.mark.parametrize("script", ["run_forward_paper_scheduled.ps1", "install_forward_paper_task.ps1"])
def test_native_powershell_syntax(script):
    # Parse without executing either script or touching Task Scheduler.
    command = "$tokens=$null; $errors=$null; [System.Management.Automation.Language.Parser]::ParseFile($args[0],[ref]$tokens,[ref]$errors) | Out-Null; if ($errors.Count) { $errors | Out-String | Write-Error; exit 1 }"
    # -Command does not bind trailing arguments portably; escape only our fixed path.
    command = command.replace("$args[0]", "'" + str(SCRIPTS / script).replace("'", "''") + "'")
    result = subprocess.run([PS, "-NoProfile", "-NonInteractive", "-Command", command], capture_output=True, text=True, timeout=30)
    assert result.returncode == 0, result.stderr
    assert (SCRIPTS / script).is_file()


def test_wrapper_preview_has_fixed_mode_absolute_paths_and_no_mutation(paths):
    plan = preview("run_forward_paper_scheduled.ps1", paths, "-Preview")
    assert plan["mode"] == "FORWARD_PAPER"
    assert Path(plan["python"]) == ROOT / ".venv" / "Scripts" / "python.exe"
    assert Path(plan["working_directory"]) == ROOT
    assert plan["arguments"] == ["-B", str(SCRIPTS / "run_scheduled_forward_paper.py"), "--mode", "FORWARD_PAPER", "--config", str(paths[0]), "--control-root", str(paths[1])]
    assert plan["launch"] is False
    assert not paths[1].exists()


@pytest.mark.parametrize("script", ["run_forward_paper_scheduled.ps1", "install_forward_paper_task.ps1"])
@pytest.mark.parametrize("field", ["Config", "ControlRoot"])
def test_relative_paths_fail_closed(script, field, paths):
    values = {"Config": paths[0], "ControlRoot": paths[1]}
    values[field] = "relative-path"
    extra = ["-Preview"] if script.startswith("run_") else []
    result = invoke(script, "-Config", values["Config"], "-ControlRoot", values["ControlRoot"], *extra)
    assert result.returncode != 0
    assert not paths[1].exists()


@pytest.mark.parametrize("script", ["run_forward_paper_scheduled.ps1", "install_forward_paper_task.ps1"])
def test_missing_required_arguments_do_not_prompt(script):
    result = invoke(script)
    assert result.returncode != 0


def test_wrapper_refuses_mode_override(paths):
    result = invoke("run_forward_paper_scheduled.ps1", "-Config", paths[0], "-ControlRoot", paths[1], "-Mode", "LIVE", "-Preview")
    assert result.returncode != 0
    assert not paths[1].exists()


def test_installer_default_preview_disabled_task_xml(paths):
    plan = preview("install_forward_paper_task.ps1", paths)
    assert plan["register"] is False
    assert plan["required_time_zone"] == "India Standard Time"
    assert isinstance(plan["host_time_zone_matches"], bool)
    xml = ET.fromstring(plan["task_xml"])
    assert xml.findtext("t:Settings/t:Enabled", namespaces=NS) == "false"
    assert xml.findtext("t:Settings/t:MultipleInstancesPolicy", namespaces=NS) == "IgnoreNew"
    assert xml.findtext("t:Principals/t:Principal/t:LogonType", namespaces=NS) == "InteractiveToken"
    assert xml.findtext("t:Principals/t:Principal/t:RunLevel", namespaces=NS) == "LeastPrivilege"
    assert xml.findtext("t:Actions/t:Exec/t:WorkingDirectory", namespaces=NS) == str(ROOT)
    assert xml.findtext("t:Triggers/t:CalendarTrigger/t:StartBoundary", namespaces=NS).endswith("T09:05:00")
    days = xml.find("t:Triggers/t:CalendarTrigger/t:ScheduleByWeek/t:DaysOfWeek", NS)
    assert [day.tag.split("}")[-1] for day in days] == ["Monday", "Tuesday", "Wednesday", "Thursday", "Friday"]
    arguments = xml.findtext("t:Actions/t:Exec/t:Arguments", namespaces=NS)
    assert "-NonInteractive" in arguments and "-WindowStyle Hidden" in arguments
    assert "ExecutionPolicy" not in arguments
    assert str(paths[0]) in arguments and str(paths[1]) in arguments
    assert not paths[1].exists()


def test_installer_registration_is_explicit_and_never_enables():
    text = (SCRIPTS / "install_forward_paper_task.ps1").read_text(encoding="utf-8")
    assert "if (-not $Register)" in text
    assert "Register-ScheduledTask" in text
    assert "Get-ScheduledTask" in text
    assert "-Force" not in text
    assert "Enable-ScheduledTask" not in text
    assert "Start-ScheduledTask" not in text
    assert "Set-TimeZone" not in text


def test_wrapper_preserves_child_exit_and_isolates_logs():
    text = (SCRIPTS / "run_forward_paper_scheduled.ps1").read_text(encoding="utf-8")
    assert "$childExitCode = $child.ExitCode" in text
    assert "exit $childExitCode" in text
    assert "stdout.log" in text and "stderr.log" in text
    assert "Get-ChildItem Env:" not in text


def test_wrapper_child_invalid_config_is_blocked_without_target_writes(paths):
    # Incomplete config cannot authorize an experiment or construct a transport.
    # Parent CLI has been reviewed to return CONFIG_INVALID before engine work.
    paths[0].write_text('{"mode":"FORWARD_PAPER"}', encoding="utf-8")
    result = invoke("run_forward_paper_scheduled.ps1", "-Config", paths[0], "-ControlRoot", paths[1])
    assert result.returncode == 2, result.stderr
    assert not paths[1].exists()
    summary = json.loads(result.stdout)
    assert summary["status"] == "NO_GO"
    assert summary["reason_codes"] == ["CONFIG_INVALID"]
    assert summary["external_requests"] == 0
    assert summary["trade_placement_enabled"] is False
