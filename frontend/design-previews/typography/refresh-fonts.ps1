$ErrorActionPreference = 'Stop'
$previewRoot = $PSScriptRoot
$fontRoot = Join-Path $previewRoot 'fonts'
New-Item -ItemType Directory -Path $fontRoot -Force | Out-Null
$source = Get-Content -LiteralPath (Join-Path $previewRoot 'index.html') -Raw -Encoding utf8
$characters = -join ($source.ToCharArray() | Sort-Object -Unique -CaseSensitive)
$subsetText = [Uri]::EscapeDataString($characters)
$fontJobs = @(
    @{ Family = 'Instrument+Serif:ital@1'; File = 'instrument-serif-italic.ttf'; License = 'Instrument-Serif-OFL.txt'; Directory = 'instrumentserif' },
    @{ Family = 'Noto+Serif+SC:wght@500'; File = 'noto-serif-sc.ttf'; License = 'Noto-Serif-SC-OFL.txt'; Directory = 'notoserifsc' },
    @{ Family = 'Noto+Sans+SC:wght@400'; File = 'noto-sans-sc.ttf'; License = 'Noto-Sans-SC-OFL.txt'; Directory = 'notosanssc' }
)
$fontJobs | ForEach-Object -Parallel {
    $job = $_
    $cssUrl = 'https://fonts.googleapis.com/css2?family=' + $job.Family + '&display=swap&text=' + $using:subsetText
    $css = (Invoke-WebRequest -Uri $cssUrl -TimeoutSec 35 -MaximumRetryCount 1).Content
    $match = [regex]::Match($css, 'src:\s*url\((https://fonts\.gstatic\.com/[^)]+)\)\s*format\(''truetype''\)')
    if (-not $match.Success) { throw ('No TrueType subset returned for ' + $job.File) }
    Invoke-WebRequest -Uri $match.Groups[1].Value -OutFile (Join-Path $using:fontRoot $job.File) -TimeoutSec 35 -MaximumRetryCount 1
    $licenseUrl = 'https://raw.githubusercontent.com/google/fonts/main/ofl/' + $job.Directory + '/OFL.txt'
    try {
        Invoke-WebRequest -Uri $licenseUrl -OutFile (Join-Path $using:fontRoot $job.License) -TimeoutSec 15
    } catch {
        $licenseMirror = 'https://cdn.jsdelivr.net/gh/google/fonts@main/ofl/' + $job.Directory + '/OFL.txt'
        Invoke-WebRequest -Uri $licenseMirror -OutFile (Join-Path $using:fontRoot $job.License) -TimeoutSec 25
    }
    Get-Item -LiteralPath (Join-Path $using:fontRoot $job.File) | Select-Object Name,Length
} -ThrottleLimit 3
