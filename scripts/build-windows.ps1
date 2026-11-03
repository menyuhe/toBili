$ErrorActionPreference = 'Stop'
$root = Split-Path $PSScriptRoot -Parent
Set-Location $root
if (-not (Test-Path '.venv/Scripts/python.exe')) {
    python -m venv .venv
    if ($LASTEXITCODE -ne 0) { throw 'Python environment creation failed' }
}
$python = Join-Path $root '.venv/Scripts/python.exe'
& $python -m pip install -r requirements-desktop.txt
if ($LASTEXITCODE -ne 0) { throw 'Dependency installation failed' }
Push-Location upstream/web/frontend
try {
    npm.cmd ci
    if ($LASTEXITCODE -ne 0) { throw 'Frontend dependency installation failed' }
    npm.cmd run build
    if ($LASTEXITCODE -ne 0) { throw 'Frontend build failed' }
} finally { Pop-Location }
& $python -m PyInstaller scripts/desktop.spec --noconfirm --distpath build-output
if ($LASTEXITCODE -ne 0) { throw 'Desktop build failed' }
& $python scripts/package_release.py windows
if ($LASTEXITCODE -ne 0) { throw 'Release packaging failed' }
