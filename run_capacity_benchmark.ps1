# Compatibility entry point for the Compose-based CPU API capacity characterization.
param(
    [ValidateRange(1, 120)]
    [int]$DurationSeconds = 30,
    [ValidateRange(0, 30)]
    [int]$WarmupSeconds = 3,
    [string]$Concurrency = "1,2,4,8,16",
    [string]$ModelPath = "ml/artifacts/xlmr-v2"
)

& (Join-Path $PSScriptRoot "run_compose_capacity_benchmark.ps1") `
    -DurationSeconds $DurationSeconds `
    -WarmupSeconds $WarmupSeconds `
    -Concurrency $Concurrency `
    -ModelPath $ModelPath
if (-not $?) {
    exit 1
}
exit 0
