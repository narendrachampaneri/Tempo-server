# Tempo-server installer for Windows (PowerShell 5.1 or 7).
#
#   powershell -ExecutionPolicy ByPass -c "irm https://raw.githubusercontent.com/narendrachampaneri/Tempo-server/main/install.ps1 | iex"
#
# Installs uv (or uses pipx or uv if you have one), installs tempo-server as its own isolated
# tool, then runs `tempo-server setup`. No administrator rights, no virtual environment by hand,
# no GPU.
#
# Settings (environment variables, all optional):
#   TEMPO_SERVER_INSTALLER   auto (default: uv if present, else pipx if present, else install uv),
#                            uv, or pipx
#   TEMPO_SERVER_SOURCE      what to install (default: the latest source from GitHub; the PyPI
#                            name `tempo-server` once it is published; or a local wheel file)
#   TEMPO_SERVER_PYTHON      Python version uv installs it with (default 3.12; uv downloads it
#                            if needed)
#   TEMPO_SERVER_NO_SETUP    1 to skip `tempo-server setup`
#   TEMPO_SERVER_SETUP_ARGS  arguments for setup when there is no console to ask in
#                            (default: --non-interactive)

$ErrorActionPreference = "Stop"

$Source = if ($env:TEMPO_SERVER_SOURCE) { $env:TEMPO_SERVER_SOURCE } else {
    "https://github.com/narendrachampaneri/Tempo-server/archive/refs/heads/main.zip"
}
$Tool = if ($env:TEMPO_SERVER_INSTALLER) { $env:TEMPO_SERVER_INSTALLER } else { "auto" }
$PythonVersion = if ($env:TEMPO_SERVER_PYTHON) { $env:TEMPO_SERVER_PYTHON } else { "3.12" }

function Say([string]$Text) { Write-Host "==> $Text" }
function Fail([string]$Text) { Write-Error "Error: $Text"; exit 1 }
function Has([string]$Name) { [bool](Get-Command $Name -ErrorAction SilentlyContinue) }
function Run([string]$Exe, [string[]]$Arguments) {
    & $Exe @Arguments
    if ($LASTEXITCODE -ne 0) { Fail "$Exe $($Arguments -join ' ') failed (exit $LASTEXITCODE)" }
}

if ($Tool -eq "auto") {
    if (Has "uv") { $Tool = "uv" } elseif (Has "pipx") { $Tool = "pipx" } else { $Tool = "uv" }
}

$LocalBin = Join-Path $env:USERPROFILE ".local\bin"

switch ($Tool) {
    "uv" {
        if (-not (Has "uv")) {
            Say "Installing uv (https://docs.astral.sh/uv/), which installs Python tools"
            powershell -NoProfile -ExecutionPolicy ByPass -Command "irm https://astral.sh/uv/install.ps1 | iex"
            $env:Path = "$LocalBin;$env:USERPROFILE\.cargo\bin;$env:Path"
            if (-not (Has "uv")) { Fail "uv was installed but is not on PATH; open a new terminal and run this again" }
        }
        Say "Installing tempo-server with uv"
        Run "uv" @("tool", "install", "--force", "--python", $PythonVersion, $Source)
        & uv tool update-shell *> $null
        $BinDir = (& uv tool dir --bin).Trim()
    }
    "pipx" {
        $Pipx = @("pipx")
        if (-not (Has "pipx")) {
            $Py = if (Has "py") { "py" } elseif (Has "python") { "python" } else { $null }
            if (-not $Py) { Fail "Python 3.11+ is needed for pipx (or set TEMPO_SERVER_INSTALLER=uv)" }
            Say "Installing pipx (https://pipx.pypa.io)"
            Run $Py @("-m", "pip", "install", "--user", "--quiet", "pipx")
            $Pipx = @($Py, "-m", "pipx")
        }
        Say "Installing tempo-server with pipx"
        $Exe = $Pipx[0]; $Pre = @($Pipx | Select-Object -Skip 1)
        Run $Exe ($Pre + @("install", "--force", $Source))
        & $Exe @($Pre + @("ensurepath")) *> $null
        $BinDir = (& $Exe @($Pre + @("environment", "--value", "PIPX_BIN_DIR"))).Trim()
        if (-not $BinDir) { $BinDir = $LocalBin }
    }
    default { Fail "TEMPO_SERVER_INSTALLER must be auto, uv or pipx (got $Tool)" }
}

$Bin = Join-Path $BinDir "tempo-server.exe"
if (-not (Test-Path $Bin)) {
    $Found = Get-Command tempo-server -ErrorAction SilentlyContinue
    if (-not $Found) { Fail "tempo-server was installed but could not be found" }
    $Bin = $Found.Source
}
Say "Installed $(& $Bin --version)"

if ($env:TEMPO_SERVER_NO_SETUP -ne "1") {
    $Interactive = [Environment]::UserInteractive -and -not [Console]::IsInputRedirected -and -not $env:CI
    if ($Interactive) {
        & $Bin setup
    } else {
        $SetupArgs = if ($env:TEMPO_SERVER_SETUP_ARGS) { $env:TEMPO_SERVER_SETUP_ARGS -split " " } else { @("--non-interactive") }
        Run $Bin (@("setup") + $SetupArgs)
    }
}

if (($env:Path -split ";") -notcontains $BinDir) {
    Say "Open a new terminal so that tempo-server is on your PATH ($BinDir)."
}
Say "Done. Start it with: tempo-server serve   (then open http://127.0.0.1:8000)"
Say "Problems? Run: tempo-server doctor"
