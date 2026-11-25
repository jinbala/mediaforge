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
    Write-Output 'MediaForge 17zwd login helper is running.'
    exit
}

$helper = Join-Path $PSScriptRoot 'zwd-login'
if (-not (Test-Path (Join-Path $helper 'node_modules/playwright-core/package.json'))) {
    & npm.cmd ci --prefix $helper --no-audit --no-fund
    if ($LASTEXITCODE -ne 0) { throw 'Unable to install the 17zwd login helper.' }
}

$logDirectory = Join-Path $env:LOCALAPPDATA 'MediaForge'
New-Item -Path $logDirectory -ItemType Directory -Force | Out-Null
$process = Start-Process -FilePath (Get-Command node.exe).Source -ArgumentList 'server.mjs' `
    -WorkingDirectory $helper -WindowStyle Hidden -PassThru `
    -RedirectStandardOutput (Join-Path $logDirectory '17zwd-login.log') `
    -RedirectStandardError (Join-Path $logDirectory '17zwd-login-error.log')

for ($attempt = 0; $attempt -lt 20; $attempt++) {
    if (Test-LoginHelper) {
        Write-Output 'MediaForge 17zwd login helper is running.'
        exit
    }
    if ($process.HasExited) { break }
    Start-Sleep -Milliseconds 250
}

throw 'Unable to start the 17zwd login helper. See 17zwd-login-error.log in LocalAppData\MediaForge.'
