<#
.SYNOPSIS
  让前端访问地址跟随本机当前上网的网卡（有线/无线/热点）。

.DESCRIPTION
  1. 找出当前默认路由（跃点数最小、已连接）的物理网卡 IPv4 地址；
  2. 写入 .env 的 INVOICEOPS_HOST，并确保 INVOICEOPS_BIND_ADDRESS=0.0.0.0；
  3. 有变化时只重建 proxy 容器（docker compose up -d --no-build --no-deps proxy），
     Caddy 使用同一个本地 CA 为新 IP 签发证书，其他服务不受影响。

.EXAMPLE
  .\refresh_frontend_address.ps1                  # 检测并按需更新
  .\refresh_frontend_address.ps1 -DryRun          # 只显示检测结果，不改任何东西
  .\refresh_frontend_address.ps1 -InstallAutoRun  # 注册计划任务：登录时和网络连接变化时自动运行
  .\refresh_frontend_address.ps1 -UninstallAutoRun
#>
[CmdletBinding()]
param(
    [switch]$DryRun,
    [switch]$InstallAutoRun,
    [switch]$UninstallAutoRun,
    [switch]$Quiet
)

$ErrorActionPreference = 'Stop'
$ProjectDir = $PSScriptRoot
$EnvFile = Join-Path $ProjectDir '.env'
$TaskName = 'InvoiceOps-RefreshFrontendAddress'
$LogDir = Join-Path $env:LOCALAPPDATA 'InvoiceOps'
$LogFile = Join-Path $LogDir 'refresh-frontend-address.log'

function Write-Log([string]$Message) {
    $line = '{0:yyyy-MM-dd HH:mm:ss}  {1}' -f (Get-Date), $Message
    if (-not $Quiet) { Write-Host $Message }
    try {
        if (-not (Test-Path $LogDir)) { New-Item -ItemType Directory -Path $LogDir -Force | Out-Null }
        Add-Content -LiteralPath $LogFile -Value $line -Encoding UTF8
    } catch { }
}

function Get-InternetIPv4 {
    # 当前真正用于上网的网卡：有默认网关、已连接、非虚拟网卡，路由跃点 + 接口跃点最小
    $virtual = 'Hyper-V|WSL|VirtualBox|VMware|Loopback|Bluetooth|TAP-|Wintun|WireGuard'
    $best = $null
    $routes = Get-NetRoute -AddressFamily IPv4 -DestinationPrefix '0.0.0.0/0' -ErrorAction SilentlyContinue |
        Where-Object { $_.NextHop -and $_.NextHop -ne '0.0.0.0' }
    foreach ($route in $routes) {
        $iface = Get-NetIPInterface -InterfaceIndex $route.InterfaceIndex -AddressFamily IPv4 -ErrorAction SilentlyContinue
        if (-not $iface -or $iface.ConnectionState -ne 'Connected') { continue }
        $adapter = Get-NetAdapter -InterfaceIndex $route.InterfaceIndex -ErrorAction SilentlyContinue
        if (-not $adapter -or $adapter.Status -ne 'Up') { continue }
        if ($adapter.InterfaceDescription -match $virtual -or $adapter.Name -match $virtual) { continue }
        $ip = Get-NetIPAddress -InterfaceIndex $route.InterfaceIndex -AddressFamily IPv4 -ErrorAction SilentlyContinue |
            Where-Object { $_.IPAddress -notlike '169.254.*' -and $_.AddressState -eq 'Preferred' } |
            Select-Object -First 1
        if (-not $ip) { continue }
        $metric = [int]$route.RouteMetric + [int]$iface.InterfaceMetric
        if (-not $best -or $metric -lt $best.Metric) {
            $best = [pscustomobject]@{ IP = $ip.IPAddress; Alias = $adapter.Name; Description = $adapter.InterfaceDescription; Metric = $metric }
        }
    }
    return $best
}

function Get-EnvValue([string[]]$Lines, [string]$Key) {
    foreach ($l in $Lines) { if ($l -match "^\s*$([regex]::Escape($Key))\s*=(.*)$") { return $Matches[1].Trim() } }
    return $null
}

function Set-EnvValue([System.Collections.Generic.List[string]]$Lines, [string]$Key, [string]$Value) {
    for ($i = 0; $i -lt $Lines.Count; $i++) {
        if ($Lines[$i] -match "^\s*$([regex]::Escape($Key))\s*=") { $Lines[$i] = "$Key=$Value"; return }
    }
    $Lines.Add("$Key=$Value")
}

# ---------- 计划任务 ----------
if ($UninstallAutoRun) {
    Unregister-ScheduledTask -TaskName $TaskName -Confirm:$false -ErrorAction SilentlyContinue
    Write-Host "已删除计划任务 $TaskName"
    return
}

if ($InstallAutoRun) {
    $cls = Get-CimClass -ClassName MSFT_TaskEventTrigger -Namespace Root/Microsoft/Windows/TaskScheduler
    $netTrigger = New-CimInstance -CimClass $cls -ClientOnly
    $netTrigger.Enabled = $true
    $netTrigger.Delay = 'PT15S'   # 等网络稳定、拿到 IP 后再执行
    $netTrigger.Subscription = '<QueryList><Query Id="0" Path="Microsoft-Windows-NetworkProfile/Operational"><Select Path="Microsoft-Windows-NetworkProfile/Operational">*[System[(EventID=10000 or EventID=10001)]]</Select></Query></QueryList>'
    $logonTrigger = New-ScheduledTaskTrigger -AtLogOn -User "$env:USERDOMAIN\$env:USERNAME"
    $logonTrigger.Delay = 'PT1M'  # 等 Docker Desktop 启动
    $action = New-ScheduledTaskAction -Execute 'powershell.exe' -WorkingDirectory $ProjectDir `
        -Argument "-NoProfile -NonInteractive -WindowStyle Hidden -ExecutionPolicy Bypass -File `"$PSCommandPath`" -Quiet"
    $settings = New-ScheduledTaskSettingsSet -AllowStartIfOnBatteries -DontStopIfGoingOnBatteries `
        -MultipleInstances IgnoreNew -ExecutionTimeLimit (New-TimeSpan -Minutes 5)
    $principal = New-ScheduledTaskPrincipal -UserId "$env:USERDOMAIN\$env:USERNAME" -LogonType Interactive -RunLevel Limited
    Register-ScheduledTask -TaskName $TaskName -Trigger @($netTrigger, $logonTrigger) -Action $action `
        -Settings $settings -Principal $principal -Force `
        -Description 'InvoiceOps：网络变化时把前端地址更新为当前上网网卡的 IP' | Out-Null
    Write-Host "已注册计划任务 $TaskName（登录后 1 分钟、网络连接变化后 15 秒自动运行）"
    Write-Host "运行日志：$LogFile"
    return
}

# ---------- 检测并更新 ----------
if (-not (Test-Path -LiteralPath $EnvFile)) { throw "找不到 $EnvFile，请先由 .env.example 复制并填写。" }

$net = Get-InternetIPv4
if (-not $net) {
    Write-Log '未检测到可用于上网的网卡（可能正在切换网络），本次不做修改。'
    return
}

$raw = [System.IO.File]::ReadAllText($EnvFile)
$newline = if ($raw -match "`r`n") { "`r`n" } else { "`n" }
$lines = [System.Collections.Generic.List[string]]::new()
$raw -split "\r?\n" | ForEach-Object { $lines.Add($_) }
if ($lines.Count -gt 0 -and $lines[$lines.Count - 1] -eq '') { $lines.RemoveAt($lines.Count - 1) }

$oldHost = Get-EnvValue $lines 'INVOICEOPS_HOST'
$oldBind = Get-EnvValue $lines 'INVOICEOPS_BIND_ADDRESS'
$port = Get-EnvValue $lines 'INVOICEOPS_HTTPS_PORT'
if (-not $port) { $port = '8443' }

$envChanged = ($oldHost -ne $net.IP) -or ($oldBind -ne '0.0.0.0')
Write-Log ("当前上网网卡：{0}（{1}） IP={2}" -f $net.Alias, $net.Description, $net.IP)
Write-Log ("  .env：INVOICEOPS_HOST={0}  INVOICEOPS_BIND_ADDRESS={1}" -f $oldHost, $oldBind)

if ($DryRun) {
    if ($envChanged) { Write-Log "  [DryRun] 将改为 INVOICEOPS_HOST=$($net.IP)、INVOICEOPS_BIND_ADDRESS=0.0.0.0，并重建 proxy 容器" }
    else { Write-Log '  [DryRun] .env 已是最新' }
    return
}

if ($envChanged) {
    Set-EnvValue $lines 'INVOICEOPS_HOST' $net.IP
    Set-EnvValue $lines 'INVOICEOPS_BIND_ADDRESS' '0.0.0.0'
    [System.IO.File]::WriteAllText($EnvFile, (($lines -join $newline) + $newline), [System.Text.UTF8Encoding]::new($false))
    Write-Log "  已更新 .env：INVOICEOPS_HOST=$($net.IP)、INVOICEOPS_BIND_ADDRESS=0.0.0.0"
}

# 已是最新配置时 compose 不会重建容器；配置有变化时只重建 proxy。
# 服务已停止时不擅自启动，只更新 .env，下次启动时生效。
Push-Location $ProjectDir
try {
    $running = & docker compose ps --status running -q proxy 2>&1
    $code = $LASTEXITCODE
    if ($code -eq 0 -and -not $running) {
        Write-Log '  proxy 未在运行（服务已停止），只更新 .env，下次启动服务时生效。'
        return
    }
    if ($code -eq 0) {
        $out = & docker compose up -d --no-build --no-deps proxy 2>&1
        $code = $LASTEXITCODE
    } else { $out = $running }
} finally { Pop-Location }
if ($code -ne 0) {
    Write-Log "  docker compose 执行失败（Docker Desktop 是否已启动？）：$($out -join ' ')"
    exit 1
}
Write-Log "  proxy 已就绪。访问地址：https://$($net.IP):$port/  （本机也可用 https://localhost:$port/）"
