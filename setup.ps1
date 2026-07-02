# FlowLocal setup script — idempotent, PowerShell 5.1 compatible (no &&).
#
# - Verifies py -3.11 is available
# - Creates .venv if missing
# - Installs requirements.txt + NVIDIA CUDA 12 runtime libs (GPU path)
# - Smoke-tests faster_whisper CUDA availability (falls back to CPU warning)
# - Pre-downloads the default model (large-v3-turbo, ~1.6GB one-time download)
# - Enables autostart
# - Prints an Ollama install suggestion if port 11434 is closed

$ErrorActionPreference = "Stop"

$ProjectRoot = Split-Path -Parent $MyInvocation.MyCommand.Path
Set-Location $ProjectRoot

Write-Host "== FlowLocal setup ==" -ForegroundColor Cyan

# --- 1. Check py -3.11 -------------------------------------------------
Write-Host "Checking for Python 3.11 launcher..."
$pyCheck = & py -3.11 --version 2>&1
if ($LASTEXITCODE -ne 0) {
    Write-Host "ERROR: 'py -3.11' is not available. Install Python 3.11 first." -ForegroundColor Red
    exit 1
}
Write-Host "Found: $pyCheck"

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

Write-Host "Installing NVIDIA cuBLAS/cuDNN runtime (GPU path)..."
& $VenvPython -m pip install nvidia-cublas-cu12 nvidia-cudnn-cu12
if ($LASTEXITCODE -ne 0) {
    Write-Host "WARNING: NVIDIA CUDA runtime packages failed to install; GPU path may be unavailable, will fall back to CPU." -ForegroundColor Yellow
}

# --- 4. GPU smoke test ---------------------------------------------------
Write-Host "Smoke-testing faster-whisper CUDA path..."
$SmokeTest = @"
import sys
try:
    import os, glob
    for d in glob.glob(os.path.join(os.path.dirname(__file__) if '__file__' in dir() else '.', '*')):
        pass
    from faster_whisper import WhisperModel
    try:
        m = WhisperModel('small', device='cuda', compute_type='float16')
        print('CUDA path OK')
    except Exception as e:
        print('CUDA path failed (%s); will run on CPU' % e)
except Exception as e:
    print('faster_whisper import failed: %s' % e)
    sys.exit(1)
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
