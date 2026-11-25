$ErrorActionPreference = 'Stop'
$health = 'http://127.0.0.1:8317/health'

function Test-LoginHelper {
    try {
        $response = Invoke-RestMethod -Uri $health -TimeoutSec 2
        return $response.service -eq 'mediaforge-zwd-login'
    } catch {
        return $false
    }
}

if (Test-LoginHelper) {
    Write-Output 'MediaForge product browser helper is running.'
    exit
}

$frontend = Join-Path (Split-Path -Parent $PSScriptRoot) 'frontend'
if (-not (Test-Path (Join-Path $frontend 'node_modules/playwright-core/package.json')) -or
    -not (Test-Path (Join-Path $frontend 'node_modules/typescript/package.json'))) {
    & npm.cmd ci --prefix $frontend --include=dev --no-audit --no-fund
    if ($LASTEXITCODE -ne 0) { throw 'Unable to install the MediaForge frontend dependencies.' }
}
& npm.cmd --prefix $frontend run build:product-browser
if ($LASTEXITCODE -ne 0) { throw 'Unable to compile the MediaForge product browser helper.' }

$logDirectory = Join-Path $env:LOCALAPPDATA 'MediaForge'
New-Item -Path $logDirectory -ItemType Directory -Force | Out-Null
$process = Start-Process -FilePath (Get-Command node.exe).Source -ArgumentList 'build/product-browser/server.cjs' `
    -WorkingDirectory $frontend -WindowStyle Hidden -PassThru `
    -RedirectStandardOutput (Join-Path $logDirectory '17zwd-login.log') `
    -RedirectStandardError (Join-Path $logDirectory '17zwd-login-error.log')

for ($attempt = 0; $attempt -lt 20; $attempt++) {
    if (Test-LoginHelper) {
        Write-Output 'MediaForge product browser helper is running.'
        exit
    }
    if ($process.HasExited) { break }
    Start-Sleep -Milliseconds 250
}

throw 'Unable to start the MediaForge product browser helper. See 17zwd-login-error.log in LocalAppData\MediaForge.'
