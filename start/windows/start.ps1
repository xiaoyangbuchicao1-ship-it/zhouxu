$ErrorActionPreference = "Stop"
Set-StrictMode -Version Latest

# Keep this script ASCII-only so Windows PowerShell 5.1 cannot misread a
# UTF-8 string literal when the console is still on code page 936.
$Utf8 = [System.Text.UTF8Encoding]::new($false)
[Console]::InputEncoding = $Utf8
[Console]::OutputEncoding = $Utf8
$OutputEncoding = $Utf8
$env:PYTHONUTF8 = "1"
$env:PYTHONIOENCODING = "utf-8"

$StartRoot = (Resolve-Path $PSScriptRoot).Path
$ProjectRoot = (Resolve-Path (Join-Path $StartRoot "..\..")).Path
$OpenSearchRoot = Join-Path $ProjectRoot "opensearch"
$OpenSearchDev = Join-Path $OpenSearchRoot "opensearch_delivery\dev"
$ComposeFile = Join-Path $OpenSearchDev "compose.yaml"
$ComposeEnv = Join-Path $OpenSearchDev ".env"
$EnsureScript = Join-Path $StartRoot "..\common\ensure_indexes.py"
$BackendEnvironment = Join-Path $ProjectRoot "backend\.venv-windows"
$BackendPython = Join-Path $BackendEnvironment "Scripts\python.exe"
$FrontendDepsScript = Join-Path $StartRoot "..\common\prepare_frontend_deps.mjs"
$PyPiIndex = if ($env:ZHOUXU_PYPI_INDEX) { $env:ZHOUXU_PYPI_INDEX } else { "https://pypi.tuna.tsinghua.edu.cn/simple" }
$StartedOpenSearch = $false

function Require-Command([string] $Name) {
    if (-not (Get-Command $Name -ErrorAction SilentlyContinue)) {
        throw "missing command: $Name"
    }
}

function Invoke-Checked([string] $File, [string[]] $Arguments) {
    & $File @Arguments
    if ($LASTEXITCODE -ne 0) {
        throw "$File failed with exit code $LASTEXITCODE"
    }
}

function Test-TcpPort([int] $Port) {
    $client = [System.Net.Sockets.TcpClient]::new()
    try {
        $task = $client.ConnectAsync("127.0.0.1", $Port)
        return $task.Wait(2000) -and $client.Connected
    } finally {
        $client.Dispose()
    }
}

function Test-BackendEnvironment {
    if (-not (Test-Path -LiteralPath $BackendPython)) { return $false }
    & $BackendPython -c "import fastapi, numpy, torch, transformers, uvicorn" *> $null
    return $LASTEXITCODE -eq 0
}

try {
    Require-Command "docker"
    Require-Command "uv"
    Require-Command "node"
    Require-Command "npm"

    if (-not (Test-Path -LiteralPath $ComposeFile)) { throw "missing Compose file: $ComposeFile" }
    if (-not (Test-Path -LiteralPath $ComposeEnv)) { throw "missing env file: $ComposeEnv" }
    if (-not (Test-Path -LiteralPath $EnsureScript)) { throw "missing startup check: $EnsureScript" }

    $env:UV_PROJECT_ENVIRONMENT = $BackendEnvironment
    if (-not (Test-BackendEnvironment)) {
        Write-Host "Installing locked Windows backend dependencies..."
        Invoke-Checked "uv" @(
            "sync", "--project", (Join-Path $ProjectRoot "backend"),
            "--extra", "rag", "--extra", "materials", "--locked",
            "--default-index", $PyPiIndex
        )
        if (-not (Test-BackendEnvironment)) { throw "backend imports failed: $BackendEnvironment" }
    }

    if (-not (Test-Path -LiteralPath $FrontendDepsScript)) { throw "missing frontend dependency helper: $FrontendDepsScript" }
    Invoke-Checked "node" @($FrontendDepsScript, "--platform", "windows", "--install")

    $canReuse = Test-TcpPort 19200
    if ($canReuse) {
        Write-Host "Checking existing OpenSearch on port 19200..."
        Invoke-Checked $BackendPython @("-B", $EnsureScript, "--project-root", $ProjectRoot, "--check-only", "--timeout", "15")
    } else {
        Write-Host "Starting project OpenSearch..."
        Invoke-Checked "docker" @("compose", "--project-directory", $OpenSearchDev, "--env-file", $ComposeEnv, "-f", $ComposeFile, "up", "-d")
        $StartedOpenSearch = $true
        Invoke-Checked $BackendPython @("-B", $EnsureScript, "--project-root", $ProjectRoot)
    }

    if ((Test-TcpPort 8010) -and (Test-TcpPort 4173)) {
        Write-Host "Frontend and backend are already running."
        return
    }

    Write-Host "Starting backend and frontend. Press Ctrl+C to stop this session."
    $env:ZHOUXU_BACKEND_PYTHON = $BackendPython
    Push-Location (Join-Path $ProjectRoot "frontend")
    try {
        Invoke-Checked "npm" @("run", "dev:all")
    } finally {
        Pop-Location
    }
} finally {
    if ($StartedOpenSearch) {
        Write-Host "Stopping OpenSearch started by this session; data is kept."
        & docker compose --project-directory $OpenSearchDev --env-file $ComposeEnv -f $ComposeFile down
    }
}
