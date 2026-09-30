<#
.SYNOPSIS
  让前端访问地址跟随本机当前已连接的网卡（有线/无线/热点）。

.DESCRIPTION
  1. 找出所有已连接（链路已接通、有有效 IPv4）的物理网卡；
  2. 写入 .env：
     INVOICEOPS_ALLOWED_HOSTS = localhost 127.0.0.1 + 这些网卡的 IP（其他地址返回 421）
     INVOICEOPS_HOST          = 当前上网网卡（默认路由跃点最小）的 IP，用于证书；
                                没有网卡连接时为 127.0.0.1
     INVOICEOPS_BIND_ADDRESS  = 0.0.0.0
  3. 有变化时只重建 proxy 容器（docker compose up -d --no-build --no-deps proxy），
     Caddy 使用同一个本地 CA 签发证书，其他服务不受影响。

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

function Get-ConnectedIPv4 {
    # 已连接的物理网卡（排除虚拟网卡、APIPA 地址）；有默认路由的按跃点排序在前
    $virtual = 'Hyper-V|WSL|VirtualBox|VMware|Loopback|Bluetooth|TAP-|Wintun|WireGuard'
    $result = @()
    foreach ($adapter in @(Get-NetAdapter -ErrorAction SilentlyContinue)) {
        if ($adapter.Status -ne 'Up' -or "$($adapter.MediaConnectionState)" -ne 'Connected') { continue }
        if ($adapter.InterfaceDescription -match $virtual -or $adapter.Name -match $virtual) { continue }
        $ip = Get-NetIPAddress -InterfaceIndex $adapter.ifIndex -AddressFamily IPv4 -ErrorAction SilentlyContinue |
            Where-Object { $_.IPAddress -notlike '169.254.*' -and $_.AddressState -eq 'Preferred' } |
            Select-Object -First 1
        if (-not $ip) { continue }
        $iface = Get-NetIPInterface -InterfaceIndex $adapter.ifIndex -AddressFamily IPv4 -ErrorAction SilentlyContinue
        $route = Get-NetRoute -InterfaceIndex $adapter.ifIndex -AddressFamily IPv4 -DestinationPrefix '0.0.0.0/0' -ErrorAction SilentlyContinue |
            Where-Object { $_.NextHop -and $_.NextHop -ne '0.0.0.0' } | Sort-Object RouteMetric | Select-Object -First 1
        $metric = if ($route) { [int]$route.RouteMetric + [int]$iface.InterfaceMetric } else { 100000 + [int]$iface.InterfaceMetric }
        $result += [pscustomobject]@{ IP = $ip.IPAddress; Alias = $adapter.Name; Description = $adapter.InterfaceDescription; Metric = $metric; Internet = [bool]$route }
    }
    return @($result | Sort-Object Metric)
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

$nets = @(Get-ConnectedIPv4)
if ($nets.Count -gt 0) {
    $newHost = $nets[0].IP
    foreach ($n in $nets) {
        Write-Log ("已连接网卡：{0}（{1}） IP={2}{3}" -f $n.Alias, $n.Description, $n.IP, $(if ($n.Internet) { '，有默认网关' } else { '' }))
    }
} else {
    $newHost = '127.0.0.1'
    Write-Log '没有已连接的网卡（断网），只保留 localhost 访问。'
}
$allowed = @('localhost', '127.0.0.1') + @($nets | ForEach-Object { $_.IP }) | Select-Object -Unique
$newAllowed = $allowed -join ' '

$raw = [System.IO.File]::ReadAllText($EnvFile)
$newline = if ($raw -match "`r`n") { "`r`n" } else { "`n" }
$lines = [System.Collections.Generic.List[string]]::new()
$raw -split "\r?\n" | ForEach-Object { $lines.Add($_) }
if ($lines.Count -gt 0 -and $lines[$lines.Count - 1] -eq '') { $lines.RemoveAt($lines.Count - 1) }

$oldHost = Get-EnvValue $lines 'INVOICEOPS_HOST'
$oldBind = Get-EnvValue $lines 'INVOICEOPS_BIND_ADDRESS'
$oldAllowed = Get-EnvValue $lines 'INVOICEOPS_ALLOWED_HOSTS'
$port = Get-EnvValue $lines 'INVOICEOPS_HTTPS_PORT'
if (-not $port) { $port = '8443' }

$envChanged = ($oldHost -ne $newHost) -or ($oldBind -ne '0.0.0.0') -or ($oldAllowed -ne $newAllowed)
Write-Log ("  .env 现值：HOST={0}  BIND={1}  ALLOWED={2}" -f $oldHost, $oldBind, $oldAllowed)

if ($DryRun) {
    if ($envChanged) { Write-Log "  [DryRun] 将改为 HOST=$newHost  BIND=0.0.0.0  ALLOWED=$newAllowed，并重建 proxy 容器" }
    else { Write-Log '  [DryRun] .env 已是最新' }
    return
}

if ($envChanged) {
    Set-EnvValue $lines 'INVOICEOPS_HOST' $newHost
    Set-EnvValue $lines 'INVOICEOPS_BIND_ADDRESS' '0.0.0.0'
    Set-EnvValue $lines 'INVOICEOPS_ALLOWED_HOSTS' $newAllowed
    [System.IO.File]::WriteAllText($EnvFile, (($lines -join $newline) + $newline), [System.Text.UTF8Encoding]::new($false))
    Write-Log "  已更新 .env：HOST=$newHost  BIND=0.0.0.0  ALLOWED=$newAllowed"
}

# 已是最新配置时 compose 不会重建容器；配置有变化时只重建 proxy。
# 服务已停止时不擅自启动，只更新 .env，下次启动时生效。
Push-Location $ProjectDir
# docker 会把提示（如孤立容器警告）写到 stderr；Windows PowerShell 在 Stop 模式下会把它当成异常
$ErrorActionPreference = 'Continue'
try {
    $running = @(& docker compose ps --status running -q proxy 2>$null)
    $code = $LASTEXITCODE
    if ($code -eq 0 -and -not $running) {
        Write-Log '  proxy 未在运行（服务已停止），只更新 .env，下次启动服务时生效。'
        return
    }
    if ($code -eq 0) {
        $out = @(& docker compose up -d --no-build --no-deps proxy 2>&1 | ForEach-Object { "$_" })
        $code = $LASTEXITCODE
    } else { $out = @(& docker compose ps proxy 2>&1 | ForEach-Object { "$_" }) }
} finally { Pop-Location; $ErrorActionPreference = 'Stop' }
if ($code -ne 0) {
    Write-Log "  docker compose 执行失败（Docker Desktop 是否已启动？）：$($out -join ' ')"
    exit 1
}
$urls = ($allowed | Where-Object { $_ -ne '127.0.0.1' } | ForEach-Object { "https://${_}:$port/" }) -join '  '
Write-Log "  proxy 已就绪。可用地址：$urls"
