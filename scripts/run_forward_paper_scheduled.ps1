[CmdletBinding()]
param(
    [Parameter(Mandatory = $true)][string]$Config,
    [Parameter(Mandatory = $true)][string]$ControlRoot,
    [switch]$Preview
)

$ErrorActionPreference = 'Stop'
Set-StrictMode -Version Latest

function Get-AbsoluteSafePath([string]$Value, [string]$Name) {
    if ($Value -notmatch '^(?:[A-Za-z]:[\\/]|\\\\[^\\]+\\[^\\]+)' -or $Value -match '["\r\n\x00]') {
        throw "$Name must be an absolute Windows path without quotes or control characters."
    }
    return [IO.Path]::GetFullPath($Value)
}

$repoRoot = [IO.Path]::GetFullPath((Join-Path $PSScriptRoot '..'))
$configPath = Get-AbsoluteSafePath $Config 'Config'
$controlPath = Get-AbsoluteSafePath $ControlRoot 'ControlRoot'
$python = Join-Path $repoRoot '.venv\Scripts\python.exe'
$entryPoint = Join-Path $repoRoot 'scripts\run_scheduled_forward_paper.py'
if (-not (Test-Path -LiteralPath $configPath -PathType Leaf)) { throw 'Config file does not exist.' }
if (-not (Test-Path -LiteralPath $python -PathType Leaf)) { throw 'Project virtual environment Python does not exist.' }
$childArguments = @('-B', $entryPoint, '--mode', 'FORWARD_PAPER', '--config', $configPath, '--control-root', $controlPath)
if ($Preview) {
    [ordered]@{
        mode = 'FORWARD_PAPER'
        python = $python
        working_directory = $repoRoot
        arguments = $childArguments
        launcher_log_directory = (Join-Path $controlPath 'launcher-logs')
        launch = $false
    } | ConvertTo-Json -Depth 4
    return
}
if (-not (Test-Path -LiteralPath $entryPoint -PathType Leaf)) { throw 'Scheduled Python entry point does not exist.' }
# This owned metadata preparation validates config/root before target log writes.
# It cannot construct an engine or authorize transport. Actual launch revalidates.
$preparation = & $python @childArguments '--prepare-launch'
if ($LASTEXITCODE -ne 0) {
    $preparationExitCode = $LASTEXITCODE
    $preparation | Write-Output
    exit $preparationExitCode
}
$prepared = $preparation | ConvertFrom-Json
if ($prepared.status -ne 'CONTROL_PREPARED') { throw 'Control-root preparation failed.' }
$logDirectory = Join-Path $controlPath 'launcher-logs'
[void][IO.Directory]::CreateDirectory($logDirectory)
$logPrefix = (Get-Date -Format 'yyyyMMddTHHmmssfffffff') + '-' + [guid]::NewGuid().ToString('N')
$stdoutLog = Join-Path $logDirectory ($logPrefix + '.stdout.log')
$stderrLog = Join-Path $logDirectory ($logPrefix + '.stderr.log')
$quotedArguments = $childArguments | ForEach-Object {
    '"' + [regex]::Replace($_, '(\\+)$', '$1$1') + '"'
}
$child = Start-Process -FilePath $python -ArgumentList ($quotedArguments -join ' ') `
    -WorkingDirectory $repoRoot -RedirectStandardOutput $stdoutLog -RedirectStandardError $stderrLog `
    -WindowStyle Hidden -Wait -PassThru
$childExitCode = $child.ExitCode
exit $childExitCode
