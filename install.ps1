#Requires -Version 5.1
<#
  Ari source installer — Windows (native PowerShell, no WSL required).

    iex (irm https://raw.githubusercontent.com/Gabow135/ari/main/install.ps1)

  Bootstraps the prerequisites Ari needs (Git, Python >=3.11, Node/npm, uv, the
  Claude Code CLI), checks out the source into %USERPROFILE%\.ari, builds the
  virtualenv and drops an `ari` launcher on your PATH. It never replaces tools
  you already have.

  Tunables (optional env vars): ARI_HOME, ARI_REPO, ARI_REF, ARI_BIN_DIR.
#>
$ErrorActionPreference = 'Stop'

$Slug    = 'Gabow135/ari'
$AriHome = if ($env:ARI_HOME)    { $env:ARI_HOME }    else { Join-Path $env:USERPROFILE '.ari' }
$Repo    = if ($env:ARI_REPO)    { $env:ARI_REPO }    else { "https://github.com/$Slug.git" }
$BinDir  = if ($env:ARI_BIN_DIR) { $env:ARI_BIN_DIR } else { Join-Path $AriHome 'bin' }
$Venv    = Join-Path $AriHome '.venv'

function Info($m) { Write-Host "==> $m" -ForegroundColor Cyan }
function Ok($m)   { Write-Host "  ok $m" -ForegroundColor Green }
function Warn($m) { Write-Host "warn $m" -ForegroundColor Yellow }
function Die($m)  { Write-Host "error $m" -ForegroundColor Red; exit 1 }
function Have($c) { [bool](Get-Command $c -ErrorAction SilentlyContinue) }

function Refresh-Path {
  $env:Path = [Environment]::GetEnvironmentVariable('Path', 'Machine') + ';' +
              [Environment]::GetEnvironmentVariable('Path', 'User')
}

# Pm-Install <winget-id> <scoop-pkg> <choco-pkg>
function Pm-Install($wingetId, $scoopPkg, $chocoPkg) {
  if (Have winget) { winget install --silent -e --id $wingetId `
      --accept-source-agreements --accept-package-agreements; return }
  if (Have scoop)  { scoop install $scoopPkg; return }
  if (Have choco)  { choco install -y $chocoPkg; return }
  throw "No package manager (winget/scoop/choco) available to install $wingetId."
}

function Find-Python {
  foreach ($c in @('python', 'python3')) {
    if (Have $c) {
      try {
        $v = & $c -c 'import sys;print("%d.%d"%sys.version_info[:2])' 2>$null
        if ($v -match '^3\.(\d+)$' -and [int]$Matches[1] -ge 11) { return $c }
      } catch { }
    }
  }
  return $null
}

function Ensure-Prereqs {
  Info 'Checking prerequisites...'

  if (-not (Have git)) { Info 'Installing Git'; Pm-Install 'Git.Git' 'git' 'git'; Refresh-Path }

  $py = Find-Python
  if (-not $py) {
    Info 'Installing Python 3.12'
    Pm-Install 'Python.Python.3.12' 'python' 'python'
    Refresh-Path
    $py = Find-Python
  }
  if (-not $py) { Die 'Python >=3.11 is required but could not be installed automatically.' }
  Ok "Python: $(& $py --version)"

  if (-not (Have npm)) { Info 'Installing Node.js + npm'; Pm-Install 'OpenJS.NodeJS' 'nodejs' 'nodejs'; Refresh-Path }
  if (Have node) { Ok "Node: $(node --version)" } else { Warn 'Node/npm missing — npx MCP servers (mysql, filesystem, email) need it.' }

  if (-not (Have uv)) { Info 'Installing uv (astral.sh)'; Invoke-RestMethod https://astral.sh/uv/install.ps1 | Invoke-Expression; Refresh-Path }
  if (-not (Have uv)) { $env:Path = "$env:USERPROFILE\.local\bin;$env:Path" }
  if (-not (Have uv)) { Die 'uv not on PATH after install — open a new terminal and re-run.' }
  Ok "uv: $(uv --version)"

  if (-not (Have claude)) {
    Info 'Installing the Claude Code CLI'
    if (Have npm) {
      try { npm install -g '@anthropic-ai/claude-code' | Out-Null; Ok 'Claude Code CLI installed' }
      catch { Warn 'Could not install the Claude Code CLI — run: npm install -g @anthropic-ai/claude-code' }
    } else { Warn 'npm unavailable — install the Claude Code CLI manually.' }
  } else { Ok 'Claude Code CLI: present' }

  return $py
}

function Resolve-Ref {
  if ($env:ARI_REF) { return $env:ARI_REF }
  try {
    $r = Invoke-RestMethod "https://api.github.com/repos/$Slug/releases/latest" -Headers @{ 'User-Agent' = 'ari-installer' }
    if ($r.tag_name) { return $r.tag_name }
  } catch { }
  return 'main'
}

function Fetch-Source {
  $ref = Resolve-Ref
  if (Test-Path (Join-Path $AriHome '.git')) {
    Info "Updating Ari in $AriHome (ref: $ref)"
    git -C $AriHome fetch --tags --quiet origin
    git -C $AriHome checkout --quiet $ref
    git -C $AriHome pull --quiet --ff-only origin $ref 2>$null
  } else {
    Info "Cloning Ari into $AriHome (ref: $ref)"
    git clone --quiet $Repo $AriHome
    git -C $AriHome checkout --quiet $ref
  }
  Ok ("Source at " + (git -C $AriHome rev-parse --short HEAD))
}

function Build-Venv($py) {
  Info 'Creating virtualenv and installing dependencies (this can take a minute)...'
  $pyExe = (Get-Command $py -ErrorAction SilentlyContinue).Source
  if (-not $pyExe) { $pyExe = $py }
  uv venv --python $pyExe $Venv | Out-Null
  uv pip install --python (Join-Path $Venv 'Scripts\python.exe') -e $AriHome | Out-Null
  Ok "Dependencies installed into $Venv"
}

function Install-Launcher {
  New-Item -ItemType Directory -Force -Path $BinDir | Out-Null
  $cmd   = Join-Path $BinDir 'ari.cmd'
  $pyExe = Join-Path $Venv 'Scripts\python.exe'
  @"
@echo off
"$pyExe" -m ari.main %*
"@ | Set-Content -Path $cmd -Encoding ASCII
  Ok "Launcher installed: $cmd"

  $userPath = [Environment]::GetEnvironmentVariable('Path', 'User')
  if ($userPath -notlike "*$BinDir*") {
    [Environment]::SetEnvironmentVariable('Path', "$userPath;$BinDir", 'User')
    Warn "Added $BinDir to your user PATH — open a NEW terminal for 'ari' to resolve."
  }
}

function Seed-Env {
  $envFile = Join-Path $AriHome '.env'
  $tplFile = Join-Path $AriHome '.env.example'
  if ((-not (Test-Path $envFile)) -and (Test-Path $tplFile)) {
    Copy-Item $tplFile $envFile
    Ok "Created $envFile from the template"
  }
}

Write-Host 'Ari installer — Windows (native PowerShell)' -ForegroundColor White
$py = Ensure-Prereqs
Fetch-Source
Build-Venv $py
Install-Launcher
Seed-Env

Write-Host ''
Write-Host 'Ari is installed.' -ForegroundColor Green
Info 'Next steps:'
Write-Host '  1. claude login                  # authenticate the Claude Code CLI'
Write-Host "  2. edit $AriHome\.env            # Telegram token, owner id, credentials"
Write-Host '  3. run ari   (in a NEW terminal)'
