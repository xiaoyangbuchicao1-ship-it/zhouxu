$ErrorActionPreference = "Stop"
Set-StrictMode -Version Latest

# ASCII-only: this file must parse in Windows PowerShell 5.1 on code page 936.
$StartRoot = (Resolve-Path $PSScriptRoot).Path
$ProjectRoot = (Resolve-Path (Join-Path $StartRoot "..\..")).Path
$ComposeFile = Join-Path $ProjectRoot "deploy\app\compose.yaml"
$ComposeEnv = Join-Path $ProjectRoot "deploy\app\.env"

function Invoke-Compose([string[]] $Arguments) {
    & docker compose --env-file $ComposeEnv -f $ComposeFile @Arguments
    if ($LASTEXITCODE -ne 0) {
        throw "docker compose failed with exit code $LASTEXITCODE"
    }
}

if (-not (Get-Command docker -ErrorAction SilentlyContinue)) {
    throw "docker is required; install Docker Desktop and start it first"
}
if (-not (Test-Path -LiteralPath $ComposeFile)) {
    throw "missing Compose file: $ComposeFile"
}
if (-not (Test-Path -LiteralPath $ComposeEnv)) {
    throw "missing env file: $ComposeEnv; copy .env.example to .env and set secrets"
}

Invoke-Compose @("config", "--quiet")
Invoke-Compose @("up", "-d", "--build")
Invoke-Compose @("ps", "-a")
