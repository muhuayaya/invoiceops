param(
    [ValidateRange(1, 120)]
    [int]$DurationSeconds = 30,
    [ValidateRange(0, 30)]
    [int]$WarmupSeconds = 3,
    [string]$Concurrency = "1,2,4,8,16",
    [string]$ModelPath = "ml/artifacts/xlmr-v2"
)

$ErrorActionPreference = "Stop"
$projectRoot = Split-Path -Parent $MyInvocation.MyCommand.Path
Push-Location $projectRoot
$runId = [Guid]::NewGuid().ToString("N")
$composeProject = "invoiceops-capacity-$runId"
$imageName = "invoiceops-capacity-api:$runId"
$envPath = Join-Path $env:TEMP "invoiceops-capacity-$runId.env"
$composeArgs = @()
$composeStarted = $false
$downClean = $false
$primaryError = $null
$cleanupErrors = [System.Collections.Generic.List[string]]::new()

try {
    $productionContainers = @(docker ps --filter "label=com.docker.compose.project=invoiceops" --format "{{.Names}}")
    if ($LASTEXITCODE -ne 0) {
        throw "Could not inspect the active InvoiceOps Compose stack."
    }
    if ($productionContainers.Count -gt 0) {
        throw "Refusing to benchmark while InvoiceOps containers are running: $($productionContainers -join ', '). The runner will not stop or alter them."
    }
    $otherBenchmarkContainers = @(docker ps --filter "name=invoiceops-capacity-" --format "{{.Names}}")
    if ($LASTEXITCODE -ne 0) {
        throw "Could not inspect for another active capacity benchmark."
    }
    if ($otherBenchmarkContainers.Count -gt 0) {
        throw "Refusing to overlap an active capacity benchmark: $($otherBenchmarkContainers -join ', ')."
    }

    $artifactsRoot = (Resolve-Path -LiteralPath "ml/artifacts").Path.TrimEnd("\") + "\"
    $resolvedModel = (Resolve-Path -LiteralPath $ModelPath).Path.TrimEnd("\")
    if (-not $resolvedModel.StartsWith($artifactsRoot, [StringComparison]::OrdinalIgnoreCase)) {
        throw "ModelPath must point to a candidate under ml/artifacts."
    }
    if (Test-Path -LiteralPath (Join-Path $resolvedModel "approval.json")) {
        throw "This benchmark requires an unapproved candidate without approval.json."
    }
    $manifest = Get-Content -LiteralPath (Join-Path $resolvedModel "manifest.json") -Raw -Encoding utf8 | ConvertFrom-Json
    if ($manifest.model_type -ne "xlmr") {
        throw "The Compose API capacity benchmark supports the XLM-R primary candidate only."
    }

    $modelPathForDocker = $resolvedModel.Replace("\", "/")
    $dbPassword = "bench-$runId"
    $envContent = @(
        "INVOICEOPS_CAPACITY_DB_PASSWORD=$dbPassword",
        "INVOICEOPS_CAPACITY_IMAGE=$imageName",
        "INVOICEOPS_CAPACITY_MODEL_PATH=$modelPathForDocker",
        "INVOICEOPS_CAPACITY_RUN_ID=$runId"
    ) -join "`n"
    [System.IO.File]::WriteAllText($envPath, $envContent + "`n", [System.Text.UTF8Encoding]::new($false))

    $composeArgs = @("-p", $composeProject, "-f", "compose.capacity.yaml", "--env-file", $envPath)
    & docker compose @composeArgs config --quiet
    if ($LASTEXITCODE -ne 0) {
        throw "Capacity Compose configuration is invalid."
    }

    $composeStarted = $true
    $proxyNames = @("HTTP_PROXY", "HTTPS_PROXY")
    $originalProxyValues = @{}
    foreach ($proxyName in $proxyNames) {
        $originalProxyValues[$proxyName] = [Environment]::GetEnvironmentVariable($proxyName, "Process")
    }
    try {
        foreach ($proxyName in $proxyNames) {
            $proxyValue = $originalProxyValues[$proxyName]
            $proxyUri = $null
            if ($proxyValue -and [Uri]::TryCreate($proxyValue, [UriKind]::Absolute, [ref]$proxyUri) -and $proxyUri.IsLoopback) {
                $proxyBuilder = [UriBuilder]::new($proxyUri)
                $proxyBuilder.Host = "host.docker.internal"
                [Environment]::SetEnvironmentVariable($proxyName, $proxyBuilder.Uri.AbsoluteUri, "Process")
            }
        }
        & docker compose @composeArgs up --build --detach
        if ($LASTEXITCODE -ne 0) {
            throw "Isolated capacity Compose services failed to start."
        }
    }
    finally {
        foreach ($proxyName in $proxyNames) {
            [Environment]::SetEnvironmentVariable($proxyName, $originalProxyValues[$proxyName], "Process")
        }
    }

    $containerIds = @{}
    foreach ($service in @("api", "postgres", "redis")) {
        $containerId = (& docker compose @composeArgs ps -q $service).Trim()
        if ($LASTEXITCODE -ne 0 -or -not $containerId) {
            throw "Could not determine the isolated $service container id."
        }
        $containerIds[$service] = $containerId
    }

    $portBinding = & docker compose @composeArgs port api 8000
    if ($LASTEXITCODE -ne 0 -or -not $portBinding) {
        throw "Could not determine the isolated API loopback port."
    }
    $binding = $portBinding.Trim()
    if (-not $binding.StartsWith("127.0.0.1:")) {
        throw "The benchmark API port is not bound exclusively to 127.0.0.1: $binding"
    }
    $hostPort = [int](($binding -split ":")[-1])
    $baseUrl = "http://127.0.0.1:$hostPort"

    $apiReady = $false
    for ($attempt = 0; $attempt -lt 240; $attempt++) {
        try {
            $ready = Invoke-RestMethod -Uri "$baseUrl/readyz" -TimeoutSec 2
            if ($ready.model_version -eq $manifest.version) {
                $apiReady = $true
                break
            }
        }
        catch {
            Start-Sleep -Seconds 1
        }
    }
    if (-not $apiReady) {
        throw "Isolated Compose API did not become ready with the pinned candidate within 240 seconds."
    }

    $reportVersion = $manifest.version -replace "[^A-Za-z0-9._-]", "_"
    $outputPath = "ml/benchmarks/$reportVersion-api-compose-$runId.json"
    & uv run --locked python -m ml.capacity_loadgen `
        --base-url $baseUrl `
        --concurrency $Concurrency `
        --warmup-seconds $WarmupSeconds `
        --duration-seconds $DurationSeconds `
        --api-container $containerIds.api `
        --postgres-container $containerIds.postgres `
        --redis-container $containerIds.redis `
        --benchmark-id $runId `
        --manifest (Join-Path $resolvedModel "manifest.json") `
        --output $outputPath
    if ($LASTEXITCODE -ne 0) {
        throw "Compose capacity load generation failed."
    }
    Write-Output "Compose API capacity characterization saved to $outputPath"
}
catch {
    $primaryError = $_.Exception.Message
}
finally {
    if ($composeStarted) {
        try {
            & docker compose @composeArgs down --volumes --remove-orphans
            $downExitCode = $LASTEXITCODE
            $remainingContainers = @(& docker compose @composeArgs ps -aq)
            $containersQueryExitCode = $LASTEXITCODE
            if ($downExitCode -ne 0) {
                $cleanupErrors.Add("docker compose down failed with exit code $downExitCode")
            }
            if ($containersQueryExitCode -ne 0) {
                $cleanupErrors.Add("could not verify remaining benchmark containers (exit code $containersQueryExitCode)")
            }
            elseif ($remainingContainers.Count -gt 0) {
                $cleanupErrors.Add("benchmark containers remain: $($remainingContainers -join ', ')")
            }
            if ($downExitCode -eq 0 -and $containersQueryExitCode -eq 0 -and $remainingContainers.Count -eq 0) {
                $downClean = $true
            }
            $remainingNetworks = @(docker network ls -q --filter "label=com.docker.compose.project=$composeProject")
            $networksQueryExitCode = $LASTEXITCODE
            $remainingVolumes = @(docker volume ls -q --filter "label=com.docker.compose.project=$composeProject")
            $volumesQueryExitCode = $LASTEXITCODE
            if ($networksQueryExitCode -ne 0) {
                $cleanupErrors.Add("could not verify remaining benchmark networks (exit code $networksQueryExitCode)")
                $downClean = $false
            }
            if ($remainingNetworks.Count -gt 0) {
                $cleanupErrors.Add("benchmark networks remain: $($remainingNetworks -join ', ')")
                $downClean = $false
            }
            if ($volumesQueryExitCode -ne 0) {
                $cleanupErrors.Add("could not verify remaining benchmark volumes (exit code $volumesQueryExitCode)")
                $downClean = $false
            }
            if ($remainingVolumes.Count -gt 0) {
                $cleanupErrors.Add("benchmark volumes remain: $($remainingVolumes -join ', ')")
                $downClean = $false
            }
        }
        catch {
            $cleanupErrors.Add("Compose cleanup failed: $($_.Exception.Message)")
        }
    }

    if ((Test-Path -LiteralPath $envPath) -and (-not $composeStarted -or $downClean)) {
        try {
            Remove-Item -LiteralPath $envPath -Force
        }
        catch {
            $cleanupErrors.Add("temporary env file cleanup failed: $envPath")
        }
    }
    elseif (Test-Path -LiteralPath $envPath) {
        $cleanupErrors.Add("preserved temporary env file for cleanup retry: $envPath")
    }

    if ($composeStarted -and $downClean) {
        try {
            $remainingImage = @(docker image ls -q --filter "reference=$imageName")
            $imageQueryExitCode = $LASTEXITCODE
            if ($imageQueryExitCode -ne 0) {
                $cleanupErrors.Add("could not verify temporary benchmark image (exit code $imageQueryExitCode)")
            }
            elseif ($remainingImage.Count -gt 0) {
                $imageUsers = @(docker ps -a --filter "ancestor=$imageName" --format "{{.Names}}")
                $imageUsersQueryExitCode = $LASTEXITCODE
                if ($imageUsersQueryExitCode -ne 0) {
                    $cleanupErrors.Add("could not verify temporary benchmark image users (exit code $imageUsersQueryExitCode)")
                }
                elseif ($imageUsers.Count -gt 0) {
                    $cleanupErrors.Add("benchmark image is still used by containers: $($imageUsers -join ', ')")
                }
                else {
                    docker image rm $imageName *> $null
                    $imageRemoveExitCode = $LASTEXITCODE
                    if ($imageRemoveExitCode -ne 0) {
                        $cleanupErrors.Add("temporary benchmark image removal failed: $imageName")
                    }
                    else {
                        $remainingImage = @(docker image ls -q --filter "reference=$imageName")
                        $imageQueryExitCode = $LASTEXITCODE
                        if ($imageQueryExitCode -ne 0) {
                            $cleanupErrors.Add("could not verify temporary benchmark image removal (exit code $imageQueryExitCode)")
                        }
                        elseif ($remainingImage.Count -gt 0) {
                            $cleanupErrors.Add("temporary benchmark image tag remains: $imageName")
                        }
                    }
                }
            }
        }
        catch {
            $cleanupErrors.Add("image cleanup failed: $($_.Exception.Message)")
        }
    }

    try {
        Pop-Location
    }
    catch {
        $cleanupErrors.Add("could not restore the original working directory")
    }
}

if ($cleanupErrors.Count -gt 0) {
    $cleanupMessage = "Benchmark run $runId ($composeProject) cleanup failed: $($cleanupErrors -join '; ')"
    if ($primaryError) {
        throw "$primaryError; additionally, $cleanupMessage"
    }
    throw $cleanupMessage
}
if ($primaryError) {
    throw "Benchmark run $runId failed: $primaryError"
}
