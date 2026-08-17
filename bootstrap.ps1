<#
.SYNOPSIS
    One-shot setup for the RTS Combat Overworld (Evennia) project on Windows.

.DESCRIPTION
    Idempotent bootstrap. Safe to re-run. It will:
      1. Verify a compatible Python interpreter (>= 3.12, 3.14 recommended).
      2. Create a virtualenv at .venv (if missing).
      3. Install the vendored Evennia + deps in editable mode (pip install -e .).
      4. Run the mygame first-time init (evennia --initmissing, evennia migrate).

    On the first `evennia migrate`, Evennia creates the "Account #1" superuser.
    By default this is an INTERACTIVE prompt (username / email / password), so
    run this script from a real terminal the first time. For non-interactive or
    CI use, set these environment variables before running and the superuser is
    created without prompting:
        EVENNIA_SUPERUSER_USERNAME, EVENNIA_SUPERUSER_PASSWORD
        (EVENNIA_SUPERUSER_EMAIL is optional)

    It intentionally does NOT run `evennia start` (that runs the live server in
    the foreground). After this script finishes, follow the printed next steps.

.PARAMETER SkipInit
    Skip the mygame init flow (--initmissing / migrate / superuser). Use when
    you only want the Python environment set up.

.EXAMPLE
    .\bootstrap.ps1

.EXAMPLE
    # Non-interactive (e.g. CI): create the superuser from env vars.
    $env:EVENNIA_SUPERUSER_USERNAME = 'admin'
    $env:EVENNIA_SUPERUSER_PASSWORD = 'change-me'
    .\bootstrap.ps1
#>
[CmdletBinding()]
param(
    [switch]$SkipInit
)

$ErrorActionPreference = 'Stop'

# Pin the interpreter requirements. 3.14 is recommended; 3.12 is the floor.
$MinMajor = 3
$MinMinor = 12
$RecommendedVersion = '3.14'

$RepoRoot = $PSScriptRoot
$VenvDir = Join-Path $RepoRoot '.venv'
$VenvPython = Join-Path $VenvDir 'Scripts\python.exe'
$VenvEvennia = Join-Path $VenvDir 'Scripts\evennia.exe'
$GameDir = Join-Path $RepoRoot 'mygame'

function Write-Step($msg) { Write-Host "==> $msg" -ForegroundColor Cyan }
function Write-Ok($msg)   { Write-Host "    $msg" -ForegroundColor Green }
function Write-Warn($msg)  { Write-Host "    $msg" -ForegroundColor Yellow }

# ---------------------------------------------------------------------------
# 1. Find a compatible Python interpreter (only needed to build the venv).
# ---------------------------------------------------------------------------
function Get-CompatiblePython {
    # Candidate commands, in preference order. The py launcher lets us ask for
    # an exact version; fall back to whatever `python` resolves to.
    $candidates = @(
        @{ Exe = 'py';     Args = @("-$RecommendedVersion") },
        @{ Exe = 'py';     Args = @('-3') },
        @{ Exe = 'python'; Args = @() },
        @{ Exe = 'python3'; Args = @() }
    )
    # Fallback: probe the standard per-user install locations directly. This
    # handles the common case where Python was just installed and the current
    # shell's PATH hasn't been refreshed yet. Prefer newer versions first.
    $programsRoot = Join-Path $env:LOCALAPPDATA 'Programs\Python'
    if (Test-Path $programsRoot) {
        Get-ChildItem $programsRoot -Directory -Filter 'Python3*' -ErrorAction SilentlyContinue |
            Sort-Object Name -Descending |
            ForEach-Object {
                $exe = Join-Path $_.FullName 'python.exe'
                if (Test-Path $exe) { $candidates += @{ Exe = $exe; Args = @() } }
            }
    }
    foreach ($c in $candidates) {
        $cmd = Get-Command $c.Exe -ErrorAction SilentlyContinue
        if (-not $cmd) { continue }
        # Skip the Windows Store alias stub, which isn't a real interpreter.
        if ($cmd.Source -like '*WindowsApps*') { continue }
        # NOTE: avoid embedding double quotes in the -c argument; PowerShell
        # strips inner quotes when passing args to native exes. Print the two
        # version components space-separated instead.
        try {
            $verOut = & $c.Exe @($c.Args) -c 'import sys; print(sys.version_info[0], sys.version_info[1])' 2>$null
        } catch { continue }
        if (-not $verOut) { continue }
        $parts = $verOut.Trim().Split(' ')
        if ($parts.Count -lt 2) { continue }
        $maj = [int]$parts[0]; $min = [int]$parts[1]
        if ($maj -gt $MinMajor -or ($maj -eq $MinMajor -and $min -ge $MinMinor)) {
            return @{ Exe = $c.Exe; Args = $c.Args; Version = "$maj.$min" }
        }
    }
    return $null
}

Write-Step "Checking for Python >= $MinMajor.$MinMinor (recommended $RecommendedVersion)..."
$py = Get-CompatiblePython
if (-not $py) {
    Write-Warn "No compatible Python interpreter found on PATH."
    Write-Host ""
    Write-Host "Install Python $RecommendedVersion, then re-run this script:" -ForegroundColor Yellow
    Write-Host "  - Download: https://www.python.org/downloads/" -ForegroundColor Yellow
    Write-Host "  - Or via winget: winget install Python.Python.3.14" -ForegroundColor Yellow
    Write-Host "  During install, tick 'Add python.exe to PATH'." -ForegroundColor Yellow
    exit 1
}
Write-Ok "Using $($py.Exe) $($py.Args -join ' ') -> Python $($py.Version)"

# ---------------------------------------------------------------------------
# 2. Create the virtualenv.
# ---------------------------------------------------------------------------
if (Test-Path $VenvPython) {
    Write-Step "Virtualenv already exists at .venv (reusing)."
} else {
    Write-Step "Creating virtualenv at .venv..."
    & $py.Exe @($py.Args) -m venv $VenvDir
    if (-not (Test-Path $VenvPython)) { throw "venv creation failed: $VenvPython not found." }
    Write-Ok "Created .venv"
}

# ---------------------------------------------------------------------------
# 3. Install the project (vendored Evennia + deps) in editable mode.
# ---------------------------------------------------------------------------
Write-Step "Upgrading pip..."
& $VenvPython -m pip install --upgrade pip | Out-Null

Write-Step "Installing project dependencies (pip install -e .) - this can take a few minutes..."
& $VenvPython -m pip install -e $RepoRoot
if ($LASTEXITCODE -ne 0) { throw "pip install -e . failed." }

# Sanity-check: import must resolve to the vendored copy, not a PyPI Evennia.
$resolved = & $VenvPython -c "import evennia; print(evennia.__file__)"
$expected = Join-Path $RepoRoot 'evennia'
if ($resolved -notlike "$expected*") {
    Write-Warn "WARNING: 'import evennia' resolved to $resolved"
    Write-Warn "Expected it under $expected (the vendored copy). Check for a stray pip-installed evennia."
} else {
    Write-Ok "evennia resolves to the vendored copy."
}
& $VenvEvennia --version

# ---------------------------------------------------------------------------
# 4. First-time game init (from mygame/).
# ---------------------------------------------------------------------------
if ($SkipInit) {
    Write-Step "Skipping game init (--SkipInit)."
} else {
    Write-Step "Initialising the game in mygame/ (--initmissing, migrate)..."
    if (-not $env:EVENNIA_SUPERUSER_USERNAME) {
        Write-Warn "First run: 'migrate' will prompt you to create the Account #1 superuser."
        Write-Warn "(Set EVENNIA_SUPERUSER_USERNAME/PASSWORD to do this non-interactively.)"
    }
    Push-Location $GameDir
    try {
        & $VenvEvennia --initmissing
        & $VenvEvennia migrate
        Write-Ok "Game initialised."
    } finally {
        Pop-Location
    }
}

# ---------------------------------------------------------------------------
# Done - print next steps.
# ---------------------------------------------------------------------------
Write-Host ""
Write-Step "Setup complete. Next steps:"
Write-Host "  1. Activate the venv:   .\.venv\Scripts\Activate.ps1"
Write-Host "  2. Go to the game dir:  cd mygame"
Write-Host "  3. Start the server:    evennia start"
Write-Host "     (if you skipped superuser creation above, start will prompt for it)"
Write-Host ""
Write-Host "  Connect: telnet localhost 4000  |  web client: http://localhost:4001"
