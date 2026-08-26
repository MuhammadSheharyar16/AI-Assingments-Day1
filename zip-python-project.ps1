# Zip Python Project
# Creates a clean zip of the current Python project:
#   - auto-detects the project's virtualenv (or uses the active one)
#   - runs pip freeze > requirements.txt using that venv
#   - excludes caches, virtualenvs, node_modules, etc. (.git IS included)
# Nothing in your project is deleted or modified (except requirements.txt).
#
# Usage:
#   powershell -ExecutionPolicy Bypass -File .\zip-python-project.ps1

$pipPath = $null

# 1a. Already-activated venv wins
if ($env:VIRTUAL_ENV -and (Test-Path "$env:VIRTUAL_ENV\Scripts\pip.exe")) {
    $pipPath = "$env:VIRTUAL_ENV\Scripts\pip.exe"
    Write-Host "Using active virtualenv: $env:VIRTUAL_ENV"
}

# 1b. Otherwise look for a venv folder in the project root
if (-not $pipPath) {
    foreach ($d in @("venv", ".venv", "env", "virtualenv")) {
        $candidate = Join-Path (Get-Location) "$d\Scripts\pip.exe"
        if (Test-Path $candidate) {
            $pipPath = $candidate
            Write-Host "Found virtualenv: .\$d"
            break
        }
    }
}

# 1c. Last resort: any folder with pyvenv.cfg, up to three levels deep
if (-not $pipPath) {
    $cfg = Get-ChildItem -Recurse -Depth 2 -Filter pyvenv.cfg -File -ErrorAction SilentlyContinue |
           Select-Object -First 1
    if ($cfg) {
        $candidate = Join-Path $cfg.DirectoryName "Scripts\pip.exe"
        if (Test-Path $candidate) {
            $pipPath = $candidate
            Write-Host "Found virtualenv: $($cfg.DirectoryName)"
        }
    }
}

if (-not $pipPath) {
    Write-Host "No virtualenv found in this folder."
    Write-Host "Expected one of: venv\  .venv\  env\  -- or activate yours and re-run."
    exit 1
}

# 2. Freeze dependencies
& $pipPath freeze > requirements.txt
$count = (Get-Content requirements.txt | Measure-Object -Line).Lines
Write-Host "requirements.txt updated ($count packages)"

# 3. Copy to a temp staging folder, skipping junk
#    (Compress-Archive has no exclude option, so we stage first)
$name  = Split-Path -Leaf (Get-Location)
$stage = Join-Path $env:TEMP "$name-zipstage"
$zip   = "..\$name.zip"

if (Test-Path $stage) { Remove-Item $stage -Recurse -Force }

robocopy . $stage /E /NFL /NDL /NJH /NJS /NP `
  /XD __pycache__ .pytest_cache .mypy_cache .ruff_cache .ipynb_checkpoints venv .venv env virtualenv node_modules `
  /XF *.pyc *.pyo *.sqlite3 .env | Out-Null

# 4. Zip the staged copy, then clean up the temp folder
if (Test-Path $zip) { Remove-Item $zip -Force }
Compress-Archive -Path "$stage\*" -DestinationPath $zip
Remove-Item $stage -Recurse -Force

Write-Host "Done -> $zip"
