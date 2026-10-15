$ErrorActionPreference = "Stop"

$ProjectRoot = Split-Path -Parent $PSScriptRoot
$SearchRoot = Join-Path $ProjectRoot "infra\searxng"
$SecretFile = Join-Path $SearchRoot ".env.local"
$Port = if ($env:ZHOUXU_SEARXNG_PORT) { $env:ZHOUXU_SEARXNG_PORT } else { "18080" }
$Endpoint = "http://127.0.0.1:$Port"

try {
    docker info *> $null
} catch {
    throw "Docker Desktop 尚未启动。启动后重新运行 scripts\start-search.ps1。"
}

if (-not (Test-Path -LiteralPath $SecretFile)) {
    $Bytes = New-Object byte[] 48
    [System.Security.Cryptography.RandomNumberGenerator]::Fill($Bytes)
    $Secret = [Convert]::ToHexString($Bytes).ToLowerInvariant()
    [System.IO.File]::WriteAllText($SecretFile, "SEARXNG_SECRET=$Secret`n")
}

$env:SEARXNG_PORT = $Port
docker compose --env-file $SecretFile -f (Join-Path $SearchRoot "compose.yml") up -d

$Ready = $false
for ($Attempt = 1; $Attempt -le 30; $Attempt++) {
    try {
        $Config = Invoke-RestMethod -Uri "$Endpoint/config" -TimeoutSec 2
        if ($null -ne $Config) {
            $Ready = $true
            break
        }
    } catch {
        Start-Sleep -Seconds 2
    }
}

if (-not $Ready) {
    throw "SearXNG 容器已启动，但 60 秒内未通过 JSON 接口检查。请运行 docker compose logs searxng。"
}

Write-Output "SearXNG 已就绪：$Endpoint"
Write-Output "启动后端前设置：`$env:ZHOUXU_SEARCH_ENDPOINT='$Endpoint'"

