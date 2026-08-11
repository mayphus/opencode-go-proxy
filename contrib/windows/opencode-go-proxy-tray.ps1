[CmdletBinding(DefaultParameterSetName = 'Run')]
param(
    [Parameter(ParameterSetName = 'Install')]
    [switch]$Install,

    [Parameter(ParameterSetName = 'Uninstall')]
    [switch]$Uninstall,

    [Parameter(ParameterSetName = 'Run')]
    [switch]$Run
)

$ErrorActionPreference = 'Stop'
$ProxyTaskName = 'OpenCode Go Proxy'
$TrayTaskName = 'OpenCode Go Proxy Tray'
$TaskScript = Join-Path $PSScriptRoot 'opencode-go-proxy-task.ps1'
$LogDirectory = Join-Path $HOME '.codex\logs'
$HealthUrl = 'http://127.0.0.1:8787/health'
$BaseUrl = 'http://127.0.0.1:8787/v1'

function Get-TaskUser {
    return "$env:USERDOMAIN\$env:USERNAME"
}

if ($Install) {
    & $TaskScript -Install
    $powershell = (Get-Command powershell.exe -ErrorAction Stop).Source
    $arguments = '-NoProfile -WindowStyle Hidden -ExecutionPolicy Bypass -File "{0}" -Run' -f $PSCommandPath
    $action = New-ScheduledTaskAction -Execute $powershell -Argument $arguments -WorkingDirectory $PSScriptRoot
    $trigger = New-ScheduledTaskTrigger -AtLogOn -User (Get-TaskUser)
    $settings = New-ScheduledTaskSettingsSet -StartWhenAvailable -ExecutionTimeLimit (New-TimeSpan -Days 3650)
    $principal = New-ScheduledTaskPrincipal -UserId (Get-TaskUser) -LogonType Interactive -RunLevel Limited
    Register-ScheduledTask -TaskName $TrayTaskName -Action $action -Trigger $trigger -Settings $settings -Principal $principal -Description 'OpenCode Go proxy notification-area controller' -Force | Out-Null
    Start-ScheduledTask -TaskName $TrayTaskName
    Write-Output "Installed and started: $ProxyTaskName and $TrayTaskName"
    exit 0
}

if ($Uninstall) {
    Stop-ScheduledTask -TaskName $TrayTaskName -ErrorAction SilentlyContinue
    Unregister-ScheduledTask -TaskName $TrayTaskName -Confirm:$false -ErrorAction SilentlyContinue
    Write-Output "Removed scheduled task: $TrayTaskName"
    Write-Output "The background proxy task is unchanged. Remove it with opencode-go-proxy-task.ps1 -Uninstall."
    exit 0
}

Add-Type -AssemblyName System.Windows.Forms
Add-Type -AssemblyName System.Drawing

$createdNew = $false
$mutex = [System.Threading.Mutex]::new($true, 'Local\OpenCodeGoProxyTray', [ref]$createdNew)
if (-not $createdNew) {
    $mutex.Dispose()
    exit 0
}

$notifyIcon = New-Object System.Windows.Forms.NotifyIcon
$notifyIcon.Icon = [System.Drawing.SystemIcons]::Application
$notifyIcon.Text = 'OpenCode Go Proxy'
$notifyIcon.Visible = $true

$menu = New-Object System.Windows.Forms.ContextMenuStrip
$statusItem = $menu.Items.Add('Checking status...')
$statusItem.Enabled = $false
$menu.Items.Add('-') | Out-Null
$startItem = $menu.Items.Add('Start Proxy')
$stopItem = $menu.Items.Add('Stop Proxy')
$openLogsItem = $menu.Items.Add('Open Logs')
$copyUrlItem = $menu.Items.Add('Copy API URL')
$menu.Items.Add('-') | Out-Null
$exitItem = $menu.Items.Add('Exit Tray')
$notifyIcon.ContextMenuStrip = $menu

function Test-ProxyHealth {
    try {
        $response = Invoke-WebRequest -UseBasicParsing -Uri $HealthUrl -TimeoutSec 2
        return $response.StatusCode -eq 200
    } catch {
        return $false
    }
}

function Update-ProxyStatus {
    $healthy = Test-ProxyHealth
    $task = Get-ScheduledTask -TaskName $ProxyTaskName -ErrorAction SilentlyContinue
    if ($healthy) {
        $statusItem.Text = 'Status: Running'
        $notifyIcon.Text = 'OpenCode Go Proxy - Running'
    } elseif ($null -eq $task) {
        $statusItem.Text = 'Status: Not installed'
        $notifyIcon.Text = 'OpenCode Go Proxy - Not installed'
    } else {
        $statusItem.Text = 'Status: Stopped'
        $notifyIcon.Text = 'OpenCode Go Proxy - Stopped'
    }
    $startItem.Enabled = -not $healthy -and $null -ne $task
    $stopItem.Enabled = $healthy -or ($null -ne $task -and $task.State -eq 'Running')
}

$startItem.add_Click({
    try {
        Start-ScheduledTask -TaskName $ProxyTaskName
        Start-Sleep -Milliseconds 400
        Update-ProxyStatus
    } catch {
        $notifyIcon.ShowBalloonTip(5000, 'OpenCode Go Proxy', $_.Exception.Message, 'Error')
    }
})

$stopItem.add_Click({
    Stop-ScheduledTask -TaskName $ProxyTaskName -ErrorAction SilentlyContinue
    Start-Sleep -Milliseconds 200
    Update-ProxyStatus
})

$openLogsItem.add_Click({
    New-Item -ItemType Directory -Path $LogDirectory -Force | Out-Null
    Start-Process explorer.exe -ArgumentList $LogDirectory
})

$copyUrlItem.add_Click({
    [System.Windows.Forms.Clipboard]::SetText($BaseUrl)
    $notifyIcon.ShowBalloonTip(2000, 'OpenCode Go Proxy', 'API URL copied.', 'Info')
})

$exitItem.add_Click({ [System.Windows.Forms.Application]::Exit() })
$notifyIcon.add_DoubleClick({
    if (Test-ProxyHealth) {
        Stop-ScheduledTask -TaskName $ProxyTaskName -ErrorAction SilentlyContinue
    } else {
        Start-ScheduledTask -TaskName $ProxyTaskName -ErrorAction SilentlyContinue
    }
    Start-Sleep -Milliseconds 300
    Update-ProxyStatus
})

$timer = New-Object System.Windows.Forms.Timer
$timer.Interval = 3000
$timer.add_Tick({ Update-ProxyStatus })
$timer.Start()
Update-ProxyStatus

try {
    [System.Windows.Forms.Application]::Run()
} finally {
    $timer.Stop()
    $timer.Dispose()
    $notifyIcon.Visible = $false
    $notifyIcon.Dispose()
    $menu.Dispose()
    $mutex.ReleaseMutex()
    $mutex.Dispose()
}
