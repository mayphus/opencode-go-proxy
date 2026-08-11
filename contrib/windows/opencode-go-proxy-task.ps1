[CmdletBinding(DefaultParameterSetName = 'Install')]
param(
    [Parameter(ParameterSetName = 'Install')]
    [switch]$Install,

    [Parameter(ParameterSetName = 'Uninstall')]
    [switch]$Uninstall,

    [Parameter(ParameterSetName = 'Status')]
    [switch]$Status,

    [Parameter(ParameterSetName = 'Run')]
    [switch]$Run,

    [Parameter(ParameterSetName = 'Run')]
    [string]$PythonPath
)

$ErrorActionPreference = 'Stop'
$TaskName = 'OpenCode Go Proxy'
$RepoRoot = (Resolve-Path (Join-Path $PSScriptRoot '..\..')).Path
$LogDirectory = Join-Path $HOME '.codex\logs'
$LogPath = Join-Path $LogDirectory 'opencode-go-proxy.log'
$ErrorLogPath = Join-Path $LogDirectory 'opencode-go-proxy.err.log'

function Get-TaskUser {
    return "$env:USERDOMAIN\$env:USERNAME"
}

if ($Status) {
    Get-ScheduledTask -TaskName $TaskName -ErrorAction SilentlyContinue |
        Get-ScheduledTaskInfo |
        Format-List TaskName, State, LastRunTime, LastTaskResult, NextRunTime
    exit 0
}

if ($Uninstall) {
    Stop-ScheduledTask -TaskName $TaskName -ErrorAction SilentlyContinue
    Unregister-ScheduledTask -TaskName $TaskName -Confirm:$false -ErrorAction SilentlyContinue
    Write-Output "Removed scheduled task: $TaskName"
    exit 0
}

if ($Run) {
    if (-not $PythonPath) {
        $PythonPath = Join-Path $RepoRoot '.venv\Scripts\python.exe'
    }
    if (-not (Test-Path -LiteralPath $PythonPath)) {
        throw "Python executable not found: $PythonPath"
    }
    New-Item -ItemType Directory -Path $LogDirectory -Force | Out-Null
    Set-Location -LiteralPath $RepoRoot
    $process = Start-Process -FilePath $PythonPath `
        -ArgumentList '-m opencode_go_proxy --bind 127.0.0.1 --port 8787' `
        -WorkingDirectory $RepoRoot `
        -WindowStyle Hidden `
        -RedirectStandardOutput $LogPath `
        -RedirectStandardError $ErrorLogPath `
        -Wait `
        -PassThru
    exit $process.ExitCode
}

$uv = (Get-Command uv -ErrorAction Stop).Source
& $uv sync --no-editable --reinstall-package opencode-go-proxy --directory $RepoRoot
$python = Join-Path $RepoRoot '.venv\Scripts\python.exe'
if (-not (Test-Path -LiteralPath $python)) {
    throw "Python executable was not created: $python"
}
$powershell = (Get-Command powershell.exe -ErrorAction Stop).Source
$taskArguments = '-NoProfile -WindowStyle Hidden -ExecutionPolicy Bypass -File "{0}" -Run -PythonPath "{1}"' -f $PSCommandPath, $python
$action = New-ScheduledTaskAction -Execute $powershell -Argument $taskArguments -WorkingDirectory $RepoRoot
$trigger = New-ScheduledTaskTrigger -AtLogOn -User (Get-TaskUser)
$settings = New-ScheduledTaskSettingsSet -StartWhenAvailable -RestartCount 10 -RestartInterval (New-TimeSpan -Minutes 1)
$principal = New-ScheduledTaskPrincipal -UserId (Get-TaskUser) -LogonType Interactive -RunLevel Limited

Register-ScheduledTask -TaskName $TaskName -Action $action -Trigger $trigger -Settings $settings -Principal $principal -Description 'OpenCode Go Responses proxy' -Force | Out-Null
Start-ScheduledTask -TaskName $TaskName
Write-Output "Installed and started scheduled task: $TaskName"
Write-Output "Logs: $LogPath and $ErrorLogPath"
