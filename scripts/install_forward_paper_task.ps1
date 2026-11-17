[CmdletBinding()]
param(
    [Parameter(Mandatory = $true)][string]$Config,
    [Parameter(Mandatory = $true)][string]$ControlRoot,
    [string]$TaskName = 'AITradingBot-ForwardPaper',
    [switch]$Register
)

$ErrorActionPreference = 'Stop'
Set-StrictMode -Version Latest
if ([string]::IsNullOrWhiteSpace($TaskName) -or $TaskName -match '[\\/\x00-\x1f]') { throw 'Invalid task name.' }
$wrapper = Join-Path $PSScriptRoot 'run_forward_paper_scheduled.ps1'
$plan = (& $wrapper -Config $Config -ControlRoot $ControlRoot -Preview) | ConvertFrom-Json
$hostTimeZone = [TimeZoneInfo]::Local.Id
$requiredTimeZone = 'India Standard Time'
$timeZoneMatches = $hostTimeZone -eq $requiredTimeZone
$powershell = Join-Path $env:SystemRoot 'System32\WindowsPowerShell\v1.0\powershell.exe'
$principal = [Security.Principal.WindowsIdentity]::GetCurrent().User.Value
# Task arguments use quoted paths. Doubling trailing backslashes preserves them
# under the Windows native command-line argument parser.
function Quote-TaskPath([string]$Value) {
    return '"' + [regex]::Replace($Value, '(\\+)$', '$1$1') + '"'
}
function Escape-Xml([string]$Value) { return [Security.SecurityElement]::Escape($Value) }
$arguments = '-NoLogo -NoProfile -NonInteractive -WindowStyle Hidden -File ' + (Quote-TaskPath $wrapper) +
    ' -Config ' + (Quote-TaskPath $plan.arguments[5]) + ' -ControlRoot ' + (Quote-TaskPath $plan.arguments[7])
$taskXml = @"
<?xml version="1.0" encoding="UTF-16"?>
<Task version="1.2" xmlns="http://schemas.microsoft.com/windows/2004/02/mit/task">
  <RegistrationInfo><Description>Preparation only: disabled FORWARD_PAPER launcher; engine calendar and preflight remain authoritative.</Description></RegistrationInfo>
  <Triggers><CalendarTrigger><StartBoundary>2026-01-01T09:05:00</StartBoundary><Enabled>true</Enabled><ScheduleByWeek><WeeksInterval>1</WeeksInterval><DaysOfWeek><Monday/><Tuesday/><Wednesday/><Thursday/><Friday/></DaysOfWeek></ScheduleByWeek></CalendarTrigger></Triggers>
  <Principals><Principal id="CurrentUser"><UserId>$(Escape-Xml $principal)</UserId><LogonType>InteractiveToken</LogonType><RunLevel>LeastPrivilege</RunLevel></Principal></Principals>
  <Settings><MultipleInstancesPolicy>IgnoreNew</MultipleInstancesPolicy><DisallowStartIfOnBatteries>false</DisallowStartIfOnBatteries><StopIfGoingOnBatteries>false</StopIfGoingOnBatteries><StartWhenAvailable>false</StartWhenAvailable><Enabled>false</Enabled><ExecutionTimeLimit>PT12H</ExecutionTimeLimit></Settings>
  <Actions Context="CurrentUser"><Exec><Command>$(Escape-Xml $powershell)</Command><Arguments>$(Escape-Xml $arguments)</Arguments><WorkingDirectory>$(Escape-Xml $plan.working_directory)</WorkingDirectory></Exec></Actions>
</Task>
"@
if (-not $Register) {
    [ordered]@{
        task_name = $TaskName
        register = $false
        required_time_zone = $requiredTimeZone
        host_time_zone = $hostTimeZone
        host_time_zone_matches = $timeZoneMatches
        task_xml = $taskXml
    } | ConvertTo-Json -Depth 4
    return
}
if (-not $timeZoneMatches) { throw 'Registration refused: host time zone must already be India Standard Time.' }
Import-Module ScheduledTasks -ErrorAction Stop
$existing = Get-ScheduledTask -TaskPath '\' -ErrorAction Stop | Where-Object { $_.TaskName -eq $TaskName }
if ($existing) { throw 'Task already exists; refusing to replace it.' }
Register-ScheduledTask -TaskName $TaskName -TaskPath '\' -Xml $taskXml -ErrorAction Stop
