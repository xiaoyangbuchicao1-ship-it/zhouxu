$ErrorActionPreference = "Stop"
Set-StrictMode -Version Latest

$StartRoot = (Resolve-Path $PSScriptRoot).Path
$ProjectRoot = (Resolve-Path (Join-Path $StartRoot "..\..")).Path
$OpenSearchRoot = Join-Path $ProjectRoot "opensearch"
$OpenSearchDev = Join-Path $OpenSearchRoot "opensearch_delivery\dev"
$ComposeFile = Join-Path $OpenSearchRoot "opensearch_delivery\dev\compose.yaml"
$ComposeEnv = Join-Path $OpenSearchRoot "opensearch_delivery\dev\.env"

foreach ($port in @(8010, 4173)) {
    $connections = @(Get-NetTCPConnection -LocalAddress 127.0.0.1 -LocalPort $port -State Listen -ErrorAction SilentlyContinue)
    foreach ($connection in $connections) {
        $process = Get-CimInstance Win32_Process -Filter "ProcessId = $($connection.OwningProcess)"
        if ($process.CommandLine -and $process.CommandLine.Contains($ProjectRoot)) {
            Stop-Process -Id $connection.OwningProcess -Force
        }
    }
}

if (Get-Command docker -ErrorAction SilentlyContinue) {
    & docker compose --project-directory $OpenSearchDev --env-file $ComposeEnv -f $ComposeFile down
    if ($LASTEXITCODE -ne 0) { throw "docker compose stop failed: $LASTEXITCODE" }
}
Write-Host "Local Windows services stopped. Data directories were kept."
