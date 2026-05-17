<#
.SYNOPSIS
    Start the LLM Gateway V2 on Windows (port 8100) for the Session 5 tool.

.DESCRIPTION
    One-shot helper. Idempotent: creates a Windows venv on first run, installs
    requirements, sanity-checks the .env, then starts main.py in the foreground.

    Run from anywhere — paths are resolved relative to this script.

.EXAMPLE
    .\run_gateway.ps1
    .\run_gateway.ps1 -Reinstall          # nuke the venv and start fresh
    .\run_gateway.ps1 -Port 8200          # override gateway port
#>

[CmdletBinding()]
param(
    [int]    $Port      = 8100,
    [switch] $Reinstall
)

$ErrorActionPreference = "Stop"

# ── Resolve paths relative to this script ──────────────────────────────────
$ScriptDir   = Split-Path -Parent $MyInvocation.MyCommand.Path
$SessionRoot = Resolve-Path (Join-Path $ScriptDir "..")
$GatewayDir  = Join-Path $SessionRoot "llm_gatewayV2"
$VenvDir     = Join-Path $GatewayDir ".venv-win"
$VenvPython  = Join-Path $VenvDir "Scripts\python.exe"
$EnvFile     = Join-Path $SessionRoot ".env"
$MainPy      = Join-Path $GatewayDir "main.py"
$ReqsFile    = Join-Path $GatewayDir "requirements.txt"

Write-Host "═══════════════════════════════════════════════════════════════"
Write-Host " LLM Gateway V2 — local launcher"
Write-Host "═══════════════════════════════════════════════════════════════"
Write-Host "  gateway dir : $GatewayDir"
Write-Host "  venv        : $VenvDir"
Write-Host "  .env        : $EnvFile"
Write-Host "  port        : $Port"
Write-Host ""

# ── Sanity checks ──────────────────────────────────────────────────────────
if (-not (Test-Path $MainPy)) {
    throw "Gateway main.py not found at $MainPy — is the llm_gatewayV2 folder in place?"
}

if (-not (Test-Path $EnvFile)) {
    Write-Warning ".env not found at $EnvFile"
    Write-Host "  The gateway needs at least one provider key (e.g. GROQ_API_KEY=...)."
    Write-Host "  Create the file and re-run, or press Ctrl+C to abort."
    Write-Host ""
    $reply = Read-Host "Continue anyway? (y/N)"
    if ($reply -notmatch '^[Yy]$') { return }
}
else {
    $hasKey = Select-String -Path $EnvFile -Pattern '^(GROQ|GEMINI|NVIDIA|CEREBRAS|OPEN_ROUTER|GITHUB_ACCESS_TOKEN|OLLAMA_MODEL)' -Quiet
    if (-not $hasKey) {
        Write-Warning ".env exists but no recognised provider key was found."
        Write-Host "  Expected one of: GROQ_API_KEY, GEMINI_API_KEY, NVIDIA_API_KEY,"
        Write-Host "  CEREBRAS_API_KEY, OPEN_ROUTER_API_KEY, GITHUB_ACCESS_TOKEN, OLLAMA_MODEL."
    }
}

# ── Venv setup ─────────────────────────────────────────────────────────────
if ($Reinstall -and (Test-Path $VenvDir)) {
    Write-Host "[setup] -Reinstall set; removing existing venv ..."
    Remove-Item -Recurse -Force $VenvDir
}

if (-not (Test-Path $VenvPython)) {
    Write-Host "[setup] Creating Windows venv at $VenvDir ..."
    python -m venv $VenvDir
    if (-not (Test-Path $VenvPython)) {
        throw "venv creation failed — is 'python' on PATH and >= 3.10?"
    }

    Write-Host "[setup] Upgrading pip ..."
    & $VenvPython -m pip install --upgrade pip --quiet

    Write-Host "[setup] Installing requirements ..."
    & $VenvPython -m pip install -r $ReqsFile --quiet
}
else {
    Write-Host "[setup] Reusing existing venv. (Use -Reinstall to rebuild.)"
}

# ── Launch ─────────────────────────────────────────────────────────────────
$env:GATEWAY_V2_PORT = "$Port"

Write-Host ""
Write-Host "[run] Starting gateway on http://localhost:$Port  (Ctrl+C to stop)"
Write-Host "      In another shell, verify with:"
Write-Host "        curl http://localhost:$Port/v1/capabilities"
Write-Host "      Then run the tool:"
Write-Host "        python llm_recommender.py `"<your problem statement>`""
Write-Host ""

Push-Location $GatewayDir
try {
    & $VenvPython main.py
}
finally {
    Pop-Location
}
