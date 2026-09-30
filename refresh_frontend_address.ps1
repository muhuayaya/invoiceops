<#
.SYNOPSIS
  让前端访问地址跟随本机当前已连接的网卡（有线/无线/热点）。

.DESCRIPTION
  1. 找出所有已连接（链路已接通、有有效 IPv4）的物理网卡；
  2. 写入 .env：
     INVOICEOPS_ALLOWED_HOSTS = localhost 127.0.0.1 + 这些网卡的 IP（其他地址返回 421）
     INVOICEOPS_HOST          = 当前上网网卡（默认路由跃点最小）的 IP；没有网卡连接时为 127.0.0.1
     INVOICEOPS_BIND_ADDRESS  = 0.0.0.0
  3. 用 Caddy 本地根证书签发一张同时包含 localhost、127.0.0.1 和上述所有 IP 的证书，
     写到 infra/caddy/certs/site.pem（Caddy 优先使用它；没有时退回 tls internal）。
     已导入过 root.crt 的设备无需重新导入。根证书私钥只在内存中使用，不落盘。
  4. 有变化时只重建/重启 proxy 容器，其他服务不受影响；服务已停止时不擅自启动。

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
$CertDir = Join-Path $ProjectDir 'infra\caddy\certs'
$CertFile = Join-Path $CertDir 'site.pem'
$CertMeta = Join-Path $CertDir 'site.meta.txt'
$CaDir = '/data/caddy/pki/authorities/local'
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

# ---------- 证书 ----------
function ConvertFrom-Pem([string]$Pem, [string]$Label) {
    $m = [regex]::Match($Pem, "-----BEGIN $Label-----(.+?)-----END $Label-----", 'Singleline')
    if (-not $m.Success) { throw "PEM 内容中没有 $Label" }
    return , [Convert]::FromBase64String(($m.Groups[1].Value -replace '\s', ''))
}

function ConvertTo-Pem([byte[]]$Der, [string]$Label) {
    $b64 = [Convert]::ToBase64String($Der)
    $lines = for ($i = 0; $i -lt $b64.Length; $i += 64) { $b64.Substring($i, [Math]::Min(64, $b64.Length - $i)) }
    return "-----BEGIN $Label-----`n" + ($lines -join "`n") + "`n-----END $Label-----`n"
}

function Get-Sec1PrivateScalar([byte[]]$Der) {
    # SEC1 ECPrivateKey ::= SEQUENCE { version INTEGER(1), privateKey OCTET STRING, ... }
    $i = 0
    if ($Der[$i] -ne 0x30) { throw 'EC 私钥格式不正确' }
    $i++
    if ($Der[$i] -band 0x80) { $i += ($Der[$i] -band 0x7f) }
    $i++
    if ($Der[$i] -ne 0x02 -or $Der[$i + 1] -ne 0x01 -or $Der[$i + 2] -ne 0x01) { throw 'EC 私钥格式不正确' }
    $i += 3
    if ($Der[$i] -ne 0x04) { throw 'EC 私钥格式不正确' }
    $len = [int]$Der[$i + 1]
    $d = New-Object byte[] $len
    [Array]::Copy($Der, $i + 2, $d, 0, $len)
    return , $d
}

function ConvertTo-Sec1Der([System.Security.Cryptography.ECParameters]$P) {
    # P-256：30 77 | 02 01 01 | 04 20 <d> | a0 0a <OID prime256v1> | a1 44 03 42 00 04 <X> <Y>
    $der = [byte[]](0x30, 0x77, 0x02, 0x01, 0x01, 0x04, 0x20) + $P.D +
        [byte[]](0xa0, 0x0a, 0x06, 0x08, 0x2a, 0x86, 0x48, 0xce, 0x3d, 0x03, 0x01, 0x07, 0xa1, 0x44, 0x03, 0x42, 0x00, 0x04) +
        $P.Q.X + $P.Q.Y
    return , [byte[]]$der
}

function Read-ProxyFile([string]$Path, [bool]$Running) {
    # 从 Caddy 数据卷读取文件：proxy 运行中用 exec，否则用一次性容器（使用本地镜像，断网也可用）
    if ($Running) { $out = & docker compose exec -T proxy cat $Path 2>$null }
    else { $out = & docker compose run --rm --no-deps -T --entrypoint cat proxy $Path 2>$null }
    if ($LASTEXITCODE -ne 0 -or -not $out) { throw "无法读取 proxy 容器中的 $Path" }
    return ($out -join "`n")
}

function New-SiteCertificate([string[]]$IpList, [string]$RootCertPem, [string]$RootKeyPem) {
    $X509 = 'System.Security.Cryptography.X509Certificates'
    $rootCert = New-Object "$X509.X509Certificate2" (, (ConvertFrom-Pem $RootCertPem 'CERTIFICATE'))
    $rootPub = [System.Security.Cryptography.X509Certificates.ECDsaCertificateExtensions]::GetECDsaPublicKey($rootCert)
    if (-not $rootPub) { throw '根证书不是 ECDSA 证书' }
    $rp = $rootPub.ExportParameters($false)
    $rp.D = Get-Sec1PrivateScalar (ConvertFrom-Pem $RootKeyPem 'EC PRIVATE KEY')
    $rootKey = [System.Security.Cryptography.ECDsa]::Create()
    $rootKey.ImportParameters($rp)

    $leafKey = [System.Security.Cryptography.ECDsa]::Create([System.Security.Cryptography.ECCurve+NamedCurves]::nistP256)
    $req = New-Object "$X509.CertificateRequest" ('CN=InvoiceOps local', $leafKey, [System.Security.Cryptography.HashAlgorithmName]::SHA256)
    $san = New-Object "$X509.SubjectAlternativeNameBuilder"
    $san.AddDnsName('localhost')
    foreach ($ip in $IpList) { $san.AddIpAddress([System.Net.IPAddress]::Parse($ip)) }
    $req.CertificateExtensions.Add($san.Build($false))
    $req.CertificateExtensions.Add((New-Object "$X509.X509BasicConstraintsExtension" ($false, $false, 0, $true)))
    $req.CertificateExtensions.Add((New-Object "$X509.X509KeyUsageExtension" ([System.Security.Cryptography.X509Certificates.X509KeyUsageFlags]::DigitalSignature, $true)))
    $eku = New-Object System.Security.Cryptography.OidCollection
    [void]$eku.Add((New-Object System.Security.Cryptography.Oid '1.3.6.1.5.5.7.3.1'))
    $req.CertificateExtensions.Add((New-Object "$X509.X509EnhancedKeyUsageExtension" ($eku, $false)))
    $req.CertificateExtensions.Add((New-Object "$X509.X509SubjectKeyIdentifierExtension" ($req.PublicKey, $false)))
    $ski = $rootCert.Extensions | Where-Object { $_.Oid.Value -eq '2.5.29.14' } | Select-Object -First 1
    if ($ski) {
        $hex = $ski.SubjectKeyIdentifier
        $kid = [byte[]](0..($hex.Length / 2 - 1) | ForEach-Object { [Convert]::ToByte($hex.Substring($_ * 2, 2), 16) })
        $aki = [byte[]](0x30, ($kid.Length + 2), 0x80, $kid.Length) + $kid
        $req.CertificateExtensions.Add((New-Object "$X509.X509Extension" ('2.5.29.35', [byte[]]$aki, $false)))
    }

    $serial = New-Object byte[] 16
    [System.Security.Cryptography.RandomNumberGenerator]::Create().GetBytes($serial)
    $serial[0] = $serial[0] -band 0x7f
    $notBefore = [DateTimeOffset]::UtcNow.AddHours(-1)
    $notAfter = [DateTimeOffset]::UtcNow.AddDays(365)
    $rootEnd = [DateTimeOffset]$rootCert.NotAfter.ToUniversalTime()
    if ($notAfter -gt $rootEnd) { $notAfter = $rootEnd.AddDays(-1) }
    $gen = [System.Security.Cryptography.X509Certificates.X509SignatureGenerator]::CreateForECDsa($rootKey)
    $leaf = $req.Create($rootCert.SubjectName, $gen, $notBefore, $notAfter, $serial)

    $pem = (ConvertTo-Pem $leaf.RawData 'CERTIFICATE') + (ConvertTo-Pem (ConvertTo-Sec1Der $leafKey.ExportParameters($true)) 'EC PRIVATE KEY')
    $rootKey.Dispose()
    return [pscustomobject]@{ Pem = $pem; RootThumbprint = $rootCert.Thumbprint; NotAfter = $notAfter }
}

function Get-CertMeta {
    $meta = @{}
    if ((Test-Path -LiteralPath $CertFile) -and (Test-Path -LiteralPath $CertMeta)) {
        foreach ($l in [System.IO.File]::ReadAllLines($CertMeta)) { if ($l -match '^(\w+)=(.*)$') { $meta[$Matches[1]] = $Matches[2] } }
    }
    return $meta
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
    # Queue：连续切换网络（如先拔网线再关 WLAN）时每次都会执行，最后一次反映最终状态
    $settings = New-ScheduledTaskSettingsSet -AllowStartIfOnBatteries -DontStopIfGoingOnBatteries `
        -MultipleInstances Queue -ExecutionTimeLimit (New-TimeSpan -Minutes 5)
    $principal = New-ScheduledTaskPrincipal -UserId "$env:USERDOMAIN\$env:USERNAME" -LogonType Interactive -RunLevel Limited
    Register-ScheduledTask -TaskName $TaskName -Trigger @($netTrigger, $logonTrigger) -Action $action `
        -Settings $settings -Principal $principal -Force `
        -Description 'InvoiceOps：网络变化时把前端地址更新为当前已连接网卡的 IP' | Out-Null
    Write-Host "已注册计划任务 $TaskName（登录后 1 分钟、网络连接变化后 15 秒自动运行）"
    Write-Host "运行日志：$LogFile"
    return
}

# ---------- 检测 ----------
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
$certIps = @(@('127.0.0.1') + @($nets | ForEach-Object { $_.IP }) | Select-Object -Unique)
$allowed = @(@('localhost') + $certIps)
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
$meta = Get-CertMeta
$certWanted = $certIps -join ' '
$certExpiring = $true
if ($meta['notAfter']) { $certExpiring = ([DateTimeOffset]::Parse($meta['notAfter']) -lt [DateTimeOffset]::UtcNow.AddDays(30)) }
$certNeeded = ($meta['hosts'] -ne $certWanted) -or $certExpiring
Write-Log ("  .env 现值：HOST={0}  BIND={1}  ALLOWED={2}" -f $oldHost, $oldBind, $oldAllowed)
Write-Log ("  证书现有地址：{0}" -f $(if ($meta['hosts']) { "localhost $($meta['hosts'])（有效期至 $($meta['notAfter'])）" } else { '无' }))

if ($DryRun) {
    if ($envChanged) { Write-Log "  [DryRun] 将改为 HOST=$newHost  BIND=0.0.0.0  ALLOWED=$newAllowed" }
    else { Write-Log '  [DryRun] .env 已是最新' }
    if ($certNeeded) { Write-Log "  [DryRun] 将重新签发证书：localhost $certWanted" }
    else { Write-Log '  [DryRun] 证书已是最新' }
    return
}

if ($envChanged) {
    Set-EnvValue $lines 'INVOICEOPS_HOST' $newHost
    Set-EnvValue $lines 'INVOICEOPS_BIND_ADDRESS' '0.0.0.0'
    Set-EnvValue $lines 'INVOICEOPS_ALLOWED_HOSTS' $newAllowed
    [System.IO.File]::WriteAllText($EnvFile, (($lines -join $newline) + $newline), [System.Text.UTF8Encoding]::new($false))
    Write-Log "  已更新 .env：HOST=$newHost  BIND=0.0.0.0  ALLOWED=$newAllowed"
}

# ---------- 证书与 proxy ----------
Push-Location $ProjectDir
# docker 会把提示（如孤立容器警告）写到 stderr；Windows PowerShell 在 Stop 模式下会把它当成异常
$ErrorActionPreference = 'Continue'
$certChanged = $false
try {
    $running = @(& docker compose ps --status running -q proxy 2>$null)
    $code = $LASTEXITCODE
    if ($code -ne 0) { throw "docker compose 不可用（Docker Desktop 是否已启动？）" }
    $isRunning = [bool]$running

    if ($certNeeded) {
        try {
            $rootCrt = Read-ProxyFile "$CaDir/root.crt" $isRunning
            $rootKey = Read-ProxyFile "$CaDir/root.key" $isRunning
            $cert = New-SiteCertificate $certIps $rootCrt $rootKey
            $rootKey = $null
            if (-not (Test-Path -LiteralPath $CertDir)) { New-Item -ItemType Directory -Path $CertDir -Force | Out-Null }
            $utf8 = [System.Text.UTF8Encoding]::new($false)
            [System.IO.File]::WriteAllText($CertFile, $cert.Pem, $utf8)
            [System.IO.File]::WriteAllText($CertMeta, "root=$($cert.RootThumbprint)`nhosts=$certWanted`nnotAfter=$($cert.NotAfter.ToString('o'))`n", $utf8)
            $certChanged = $true
            Write-Log "  已签发证书：localhost $certWanted（有效期至 $($cert.NotAfter.ToString('yyyy-MM-dd'))）"
        } catch {
            Write-Log "  签发证书失败，继续使用 Caddy 自动证书：$($_.Exception.Message)"
        }
    }

    if (-not $isRunning) {
        Write-Log '  proxy 未在运行（服务已停止），只更新配置，下次启动服务时生效。'
        return
    }
    # 配置有变化时 compose 重建 proxy；只有证书变化时重启 proxy 让 Caddy 重新加载证书
    $out = @(& docker compose up -d --no-build --no-deps proxy 2>&1 | ForEach-Object { "$_" })
    $code = $LASTEXITCODE
    if ($code -eq 0 -and $certChanged -and -not ($out -match 'Recreate')) {
        $out = @(& docker compose restart proxy 2>&1 | ForEach-Object { "$_" })
        $code = $LASTEXITCODE
    }
} catch {
    $code = 1
    $out = @($_.Exception.Message)
} finally { Pop-Location; $ErrorActionPreference = 'Stop' }
if ($code -ne 0) {
    Write-Log "  docker compose 执行失败：$($out -join ' ')"
    exit 1
}
$urls = ($allowed | Where-Object { $_ -ne '127.0.0.1' } | ForEach-Object { "https://${_}:$port/" }) -join '  '
Write-Log "  proxy 已就绪。可用地址：$urls"
