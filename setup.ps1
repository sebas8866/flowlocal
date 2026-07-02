# FlowLocal setup script — idempotent, PowerShell 5.1 compatible (no &&).
#
# - Verifies py -3.11 is available
# - Creates .venv if missing
# - Installs requirements.txt (+ requirements-gpu.txt if an NVIDIA GPU is detected)
# - Smoke-tests CUDA availability via ctranslate2 (no model download)
# - Pre-downloads the default model (large-v3-turbo, ~1.6GB one-time download)
# - Enables autostart
# - Prints an Ollama install suggestion if port 11434 is closed

$ErrorActionPreference = "Stop"

$ProjectRoot = Split-Path -Parent $MyInvocation.MyCommand.Path
Set-Location $ProjectRoot

Write-Host "== FlowLocal setup ==" -ForegroundColor Cyan
Write-Host "This will download ~1.6 GB (Whisper large-v3-turbo model) and, if an NVIDIA GPU is detected, NVIDIA CUDA runtime pip wheels (a few hundred MB more)." -ForegroundColor Cyan
Write-Host ""

# --- 1. Check py -3.11 -------------------------------------------------
Write-Host "Checking for Python 3.11 launcher..."

$pyCommand = Get-Command py -ErrorAction SilentlyContinue
if (-not $pyCommand) {
    Write-Host "ERROR: the 'py' launcher was not found on PATH." -ForegroundColor Red
    Write-Host "Install Python 3.11 from python.org, then re-run this script." -ForegroundColor Red
    exit 1
}

$pyOk = $true
try {
    $pyVersion = & py -3.11 --version
    if ($LASTEXITCODE -ne 0) {
        $pyOk = $false
    }
} catch {
    $pyOk = $false
}

if (-not $pyOk) {
    Write-Host "ERROR: Python 3.11 is not available via 'py -3.11'." -ForegroundColor Red
    Write-Host "Install Python 3.11 from python.org, then re-run this script." -ForegroundColor Red
    exit 1
}
Write-Host "Found: $pyVersion"

# --- 2. Create .venv if missing -----------------------------------------
$VenvPath = Join-Path $ProjectRoot ".venv"
$VenvPython = Join-Path $VenvPath "Scripts\python.exe"

if (-not (Test-Path $VenvPython)) {
    Write-Host "Creating virtual environment at .venv ..."
    & py -3.11 -m venv $VenvPath
    if ($LASTEXITCODE -ne 0) {
        Write-Host "ERROR: venv creation failed." -ForegroundColor Red
        exit 1
    }
} else {
    Write-Host ".venv already exists, skipping creation."
}

# --- 3. Install dependencies ---------------------------------------------
Write-Host "Upgrading pip..."
& $VenvPython -m pip install --upgrade pip

Write-Host "Installing requirements.txt ..."
& $VenvPython -m pip install -r (Join-Path $ProjectRoot "requirements.txt")
if ($LASTEXITCODE -ne 0) {
    Write-Host "ERROR: pip install -r requirements.txt failed." -ForegroundColor Red
    exit 1
}

$HasNvidiaGpu = [bool](Get-Command nvidia-smi -ErrorAction SilentlyContinue)
if ($HasNvidiaGpu) {
    Write-Host "NVIDIA GPU detected. Installing CUDA runtime libraries (requirements-gpu.txt)..."
    & $VenvPython -m pip install -r (Join-Path $ProjectRoot "requirements-gpu.txt")
    if ($LASTEXITCODE -ne 0) {
        Write-Host "WARNING: NVIDIA CUDA runtime packages failed to install; GPU path may be unavailable, will fall back to CPU." -ForegroundColor Yellow
    }
} else {
    Write-Host "No NVIDIA GPU detected (nvidia-smi not found); skipping CUDA runtime install. FlowLocal will run on CPU." -ForegroundColor Yellow
}

# --- 4. GPU smoke test ---------------------------------------------------
Write-Host "Smoke-testing CUDA availability..."
$SmokeTest = @"
try:
    import ctranslate2
    count = ctranslate2.get_cuda_device_count()
    if count > 0:
        print('CUDA path OK (%d device(s))' % count)
    else:
        print('No CUDA device found; will run on CPU')
except Exception as e:
    print('CUDA check failed (%s); will run on CPU' % e)
"@
$SmokeTestPath = Join-Path $env:TEMP "flowlocal_smoke_test.py"
Set-Content -Path $SmokeTestPath -Value $SmokeTest -Encoding utf8
& $VenvPython $SmokeTestPath
Remove-Item $SmokeTestPath -ErrorAction SilentlyContinue

# --- 5. Pre-download default model ----------------------------------------
Write-Host ""
Write-Host "Pre-downloading default model 'large-v3-turbo' (~1.6GB, one-time download)..." -ForegroundColor Cyan
$ModelDownload = @"
from faster_whisper import WhisperModel
try:
    WhisperModel('large-v3-turbo', device='cpu', compute_type='int8')
    print('Model downloaded/cached OK')
except Exception as e:
    print('Model download failed: %s' % e)
"@
$ModelDownloadPath = Join-Path $env:TEMP "flowlocal_model_download.py"
Set-Content -Path $ModelDownloadPath -Value $ModelDownload -Encoding utf8
& $VenvPython $ModelDownloadPath
Remove-Item $ModelDownloadPath -ErrorAction SilentlyContinue

# --- 6. Enable autostart ---------------------------------------------------
Write-Host "Enabling autostart..."
$AutostartScript = @"
import sys
sys.path.insert(0, r'$ProjectRoot')
from flowlocal import autostart
autostart.enable()
print('Autostart enabled')
"@
$AutostartScriptPath = Join-Path $env:TEMP "flowlocal_autostart.py"
Set-Content -Path $AutostartScriptPath -Value $AutostartScript -Encoding utf8
& $VenvPython $AutostartScriptPath
Remove-Item $AutostartScriptPath -ErrorAction SilentlyContinue

# --- 7. Ollama suggestion ---------------------------------------------------
Write-Host ""
Write-Host "Checking for Ollama (optional local LLM cleanup)..."
$OllamaOpen = Test-NetConnection -ComputerName "127.0.0.1" -Port 11434 -WarningAction SilentlyContinue -InformationLevel Quiet
if (-not $OllamaOpen) {
    Write-Host "Ollama not detected on port 11434." -ForegroundColor Yellow
    Write-Host "For better cleanup quality (grammar + false-start collapse), install Ollama:" -ForegroundColor Yellow
    Write-Host "  1. https://ollama.com/download" -ForegroundColor Yellow
    Write-Host "  2. ollama pull qwen2.5:7b-instruct" -ForegroundColor Yellow
    Write-Host "FlowLocal works fully without it (rule-based cleanup only)." -ForegroundColor Yellow
} else {
    Write-Host "Ollama detected on port 11434." -ForegroundColor Green
}

Write-Host ""
Write-Host "== Setup complete ==" -ForegroundColor Cyan
Write-Host "Run FlowLocal with: .venv\Scripts\pythonw.exe run_flowlocal.pyw"
