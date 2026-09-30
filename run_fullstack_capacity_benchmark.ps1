param(
    [string[]]$ModelPaths = @("ml/artifacts/xlmr-v2", "ml/artifacts/tfidf-v2"),
    [string]$GatePath = "ml/gates/engineering-capacity-v1.json",
    [ValidateRange(1, 64)]
    [int]$BatchConcurrency = 8,
    [ValidateRange(1, 1000)]
    [int]$BatchRows = 100,
    [ValidateRange(30, 7200)]
    [int]$BatchTimeoutSeconds = 1800,
    [ValidateRange(1024, 65535)]
    [int]$HttpsPort = 0
)

$ErrorActionPreference = "Stop"
$projectRoot = Split-Path -Parent $MyInvocation.MyCommand.Path
Push-Location $projectRoot
$allResults = [System.Collections.Generic.List[object]]::new()
$runErrors = [System.Collections.Generic.List[string]]::new()
$gateFailures = [System.Collections.Generic.List[string]]::new()
$originalProxyValues = @{}
$proxyEnvironmentCaptured = $false
$resolvedGatePath = (Resolve-Path -LiteralPath $GatePath).Path

function Get-ContainerDetails {
    param([Parameter(Mandatory)][string]$ContainerId)
    $json = & docker inspect --format '{{json .}}' $ContainerId
    if ($LASTEXITCODE -ne 0 -or -not $json) { throw "Could not inspect Docker container $ContainerId." }
    return ($json | ConvertFrom-Json)
}

try {
    $allProjectContainers = @(docker ps -a --filter "label=com.docker.compose.project=invoiceops" --format "{{.ID}}")
    if ($LASTEXITCODE -ne 0) { throw "Could not inspect the InvoiceOps Compose project." }

    $productionInfo = @{}
    $productionContainers = [System.Collections.Generic.List[string]]::new()
    foreach ($containerId in $allProjectContainers) {
        $details = Get-ContainerDetails -ContainerId $containerId
        $labels = $details.Config.Labels
        $workingDirLabel = $labels.PSObject.Properties['com.docker.compose.project.working_dir'].Value
        if ($workingDirLabel -ne $projectRoot) {
            if ($details.State.Status -eq "running") {
                throw "A running container with the invoiceops project label belongs to another directory: $($details.Name)."
            }
            continue
        }
        $service = $labels.PSObject.Properties['com.docker.compose.service'].Value
        if ($service -notin @("proxy", "web", "api", "worker", "postgres", "redis")) {
            throw "Unexpected container for this project directory: $($details.Name)."
        }
        if ($details.State.Status -ne "running") { continue }
        if ($productionInfo.ContainsKey($service)) { throw "More than one running container exists for InvoiceOps service $service." }
        $productionInfo[$service] = @{
            id = $details.Id
            name = $details.Name.TrimStart('/')
            status = $details.State.Status
            image_id = $details.Image
            image_reference = $details.Config.Image
        }
        $productionContainers.Add($containerId)
    }
    $expectedServices = @("proxy", "web", "api", "worker", "postgres", "redis")
    $actualServiceList = @($productionInfo.Keys | Sort-Object) -join ","
    $expectedServiceList = @($expectedServices | Sort-Object) -join ","
    if ($actualServiceList -ne $expectedServiceList) {
        throw "The InvoiceOps Compose service set differs from the six-service capacity target."
    }
    if (@($productionInfo.Values | Where-Object { $_.status -ne "running" }).Count -gt 0) {
        throw "All six InvoiceOps services must be running before the measured maintenance window."
    }
    $productionContainerIds = @($productionContainers.ToArray())
    $capacityBaseImage = $productionInfo.api.image_reference
    $resolvedBaseImageId = (& docker image inspect $capacityBaseImage --format '{{.Id}}').Trim()
    if ($LASTEXITCODE -ne 0 -or $resolvedBaseImageId -ne $productionInfo.api.image_id) {
        throw "The running API image reference does not resolve to the current local production image."
    }
    $localPyprojectHash = (Get-FileHash -LiteralPath "pyproject.toml" -Algorithm SHA256).Hash.ToLowerInvariant()
    $localUvLockHash = (Get-FileHash -LiteralPath "uv.lock" -Algorithm SHA256).Hash.ToLowerInvariant()
    $imageLockHashes = @(docker exec $productionInfo.api.id sh -c "sha256sum /app/pyproject.toml /app/uv.lock")
    if ($LASTEXITCODE -ne 0 -or $imageLockHashes.Count -lt 2) {
        throw "Could not verify dependency lock files in the local production API image."
    }
    if (($imageLockHashes[0] -split '\s+')[0].ToLowerInvariant() -ne $localPyprojectHash -or
        ($imageLockHashes[1] -split '\s+')[0].ToLowerInvariant() -ne $localUvLockHash) {
        throw "The production API image dependency locks differ from the current project; refusing to use it as the capacity base."
    }

    $runningLines = @(docker ps --format "{{.ID}}`t{{.Names}}")
    if ($LASTEXITCODE -ne 0) { throw "Could not inspect running Docker containers." }
    $otherRunning = @(
        foreach ($line in $runningLines) {
            $parts = $line -split "`t", 2
            if ($parts.Count -eq 2 -and $parts[0] -notin $productionContainerIds) { $parts[1] }
        }
    )
    if ($otherRunning.Count -gt 0) {
        throw "Unrelated Docker containers are running and would contaminate the capacity result: $($otherRunning -join ', ')."
    }
    $existingCapacity = @(docker ps -a --filter "name=invoiceops-capacity-full-" --format "{{.Names}}")
    if ($LASTEXITCODE -ne 0) { throw "Could not inspect for a previous full-stack capacity run." }
    if ($existingCapacity.Count -gt 0) { throw "A previous capacity stack still exists: $($existingCapacity -join ', '). Clean it up before starting another run." }

    $proxy = Get-ContainerDetails -ContainerId $productionInfo.proxy.id
    $tlsMount = @($proxy.Mounts | Where-Object { $_.Destination -eq "/data" -and $_.Name })
    if ($tlsMount.Count -ne 1) { throw "Could not identify the production Caddy TLS data volume." }
    $tlsVolume = $tlsMount[0].Name
    $localRootCert = (Resolve-Path -LiteralPath "infra/caddy/root.crt").Path
    $localRootHash = (Get-FileHash -LiteralPath $localRootCert -Algorithm SHA256).Hash.ToLowerInvariant()
    $containerRootHash = docker exec $productionInfo.proxy.id sh -c "sha256sum /data/caddy/pki/authorities/local/root.crt"
    if ($LASTEXITCODE -ne 0 -or ($containerRootHash -split '\s+')[0].ToLowerInvariant() -ne $localRootHash) {
        throw "The project root certificate does not match the running Caddy CA. Refusing to run TLS acceptance."
    }

    $envValues = @{}
    foreach ($line in [System.IO.File]::ReadAllLines((Resolve-Path -LiteralPath ".env").Path)) {
        if ($line -match '^([^#=]+)=(.*)$') { $envValues[$matches[1]] = $matches[2] }
    }
    $hostName = $envValues["INVOICEOPS_HOST"]
    if (-not $hostName -or $hostName -notmatch '^[A-Za-z0-9.-]+$') { throw "INVOICEOPS_HOST must be a DNS name or IPv4 address for the existing Caddy certificate." }
    if ($HttpsPort -eq 0) { $HttpsPort = [int]$envValues["INVOICEOPS_HTTPS_PORT"] }
    if (-not $HttpsPort) { throw "INVOICEOPS_HTTPS_PORT is missing." }
    $imageRunId = [Guid]::NewGuid().ToString("N")
    $imageName = "invoiceops-capacity-full:$imageRunId"
    $envRoot = $env:TEMP
    foreach ($proxyName in @("HTTP_PROXY", "HTTPS_PROXY")) {
        $originalProxyValues[$proxyName] = [Environment]::GetEnvironmentVariable($proxyName, "Process")
    }
    $proxyEnvironmentCaptured = $true

    foreach ($modelPath in $ModelPaths) {
        $runId = [Guid]::NewGuid().ToString("N")
        $composeProject = "invoiceops-capacity-full-$runId"
        $envPath = Join-Path $envRoot "invoiceops-capacity-full-$runId.env"
        $composeArgs = @("-p", $composeProject, "-f", "compose.capacity.full.yaml", "--env-file", $envPath)
        $composeStarted = $false
        $productionStopped = $false
        $productionRestored = $false
        $downClean = $false
        $candidateError = $null
        $gateExitCode = 0
        $reportPath = $null
        $resolvedModel = $null

        try {
            $artifactsRoot = (Resolve-Path -LiteralPath "ml/artifacts").Path.TrimEnd("\") + "\"
            $resolvedModel = (Resolve-Path -LiteralPath $modelPath).Path.TrimEnd("\")
            if (-not $resolvedModel.StartsWith($artifactsRoot, [StringComparison]::OrdinalIgnoreCase)) {
                throw "Candidate model path must be under ml/artifacts."
            }
            if (Test-Path -LiteralPath (Join-Path $resolvedModel "approval.json")) {
                throw "The isolated benchmark only accepts a candidate without approval.json."
            }
            $manifest = Get-Content -LiteralPath (Join-Path $resolvedModel "manifest.json") -Raw -Encoding utf8 | ConvertFrom-Json
            $candidateRole = switch ($manifest.model_type) {
                "xlmr" { "primary" }
                "tfidf" { "fallback" }
                default { throw "Unsupported capacity candidate type: $($manifest.model_type)" }
            }
            $gate = Get-Content -LiteralPath $resolvedGatePath -Raw -Encoding utf8 | ConvertFrom-Json
            if ($manifest.version -notin $gate.scope.candidate_versions) {
                throw "Candidate $($manifest.version) is not named in the frozen capacity gate."
            }

            $modelPathForDocker = $resolvedModel.Replace("\", "/")
            $primaryModelPath = if ($candidateRole -eq "primary") { "/bench-model" } else { "" }
            $fallbackModelPath = if ($candidateRole -eq "fallback") { "/bench-model" } else { "" }
            $dbPassword = "bench-$runId"
            $envContent = @(
                "INVOICEOPS_HOST=$hostName",
                "INVOICEOPS_CAPACITY_HTTPS_PORT=$HttpsPort",
                "INVOICEOPS_CAPACITY_BIND_ADDRESS=0.0.0.0",
                "INVOICEOPS_CAPACITY_DB_PASSWORD=$dbPassword",
                "INVOICEOPS_CAPACITY_IMAGE=$imageName",
                "INVOICEOPS_CAPACITY_BASE_IMAGE=$capacityBaseImage",
                "INVOICEOPS_CAPACITY_MODEL_PATH=$modelPathForDocker",
                "INVOICEOPS_CAPACITY_CANDIDATE_ROLE=$candidateRole",
                "INVOICEOPS_CAPACITY_PRIMARY_MODEL=$primaryModelPath",
                "INVOICEOPS_CAPACITY_FALLBACK_MODEL=$fallbackModelPath",
                "INVOICEOPS_CAPACITY_RUN_ID=$runId",
                "INVOICEOPS_CAPACITY_TLS_VOLUME=$tlsVolume"
            ) -join "`n"
            [System.IO.File]::WriteAllText($envPath, $envContent + "`n", [System.Text.UTF8Encoding]::new($false))

            & docker compose @composeArgs config --quiet
            if ($LASTEXITCODE -ne 0) { throw "Full-stack capacity Compose configuration is invalid." }
            foreach ($proxyName in @("HTTP_PROXY", "HTTPS_PROXY")) {
                $proxyUri = $null
                $proxyValue = $originalProxyValues[$proxyName]
                if ($proxyValue -and [Uri]::TryCreate($proxyValue, [UriKind]::Absolute, [ref]$proxyUri) -and $proxyUri.IsLoopback) {
                    $proxyBuilder = [UriBuilder]::new($proxyUri)
                    $proxyBuilder.Host = "host.docker.internal"
                    [Environment]::SetEnvironmentVariable($proxyName, $proxyBuilder.Uri.AbsoluteUri, "Process")
                }
            }
            & docker compose @composeArgs build api
            if ($LASTEXITCODE -ne 0) { throw "Could not build the isolated six-service application image." }
            foreach ($proxyName in @("HTTP_PROXY", "HTTPS_PROXY")) {
                [Environment]::SetEnvironmentVariable($proxyName, $originalProxyValues[$proxyName], "Process")
            }

            Write-Warning "Maintenance window: the original InvoiceOps stack will be stopped temporarily. Its HTTPS address will serve an isolated test deployment; use test data only."
            $productionStopped = $true
            & docker stop --time 30 @productionContainerIds | Out-Null
            if ($LASTEXITCODE -ne 0) { throw "Could not pause the original InvoiceOps Compose stack." }
            Start-Sleep -Seconds 2
            if (Get-NetTCPConnection -State Listen -LocalPort $HttpsPort -ErrorAction SilentlyContinue) {
                throw "HTTPS port $HttpsPort remains in use after pausing InvoiceOps; refusing to start a test listener."
            }
            foreach ($containerId in $productionContainerIds) {
                $stopped = Get-ContainerDetails -ContainerId $containerId
                if ($LASTEXITCODE -ne 0 -or $stopped.State.Status -ne "exited") {
                    throw "Could not verify that the original container $containerId stopped cleanly."
                }
            }

            $composeStarted = $true
            & docker compose @composeArgs up --detach
            if ($LASTEXITCODE -ne 0) { throw "Could not start the isolated six-service capacity stack." }

            $baseUrl = "https://localhost`:$HttpsPort"
            $lanBaseUrl = "https://$hostName`:$HttpsPort"
            $ready = $false
            for ($attempt = 0; $attempt -lt 240; $attempt++) {
                $status = & curl.exe --noproxy "*" --ssl-no-revoke --cacert $localRootCert --silent --output NUL --write-out "%{http_code}" "$baseUrl/readyz" 2>$null
                if ($LASTEXITCODE -eq 0 -and $status.Trim() -eq "200") { $ready = $true; break }
                Start-Sleep -Seconds 1
            }
            if (-not $ready) { throw "Isolated HTTPS API did not become ready with the pinned candidate in 240 seconds." }

            foreach ($path in @("/healthz", "/readyz", "/_stcore/health", "/")) {
                $status = & curl.exe --noproxy "*" --ssl-no-revoke --cacert $localRootCert --silent --output NUL --write-out "%{http_code}" "$baseUrl$path" 2>$null
                if ($LASTEXITCODE -ne 0 -or $status.Trim() -ne "200") { throw "HTTPS smoke check failed for $path." }
            }
            $lanHealth = & uv run --locked python -c "import httpx,sys; r=httpx.get(sys.argv[1], verify=sys.argv[2], timeout=10.0, trust_env=False); print(r.status_code)" "$lanBaseUrl/healthz" $localRootCert 2>&1
            $lanHealthExit = $LASTEXITCODE
            if ($lanHealthExit -ne 0 -or ($lanHealth -join "").Trim() -ne "200") { throw "LAN HTTPS listener smoke check failed (probe exit $($lanHealthExit)): $($lanHealth -join ' ')" }

            $containerIds = @{}
            foreach ($service in @("proxy", "web", "api", "worker", "postgres", "redis")) {
                $id = (& docker compose @composeArgs ps -q $service).Trim()
                if ($LASTEXITCODE -ne 0 -or -not $id) { throw "Could not identify isolated $service container." }
                $containerIds[$service] = $id
            }
            $reportVersion = $manifest.version -replace "[^A-Za-z0-9._-]", "_"
            $reportPath = "ml/benchmarks/$reportVersion-fullstack-capacity-$runId.json"
            & uv run --locked python -m ml.capacity_fullstack_loadgen `
                --base-url $baseUrl `
                --lan-base-url $lanBaseUrl `
                --ca-bundle $localRootCert `
                --gate $resolvedGatePath `
                --candidate-role $candidateRole `
                --manifest (Join-Path $resolvedModel "manifest.json") `
                --benchmark-id $runId `
                --proxy-container $containerIds.proxy `
                --web-container $containerIds.web `
                --api-container $containerIds.api `
                --worker-container $containerIds.worker `
                --postgres-container $containerIds.postgres `
                --redis-container $containerIds.redis `
                --batch-concurrency $BatchConcurrency `
                --batch-rows $BatchRows `
                --batch-timeout-seconds $BatchTimeoutSeconds `
                --output $reportPath
            $gateExitCode = $LASTEXITCODE
            if ($gateExitCode -notin @(0, 2)) { throw "Full-stack load generation failed with exit code $gateExitCode." }
            if (-not (Test-Path -LiteralPath $reportPath)) { throw "Load generator exited without producing its report: $reportPath" }
            if ($gateExitCode -eq 2) { $gateFailures.Add("$($manifest.version): gate failed; report $reportPath") }
            if ($gateExitCode -eq 2) {
                Write-Host "Worker logs (last 120 lines) for CSV capacity diagnostics:"
                & docker compose @composeArgs logs --no-color --tail 120 worker
            }
            $allResults.Add(@{ model_version = $manifest.version; report = $reportPath; gate_exit_code = $gateExitCode })
        }
        catch {
            $candidateError = $_.Exception.Message
            $runErrors.Add("$runId ($modelPath): $candidateError")
        }
        finally {
            foreach ($proxyName in @("HTTP_PROXY", "HTTPS_PROXY")) {
                [Environment]::SetEnvironmentVariable($proxyName, $originalProxyValues[$proxyName], "Process")
            }
            if ($composeStarted) {
                try {
                    & docker compose @composeArgs down --volumes --remove-orphans
                    $downCode = $LASTEXITCODE
                    $remaining = @(& docker compose @composeArgs ps -aq)
                    $remainingCode = $LASTEXITCODE
                    $remainingNetworks = @(& docker network ls -q --filter "label=com.docker.compose.project=$composeProject")
                    $networkCode = $LASTEXITCODE
                    $remainingVolumes = @(& docker volume ls -q --filter "label=com.docker.compose.project=$composeProject")
                    $volumeCode = $LASTEXITCODE
                    if ($downCode -eq 0 -and $remainingCode -eq 0 -and $networkCode -eq 0 -and $volumeCode -eq 0 -and $remaining.Count -eq 0 -and $remainingNetworks.Count -eq 0 -and $remainingVolumes.Count -eq 0) {
                        $downClean = $true
                    }
                    else {
                        $runErrors.Add("$runId ($composeProject): isolated stack cleanup incomplete; inspect only this project before retrying cleanup.")
                    }
                }
                catch {
                    $runErrors.Add("$runId ($composeProject): cleanup failed: $($_.Exception.Message)")
                }
            }
            if ($productionStopped) {
                $restoreErrors = [System.Collections.Generic.List[string]]::new()
                foreach ($service in @("postgres", "redis", "api", "worker", "web", "proxy")) {
                    $containerId = $productionInfo[$service].id
                    try {
                        $running = $false
                        for ($attempt = 0; $attempt -lt 90; $attempt++) {
                            $state = Get-ContainerDetails -ContainerId $containerId
                            if ($state.State.Status -eq "running") { $running = $true; break }
                            if ($state.State.Status -notin @("stopping", "restarting")) {
                                docker start $containerId | Out-Null
                                if ($LASTEXITCODE -ne 0) { throw "Could not restart original $service container." }
                            }
                            Start-Sleep -Seconds 1
                        }
                        if (-not $running) {
                            $state = Get-ContainerDetails -ContainerId $containerId
                            if ($state.State.Status -eq "running") { $running = $true }
                        }
                        if (-not $running) { throw "Original $service container did not return to running state." }
                    }
                    catch {
                        $restoreErrors.Add("$($service): $($_.Exception.Message)")
                    }
                }
                foreach ($containerId in $productionContainerIds) {
                    try {
                        $restored = Get-ContainerDetails -ContainerId $containerId
                        if (-not $restored.Id.StartsWith($containerId, [StringComparison]::OrdinalIgnoreCase) -or $restored.State.Status -ne "running") {
                            throw "The original container $containerId did not return with its original id and running state."
                        }
                    }
                    catch { $restoreErrors.Add($_.Exception.Message) }
                }
                $rootMatches = $false
                for ($attempt = 0; $attempt -lt 90; $attempt++) {
                    $restoredRoot = docker exec $productionInfo.proxy.id sh -c "sha256sum /data/caddy/pki/authorities/local/root.crt" 2>$null
                    if ($LASTEXITCODE -eq 0 -and ($restoredRoot -split ' +')[0].ToLowerInvariant() -eq $localRootHash) {
                        $rootMatches = $true
                        break
                    }
                    Start-Sleep -Seconds 1
                }
                if (-not $rootMatches) { $restoreErrors.Add("The existing Caddy root certificate fingerprint did not recover to its original value.") }
                foreach ($service in @("postgres", "redis")) {
                    $healthy = $false
                    for ($attempt = 0; $attempt -lt 90; $attempt++) {
                        try {
                            $state = Get-ContainerDetails -ContainerId $productionInfo[$service].id
                            if ($state.State.Health.Status -eq "healthy") { $healthy = $true; break }
                        }
                        catch { }
                        Start-Sleep -Seconds 1
                    }
                    if (-not $healthy) { $restoreErrors.Add("Original $service health check did not recover.") }
                }
                $healthOk = $false
                $healthUrl = "https://{0}:{1}/healthz" -f $hostName, $HttpsPort
                for ($attempt = 0; $attempt -lt 90; $attempt++) {
                    $health = & curl.exe --noproxy "*" --ssl-no-revoke --cacert $localRootCert --silent --output NUL --write-out "%{http_code}" $healthUrl 2>$null
                    if ($LASTEXITCODE -eq 0 -and $health.Trim() -eq "200") { $healthOk = $true; break }
                    Start-Sleep -Seconds 1
                }
                if (-not $healthOk) { $restoreErrors.Add("The original InvoiceOps HTTPS health check did not recover.") }
                if ($healthOk) {
                    $readyUrl = "https://{0}:{1}/readyz" -f $hostName, $HttpsPort
                    $readiness = & curl.exe --noproxy "*" --ssl-no-revoke --cacert $localRootCert --silent --show-error --write-out "__STATUS__%{http_code}" $readyUrl 2>$null
                    $readinessText = $readiness -join ""
                    $readinessSplit = $readinessText.LastIndexOf("__STATUS__")
                    $readinessBody = if ($readinessSplit -ge 0) { $readinessText.Substring(0, $readinessSplit) } else { $readinessText }
                    $readinessCode = if ($readinessSplit -ge 0) { $readinessText.Substring($readinessSplit + 10).Trim() } else { "" }
                    if ($LASTEXITCODE -ne 0 -or $readinessCode -ne "503" -or $readinessBody -notmatch "no approved model available") {
                        $restoreErrors.Add("Original API readiness did not recover to its expected no-approved-model state.")
                    }
                }
                if ($restoreErrors.Count -eq 0) {
                    $productionRestored = $true
                }
                else {
                    $runErrors.Add("$($runId): failed to restore the original InvoiceOps stack: $($restoreErrors -join '; ')")
                }
            }
            if (Test-Path -LiteralPath $envPath) {
                if (-not $composeStarted -or $downClean) {
                    Remove-Item -LiteralPath $envPath -Force
                }
                else {
                    $runErrors.Add("${runId}: preserved temporary env file for isolated-stack cleanup retry: $envPath")
                }
            }
            if ($downClean -or -not $composeStarted) {
                $taggedImage = @(docker image ls -q --filter "reference=$imageName")
                if ($LASTEXITCODE -ne 0) {
                    $runErrors.Add("$($runId): could not verify isolated image tag cleanup: $imageName")
                }
                elseif ($taggedImage.Count -gt 0) {
                    docker image rm $imageName | Out-Null
                    if ($LASTEXITCODE -ne 0) { $runErrors.Add("${runId}: isolated image tag cleanup failed: $imageName") }
                }
            }
            if ($productionStopped -and -not $productionRestored) {
                $runErrors.Add("${runId}: production InvoiceOps stack restoration needs immediate attention.")
            }
        }
        if ($runErrors.Count -gt 0) { break }
    }
}
catch {
    $runErrors.Add($_.Exception.Message)
}
finally {
    if ($proxyEnvironmentCaptured) {
        foreach ($proxyName in @("HTTP_PROXY", "HTTPS_PROXY")) {
            [Environment]::SetEnvironmentVariable($proxyName, $originalProxyValues[$proxyName], "Process")
        }
    }
    Pop-Location
}

Write-Output (ConvertTo-Json -InputObject @{ runs = $allResults; gate_failures = $gateFailures; errors = $runErrors } -Depth 5)
if ($runErrors.Count -gt 0) { exit 1 }
if ($gateFailures.Count -gt 0) { exit 2 }
